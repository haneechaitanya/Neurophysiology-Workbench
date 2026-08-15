from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
import pandas as pd
import mne

from erpworkbench import engine
from erpworkbench.models import ProtocolDefinition, EpochSettings, SessionMetadata

sfreq = 1000.0
info = mne.create_info(['Fp1', 'Cz'], sfreq, ['eeg', 'eeg'])
# 3 epochs, 1 second each. Epoch 2 has a 150 uV artifact only in Fp1.
data = np.zeros((3, 2, 1001), dtype=float)
data += 2e-6 * np.random.default_rng(11).normal(size=data.shape)
data[1, 0, 500] = 150e-6
events = np.array([[1000, 0, 1], [3000, 0, 1], [5000, 0, 1]], dtype=int)
metadata = pd.DataFrame({
    'condition': ['Neu_Red'] * 3,
    'source_event_label': ['Neu_Red_001.jpg', 'Neu_Red_002.jpg', 'Neu_Red_003.jpg'],
    'source_event_code': [1, 2, 3],
    'event_sample': [1000, 3000, 5000],
    'event_onset_sec': [1.0, 3.0, 5.0],
})
epochs = mne.EpochsArray(data, info, events=events, event_id={'Neu_Red': 1}, tmin=-0.2, metadata=metadata, verbose=False)

protocol = ProtocolDefinition(name='test', epoch=EpochSettings(
    tmin_ms=-200, tmax_ms=800, absolute_threshold_uv=75.0,
    p2p_threshold_uv=None, flat_threshold_uv=None,
    rejection_channels=['Cz'],
))
review_cz = engine.auto_review_epochs(epochs, protocol)
assert not review_cz.auto_bad.any(), review_cz.auto_bad

protocol.epoch.rejection_channels = ['Fp1']
review_fp1 = engine.auto_review_epochs(epochs, protocol)
assert review_fp1.auto_bad.tolist() == [False, True, False], review_fp1.auto_bad

# Manual decisions + replayable log round trip.
review_fp1.manual_decision[0] = -1  # reject first manually
review_fp1.manual_decision[1] = 1   # keep auto-flagged second manually
expected = review_fp1.accepted_mask().tolist()

with TemporaryDirectory() as td:
    p = Path(td) / 'review.json'
    session = SessionMetadata(input_path=Path('subject.edf'), subject_id='S01')
    engine.export_epoch_review_log(p, session, protocol, epochs, review_fp1, history=[{'action': 'test'}])
    fresh = engine.auto_review_epochs(epochs, protocol)
    stats = engine.apply_epoch_review_log(p, epochs, fresh)
    assert stats['matched'] == 3, stats
    assert fresh.accepted_mask().tolist() == expected, (fresh.accepted_mask(), expected)

rows = engine.epoch_review_rows(epochs, review_fp1)
assert rows[0]['source_event_label'] == 'Neu_Red_001.jpg'
assert rows[1]['event_sample'] == 3000
print('REVIEW_WORKFLOW_V06_SMOKE_TEST_OK', expected)
