from __future__ import annotations

from pathlib import Path
import json
import tempfile
import zipfile

import mne
import numpy as np
import openpyxl

from erpworkbench import __version__
from erpworkbench import engine
from erpworkbench.models import (
    EpochReviewState,
    PreprocessingSettings,
    ProtocolDefinition,
    SessionMetadata,
)


def main():
    sfreq = 100.0
    info = mne.create_info(["Fz", "Cz"], sfreq, ch_types="eeg")
    ev = mne.EvokedArray(np.zeros((2, 101)), info, tmin=-0.2, comment="A", nave=5)
    meta = SessionMetadata(subject_id="SYNTH")
    prep = PreprocessingSettings()
    protocol = ProtocolDefinition(name="Provenance smoke")

    with tempfile.TemporaryDirectory(prefix="erpwb_prov_") as td:
        td = Path(td)
        pkg = td / "subject.erpavg"
        engine.save_average_package(pkg, {"A": ev}, meta, prep, protocol, [])
        with zipfile.ZipFile(pkg, "r") as zf:
            manifest = json.loads(zf.read("session.json").decode("utf-8"))
        assert manifest.get("erp_workbench_version") == __version__

        # Minimal Epochs/review object for the regular subject workbook.
        raw = mne.io.RawArray(np.zeros((2, 500)), info, verbose="ERROR")
        events = np.array([[100, 0, 1], [250, 0, 1]], dtype=int)
        epochs = mne.Epochs(raw, events, {"A": 1}, tmin=-0.1, tmax=0.2,
                            baseline=None, preload=True, verbose="ERROR")
        review = EpochReviewState(
            auto_bad=np.zeros(len(epochs), dtype=bool),
            auto_reason=[""] * len(epochs),
            manual_decision=np.zeros(len(epochs), dtype=np.int8),
        )
        xlsx = td / "subject.xlsx"
        engine.export_excel(xlsx, meta, prep, protocol, epochs, review, [])
        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        try:
            ws = wb["Processing Metadata"]
            values = {str(r[0].value): r[1].value for r in ws.iter_rows(min_row=2, max_col=2)}
            assert values.get("erp_workbench_version") == __version__
        finally:
            # On Windows, openpyxl keeps the XLSX/ZIP file handle open until the
            # workbook is explicitly closed. Without this, TemporaryDirectory
            # cleanup fails with WinError 32 even though the provenance assertions
            # themselves have already passed.
            wb.close()

    print("RELEASE_PROVENANCE_V10_SMOKE_TEST_OK", __version__)


if __name__ == "__main__":
    main()
