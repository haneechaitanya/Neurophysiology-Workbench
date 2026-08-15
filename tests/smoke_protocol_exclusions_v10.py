import numpy as np
import mne
import tempfile
from pathlib import Path

from erpworkbench import engine
from erpworkbench.models import ProtocolDefinition, EpochSettings, EventGroupRule
from erpworkbench.protocols import save_protocol, load_protocol

sfreq = 100.0
info = mne.create_info(["Fz", "Cz"], sfreq, ["eeg", "eeg"])
raw = mne.io.RawArray(np.zeros((2, 1000)), info, verbose=False)
# Three exact labels all belong to one broad string group.
labels = {1: "Neu_Red_001.jpg", 2: "Neu_Red_002.jpg", 3: "Neu_Red_003.jpg"}
events = np.asarray([[200, 0, 1], [400, 0, 2], [600, 0, 3]], dtype=int)
protocol = ProtocolDefinition(
    name="Exclusion smoke",
    epoch=EpochSettings(tmin_ms=-100, tmax_ms=300, baseline_enabled=False),
    event_groups=[EventGroupRule("Neu_Red", "Neutral Red")],
)
plan = engine.epoch_preflight(raw, events, protocol, labels, {1, 2, 3})
assert plan["selected_total"] == 3, plan

protocol.excluded_event_labels = {"Neutral Red": ["Neu_Red_002.jpg"]}
plan2 = engine.epoch_preflight(raw, events, protocol, labels, {1, 2, 3})
assert plan2["selected_total"] == 2, plan2
epochs = engine.create_epochs(raw, events, protocol, labels, {1, 2, 3})
assert len(epochs) == 2
assert "Neu_Red_002.jpg" not in set(epochs.metadata["source_event_label"])
assert set(epochs.metadata["source_event_label"]) == {"Neu_Red_001.jpg", "Neu_Red_003.jpg"}
with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "protocol.json"
    save_protocol(protocol, path)
    loaded = load_protocol(path)
    assert loaded.excluded_event_labels == protocol.excluded_event_labels
print("PROTOCOL_EXCLUSIONS_V10_SMOKE_TEST_OK", len(epochs))
