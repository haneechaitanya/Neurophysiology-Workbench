"""Core non-GUI smoke test using synthetic EEG.

Run from repository root:
    python tests/smoke_core.py
"""
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mne
import numpy as np

from erpworkbench import engine
from erpworkbench.models import (
    ComponentDefinition,
    EpochSettings,
    PreprocessingSettings,
    ProtocolDefinition,
    SessionMetadata,
)


def main():
    sfreq = 250.0
    names = ["Fp1", "Fp2", "F3", "F4", "Cz", "Pz", "O1", "O2"]
    info = mne.create_info(names, sfreq, "eeg")
    rng = np.random.default_rng(42)
    data = rng.normal(0, 5e-6, (len(names), int(15 * sfreq)))
    raw = mne.io.RawArray(data, info, verbose="ERROR")
    raw.set_montage("standard_1020", match_case=False, on_missing="warn", verbose="ERROR")
    raw.set_annotations(mne.Annotations([1, 3, 5, 7, 9, 11, 13], [0] * 7, ["A", "B", "A", "B", "A", "B", "A"]))

    prep = PreprocessingSettings()
    prep.filter.enabled = True
    prep.filter.l_freq = 0.5
    prep.filter.h_freq = 35.0
    prep.reference.enabled = True

    processed = engine.apply_preprocessing(raw, prep)
    events, labels = engine.discover_events(processed)
    protocol = ProtocolDefinition(
        name="Smoke",
        epoch=EpochSettings(
            tmin_ms=-200,
            tmax_ms=600,
            baseline_enabled=True,
            baseline_start_ms=-200,
            baseline_end_ms=0,
            absolute_threshold_uv=75,
        ),
        event_map={str(code): label for code, label in labels.items()},
        components=[ComponentDefinition("N2", 200, 350, "negative", "peak", ["Cz"])],
    )
    epochs = engine.create_epochs(processed, events, protocol)
    review = engine.auto_review_epochs(epochs, protocol)
    clean = engine.clean_epochs(epochs, review)
    evokeds = engine.condition_averages(clean)
    result = engine.measure_evoked(evokeds["A"], "A", "Cz", protocol.components[0], len(clean["A"]))

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "smoke.xlsx"
        engine.export_excel(out, SessionMetadata(input_path=Path("synthetic.edf")), prep, protocol, epochs, review, [result])
        assert out.exists() and out.stat().st_size > 0

    print("CORE_SMOKE_TEST_OK")


if __name__ == "__main__":
    main()
