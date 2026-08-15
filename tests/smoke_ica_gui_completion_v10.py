from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MNE_LOGGING_LEVEL", "WARNING")

import mne
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import erpworkbench.main_window as main_window


def main():
    # Keep the smoke test's protocol folder inside its working directory.
    main_window.QStandardPaths.writableLocation = staticmethod(lambda *_: os.getcwd())
    app = QApplication.instance() or QApplication([])
    window = main_window.ERPWorkbench()

    info = mne.create_info(["Fp1", "Fp2", "Cz", "Pz"], 100.0, ch_types="eeg")
    times = np.arange(1000, dtype=float) / 100.0
    data = np.vstack(
        [
            20e-6 * np.sin(2 * np.pi * frequency * times)
            for frequency in (2.0, 3.0, 5.0, 7.0)
        ]
    )
    raw = mne.io.RawArray(data, info, verbose="ERROR")
    window.original_raw = raw
    window.processed_raw = raw.copy()
    window._pre_ica_raw = raw.copy()

    # Reproduce the real pre/post viewer lifecycle: it previously displayed a
    # recording, ICA state clearing temporarily removes it, then reconstruction
    # loads the same channel layout again. Detached cached curves must not be
    # reused after the clear.
    window.ica_post_view.set_raw(window._pre_ica_raw)
    assert window.ica_post_view._trace_items
    window.ica_post_view.set_raw(None)
    assert not window.ica_post_view._trace_items

    # Deliberately omit the result-signal connection to reproduce the RC3
    # failure mode: computation finishes, but the normal queued GUI result
    # handoff is absent. The finished-handler fallback must retain and expose
    # the reconstructed Raw anyway.
    worker = main_window.FunctionWorker(lambda progress=None: raw.copy())
    window._ica_reconstruction_worker = worker
    window._ica_reconstruction_pending_excluded = [0]
    window._ica_reconstruction_result_received = False
    window._ica_reconstruction_failed = False
    worker.signals.error.connect(window._ica_removal_error)
    worker.signals.finished.connect(window._ica_removal_finished)

    loop = QEventLoop()
    worker.signals.finished.connect(loop.quit)
    window.thread_pool.start(worker)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()

    assert window._ica_cleaned_raw is not None
    assert window._ica_reconstruction_result_received
    assert window.ica_post_display_combo.findData("ica_cleaned") >= 0
    assert window.ica_epoch_input_combo.findData("ica_cleaned") >= 0
    assert window.ica_post_display_combo.currentData() == "ica_cleaned"
    assert window.ica_epoch_input_combo.currentData() == "ica_cleaned"
    assert window._epoching_raw() is window._ica_cleaned_raw
    plotted_items = set(window.ica_post_view.plot.listDataItems())
    assert window.ica_post_view._trace_items
    assert all(item in plotted_items for item in window.ica_post_view._trace_items)
    assert all(np.ptp(item.getData()[1]) > 0.05 for item in window.ica_post_view._trace_items)
    print("ICA_GUI_COMPLETION_V10_SMOKE_TEST_OK")


if __name__ == "__main__":
    main()
