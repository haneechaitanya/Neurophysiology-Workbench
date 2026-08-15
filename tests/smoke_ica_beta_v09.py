from pathlib import Path
import numpy as np
import mne

from erpworkbench import engine


def main():
    sfreq = 100.0
    duration = 20.0
    n_times = int(sfreq * duration)
    rng = np.random.default_rng(7)
    names = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4"]
    data = rng.normal(scale=2e-6, size=(len(names), n_times))
    t = np.arange(n_times) / sfreq
    # Shared frontal blink-like source, plus a gross movement section that should
    # be removable from the ICA fit only.
    blink = 40e-6 * np.exp(-0.5 * ((t - 5.0) / 0.12) ** 2)
    data[0] += blink; data[1] += 0.9 * blink; data[2] += 0.4 * blink; data[3] += 0.4 * blink
    move = (t >= 10.0) & (t < 12.0)
    data[:, move] += 150e-6 * np.sin(2 * np.pi * 3 * t[move])
    info = mne.create_info(names, sfreq, "eeg")
    raw = mne.io.RawArray(data, info, verbose=False)
    raw.set_montage("standard_1020")
    original_annotations = len(raw.annotations)

    exclusions = [{"start_sec": 10.0, "end_sec": 12.0, "reason": "neck movement"}]
    ica = engine.run_ica(raw, method="fastica", n_components=0.99, random_state=97, fit_exclude_spans=exclusions)
    assert len(raw.annotations) == original_annotations, "ICA-fit exclusions must not modify the analysis Raw annotations"
    assert getattr(ica, "_erpworkbench_fit_exclusions", [])
    assert ica.n_samples_ < raw.n_times, "Excluded samples should not be used in ICA fitting"

    cleaned = engine.apply_ica(raw, ica, [])
    assert cleaned.n_times == raw.n_times
    assert np.array_equal(cleaned.times, raw.times)
    assert len(cleaned.annotations) == len(raw.annotations)
    print("v0.9 ICA beta smoke test passed", ica.n_components_, ica.n_samples_, raw.n_times)


if __name__ == "__main__":
    main()
