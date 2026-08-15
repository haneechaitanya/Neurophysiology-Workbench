import numpy as np
import mne
from erpworkbench import engine
from erpworkbench.models import ProtocolDefinition, EpochSettings

sfreq=1000.0
info=mne.create_info(['Cz'],sfreq,['eeg'])
# one epoch, +/-60 uV -> abs=60 (below 100), p2p=120 (above 100)
data=np.zeros((1,1,201),float)
data[0,0,50]=60e-6
data[0,0,150]=-60e-6
events=np.array([[1000,0,1]])
epochs=mne.EpochsArray(data,info,events=events,event_id={'test':1},tmin=-0.1,verbose=False)
protocol=ProtocolDefinition(name='p2p-test', epoch=EpochSettings(
    tmin_ms=-100,tmax_ms=100,baseline_enabled=False,
    absolute_threshold_uv=100.0,p2p_threshold_uv=100.0,flat_threshold_uv=None,
))
review=engine.auto_review_epochs(epochs,protocol)
assert bool(review.auto_bad[0])
assert 'p2p>100' in review.auto_reason[0], review.auto_reason[0]
assert 'abs>' not in review.auto_reason[0], review.auto_reason[0]
print('P2P_V10_SMOKE_TEST_OK', review.auto_reason[0])
