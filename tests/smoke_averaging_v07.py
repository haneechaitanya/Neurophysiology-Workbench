from pathlib import Path
import tempfile
import numpy as np
import mne

from erpworkbench import engine
from erpworkbench.models import (
    ComponentDefinition, EpochReviewState, MeasurementResult,
    PreprocessingSettings, ProtocolDefinition, SessionMetadata,
)

sfreq = 250.0
ch_names = ["Fz", "Cz", "Pz", "O1"]
info = mne.create_info(ch_names, sfreq, ["eeg"] * len(ch_names))
tmin = -0.2
times = np.arange(int(1.0 * sfreq) + 1) / sfreq + tmin
rng = np.random.default_rng(17)

# 12 epochs, alternating A/B. Add a P3-like positive bump with larger B amplitude.
n_epochs = 12
data = rng.normal(0, 0.4e-6, (n_epochs, len(ch_names), len(times)))
for i in range(n_epochs):
    amp = 4e-6 if i % 2 == 0 else 6e-6
    bump = amp * np.exp(-0.5 * ((times - 0.35) / 0.05) ** 2)
    data[i, ch_names.index("Pz")] += bump
    data[i, ch_names.index("Cz")] += 0.7 * bump

events = np.c_[np.arange(n_epochs) * 400 + 100, np.zeros(n_epochs, int), np.where(np.arange(n_epochs) % 2 == 0, 1, 2)]
metadata = None
epochs = mne.EpochsArray(data, info, events=events.astype(int), event_id={"A": 1, "B": 2}, tmin=tmin, metadata=metadata, verbose=False)
review = EpochReviewState(auto_bad=np.zeros(n_epochs, bool), auto_reason=[""] * n_epochs, manual_decision=np.zeros(n_epochs, np.int8))
review.manual_decision[0] = -1
clean = engine.clean_epochs(epochs, review)
evokeds = engine.condition_averages(clean)
assert set(evokeds) == {"A", "B"}
counts = engine.accepted_condition_counts(clean)
assert counts == {"A": 5, "B": 6}, counts

component = ComponentDefinition("P3", 300, 450, "positive", "peak", ["Pz"])
res = engine.measure_evoked(evokeds["A"], "A", "Pz", component, counts["A"])
assert 300 <= res.latency_ms <= 450
assert res.amplitude_uv > 2.0
mean_component = ComponentDefinition("P3mean", 300, 450, "positive", "mean", ["Pz"])
mean_res = engine.measure_evoked(evokeds["B"], "B", "Pz", mean_component, counts["B"])
assert mean_res.latency_ms is None

diff = engine.difference_evoked(evokeds["B"], evokeds["A"], "B - A")
assert diff.comment == "B - A"
assert np.max(diff.data[ch_names.index("Pz")]) > 0

protocol = ProtocolDefinition(name="Smoke", components=[component, mean_component])
meta = SessionMetadata(subject_id="SYNTH", notes="v0.7 package smoke")
prep = PreprocessingSettings()
measurements = [res, mean_res]

with tempfile.TemporaryDirectory() as td:
    package = Path(td) / "synth.erpavg"
    engine.save_average_package(
        package, evokeds, meta, prep, protocol, measurements,
        condition_counts=counts, epochs=epochs, review=review,
    )
    assert package.exists() and package.stat().st_size > 0
    loaded = engine.load_average_package(package)
    assert set(loaded["evokeds"]) == {"A", "B"}
    assert loaded["manifest"]["condition_counts"] == {"A": 5, "B": 6}
    assert len(loaded["manifest"]["measurements"]) == 2
    assert "B - A" not in loaded["evokeds"]  # temporary difference must never leak into saved subject conditions

print("AVERAGING_V07_SMOKE_TEST_OK", counts, round(res.amplitude_uv, 3), round(mean_res.amplitude_uv, 3))
