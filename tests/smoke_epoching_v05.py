from pathlib import Path
import tempfile
import numpy as np
import mne

from erpworkbench import engine
from erpworkbench.models import EventGroupRule, EpochSettings, ProtocolDefinition

with tempfile.TemporaryDirectory() as fixture_dir:
    annotation_path = Path(fixture_dir) / "Synthetic_Annotation.txt"
    prefixes = ("Neu_Red", "Neu_Green", "NH_Red", "NH_Green")
    rows = ["number\tName\tlatency\turevent\tduration\tchannel"]
    for index in range(120):
        label = f"{prefixes[index % len(prefixes)]}_{index:03d}.PNG"
        rows.append(f"{index + 1}\t{label}\t{1.0 + index * 0.45:.3f}\t{index + 1}\t-1\t-1")
    annotation_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    table = engine.read_annotation_txt(annotation_path)
assert len(table) == 120

sfreq = 100.0
duration = 60.0
info = mne.create_info(['Fz','Cz','Pz'], sfreq, ['eeg','eeg','eeg'])
rng = np.random.default_rng(7)
raw = mne.io.RawArray(rng.normal(0, 3e-6, (3, int(duration*sfreq))), info, verbose=False)

# Simulate one already-embedded exact duplicate to ensure TXT attachment cannot double an ERP event.
first_desc = str(table.iloc[0]['description'])
first_lat = float(table.iloc[0]['latency'])
raw.set_annotations(mne.Annotations([first_lat], [0.0], [first_desc]))
native = raw.annotations.copy()
stats = engine.attach_external_annotations(raw, native, table)
assert stats['duplicates_skipped'] == 1, stats
assert stats['attached'] == 119, stats

# Event discovery must expose the attached timeline for GUI auto-population.
events, labels = engine.discover_events(raw, 'annotations', '')
assert len(events) >= 120
assert len(labels) > 100

protocol = ProtocolDefinition(
    name='Epoch smoke test',
    epoch=EpochSettings(
        tmin_ms=-200, tmax_ms=1000,
        baseline_enabled=True, baseline_start_ms=-200, baseline_end_ms=0,
        absolute_threshold_uv=75,
    ),
)
protocol.event_groups = [
    EventGroupRule('Neu_Red', 'Neutral Red'),
    EventGroupRule('Neu_Green', 'Neutral Green'),
    EventGroupRule('NH_Red', 'Negative High Red'),
    EventGroupRule('NH_Green', 'Negative High Green'),
]
counts = engine.event_code_counts(events)
allowed = {code for code, label in labels.items() if engine.resolve_event_group_condition(label, protocol.event_groups)}
assert allowed
plan = engine.epoch_preflight(raw, events, protocol, labels, allowed)
assert plan['selected_total'] > 0
assert plan['in_bounds_total'] > 0
assert not plan['conflicts']
assert sum(x['selected'] for x in plan['conditions']) == plan['selected_total']

epochs = engine.create_epochs(raw, events, protocol, labels, allowed)
assert len(epochs) == plan['in_bounds_total'], (len(epochs), plan['in_bounds_total'])
assert epochs.metadata is not None
assert {'condition','source_event_label','source_event_code','event_sample','event_onset_sec'} <= set(epochs.metadata.columns)
assert set(epochs.event_id) >= {'Neutral Red','Neutral Green','Negative High Red','Negative High Green'}

# Overlapping groups mapping the same label to different conditions must be blocked.
protocol.event_groups.append(EventGroupRule('Neu_', 'Ambiguous Neutral'))
conflict_plan = engine.epoch_preflight(raw, events, protocol, labels, allowed)
assert conflict_plan['conflicts']

print('EPOCHING_V05_SMOKE_TEST_OK', len(events), len(labels), len(epochs), counts.get(next(iter(allowed)), 0))
