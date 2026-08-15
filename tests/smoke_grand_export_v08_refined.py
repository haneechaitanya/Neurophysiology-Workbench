from pathlib import Path
import tempfile
import numpy as np
import pandas as pd
import mne

from erpworkbench import engine
from erpworkbench.models import ComponentDefinition, MeasurementResult, PreprocessingSettings, ProtocolDefinition, SessionMetadata

sfreq = 250.0
info = mne.create_info(["Fz", "Cz", "Pz"], sfreq, ["eeg"] * 3)
times = np.arange(251) / sfreq - 0.2
component = ComponentDefinition("P3", 300, 500, "positive", "peak", ["Pz"])
protocol = ProtocolDefinition(name="GrandExportSmoke", components=[component])
prep = PreprocessingSettings()

def make_evoked(scale, condition):
    data = np.zeros((3, len(times)))
    data[2] = scale * 1e-6 * np.exp(-0.5 * ((times - 0.36) / 0.06) ** 2)
    return mne.EvokedArray(data, info.copy(), tmin=-0.2, comment=condition, nave=20)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    p1, p2 = td / "s1.erpavg", td / "s2.erpavg"
    ev1 = {"A": make_evoked(4.0, "A"), "B": make_evoked(5.0, "B")}
    ev2 = {"A": make_evoked(6.0, "A"), "B": make_evoked(7.0, "B")}
    manual_s1 = MeasurementResult("A", "Pz", "P3", "manual", 300, 500, 9.25, 421.0, 20, "manual subject correction")
    engine.save_average_package(p1, ev1, SessionMetadata(subject_id="S1"), prep, protocol, [manual_s1], condition_counts={"A":20,"B":18})
    engine.save_average_package(p2, ev2, SessionMetadata(subject_id="S2"), prep, protocol, [], condition_counts={"A":19,"B":20})
    ga = engine.grand_average_packages([p1, p2])
    grand_manual = [MeasurementResult("A", "Pz", "P3", "manual", 300, 500, 8.5, 430.0, 2, "grand manual")]
    xlsx = td / "grand.xlsx"
    summary = engine.export_grand_average_excel(
        xlsx, [p1, p2], ga["evokeds"], ga["protocol"], [component], grand_manual,
        auto_mode="peak", default_channels=["Pz"], difference_pairs=[("A", "B")],
    )
    assert xlsx.exists()
    assert summary["subjects"] == 2 and summary["differences"] == 1
    subj = pd.read_excel(xlsx, sheet_name="Subject Components")
    grand = pd.read_excel(xlsx, sheet_name="Grand Average Components")
    s1a = subj[(subj.subject_id == "S1") & (subj.condition == "A") & (subj.component == "P3")].iloc[0]
    assert s1a.method == "manual" and abs(float(s1a.amplitude_uv) - 9.25) < 1e-9
    assert ((subj.wave_type == "Difference") & (subj.difference_a == "A") & (subj.difference_b == "B")).any()
    ga_a = grand[(grand.condition == "A") & (grand.component == "P3")].iloc[0]
    assert ga_a.method == "manual" and abs(float(ga_a.amplitude_uv) - 8.5) < 1e-9
    assert ((grand.wave_type == "Difference") & (grand.difference_a == "A") & (grand.difference_b == "B")).any()

print("GRAND_EXPORT_V08_REFINED_SMOKE_TEST_OK")
