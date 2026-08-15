import numpy as np
import mne

from erpworkbench import engine
from erpworkbench.models import EpochSettings, EventGroupRule, ProtocolDefinition

sfreq = 1000.0
raw = mne.io.RawArray(
    np.zeros((2, int(8 * sfreq))),
    mne.create_info(["Cz", "Pz"], sfreq, ["eeg", "eeg"]),
    verbose=False,
)
raw.set_annotations(mne.Annotations(
    onset=[1.0, 2.0, 3.0, 4.0, 5.0],
    duration=[0.0] * 5,
    description=[
        "Neu_Red_001.jpg",
        "Neu_Red_002.jpg",
        "Neu_Green_001.jpg",
        "NH_Red_001.jpg",
        "Blank.PNG",
    ],
))
events, labels = engine.discover_events(raw, "annotations", "")
rules = [
    EventGroupRule("Neu_Red", "Neutral Red"),
    EventGroupRule("Neu_Green", "Neutral Green"),
]
stats = engine.event_group_stats(labels, events, rules)
assert stats[0]["markers"] == 2
assert stats[1]["markers"] == 1

protocol = ProtocolDefinition(
    name="group smoke",
    epoch=EpochSettings(
        tmin_ms=-100,
        tmax_ms=200,
        baseline_enabled=False,
        absolute_threshold_uv=None,
    ),
    event_groups=rules,
)
epochs = engine.create_epochs(raw, events, protocol, event_labels=labels)
assert len(epochs) == 3
assert set(epochs.event_id) == {"Neutral Red", "Neutral Green"}

# Starts-with mode prevents broad strings such as "Neu" from capturing practice trials.
start_rule = EventGroupRule("Neu", "Neutral", starts_with=True)
assert engine.event_group_match("Neu_Red_001.jpg", start_rule)
assert engine.event_group_match("Neu_Green_001.jpg", start_rule)
assert not engine.event_group_match("PracNeu_Red_001.jpg", start_rule)
# Default contains behavior remains available for backwards compatibility.
contains_rule = EventGroupRule("Neu", "Neutral", starts_with=False)
assert engine.event_group_match("PracNeu_Red_001.jpg", contains_rule)

print("EVENT_GROUP_SMOKE_TEST_OK")
