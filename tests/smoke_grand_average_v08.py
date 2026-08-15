from pathlib import Path
import tempfile
import numpy as np
import mne

from erpworkbench import engine
from erpworkbench.models import ComponentDefinition, PreprocessingSettings, ProtocolDefinition, SessionMetadata

sfreq = 250.0
ch_names = ["Fz", "Cz", "Pz"]
info = mne.create_info(ch_names, sfreq, ["eeg"] * 3)
times = np.arange(251) / sfreq - 0.2
protocol = ProtocolDefinition(name="GrandSmoke", components=[ComponentDefinition("P3", 300, 500, "positive", "peak", ["Pz"])])
prep = PreprocessingSettings()

def make_evoked(scale, condition):
    data = np.zeros((3, len(times)))
    data[2] = scale * 1e-6 * np.exp(-0.5 * ((times - 0.36) / 0.06) ** 2)
    return mne.EvokedArray(data, info.copy(), tmin=-0.2, comment=condition, nave=20)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    p1, p2 = td/"s1.erpavg", td/"nested"/"s2.erpavg"
    p2.parent.mkdir()
    ev1 = {"A": make_evoked(4.0, "A"), "B": make_evoked(5.0, "B")}
    ev2 = {"A": make_evoked(6.0, "A"), "B": make_evoked(7.0, "B")}
    engine.save_average_package(p1, ev1, SessionMetadata(subject_id="S1"), prep, protocol, [], condition_counts={"A":20,"B":20})
    engine.save_average_package(p2, ev2, SessionMetadata(subject_id="S2"), prep, protocol, [], condition_counts={"A":20,"B":20})
    val = engine.validate_average_package_manifests([p1, p2])
    assert val["protocol_hash"] == engine.protocol_fingerprint(protocol.to_dict())
    result = engine.grand_average_packages([p1, p2])
    assert result["subject_count"] == 2
    assert set(result["evokeds"]) == {"A", "B"}
    ga = result["evokeds"]["A"]
    peak = float(np.max(ga.data[2]) * 1e6)
    assert 4.8 < peak < 5.2, peak
    assert int(ga.nave) == 2

    mismatch = ProtocolDefinition(name="GrandSmoke", components=[ComponentDefinition("P3", 250, 500, "positive", "peak", ["Pz"])])
    p3 = td/"s3.erpavg"
    engine.save_average_package(p3, ev2, SessionMetadata(subject_id="S3"), prep, mismatch, [], condition_counts={"A":20,"B":20})
    try:
        engine.validate_average_package_manifests([p1, p3])
    except ValueError as exc:
        assert "protocol mismatch" in str(exc).lower()
    else:
        raise AssertionError("Protocol mismatch was not rejected")

print("GRAND_AVERAGE_V08_SMOKE_TEST_OK")
