from __future__ import annotations

import argparse
import sys

import mne
import numpy as np
from PySide6.QtWidgets import QApplication

from erpworkbench.main_window import ERPWorkbench


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

    app = QApplication(sys.argv)
    app.setApplicationName("ERP Workbench")
    app.setApplicationVersion("1.0.0rc4")
    app.setOrganizationName("ERP Workbench")
    window = ERPWorkbench()
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
