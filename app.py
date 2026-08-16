from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

import mne
import numpy as np
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from erpworkbench import __version__
from erpworkbench.build_channel import IS_STORE_BUILD
from erpworkbench.main_window import ERPWorkbench



def resource_path(*parts: str) -> Path:
    """Resolve bundled/static resources in source and PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def configure_windows_app_identity() -> None:
    """Give the frozen app a stable Windows taskbar identity."""
    if sys.platform != "win32" or IS_STORE_BUILD:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "NeurophysiologyWorkbench.ERPWorkbench"
        )
    except Exception:
        pass

def make_demo_raw():
    """Synthetic EEG for GUI development: use `python app.py --demo`."""
    sfreq = 500.0
    duration = 90.0
    names = [
        "Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "FC5", "FC1", "FC2", "FC6",
        "T7", "C3", "Cz", "C4", "T8", "CP5", "CP1", "CP2", "CP6", "P7", "P3",
        "Pz", "P4", "P8", "POz", "O1", "Oz", "O2", "M1", "M2", "EOG",
    ]
    ch_types = ["eeg"] * 31 + ["eog"]
    info = mne.create_info(names, sfreq, ch_types)
    rng = np.random.default_rng(7)
    n = int(duration * sfreq)
    t = np.arange(n) / sfreq
    data = rng.normal(0, 6e-6, (len(names), n))
    for i in range(31):
        data[i] += (3e-6 * np.sin(2 * np.pi * (8 + (i % 4)) * t))
    # Blink-like activity in frontal channels.
    for center in [12, 31, 55, 74]:
        blink = 120e-6 * np.exp(-0.5 * ((t - center) / 0.12) ** 2)
        data[0] += blink; data[1] += blink; data[-1] += 1.5 * blink
    raw = mne.io.RawArray(data, info, verbose="ERROR")
    raw.set_montage("standard_1020", match_case=False, on_missing="warn", verbose="ERROR")
    onsets = np.arange(2.0, duration - 1, 1.5)
    desc = np.array(["Neutral", "Positive", "Negative"] * 30)[: len(onsets)]
    raw.set_annotations(mne.Annotations(onsets, np.zeros(len(onsets)), desc))
    return raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="start with synthetic EEG for GUI testing")
    args = parser.parse_args()

    configure_windows_app_identity()

    app = QApplication(sys.argv)
    app.setApplicationName("ERP Workbench")
    app.setApplicationVersion(__version__)
    # Keep this stable so existing QSettings/preferences are not reset.
    app.setOrganizationName("ERP Workbench")

    icon_path = resource_path("assets", "erp_workbench_icon.png")
    app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = ERPWorkbench()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    if args.demo:
        raw = make_demo_raw()
        window.original_raw = raw
        window.processed_raw = raw.copy()
        window.file_label.setText("Synthetic demo")
        window.raw_viewer.set_raw(window.processed_raw)
        window.status_label.setText("Synthetic demo EEG loaded.")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
