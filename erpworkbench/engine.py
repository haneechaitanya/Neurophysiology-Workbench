from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional
import os
import platform
import subprocess
import json
import hashlib
import warnings as py_warnings
from datetime import datetime, timezone

import mne
import numpy as np
import pandas as pd
from mne.preprocessing import ICA

from .models import (
    ComponentDefinition,
    EpochReviewState,
    EventGroupRule,
    MeasurementResult,
    PreprocessingSettings,
    ProtocolDefinition,
)


ProgressCallback = Optional[Callable[[str], None]]


def _progress(cb: ProgressCallback, message: str) -> None:
    if cb:
        cb(message)


def load_raw(path: str | Path, preload: bool = True):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".edf":
        return mne.io.read_raw_edf(path, preload=preload, verbose="ERROR")
    if suffix in {".fif", ".gz"} or path.name.lower().endswith(".fif.gz"):
        return mne.io.read_raw_fif(path, preload=preload, verbose="ERROR")
    raise ValueError("Only EDF and FIF files are supported in this build.")




def _eeg_position_count(raw) -> tuple[int, int]:
    """Return (channels_with_finite_nonzero_xyz, total_eeg_channels)."""
    picks = mne.pick_types(raw.info, eeg=True, exclude=[])
    good = 0
    for idx in picks:
        loc = np.asarray(raw.info["chs"][int(idx)].get("loc", np.zeros(12))[:3], dtype=float)
        if np.all(np.isfinite(loc)) and float(np.linalg.norm(loc)) > 1e-6:
            good += 1
    return good, int(len(picks))


def ensure_eeg_montage(raw, candidates=("standard_1020", "standard_1005", "biosemi32")) -> dict:
    """Attach standard sensor coordinates when an EDF/FIF has channel names but no digitization.

    This changes only channel-location metadata, never EEG samples. Existing usable
    positions are preserved. A standard montage is inferred only when at least 60%
    of EEG channel names match (and at least 3 channels match).
    """
    positioned, total = _eeg_position_count(raw)
    if total == 0:
        return {"applied": False, "source": "none", "matched": 0, "total": 0}
    if positioned >= max(3, int(np.ceil(total * 0.6))):
        return {"applied": False, "source": "existing", "matched": positioned, "total": total}

    eeg_names = [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True, exclude=[])]
    lower = {name.lower() for name in eeg_names}
    best = None
    for name in candidates:
        montage = mne.channels.make_standard_montage(name)
        montage_names = {ch.lower() for ch in montage.ch_names}
        matched = sum(ch in montage_names for ch in lower)
        if best is None or matched > best[0]:
            best = (matched, name, montage)
    matched, name, montage = best
    threshold = max(3, int(np.ceil(total * 0.6)))
    if matched < threshold:
        return {"applied": False, "source": "unresolved", "matched": int(matched), "total": total}

    raw.set_montage(montage, match_case=False, on_missing="ignore", verbose="ERROR")
    positioned_after, _ = _eeg_position_count(raw)
    return {
        "applied": True,
        "source": name,
        "matched": int(matched),
        "positioned": int(positioned_after),
        "total": total,
    }


def find_companion_annotation_file(recording_path: str | Path) -> Optional[Path]:
    """Find an exported ``*_Annotation.txt`` beside an EDF/FIF.

    Auto-attachment is deliberately conservative: only an annotation file whose
    stem matches the recording stem plus ``_Annotation`` (case-insensitive) is
    selected. Ambiguous/general ``*Annotation*.txt`` files are never guessed.
    """
    recording_path = Path(recording_path)
    stem = recording_path.name
    if stem.lower().endswith('.fif.gz'):
        stem = stem[:-7]
    else:
        stem = recording_path.stem
    target = f"{stem}_annotation.txt".lower()
    matches = [p for p in recording_path.parent.glob("*.txt") if p.name.lower() == target]
    return matches[0] if len(matches) == 1 else None


def read_annotation_txt(path: str | Path) -> pd.DataFrame:
    """Read the tab-separated ERP annotation export used by the recorder.

    Required columns are ``Name`` and ``latency``. Latency is interpreted as
    seconds from recording start. Negative/unknown durations are treated as
    zero-duration point markers. The original columns are preserved and a
    normalized ``description`` column is added for MNE/display use.
    """
    path = Path(path)
    try:
        df = pd.read_csv(
            path, sep="\t", dtype=str, keep_default_na=False,
            encoding="utf-8-sig", encoding_errors="replace",
        )
    except Exception as exc:
        raise ValueError(f"Could not read annotation file: {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    required = {"Name", "latency"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            "This annotation file is missing required column(s): " + ", ".join(missing)
        )

    latency = pd.to_numeric(df["latency"], errors="coerce")
    invalid_latency = latency.isna()
    if invalid_latency.any():
        bad_rows = ", ".join(str(i + 2) for i in np.flatnonzero(invalid_latency.to_numpy())[:10])
        raise ValueError(
            f"{int(invalid_latency.sum())} annotation row(s) have invalid latency values"
            + (f" (for example file row(s) {bad_rows})." if bad_rows else ".")
        )

    if "duration" in df.columns:
        duration = pd.to_numeric(df["duration"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        duration = pd.Series(np.zeros(len(df), dtype=float), index=df.index)

    if "number" in df.columns:
        numbers = df["number"].astype(str).str.strip()
    else:
        numbers = pd.Series([str(i + 1) for i in range(len(df))], index=df.index)

    names = df["Name"].astype(str)
    descriptions = []
    for number, name in zip(numbers, names):
        clean = name.strip()
        descriptions.append(clean if clean else f"[unnamed event #{number}]")

    out = df.copy()
    out["latency"] = latency.astype(float)
    out["duration"] = duration.astype(float)
    out["description"] = descriptions
    return out


def attach_external_annotations(raw, base_annotations, table: pd.DataFrame) -> dict:
    """Attach recorder annotations while protecting against exact duplicates.

    Some EDF/FIF exports already contain event annotations. Adding the companion
    recorder TXT on top of those can otherwise create duplicate markers and,
    more importantly, duplicate ERP epochs. We only de-duplicate markers with
    the same description on the same sample; different events at the same time
    are deliberately preserved.
    """
    if raw is None:
        raise ValueError("No recording is loaded.")
    if table is None or table.empty:
        raise ValueError("The annotation file contains no event rows.")

    sfreq = float(raw.info["sfreq"])
    total_duration = float(raw.n_times) / sfreq if raw.n_times else 0.0
    tolerance = max(1.0 / sfreq, 1e-6)
    onset = table["latency"].to_numpy(dtype=float)
    in_range = (onset >= -tolerance) & (onset <= total_duration + tolerance)

    if not np.any(in_range):
        raise ValueError(
            f"None of the {len(table)} annotation markers fall within this recording "
            f"(duration {total_duration:.3f} s). Check that the TXT belongs to this EDF/FIF."
        )

    annotations = base_annotations.copy() if base_annotations is not None else mne.Annotations([], [], [])
    existing = {
        (int(round(float(on) * sfreq)), str(desc))
        for on, desc in zip(annotations.onset, annotations.description)
    }
    added = 0
    duplicates = 0
    valid = table.loc[in_range]
    for row in valid.itertuples(index=False):
        event_onset = max(0.0, float(row.latency))
        description = str(row.description)
        key = (int(round(event_onset * sfreq)), description)
        if key in existing:
            duplicates += 1
            continue
        annotations.append(
            event_onset,
            max(0.0, float(row.duration)),
            description,
        )
        existing.add(key)
        added += 1
    raw.set_annotations(annotations, verbose="ERROR")

    return {
        "total": int(len(table)),
        "attached": int(added),
        "duplicates_skipped": int(duplicates),
        "out_of_range": int(np.sum(~in_range)),
        "recording_duration_sec": total_duration,
        "annotation_last_latency_sec": float(np.max(onset)),
    }


def apply_montage_if_possible(raw, montage_name: str) -> list[str]:
    """Apply a standard montage; return channel names not matched by the montage."""
    if not montage_name or montage_name.lower() == "none":
        return []
    montage = mne.channels.make_standard_montage(montage_name)
    montage_names = {x.lower() for x in montage.ch_names}
    eeg_names = [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True, exclude=[])]
    missing = [ch for ch in eeg_names if ch.lower() not in montage_names]
    raw.set_montage(montage, match_case=False, on_missing="warn", verbose="ERROR")
    return missing


def _apply_interpolation_step(raw, settings: PreprocessingSettings, progress: ProgressCallback = None):
    if not settings.interpolation.enabled or not settings.interpolation.bad_channels:
        return
    _progress(progress, "Preparing montage and interpolating bad channels …")
    apply_montage_if_possible(raw, settings.interpolation.montage)
    valid_bads = [ch for ch in settings.interpolation.bad_channels if ch in raw.ch_names]
    raw.info["bads"] = valid_bads
    if valid_bads:
        raw.interpolate_bads(reset_bads=False, verbose="ERROR")


def _apply_reference_step(raw, settings: PreprocessingSettings, progress: ProgressCallback = None):
    if not settings.reference.enabled:
        return
    _progress(progress, "Applying EEG reference …")
    if settings.reference.mode == "average":
        raw.set_eeg_reference(ref_channels="average", projection=False, verbose="ERROR")
    elif settings.reference.mode == "custom":
        refs = [ch for ch in settings.reference.custom_channels if ch in raw.ch_names]
        if not refs:
            raise ValueError("Custom re-reference is enabled but no valid reference channels were selected.")
        raw.set_eeg_reference(ref_channels=refs, projection=False, verbose="ERROR")
    else:
        raise ValueError(f"Unsupported reference mode: {settings.reference.mode}")


def apply_preprocessing(
    original_raw,
    settings: PreprocessingSettings,
    step_order: Optional[list[str]] = None,
    progress: ProgressCallback = None,
):
    """Rebuild preprocessing deterministically from the imported recording.

    Filtering is intentionally treated as order-independent by the GUI and is
    therefore applied first on every deterministic rebuild. Interpolation and
    re-referencing are applied in the user-visible ``step_order``. ICA is not
    performed here; it remains an explicit fit/review/apply stage.
    """
    raw = original_raw.copy().load_data()

    if settings.filter.enabled:
        l_freq = settings.filter.l_freq
        h_freq = settings.filter.h_freq
        _progress(progress, f"Filtering {l_freq or 0:g}–{h_freq or '∞'} Hz …")
        raw.filter(l_freq=l_freq, h_freq=h_freq, picks="eeg", verbose="ERROR")
        if settings.filter.notch_enabled and settings.filter.notch_freq > 0:
            _progress(progress, f"Applying {settings.filter.notch_freq:g} Hz notch …")
            raw.notch_filter(
                freqs=[settings.filter.notch_freq], picks="eeg", verbose="ERROR"
            )

    requested = [x for x in (step_order or settings.step_order or []) if x in {"interpolation", "reference"}]
    # Preserve the requested order while avoiding duplicates. If an enabled
    # structural step has no order entry (e.g. loading an older protocol), use
    # the physiologically sensible fallback interpolation -> reference.
    ordered = []
    for step in requested + ["interpolation", "reference"]:
        if step not in ordered:
            ordered.append(step)

    for step in ordered:
        if step == "interpolation":
            _apply_interpolation_step(raw, settings, progress)
        elif step == "reference":
            _apply_reference_step(raw, settings, progress)

    _progress(progress, "Preprocessing complete.")
    return raw


def preview_preprocessing_segment(
    original_raw,
    start_sec: float,
    duration_sec: float,
    settings: PreprocessingSettings,
    step_order: Optional[list[str]] = None,
    pad_sec: float = 2.0,
):
    """Preview the *combined* active preprocessing on the visible time region.

    A padded Raw copy is cropped, processed with the same deterministic pipeline
    used for the full recording, and then trimmed back to the requested visible
    interval. This keeps interpolation and re-reference previews consistent with
    the filter preview while leaving the imported Raw untouched.
    """
    sfreq = float(original_raw.info["sfreq"])
    total = float(original_raw.n_times) / sfreq if original_raw.n_times else 0.0
    if settings.filter.enabled and settings.filter.l_freq and settings.filter.l_freq > 0:
        pad_sec = max(pad_sec, min(10.0, 3.0 / float(settings.filter.l_freq)))
    a_sec = max(0.0, float(start_sec) - pad_sec)
    b_sec = min(total, float(start_sec) + float(duration_sec) + pad_sec)
    a = max(0, int(round(a_sec * sfreq)))
    b = min(original_raw.n_times, int(round(b_sec * sfreq)) + 1)

    segment = original_raw.copy().crop(
        tmin=a / sfreq,
        tmax=max(a / sfreq, (b - 1) / sfreq),
        include_tmax=True,
        verbose="ERROR",
    ).load_data()
    processed = apply_preprocessing(segment, settings, step_order=step_order, progress=None)

    crop_a = max(0, int(round((float(start_sec) - a_sec) * sfreq)))
    crop_b = min(processed.n_times, crop_a + int(round(float(duration_sec) * sfreq)) + 1)
    picks = mne.pick_types(processed.info, eeg=True, exclude=[])
    data = processed.get_data(picks=picks, start=crop_a, stop=crop_b)
    names = [processed.ch_names[p] for p in picks]
    return data, names, sfreq


def detect_compute_hardware(progress: ProgressCallback = None) -> dict:
    """Return a lightweight, non-invasive graphics/CUDA capability summary.

    The function reports adapters but deliberately does not claim that an iGPU
    can accelerate ICA. Current MNE ICA fitting is CPU-based. MNE CUDA support
    applies only to operations whose APIs explicitly accept ``n_jobs='cuda'``.
    """
    adapters: list[str] = []
    if platform.system().lower() == "windows":
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
            ]
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            adapters = [x.strip() for x in cp.stdout.splitlines() if x.strip()]
        except Exception:
            adapters = []

    integrated = [x for x in adapters if "intel" in x.lower() or "iris" in x.lower()]
    nvidia = [x for x in adapters if "nvidia" in x.lower()]
    cupy_ready = False
    cuda_devices = 0
    cupy_error = ""
    try:
        import cupy  # type: ignore
        cuda_devices = int(cupy.cuda.runtime.getDeviceCount())
        cupy_ready = cuda_devices > 0
    except Exception as exc:
        cupy_error = str(exc)

    return {
        "platform": platform.platform(),
        "adapters": adapters,
        "integrated_adapters": integrated,
        "nvidia_adapters": nvidia,
        "cupy_ready": cupy_ready,
        "cuda_devices": cuda_devices,
        "cupy_error": cupy_error,
        "ica_gpu_supported": False,
    }

def filtered_visible_segment(
    raw,
    start_sec: float,
    duration_sec: float,
    l_freq: Optional[float],
    h_freq: Optional[float],
    notch_freq: Optional[float] = None,
    pad_sec: float = 2.0,
):
    """Filter a padded visible segment for responsive preview without changing Raw."""
    sfreq = float(raw.info["sfreq"])
    total = raw.times[-1] if raw.n_times else 0.0
    if l_freq and l_freq > 0:
        pad_sec = max(pad_sec, min(10.0, 3.0 / float(l_freq)))
    a = max(0.0, start_sec - pad_sec)
    b = min(total, start_sec + duration_sec + pad_sec)
    start = max(0, int(round(a * sfreq)))
    stop = min(raw.n_times, int(round(b * sfreq)) + 1)
    picks = mne.pick_types(raw.info, eeg=True, exclude=[])
    data = raw.get_data(picks=picks, start=start, stop=stop)
    names = [raw.ch_names[p] for p in picks]
    seg_info = mne.create_info(names, sfreq, ch_types="eeg")
    seg = mne.io.RawArray(data, seg_info, verbose="ERROR")
    seg.filter(l_freq=l_freq, h_freq=h_freq, verbose="ERROR")
    if notch_freq and notch_freq > 0:
        seg.notch_filter(freqs=[notch_freq], verbose="ERROR")
    crop_a = max(0, int(round((start_sec - a) * sfreq)))
    crop_b = min(seg.n_times, crop_a + int(round(duration_sec * sfreq)) + 1)
    return seg.get_data(start=crop_a, stop=crop_b), names, sfreq


def run_ica(
    raw,
    method: str = "fastica",
    n_components=0.99,
    random_state: int = 97,
    decim: Optional[int] = None,
    fit_exclude_spans: Optional[list[dict]] = None,
    progress: ProgressCallback = None,
) -> ICA:
    _progress(progress, "Preparing ICA fit data …")
    fit_raw = raw.copy().load_data()
    ensure_eeg_montage(fit_raw)

    # ICA-fit-only exclusions.  They are added to the temporary fit copy as
    # BAD annotations so MNE's reject_by_annotation=True omits those samples.
    # The user's processed Raw is never modified by this operation.
    valid_spans = []
    duration_sec = float(fit_raw.n_times) / float(fit_raw.info["sfreq"]) if fit_raw.n_times else 0.0
    for span in list(fit_exclude_spans or []):
        try:
            start = max(0.0, float(span.get("start_sec", 0.0)))
            end = min(duration_sec, float(span.get("end_sec", start)))
        except Exception:
            continue
        if end <= start:
            continue
        reason = str(span.get("reason", "artifact") or "artifact").strip().replace(" ", "_")
        fit_raw.annotations.append(start, end - start, f"BAD_ICA_EXCLUDE_{reason}")
        valid_spans.append({"start_sec": start, "end_sec": end, "reason": reason})
    if valid_spans:
        total_bad = sum(x["end_sec"] - x["start_sec"] for x in valid_spans)
        _progress(progress, f"ICA fit will omit {len(valid_spans)} marked span(s), {total_bad:.2f} s total …")

    if float(fit_raw.info.get("highpass", 0.0) or 0.0) < 1.0:
        # MNE recommends high-pass filtering around 1 Hz for ICA fitting.
        fit_raw.filter(1.0, None, picks="eeg", verbose="ERROR")
    decim = None if decim in (None, 0, 1) else max(2, int(decim))
    decim_text = "all samples" if decim is None else f"every {decim}th sample"

    fit_params = None
    mne_method = method
    display_method = method
    if method in {"infomax_extended", "extended_infomax", "infomax (extended)"}:
        mne_method = "infomax"
        fit_params = dict(extended=True)
        display_method = "extended infomax"

    _progress(progress, f"Fitting ICA ({display_method}; {decim_text}) …")
    ica = ICA(
        n_components=n_components,
        method=mne_method,
        fit_params=fit_params,
        random_state=random_state,
        max_iter="auto",
    )
    ica.fit(
        fit_raw, picks="eeg", reject_by_annotation=True, decim=decim, verbose="ERROR"
    )
    # Preserve the user-facing variant because MNE's .method stores only 'infomax'.
    ica._erpworkbench_method = method
    ica._erpworkbench_fit_exclusions = valid_spans
    _progress(progress, f"ICA fit complete: {ica.n_components_} components from {ica.n_samples_} samples.")
    return ica


def auto_label_ica_components(raw, ica: ICA, progress: ProgressCallback = None) -> dict:
    """Classify fitted EEG ICA components with MNE-ICALabel/ICLabel.

    Classification is advisory only: this function never marks or removes a
    component. The GUI keeps the user's removal checkboxes fully manual.
    """
    _progress(progress, "Preparing data for ICLabel …")
    try:
        from mne_icalabel import label_components
    except Exception as exc:
        raise RuntimeError(
            "ICLabel support is unavailable in this installation. "
            "Install the optional mne-icalabel dependency to use automatic classification; manual ICA review remains available."
        ) from exc

    inst = raw.copy().load_data()
    montage_info = ensure_eeg_montage(inst)
    if float(inst.info.get("highpass", 0.0) or 0.0) < 1.0:
        inst.filter(1.0, None, picks="eeg", verbose="ERROR")

    _progress(progress, "Running ICLabel component classifier …")
    # MNE-ICALabel emits RuntimeWarnings when the data/ICA differ from the
    # conditions on which ICLabel was validated (CAR, 1–100 Hz, extended
    # Infomax). Capture those warnings instead of writing them to a terminal;
    # the GUI exposes the same compatibility information as a tooltip/note.
    with py_warnings.catch_warnings(record=True) as caught:
        py_warnings.simplefilter("always", RuntimeWarning)
        result = label_components(inst, ica, method="iclabel")
    runtime_notes = []
    for warning_record in caught:
        message = str(warning_record.message).strip()
        if message and message not in runtime_notes:
            runtime_notes.append(message)
    labels = [str(x) for x in result.get("labels", [])]
    probs = np.asarray(result.get("y_pred_proba", []), dtype=float)
    if len(labels) != int(ica.n_components_):
        raise RuntimeError(
            f"ICLabel returned {len(labels)} labels for {ica.n_components_} ICA components."
        )
    if probs.ndim != 1 or probs.size != len(labels):
        probs = np.full(len(labels), np.nan, dtype=float)

    user_method = getattr(ica, "_erpworkbench_method", getattr(ica, "method", "unknown"))
    compatibility_notes = list(runtime_notes)
    if user_method not in {"infomax_extended", "extended_infomax", "infomax (extended)"}:
        note = (
            "ICLabel is best validated with extended Infomax; current ICA was fitted with "
            f"{user_method}. Treat labels as guidance and confirm visually."
        )
        if note not in compatibility_notes:
            compatibility_notes.append(note)
    lowpass = float(inst.info.get("lowpass", 0.0) or 0.0)
    if lowpass and lowpass < 100.0:
        note = f"Current data low-pass is {lowpass:g} Hz rather than ICLabel's reference 1–100 Hz setup."
        if note not in compatibility_notes:
            compatibility_notes.append(note)

    _progress(progress, "ICLabel complete.")
    return {
        "labels": labels,
        "probabilities": probs.tolist(),
        "warnings": compatibility_notes,
        "montage": montage_info,
        "method": "ICLabel",
    }

def blink_component_correlations(
    raw,
    ica: ICA,
    fit_exclude_spans: Optional[list[dict]] = None,
    progress: ProgressCallback = None,
) -> dict:
    """Return an advisory frontal blink-correlation score for each ICA component.

    This is intentionally *not* an automatic rejection algorithm.  When a
    dedicated EOG channel is unavailable, frontal EEG channels (Fp1/Fp2 first,
    then nearby AF/Fpz channels) are useful visual/reference signals for blink
    screening.  MNE's ``ICA.score_sources`` is used to compute Pearson
    correlation after 1--10 Hz filtering, matching the frequency range commonly
    used by MNE's EOG-component detector.  The maximum absolute correlation
    across available frontal reference channels is returned for each component.

    ICA-fit-only exclusion spans are copied onto a temporary Raw as BAD
    annotations so gross movement sections do not dominate this advisory score.
    The input Raw and the ICA removal selection are never modified.
    """
    if raw is None or ica is None:
        raise ValueError("A fitted ICA and continuous Raw are required.")

    _progress(progress, "Computing frontal blink-correlation aid …")
    work = raw.copy()
    duration_sec = float(work.n_times) / float(work.info["sfreq"]) if work.n_times else 0.0
    for span in list(fit_exclude_spans or []):
        try:
            start = max(0.0, float(span.get("start_sec", 0.0)))
            end = min(duration_sec, float(span.get("end_sec", start)))
        except Exception:
            continue
        if end <= start:
            continue
        reason = str(span.get("reason", "artifact") or "artifact").strip().replace(" ", "_")
        work.annotations.append(start, end - start, f"BAD_ICA_EXCLUDE_{reason}")

    # Prefer the conventional frontopolar channels.  Fall back to nearby
    # anterior-frontal channels without pretending that they are dedicated EOG.
    priority = ["Fp1", "Fp2", "Fpz", "AF7", "AF8", "AF3", "AF4"]
    lower_to_actual = {str(ch).lower(): str(ch) for ch in work.ch_names}
    refs = [lower_to_actual[name.lower()] for name in priority if name.lower() in lower_to_actual]
    if not refs:
        return {
            "scores": [float("nan")] * int(ica.n_components_),
            "reference_channel": [""] * int(ica.n_components_),
            "reference_channels": [],
            "note": "No Fp/AF frontal reference channels were found; blink-correlation aid unavailable.",
        }

    all_scores = []
    for ch in refs:
        _progress(progress, f"Blink aid: correlating ICA sources with {ch} …")
        try:
            scores = np.asarray(
                ica.score_sources(
                    work, target=ch, score_func="pearsonr",
                    l_freq=1.0, h_freq=10.0, reject_by_annotation=True,
                    verbose="ERROR",
                ),
                dtype=float,
            )
        except Exception:
            scores = np.full(int(ica.n_components_), np.nan, dtype=float)
        if scores.size != int(ica.n_components_):
            scores = np.full(int(ica.n_components_), np.nan, dtype=float)
        all_scores.append(scores)

    mat = np.vstack(all_scores) if all_scores else np.empty((0, int(ica.n_components_)))
    out_scores = []
    out_refs = []
    for comp in range(int(ica.n_components_)):
        col = np.abs(mat[:, comp]) if mat.size else np.asarray([], dtype=float)
        finite = np.flatnonzero(np.isfinite(col))
        if finite.size == 0:
            out_scores.append(float("nan")); out_refs.append("")
            continue
        best_local = int(finite[np.argmax(col[finite])])
        out_scores.append(float(col[best_local]))
        out_refs.append(refs[best_local])

    _progress(progress, "Frontal blink-correlation aid ready.")
    return {
        "scores": out_scores,
        "reference_channel": out_refs,
        "reference_channels": refs,
        "note": (
            "Advisory only: maximum absolute 1–10 Hz Pearson correlation between each ICA source "
            "and available frontal EEG reference channels. Confirm with source morphology and topography."
        ),
    }


def apply_ica(
    raw,
    ica: ICA,
    exclude: list[int],
    progress: ProgressCallback = None,
    chunk_duration_sec: float = 30.0,
):
    """Apply selected ICA exclusions to a full-resolution Raw copy in chunks.

    Chunking avoids constructing one very large temporary reconstruction matrix
    for long/preloaded recordings and, importantly for the GUI, gives genuine
    progress updates. ICA.apply is still the MNE operation performing the
    reconstruction; start/stop merely divide the same Raw into consecutive spans.
    """
    excluded = sorted(set(int(x) for x in exclude))
    _progress(progress, "ICA_RECON_PROGRESS|0|Preparing post-ICA EEG reconstruction…")
    cleaned = raw.copy().load_data()
    n_times = int(cleaned.n_times)
    if n_times <= 0:
        _progress(progress, "ICA_RECON_PROGRESS|100|Post-ICA EEG ready.")
        return cleaned
    sfreq = float(cleaned.info["sfreq"])
    chunk = max(1, int(round(max(1.0, float(chunk_duration_sec)) * sfreq)))
    for start in range(0, n_times, chunk):
        stop = min(n_times, start + chunk)
        ica.apply(cleaned, exclude=excluded, start=start, stop=stop, verbose="ERROR")
        percent = min(100, int(round(100.0 * stop / n_times)))
        _progress(progress, f"ICA_RECON_PROGRESS|{percent}|Reconstructing post-ICA EEG… {percent}%")
    _progress(progress, "ICA_RECON_PROGRESS|100|Post-ICA EEG reconstruction complete.")
    return cleaned


def discover_events(raw, source: str = "annotations", stim_channel: str = ""):
    """Return events array and code->human label map."""
    if source == "annotations":
        if len(raw.annotations) == 0:
            return np.empty((0, 3), dtype=int), {}
        events, desc_to_id = mne.events_from_annotations(raw, verbose="ERROR")
        id_to_desc = {int(v): str(k) for k, v in desc_to_id.items()}
        return events, id_to_desc

    kwargs = dict(shortest_event=1, verbose="ERROR")
    if stim_channel.strip():
        kwargs["stim_channel"] = stim_channel.strip()
    events = mne.find_events(raw, **kwargs)
    codes = sorted(set(int(x) for x in events[:, 2])) if len(events) else []
    return events, {c: str(c) for c in codes}




def event_group_match(label: str, rule: EventGroupRule) -> bool:
    """Return True when an annotation label matches a literal group rule.

    By default the pattern may occur anywhere in the label (``contains``).
    With ``starts_with=True`` it must occur at the beginning of the label.
    Matching is intentionally literal rather than regex-based so non-coding
    users can type exactly what they see in the annotation name.
    """
    if not rule.enabled or not rule.pattern.strip():
        return False
    label = str(label)
    pattern = rule.pattern.strip()
    if not rule.case_sensitive:
        label = label.casefold()
        pattern = pattern.casefold()
    if getattr(rule, "starts_with", False):
        return label.startswith(pattern)
    return pattern in label


def resolve_event_group_condition(label: str, rules: list[EventGroupRule]) -> Optional[str]:
    """Resolve a description to the first enabled matching string group."""
    for rule in rules:
        if event_group_match(label, rule):
            condition = rule.condition.strip()
            if condition:
                return condition
    return None


def event_group_stats(
    event_labels: dict[int, str],
    events: np.ndarray,
    rules: list[EventGroupRule],
) -> list[dict]:
    """Return per-rule counts of unique event descriptions and event markers."""
    code_counts: dict[int, int] = {}
    if events is not None and len(events):
        vals, counts = np.unique(events[:, 2].astype(int), return_counts=True)
        code_counts = {int(v): int(c) for v, c in zip(vals, counts)}
    out = []
    for rule in rules:
        matched_codes = [
            int(code) for code, label in event_labels.items()
            if event_group_match(label, rule)
        ]
        out.append({
            "pattern": rule.pattern,
            "condition": rule.condition,
            "case_sensitive": bool(rule.case_sensitive),
            "starts_with": bool(getattr(rule, "starts_with", False)),
            "enabled": bool(rule.enabled),
            "unique_labels": len(matched_codes),
            "markers": sum(code_counts.get(code, 0) for code in matched_codes),
            "matched_codes": matched_codes,
        })
    return out


def event_code_counts(events: np.ndarray) -> dict[int, int]:
    """Return number of markers for every numerical event code."""
    if events is None or len(events) == 0:
        return {}
    values, counts = np.unique(events[:, 2].astype(int), return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts)}


def matching_event_groups(label: str, rules: list[EventGroupRule]) -> list[EventGroupRule]:
    """Return all enabled common-string rules matching one annotation label."""
    return [rule for rule in rules if event_group_match(label, rule)]


def event_group_conflicts(event_labels: dict[int, str], rules: list[EventGroupRule]) -> list[dict]:
    """Describe ambiguous annotation names that match more than one active rule."""
    conflicts = []
    for code, label in event_labels.items():
        matches = matching_event_groups(label, rules)
        conditions = list(dict.fromkeys(r.condition.strip() for r in matches if r.condition.strip()))
        if len(conditions) > 1:
            conflicts.append({
                "event_code": int(code),
                "label": str(label),
                "conditions": conditions,
                "patterns": [r.pattern for r in matches],
            })
    return conflicts


def _selected_epoch_rows(
    events: np.ndarray,
    protocol: ProtocolDefinition,
    event_labels: Optional[dict[int, str]] = None,
    allowed_event_codes: Optional[set[int]] = None,
):
    """Resolve raw events to ERP conditions without changing their event codes."""
    labels = event_labels or {}
    rows = []
    for event in events:
        numeric_code = int(event[2])
        if allowed_event_codes is not None and numeric_code not in allowed_event_codes:
            continue
        code = str(numeric_code)
        label = labels.get(numeric_code, code)
        condition = resolve_event_group_condition(label, protocol.event_groups)
        if condition is None:
            condition = protocol.event_map.get(code, "").strip()
        if condition:
            excluded = set(str(x) for x in protocol.excluded_event_labels.get(str(condition), []))
            if str(label) in excluded:
                continue
            rows.append({
                "sample": int(event[0]),
                "previous": int(event[1]),
                "original_code": numeric_code,
                "source_label": str(label),
                "condition": str(condition),
            })
    return rows


def epoch_preflight(
    raw,
    events: np.ndarray,
    protocol: ProtocolDefinition,
    event_labels: Optional[dict[int, str]] = None,
    allowed_event_codes: Optional[set[int]] = None,
) -> dict:
    """Summarize exactly what will be cut before constructing MNE Epochs.

    The summary is deliberately condition-centric and reports markers that would
    fall outside the recording given the selected tmin/tmax. It also reports
    overlapping common-string group definitions so the GUI can block ambiguous
    epoch assignment instead of silently using first-match-wins.
    """
    if raw is None:
        raise ValueError("No recording is loaded.")
    labels = event_labels or {}
    conflicts = event_group_conflicts(labels, protocol.event_groups)
    if allowed_event_codes is not None:
        conflicts = [c for c in conflicts if int(c["event_code"]) in allowed_event_codes]
    rows = _selected_epoch_rows(events, protocol, labels, allowed_event_codes)
    sfreq = float(raw.info["sfreq"])
    tmin_s = float(protocol.epoch.tmin_ms) / 1000.0
    tmax_s = float(protocol.epoch.tmax_ms) / 1000.0
    n_times = int(raw.n_times)

    condition_order = []
    counts = {}
    valid_rows = []
    boundary_rows = []
    for row in rows:
        condition = row["condition"]
        if condition not in counts:
            counts[condition] = {"condition": condition, "selected": 0, "in_bounds": 0, "boundary_drop": 0}
            condition_order.append(condition)
        counts[condition]["selected"] += 1
        start = row["sample"] + int(round(tmin_s * sfreq))
        stop = row["sample"] + int(round(tmax_s * sfreq))
        row = dict(row)
        row["onset_sec"] = row["sample"] / sfreq
        row["epoch_start_sample"] = start
        row["epoch_stop_sample"] = stop
        if start < 0 or stop >= n_times:
            counts[condition]["boundary_drop"] += 1
            boundary_rows.append(row)
        else:
            counts[condition]["in_bounds"] += 1
            valid_rows.append(row)

    return {
        "conditions": [counts[c] for c in condition_order],
        "selected_total": len(rows),
        "in_bounds_total": len(valid_rows),
        "boundary_drop_total": len(boundary_rows),
        "conflicts": conflicts,
        "valid_rows": valid_rows,
        "boundary_rows": boundary_rows,
    }


def create_epochs(
    raw,
    events: np.ndarray,
    protocol: ProtocolDefinition,
    event_labels: Optional[dict[int, str]] = None,
    allowed_event_codes: Optional[set[int]] = None,
):
    if len(events) == 0:
        raise ValueError("No events are available for epoching.")

    conflicts = event_group_conflicts(event_labels or {}, protocol.event_groups)
    if allowed_event_codes is not None:
        conflicts = [c for c in conflicts if int(c["event_code"]) in allowed_event_codes]
    if conflicts:
        example = conflicts[0]
        raise ValueError(
            "Ambiguous common-string event groups are present. "
            f"For example {example['label']!r} matches multiple conditions: "
            + ", ".join(example["conditions"])
            + ". Edit the string groups so every stimulus belongs to only one ERP condition."
        )

    rows = _selected_epoch_rows(events, protocol, event_labels, allowed_event_codes)
    if not rows:
        raise ValueError("No events are mapped to conditions. Define at least one event/string group before epoching.")

    # MNE event_id requires one numerical code per condition. Recode selected events
    # while preserving the original sample locations and keep the original event
    # description/code in Epochs.metadata for transparent later review/export.
    unique_conditions = list(dict.fromkeys(row["condition"] for row in rows))
    condition_to_code = {name: i + 1 for i, name in enumerate(unique_conditions)}
    selected_events = np.asarray(
        [[row["sample"], row["previous"], condition_to_code[row["condition"]]] for row in rows],
        dtype=int,
    )
    sfreq = float(raw.info["sfreq"])
    metadata = pd.DataFrame({
        "condition": [row["condition"] for row in rows],
        "source_event_label": [row["source_label"] for row in rows],
        "source_event_code": [row["original_code"] for row in rows],
        "event_sample": [row["sample"] for row in rows],
        "event_onset_sec": [row["sample"] / sfreq for row in rows],
    })

    ep = protocol.epoch
    baseline = None
    if ep.baseline_enabled:
        b0 = None if ep.baseline_start_ms is None else ep.baseline_start_ms / 1000.0
        b1 = None if ep.baseline_end_ms is None else ep.baseline_end_ms / 1000.0
        baseline = (b0, b1)

    epochs = mne.Epochs(
        raw,
        selected_events,
        event_id=condition_to_code,
        tmin=ep.tmin_ms / 1000.0,
        tmax=ep.tmax_ms / 1000.0,
        baseline=baseline,
        preload=True,
        reject=None,
        flat=None,
        reject_by_annotation=False,
        detrend=None,
        metadata=metadata,
        verbose="ERROR",
    )
    return epochs


def auto_review_epochs(epochs, protocol: ProtocolDefinition) -> EpochReviewState:
    eeg_picks = mne.pick_types(epochs.info, eeg=True, exclude=[])
    if len(eeg_picks) == 0:
        raise ValueError("No EEG channels found in epochs.")

    requested = list(getattr(protocol.epoch, "rejection_channels", []) or [])
    if requested:
        eeg_names = {epochs.ch_names[int(i)] for i in eeg_picks}
        valid_names = [ch for ch in requested if ch in eeg_names]
        if not valid_names:
            raise ValueError(
                "None of the protocol's selected epoch-screening channels are present in this recording. "
                "Choose valid screening channels in the Epoching tab."
            )
        picks = [epochs.ch_names.index(ch) for ch in valid_names]
    else:
        picks = list(eeg_picks)

    data_uv = epochs.get_data(picks=picks) * 1e6
    n = len(epochs)
    auto_bad = np.zeros(n, dtype=bool)
    reasons = [""] * n
    ep = protocol.epoch

    abs_max = np.max(np.abs(data_uv), axis=(1, 2))
    channel_p2p = np.ptp(data_uv, axis=2)
    epoch_p2p_max = np.max(channel_p2p, axis=1)
    epoch_p2p_min = np.min(channel_p2p, axis=1)

    for i in range(n):
        rs = []
        if ep.absolute_threshold_uv is not None and abs_max[i] > ep.absolute_threshold_uv:
            rs.append(f"abs>{ep.absolute_threshold_uv:g}µV")
        if ep.p2p_threshold_uv is not None and epoch_p2p_max[i] > ep.p2p_threshold_uv:
            rs.append(f"p2p>{ep.p2p_threshold_uv:g}µV")
        if ep.flat_threshold_uv is not None and epoch_p2p_min[i] < ep.flat_threshold_uv:
            rs.append(f"flat<{ep.flat_threshold_uv:g}µV")
        if rs:
            auto_bad[i] = True
            reasons[i] = ", ".join(rs)

    return EpochReviewState(
        auto_bad=auto_bad,
        auto_reason=reasons,
        manual_decision=np.zeros(n, dtype=np.int8),
    )


def epoch_review_rows(epochs, review: EpochReviewState) -> list[dict]:
    """Return stable epoch-by-epoch QC/decision rows for audit and replay.

    The preferred identity is event_sample + condition + source_event_label from
    Epochs.metadata.  This is more robust than relying on the current table row
    number, which can change when condition filters or protocol mappings change.
    """
    if epochs is None or review.auto_bad.size != len(epochs):
        return []
    accepted = review.accepted_mask()
    reverse_id = {v: k for k, v in epochs.event_id.items()}
    metadata = epochs.metadata
    rows: list[dict] = []
    for i, event in enumerate(epochs.events):
        condition = reverse_id.get(int(event[2]), str(event[2]))
        meta = metadata.iloc[i].to_dict() if metadata is not None and i < len(metadata) else {}
        manual = int(review.manual_decision[i])
        rows.append({
            "epoch_index": int(i),
            "epoch_number": int(i + 1),
            "condition": str(meta.get("condition", condition)),
            "event_sample": int(meta.get("event_sample", event[0])),
            "event_onset_sec": float(meta.get("event_onset_sec", event[0] / float(epochs.info["sfreq"]))),
            "source_event_label": str(meta.get("source_event_label", "")),
            "source_event_code": int(meta.get("source_event_code", 0)) if str(meta.get("source_event_code", "")).strip() not in {"", "nan", "None"} else None,
            "auto_bad": bool(review.auto_bad[i]),
            "auto_reason": str(review.auto_reason[i]),
            "manual_decision": "keep" if manual == 1 else "reject" if manual == -1 else "none",
            "final_accepted": bool(accepted[i]),
        })
    return rows


def export_epoch_review_log(
    path: str | Path,
    metadata,
    protocol: ProtocolDefinition,
    epochs,
    review: EpochReviewState,
    history: Optional[list[dict]] = None,
) -> Path:
    """Write a replayable JSON snapshot of epoch acceptance/rejection decisions."""
    path = Path(path)
    payload = {
        "schema": "erp-workbench-epoch-review-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": str(getattr(metadata, "input_path", None) or ""),
        "subject_id": str(getattr(metadata, "subject_id", "") or ""),
        "protocol_name": protocol.name,
        "epoch_window_ms": [float(protocol.epoch.tmin_ms), float(protocol.epoch.tmax_ms)],
        "n_epochs": int(len(epochs)) if epochs is not None else 0,
        "screening_channels": list(getattr(protocol.epoch, "rejection_channels", []) or []),
        "epochs": epoch_review_rows(epochs, review),
        "history": list(history or []),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def apply_epoch_review_log(path: str | Path, epochs, review: EpochReviewState) -> dict:
    """Force current final decisions to match a previously exported review log.

    Matching uses event sample + condition + source label first, then falls back
    to event sample + condition.  Imported decisions are stored as manual
    overrides so the final accepted/rejected set is reproduced even if current
    automatic thresholds differ from the original run.
    """
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    logged = list(payload.get("epochs", []))
    current = epoch_review_rows(epochs, review)
    if not current:
        raise ValueError("No current epochs/review state are available for applying a decision log.")

    exact: dict[tuple, list[int]] = {}
    loose: dict[tuple, list[int]] = {}
    for i, row in enumerate(current):
        k1 = (int(row["event_sample"]), str(row["condition"]), str(row.get("source_event_label", "")))
        k2 = (int(row["event_sample"]), str(row["condition"]))
        exact.setdefault(k1, []).append(i)
        loose.setdefault(k2, []).append(i)

    used: set[int] = set()
    matched = 0
    unmatched = 0
    duplicate_ambiguous = 0
    for row in logged:
        k1 = (int(row.get("event_sample", -1)), str(row.get("condition", "")), str(row.get("source_event_label", "")))
        k2 = (int(row.get("event_sample", -1)), str(row.get("condition", "")))
        candidates = [i for i in exact.get(k1, []) if i not in used]
        if not candidates:
            candidates = [i for i in loose.get(k2, []) if i not in used]
        if len(candidates) != 1:
            unmatched += 1
            if len(candidates) > 1:
                duplicate_ambiguous += 1
            continue
        i = candidates[0]
        used.add(i)
        desired_accept = bool(row.get("final_accepted", True))
        auto_accept = not bool(review.auto_bad[i])
        if desired_accept == auto_accept:
            review.manual_decision[i] = 0
        else:
            review.manual_decision[i] = 1 if desired_accept else -1
        matched += 1

    return {
        "matched": matched,
        "unmatched": unmatched,
        "ambiguous": duplicate_ambiguous,
        "logged": len(logged),
        "current": len(current),
        "source_input_file": str(payload.get("input_file", "")),
        "source_protocol": str(payload.get("protocol_name", "")),
    }


def clean_epochs(epochs, review: EpochReviewState):
    mask = review.accepted_mask()
    if mask.size != len(epochs):
        raise ValueError("Epoch review state does not match the current epochs.")
    return epochs[np.flatnonzero(mask)]


def condition_averages(epochs_clean) -> dict[str, mne.Evoked]:
    averages = {}
    for condition in epochs_clean.event_id:
        subset = epochs_clean[condition]
        if len(subset):
            averages[condition] = subset.average()
    return averages


def measure_evoked(
    evoked,
    condition: str,
    channel: str,
    component: ComponentDefinition,
    n_epochs: int,
    manual_time_ms: Optional[float] = None,
) -> MeasurementResult:
    if channel not in evoked.ch_names:
        raise ValueError(f"Channel {channel!r} is not present in the evoked data.")
    idx = evoked.ch_names.index(channel)
    times_ms = evoked.times * 1000.0
    data_uv = evoked.data[idx] * 1e6
    mask = (times_ms >= component.start_ms) & (times_ms <= component.end_ms)
    if not np.any(mask):
        raise ValueError(f"Measurement window for {component.name} is outside the evoked time range.")
    t = times_ms[mask]
    y = data_uv[mask]

    method = component.method.lower()
    notes = ""
    if method == "peak":
        pol = component.polarity.lower()
        if pol == "positive":
            local = int(np.argmax(y))
        elif pol == "negative":
            local = int(np.argmin(y))
        else:
            local = int(np.argmax(np.abs(y)))
        amplitude = float(y[local])
        latency = float(t[local])
    elif method == "mean":
        amplitude = float(np.mean(y))
        latency = None
    elif method == "area":
        amplitude = float(np.trapezoid(y, t))
        latency = None
        notes = "amplitude_uv column contains signed area in µV·ms for area method"
    elif method == "manual":
        if manual_time_ms is None:
            raise ValueError("Manual measurement requires a selected latency.")
        if not (component.start_ms <= manual_time_ms <= component.end_ms):
            raise ValueError("Manual point must lie inside the component window.")
        amplitude = float(np.interp(manual_time_ms, times_ms, data_uv))
        latency = float(manual_time_ms)
    else:
        raise ValueError(f"Unsupported measurement method: {component.method}")

    return MeasurementResult(
        condition=condition,
        channel=channel,
        component=component.name,
        method=component.method,
        window_start_ms=component.start_ms,
        window_end_ms=component.end_ms,
        amplitude_uv=amplitude,
        latency_ms=latency,
        n_epochs=n_epochs,
        notes=notes,
    )


def export_excel(
    path: str | Path,
    metadata,
    preprocessing: PreprocessingSettings,
    protocol: ProtocolDefinition,
    epochs,
    review: EpochReviewState,
    measurements: list[MeasurementResult],
    annotation_table: Optional[pd.DataFrame] = None,
):
    path = Path(path)

    result_df = pd.DataFrame([asdict(r) for r in measurements])
    if result_df.empty:
        result_df = pd.DataFrame(columns=[
            "condition", "channel", "component", "method", "window_start_ms",
            "window_end_ms", "amplitude_uv", "latency_ms", "n_epochs", "notes"
        ])

    prep = asdict(preprocessing)
    prep_rows = [
        ("input_path", str(metadata.input_path or "")),
        ("subject_id", metadata.subject_id),
        ("notes", metadata.notes),
        ("annotation_path", str(getattr(metadata, "annotation_path", None) or "")),
        ("annotation_count", int(getattr(metadata, "annotation_count", 0) or 0)),
        ("annotation_out_of_range", int(getattr(metadata, "annotation_out_of_range", 0) or 0)),
        ("annotation_duplicates_skipped", int(getattr(metadata, "annotation_duplicates_skipped", 0) or 0)),
        ("filter_enabled", prep["filter"]["enabled"]),
        ("highpass_hz", prep["filter"]["l_freq"]),
        ("lowpass_hz", prep["filter"]["h_freq"]),
        ("notch_enabled", prep["filter"]["notch_enabled"]),
        ("notch_hz", prep["filter"]["notch_freq"]),
        ("interpolation_enabled", prep["interpolation"]["enabled"]),
        ("bad_channels", ", ".join(prep["interpolation"]["bad_channels"])),
        ("montage", prep["interpolation"]["montage"]),
        ("reference_enabled", prep["reference"]["enabled"]),
        ("reference_mode", prep["reference"]["mode"]),
        ("reference_channels", ", ".join(prep["reference"]["custom_channels"])),
        ("ica_enabled", prep["ica"]["enabled"]),
        ("ica_method", prep["ica"]["method"]),
        ("ica_fit_decim", prep["ica"].get("fit_decim")),
        ("ica_excluded", ", ".join(str(x) for x in prep["ica"]["excluded_components"])),
        ("structural_step_order", " -> ".join(prep.get("step_order", []))),
        ("protocol", protocol.name),
        ("epoch_screening_channels", ", ".join(getattr(protocol.epoch, "rejection_channels", []) or []) or "ALL EEG"),
        ("processing_log", " | ".join(metadata.processing_log)),
    ]
    prep_df = pd.DataFrame(prep_rows, columns=["parameter", "value"])

    component_df = pd.DataFrame([asdict(c) for c in protocol.components])
    event_map_df = pd.DataFrame(
        [(code, label) for code, label in protocol.event_map.items()],
        columns=["event_code", "condition"],
    )
    event_groups_df = pd.DataFrame([asdict(g) for g in protocol.event_groups])

    qc_rows = epoch_review_rows(epochs, review)
    qc_df = pd.DataFrame(qc_rows)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        result_df.to_excel(writer, sheet_name="ERP Measurements", index=False)
        prep_df.to_excel(writer, sheet_name="Processing Metadata", index=False)
        qc_df.to_excel(writer, sheet_name="Epoch QC", index=False)
        event_map_df.to_excel(writer, sheet_name="Event Mapping", index=False)
        event_groups_df.to_excel(writer, sheet_name="Event String Groups", index=False)
        component_df.to_excel(writer, sheet_name="Component Definitions", index=False)
        if annotation_table is not None and not annotation_table.empty:
            annotation_table.to_excel(writer, sheet_name="Imported Annotations", index=False)

        # Make the workbooks readable without manual formatting.
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                max_len = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 45)


# ---------- v0.7 averaged-subject / ERP helpers ----------
def difference_evoked(evoked_a, evoked_b, label: str = "Difference"):
    """Return an in-memory A-B difference Evoked for local inspection only.

    The caller deliberately keeps this object separate from the subject's saved
    condition averages so it cannot accidentally participate in grand averaging.
    """
    if evoked_a.ch_names != evoked_b.ch_names:
        raise ValueError("Difference waves require matching channel sets/order.")
    if evoked_a.data.shape != evoked_b.data.shape or not np.allclose(evoked_a.times, evoked_b.times):
        raise ValueError("Difference waves require matching sample/time axes.")
    out = evoked_a.copy()
    out.data = np.asarray(evoked_a.data) - np.asarray(evoked_b.data)
    out.comment = str(label)
    # nave is not statistically meaningful for a simple local A-B display.  Keep
    # a conservative finite value so downstream plotting code remains happy.
    try:
        out.nave = int(min(getattr(evoked_a, "nave", 1), getattr(evoked_b, "nave", 1)))
    except Exception:
        pass
    return out


def accepted_condition_counts(clean_epochs) -> dict[str, int]:
    if clean_epochs is None:
        return {}
    out = {}
    for condition in clean_epochs.event_id:
        try:
            out[str(condition)] = int(len(clean_epochs[condition]))
        except Exception:
            out[str(condition)] = 0
    return out


def save_average_package(
    path: str | Path,
    evokeds: dict[str, mne.Evoked],
    metadata,
    preprocessing: PreprocessingSettings,
    protocol: ProtocolDefinition,
    measurements: list[MeasurementResult],
    *,
    condition_counts: Optional[dict[str, int]] = None,
    epochs=None,
    review: Optional[EpochReviewState] = None,
) -> None:
    """Save subject condition averages + provenance in one ``.erpavg`` archive.

    Only real condition averages in ``evokeds`` are written.  Temporary
    difference waves are intentionally never accepted by this function unless a
    caller explicitly inserts them into that mapping (the GUI never does).
    """
    import tempfile
    import zipfile

    if not evokeds:
        raise ValueError("No condition averages are available to save.")
    path = Path(path)
    counts = dict(condition_counts or {})
    for name, ev in evokeds.items():
        counts.setdefault(str(name), int(getattr(ev, "nave", 0) or 0))

    manifest = {
        "format": "ERPWorkbenchAveragePackage",
        "format_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "subject_id": str(getattr(metadata, "subject_id", "") or ""),
        "input_path": str(getattr(metadata, "input_path", "") or ""),
        "annotation_path": str(getattr(metadata, "annotation_path", "") or ""),
        "annotation_count": int(getattr(metadata, "annotation_count", 0) or 0),
        "processing_log": list(getattr(metadata, "processing_log", []) or []),
        "preprocessing": asdict(preprocessing),
        "protocol": protocol.to_dict(),
        "condition_counts": {str(k): int(v) for k, v in counts.items()},
        "measurements": [asdict(x) for x in measurements],
        "conditions": list(evokeds.keys()),
        "note": "Temporary A-B difference waves are display-only and are not stored as subject conditions.",
    }
    if epochs is not None and review is not None:
        try:
            manifest["epoch_qc_summary"] = {
                "total": int(len(epochs)),
                "accepted": int(review.accepted_mask().sum()),
                "rejected": int(len(epochs) - review.accepted_mask().sum()),
            }
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="erpwb_avg_") as td:
        td = Path(td)
        fif_path = td / "averages-ave.fif"
        to_write = []
        for condition, evoked in evokeds.items():
            e = evoked.copy()
            e.comment = str(condition)
            to_write.append(e)
        mne.write_evokeds(fif_path, to_write, overwrite=True)
        (td / "session.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

        if epochs is not None and review is not None:
            try:
                pd.DataFrame(epoch_review_rows(epochs, review)).to_csv(td / "epoch_qc.csv", index=False)
            except Exception:
                pass
        if measurements:
            pd.DataFrame([asdict(x) for x in measurements]).to_csv(td / "measurements.csv", index=False)

        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in ("averages-ave.fif", "session.json", "epoch_qc.csv", "measurements.csv"):
                fp = td / name
                if fp.exists():
                    zf.write(fp, arcname=name)


def load_average_package(path: str | Path) -> dict:
    """Load an ``.erpavg`` archive for direct subject-average viewing.

    Returns a dictionary containing ``evokeds`` and its saved manifest.  This
    same reader is intentionally suitable for a future multi-subject grand-
    average workflow.
    """
    import tempfile
    import zipfile

    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "session.json" not in names or "averages-ave.fif" not in names:
            raise ValueError("This is not a valid ERP Workbench averaged-subject package.")
        manifest = json.loads(zf.read("session.json").decode("utf-8"))
        if manifest.get("format") != "ERPWorkbenchAveragePackage":
            raise ValueError("Unrecognized averaged-subject package format.")
        with tempfile.TemporaryDirectory(prefix="erpwb_avg_read_") as td:
            td = Path(td)
            fif_path = td / "averages-ave.fif"
            fif_path.write_bytes(zf.read("averages-ave.fif"))
            loaded = mne.read_evokeds(fif_path, condition=None, verbose="ERROR")

    if not isinstance(loaded, list):
        loaded = [loaded]
    evokeds: dict[str, mne.Evoked] = {}
    for i, ev in enumerate(loaded):
        name = str(getattr(ev, "comment", "") or "").strip() or f"Condition {i + 1}"
        # Avoid silent overwrite in a malformed package with duplicate comments.
        if name in evokeds:
            base = name
            j = 2
            while f"{base} ({j})" in evokeds:
                j += 1
            name = f"{base} ({j})"
        evokeds[name] = ev
    return {"evokeds": evokeds, "manifest": manifest}


# ---------- v0.8 grand-average helpers ----------
def load_average_manifest(path: str | Path) -> dict:
    """Read only the manifest from an ``.erpavg`` archive (no FIF loading)."""
    import zipfile
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "session.json" not in names or "averages-ave.fif" not in names:
            raise ValueError(f"{path.name} is not a valid ERP Workbench averaged-subject package.")
        manifest = json.loads(zf.read("session.json").decode("utf-8"))
    if manifest.get("format") != "ERPWorkbenchAveragePackage":
        raise ValueError(f"{path.name} has an unrecognized averaged-subject package format.")
    return manifest


def protocol_fingerprint(protocol_data: dict) -> str:
    """Stable SHA-256 fingerprint of the saved protocol definition."""
    canonical = json.dumps(protocol_data or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _diff_paths(a, b, prefix="") -> list[str]:
    """Return human-readable paths whose values differ between nested structures."""
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            p = f"{prefix}.{key}" if prefix else str(key)
            if key not in a:
                diffs.append(f"{p}: missing in reference")
            elif key not in b:
                diffs.append(f"{p}: missing in compared file")
            else:
                diffs.extend(_diff_paths(a[key], b[key], p))
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{prefix}: list length {len(a)} vs {len(b)}")
            return diffs
        for i, (av, bv) in enumerate(zip(a, b)):
            diffs.extend(_diff_paths(av, bv, f"{prefix}[{i}]"))
        return diffs
    if a != b:
        diffs.append(f"{prefix}: {a!r} vs {b!r}")
    return diffs


def validate_average_package_manifests(paths: list[str | Path]) -> dict:
    """Validate saved-subject packages before any grand averaging occurs.

    Protocol equality is intentionally strict: all saved protocol fields must
    match. The condition set must also be identical. Duplicate non-empty subject
    IDs are rejected to protect against accidentally counting the same subject
    twice under different filenames.
    """
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        raise ValueError("Select at least two averaged-subject (.erpavg) files for a grand average.")
    manifests = [load_average_manifest(p) for p in paths]
    ref_protocol = manifests[0].get("protocol", {}) or {}
    ref_hash = protocol_fingerprint(ref_protocol)
    ref_conditions = set(map(str, manifests[0].get("conditions", []) or []))
    problems = []
    for p, manifest in zip(paths[1:], manifests[1:]):
        protocol = manifest.get("protocol", {}) or {}
        if protocol_fingerprint(protocol) != ref_hash:
            diffs = _diff_paths(ref_protocol, protocol)
            preview = "; ".join(diffs[:8]) or "saved protocol JSON differs"
            if len(diffs) > 8:
                preview += f"; +{len(diffs)-8} more difference(s)"
            problems.append(f"{p.name}: protocol mismatch — {preview}")
        conditions = set(map(str, manifest.get("conditions", []) or []))
        if conditions != ref_conditions:
            missing = sorted(ref_conditions - conditions)
            extra = sorted(conditions - ref_conditions)
            detail = []
            if missing: detail.append("missing " + ", ".join(missing))
            if extra: detail.append("extra " + ", ".join(extra))
            problems.append(f"{p.name}: condition set mismatch ({'; '.join(detail)})")

    ids = {}
    for p, manifest in zip(paths, manifests):
        sid = str(manifest.get("subject_id", "") or "").strip()
        if sid:
            ids.setdefault(sid, []).append(p.name)
    dup = {sid: names for sid, names in ids.items() if len(names) > 1}
    if dup:
        for sid, names in dup.items():
            problems.append(f"Duplicate subject ID {sid!r}: " + ", ".join(names))

    if problems:
        raise ValueError("Grand-average validation failed:\n\n" + "\n".join(f"• {x}" for x in problems))
    return {
        "paths": paths,
        "manifests": manifests,
        "protocol": ref_protocol,
        "protocol_hash": ref_hash,
        "conditions": sorted(ref_conditions),
    }


def grand_average_packages(paths: list[str | Path]) -> dict:
    """Load validated ``.erpavg`` files and grand-average each condition.

    Each subject Evoked contributes one equally weighted dataset via
    :func:`mne.grand_average`. Channel sets, sampling rates and time axes must
    match; channel order may differ and is normalized without dropping channels.
    """
    validated = validate_average_package_manifests(paths)
    packages = [load_average_package(p) for p in validated["paths"]]
    conditions = validated["conditions"]
    grand: dict[str, mne.Evoked] = {}
    subject_ids = [str(x["manifest"].get("subject_id", "") or "").strip() for x in packages]

    for condition in conditions:
        evs = [pkg["evokeds"][condition].copy() for pkg in packages]
        ref = evs[0]
        ref_set = set(ref.ch_names)
        for i, ev in enumerate(evs[1:], start=1):
            if set(ev.ch_names) != ref_set:
                missing = sorted(ref_set - set(ev.ch_names))
                extra = sorted(set(ev.ch_names) - ref_set)
                raise ValueError(
                    f"Channel-set mismatch for condition {condition!r} in {validated['paths'][i].name}. "
                    f"Missing: {missing or 'none'}; extra: {extra or 'none'}. No channels were silently dropped."
                )
            if not np.isclose(float(ev.info["sfreq"]), float(ref.info["sfreq"])):
                raise ValueError(
                    f"Sampling-rate mismatch for condition {condition!r}: "
                    f"{ref.info['sfreq']} Hz vs {ev.info['sfreq']} Hz in {validated['paths'][i].name}."
                )
            if ev.data.shape[1] != ref.data.shape[1] or not np.allclose(ev.times, ref.times, atol=1e-10, rtol=0):
                raise ValueError(
                    f"Time-axis mismatch for condition {condition!r} in {validated['paths'][i].name}. "
                    "Grand averaging stopped rather than resampling silently."
                )
        # Same channel set is already guaranteed; this only normalizes ordering.
        evs = mne.equalize_channels(evs, copy=True, verbose="ERROR")
        ga = mne.grand_average(evs, interpolate_bads=True, drop_bads=False)
        ga.comment = str(condition)
        grand[condition] = ga

    return {
        "evokeds": grand,
        "protocol": validated["protocol"],
        "protocol_hash": validated["protocol_hash"],
        "subject_ids": subject_ids,
        "subject_count": len(packages),
        "paths": [str(p) for p in validated["paths"]],
        "manifests": validated["manifests"],
    }

# ---------- v0.8 refined grand-average measurement / workbook helpers ----------
def _effective_measurement_component(component: ComponentDefinition, auto_mode: str = "component") -> Optional[ComponentDefinition]:
    """Return a copy of *component* using the requested automatic strategy.

    ``auto_mode='component'`` follows the component's saved method. A component
    explicitly configured as ``manual`` has no automatic fallback in that mode.
    ``peak`` and ``mean`` are temporary measurement overrides and do not mutate
    the saved protocol/component definition.
    """
    mode = str(auto_mode or "component").strip().lower()
    c = ComponentDefinition(
        name=str(component.name),
        start_ms=float(component.start_ms),
        end_ms=float(component.end_ms),
        polarity=str(component.polarity),
        method=str(component.method),
        channels=list(component.channels),
    )
    if mode == "component":
        return None if c.method.lower() == "manual" else c
    if mode in {"peak", "mean"}:
        c.method = mode
        return c
    return c


def _component_channels_for_evoked(
    component: ComponentDefinition,
    evoked,
    default_channels: Optional[list[str]] = None,
) -> list[str]:
    """Resolve component measurement channels without silently dropping data."""
    eeg_picks = mne.pick_types(evoked.info, eeg=True, exclude=[])
    eeg_channels = [evoked.ch_names[int(i)] for i in eeg_picks]
    requested = list(component.channels or [])
    if requested:
        if len(requested) == 1 and str(requested[0]).upper() == "ALL":
            return eeg_channels
        return [ch for ch in requested if ch in eeg_channels]
    preferred = [ch for ch in (default_channels or []) if ch in eeg_channels]
    return preferred or (eeg_channels[:1] if eeg_channels else [])


def _manual_measurement_overrides(manifest: dict) -> dict[tuple[str, str, str], MeasurementResult]:
    """Return saved subject manual measurements keyed by ERP identity."""
    out: dict[tuple[str, str, str], MeasurementResult] = {}
    for raw in list(manifest.get("measurements", []) or []):
        try:
            m = MeasurementResult(**raw)
        except Exception:
            continue
        if str(m.method).lower() != "manual":
            continue
        out[(str(m.condition), str(m.channel), str(m.component))] = m
    return out


def _measurement_export_row(
    *,
    result: Optional[MeasurementResult],
    subject_id: str,
    source_file: str,
    wave_type: str,
    condition: str,
    condition_a: str = "",
    condition_b: str = "",
    component: ComponentDefinition,
    channel: str,
    accepted_epochs: Optional[int] = None,
    accepted_epochs_a: Optional[int] = None,
    accepted_epochs_b: Optional[int] = None,
    subjects_n: Optional[int] = None,
    source: str = "automatic",
    note: str = "",
) -> dict:
    """Create one normalized workbook row, including intentionally missing manual picks."""
    method = str(result.method) if result is not None else str(component.method)
    amplitude = None if result is None else float(result.amplitude_uv)
    latency = None if result is None or result.latency_ms is None else float(result.latency_ms)
    notes = []
    if result is not None and result.notes:
        notes.append(str(result.notes))
    if note:
        notes.append(str(note))
    if result is None:
        notes.append("No manual measurement was saved/selected for this component")
    return {
        "subject_id": str(subject_id),
        "source_file": str(source_file),
        "wave_type": str(wave_type),
        "condition": str(condition),
        "difference_a": str(condition_a),
        "difference_b": str(condition_b),
        "channel": str(channel),
        "component": str(component.name),
        "method": method,
        "window_start_ms": float(component.start_ms),
        "window_end_ms": float(component.end_ms),
        "polarity": str(component.polarity),
        "amplitude_uv": amplitude,
        "latency_ms": latency,
        "accepted_epochs": accepted_epochs,
        "accepted_epochs_a": accepted_epochs_a,
        "accepted_epochs_b": accepted_epochs_b,
        "subjects_n": subjects_n,
        "measurement_source": str(source),
        "notes": "; ".join(x for x in notes if x),
    }


def _measure_rows_for_evoked(
    *,
    evoked,
    condition: str,
    components: list[ComponentDefinition],
    auto_mode: str,
    default_channels: list[str],
    manual_overrides: Optional[dict[tuple[str, str, str], MeasurementResult]] = None,
    subject_id: str = "",
    source_file: str = "",
    wave_type: str = "Condition",
    condition_a: str = "",
    condition_b: str = "",
    accepted_epochs: Optional[int] = None,
    accepted_epochs_a: Optional[int] = None,
    accepted_epochs_b: Optional[int] = None,
    subjects_n: Optional[int] = None,
) -> list[dict]:
    rows: list[dict] = []
    overrides = manual_overrides or {}
    for component in components:
        effective = _effective_measurement_component(component, auto_mode)
        channels = _component_channels_for_evoked(component, evoked, default_channels)
        for channel in channels:
            key = (str(condition), str(channel), str(component.name))
            manual = overrides.get(key)
            if manual is not None:
                rows.append(_measurement_export_row(
                    result=manual,
                    subject_id=subject_id,
                    source_file=source_file,
                    wave_type=wave_type,
                    condition=condition,
                    condition_a=condition_a,
                    condition_b=condition_b,
                    component=component,
                    channel=channel,
                    accepted_epochs=accepted_epochs,
                    accepted_epochs_a=accepted_epochs_a,
                    accepted_epochs_b=accepted_epochs_b,
                    subjects_n=subjects_n,
                    source="saved manual override",
                    note=(
                        f"Saved manual window was {manual.window_start_ms:g}–{manual.window_end_ms:g} ms"
                        if (float(manual.window_start_ms) != float(component.start_ms) or float(manual.window_end_ms) != float(component.end_ms))
                        else ""
                    ),
                ))
                continue

            if effective is None:
                rows.append(_measurement_export_row(
                    result=None,
                    subject_id=subject_id,
                    source_file=source_file,
                    wave_type=wave_type,
                    condition=condition,
                    condition_a=condition_a,
                    condition_b=condition_b,
                    component=component,
                    channel=channel,
                    accepted_epochs=accepted_epochs,
                    accepted_epochs_a=accepted_epochs_a,
                    accepted_epochs_b=accepted_epochs_b,
                    subjects_n=subjects_n,
                    source="manual required",
                ))
                continue

            try:
                result = measure_evoked(
                    evoked,
                    condition,
                    channel,
                    effective,
                    int(accepted_epochs or subjects_n or 0),
                )
                rows.append(_measurement_export_row(
                    result=result,
                    subject_id=subject_id,
                    source_file=source_file,
                    wave_type=wave_type,
                    condition=condition,
                    condition_a=condition_a,
                    condition_b=condition_b,
                    component=component,
                    channel=channel,
                    accepted_epochs=accepted_epochs,
                    accepted_epochs_a=accepted_epochs_a,
                    accepted_epochs_b=accepted_epochs_b,
                    subjects_n=subjects_n,
                    source="automatic from averaged waveform",
                ))
            except Exception as exc:
                rows.append(_measurement_export_row(
                    result=None,
                    subject_id=subject_id,
                    source_file=source_file,
                    wave_type=wave_type,
                    condition=condition,
                    condition_a=condition_a,
                    condition_b=condition_b,
                    component=component,
                    channel=channel,
                    accepted_epochs=accepted_epochs,
                    accepted_epochs_a=accepted_epochs_a,
                    accepted_epochs_b=accepted_epochs_b,
                    subjects_n=subjects_n,
                    source="measurement failed",
                    note=str(exc),
                ))
    return rows


def export_grand_average_excel(
    path: str | Path,
    subject_paths: list[str | Path],
    grand_evokeds: dict[str, mne.Evoked],
    protocol_data: dict,
    components: list[ComponentDefinition],
    grand_measurements: list[MeasurementResult],
    *,
    auto_mode: str = "component",
    default_channels: Optional[list[str]] = None,
    difference_pairs: Optional[list[tuple[str, str]]] = None,
) -> dict:
    """Export subject-level and grand-average ERP measurements in one workbook.

    Subject rows are measured from each saved subject's actual Evoked averages
    using the *current* grand-average component plan. Saved manual subject picks
    take precedence for the same condition/channel/component. Grand-average
    manual picks likewise override automatic recomputation. Difference waves are
    computed transiently and included only for pairs explicitly requested by the
    caller; they are never added to real condition dictionaries.
    """
    path = Path(path)
    validated = validate_average_package_manifests(subject_paths)
    packages = [load_average_package(p) for p in validated["paths"]]
    differences = list(dict.fromkeys((str(a), str(b)) for a, b in (difference_pairs or []) if a and b and a != b))
    default_channels = list(default_channels or [])

    subject_rows: list[dict] = []
    for p, pkg in zip(validated["paths"], packages):
        manifest = pkg["manifest"]
        subject_id = str(manifest.get("subject_id", "") or "")
        counts = {str(k): int(v) for k, v in (manifest.get("condition_counts", {}) or {}).items()}
        manual = _manual_measurement_overrides(manifest)
        for condition in validated["conditions"]:
            ev = pkg["evokeds"][condition]
            subject_rows.extend(_measure_rows_for_evoked(
                evoked=ev,
                condition=condition,
                components=components,
                auto_mode=auto_mode,
                default_channels=default_channels,
                manual_overrides=manual,
                subject_id=subject_id,
                source_file=str(p),
                wave_type="Condition",
                accepted_epochs=counts.get(condition),
            ))
        for a, b in differences:
            if a not in pkg["evokeds"] or b not in pkg["evokeds"]:
                continue
            label = f"{a} − {b}"
            diff = difference_evoked(pkg["evokeds"][a], pkg["evokeds"][b], label)
            # Difference waves are not stored in subject packages, so no saved
            # manual difference override exists; they are measured consistently
            # from the current component plan at export time.
            subject_rows.extend(_measure_rows_for_evoked(
                evoked=diff,
                condition=label,
                components=components,
                auto_mode=auto_mode,
                default_channels=default_channels,
                manual_overrides={},
                subject_id=subject_id,
                source_file=str(p),
                wave_type="Difference",
                condition_a=a,
                condition_b=b,
                accepted_epochs_a=counts.get(a),
                accepted_epochs_b=counts.get(b),
            ))

    # Only explicit manual grand-average picks are authoritative during export;
    # automatic values are recomputed from the current component plan so window
    # edits / method overrides cannot leave stale numbers in the workbook.
    grand_manual: dict[tuple[str, str, str], MeasurementResult] = {}
    for m in grand_measurements:
        if str(m.method).lower() == "manual":
            grand_manual[(str(m.condition), str(m.channel), str(m.component))] = m

    grand_rows: list[dict] = []
    n_subjects = len(packages)
    for condition in validated["conditions"]:
        if condition not in grand_evokeds:
            continue
        grand_rows.extend(_measure_rows_for_evoked(
            evoked=grand_evokeds[condition],
            condition=condition,
            components=components,
            auto_mode=auto_mode,
            default_channels=default_channels,
            manual_overrides=grand_manual,
            wave_type="Condition",
            subjects_n=n_subjects,
        ))
    for a, b in differences:
        if a not in grand_evokeds or b not in grand_evokeds:
            continue
        label = f"{a} − {b}"
        diff = difference_evoked(grand_evokeds[a], grand_evokeds[b], label)
        grand_rows.extend(_measure_rows_for_evoked(
            evoked=diff,
            condition=label,
            components=components,
            auto_mode=auto_mode,
            default_channels=default_channels,
            manual_overrides=grand_manual,
            wave_type="Difference",
            condition_a=a,
            condition_b=b,
            subjects_n=n_subjects,
        ))

    subject_df = pd.DataFrame(subject_rows)
    grand_df = pd.DataFrame(grand_rows)
    component_df = pd.DataFrame([asdict(c) for c in components])
    file_rows = []
    for p, manifest in zip(validated["paths"], validated["manifests"]):
        file_rows.append({
            "subject_id": str(manifest.get("subject_id", "") or ""),
            "source_file": str(p),
            "protocol_name": str((manifest.get("protocol", {}) or {}).get("name", "") or ""),
            "protocol_id": validated["protocol_hash"],
            "conditions": len(manifest.get("conditions", []) or []),
            "created_utc": str(manifest.get("created_utc", "") or ""),
        })
    files_df = pd.DataFrame(file_rows)
    diff_df = pd.DataFrame([
        {"wave_type": "Difference", "difference_a": a, "difference_b": b, "label": f"{a} − {b}"}
        for a, b in differences
    ])
    metadata_df = pd.DataFrame([
        ("subject_count", n_subjects),
        ("protocol_name", str((protocol_data or {}).get("name", "") or "")),
        ("protocol_id", validated["protocol_hash"]),
        ("automatic_measurement_mode", str(auto_mode)),
        ("default_measurement_channels", ", ".join(default_channels) if default_channels else "component channels / first EEG fallback"),
        ("difference_waves_exported", len(differences)),
        ("grand_average_weighting", "Equal subject weight (one Evoked per subject per condition)"),
    ], columns=["parameter", "value"])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        subject_df.to_excel(writer, sheet_name="Subject Components", index=False)
        grand_df.to_excel(writer, sheet_name="Grand Average Components", index=False)
        component_df.to_excel(writer, sheet_name="Component Definitions", index=False)
        files_df.to_excel(writer, sheet_name="Included Subjects", index=False)
        metadata_df.to_excel(writer, sheet_name="Grand Average Metadata", index=False)
        if not diff_df.empty:
            diff_df.to_excel(writer, sheet_name="Difference Waves", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                max_len = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 48)

    return {
        "subject_rows": int(len(subject_rows)),
        "grand_rows": int(len(grand_rows)),
        "subjects": int(n_subjects),
        "conditions": int(len(validated["conditions"])),
        "differences": int(len(differences)),
        "protocol_hash": validated["protocol_hash"],
    }
