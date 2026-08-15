from __future__ import annotations

import traceback
import math
import copy
import os
import re
import ctypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import mne
import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QKeySequence, QPalette, QShortcut, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QKeySequenceEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import engine
from .models import (
    ComponentDefinition,
    EpochReviewState,
    EventGroupRule,
    MeasurementResult,
    PreprocessingSettings,
    ProtocolDefinition,
    SessionMetadata,
)
from .protocols import load_protocol, save_protocol
from .widgets.eeg_viewer import StackedEEGViewer
from .widgets.erp_viewer import ERPViewer
from .widgets.ica_view import ICAComponentView
from .ui_utils import ReliableDoubleSpinBox, ReliableSpinBox


DEFAULT_SHORTCUTS = {
    "previous": "Left",
    "next": "Right",
    "time_in": "+",
    "time_out": "-",
    "sensitivity_up": "*",
    "sensitivity_down": "/",
    "toggle_reject": "R",
}

OPEN_WORKBENCH_WINDOWS = []  # keeps independent top-level analysis windows alive

SHORTCUT_LABELS = {
    "previous": "Previous page / condition",
    "next": "Next page / condition",
    "time_in": "Zoom time in",
    "time_out": "Zoom time out",
    "sensitivity_up": "Increase vertical sensitivity",
    "sensitivity_down": "Decrease vertical sensitivity",
    "toggle_reject": "Toggle epoch rejection",
}

HELP_TOPICS = {
    "Import EDF / FIF and data units": {
        "summary": "Import creates the continuous MNE Raw object used by the rest of the workflow. Viewer zooming or scrolling never crops the scientific recording.",
        "what": "EEG is sampled voltage over time at named electrodes. EDF is a common exchange format; FIF is MNE's native format and can preserve richer metadata. ERP Workbench preloads the Raw data so later deterministic preprocessing can work on the complete recording while the viewer draws only the requested time window.",
        "why": "Correct channel names, sampling rate, channel types, event timing and sensor locations are prerequisites for filtering, interpolation, ICA, epoching and topographic plots. A display operation is deliberately separated from a data-processing operation.",
        "implementation": "EDF is opened with MNE read_raw_edf and FIF with read_raw_fif using preload=True. Existing annotations and sensor metadata are retained. A standard montage may be matched from recognizable EEG channel names when topographic display needs positions and the file does not already contain them.",
        "watch": "Confirm sampling rate, duration, channel names/types and event timing after import. A visually unusual trace is not changed merely by changing vertical sensitivity or positive-up/positive-down display convention.",
        "science": [("MNE-Python software paper — Gramfort et al. (2013), open access", "https://www.frontiersin.org/journals/neuroscience/articles/10.3389/fnins.2013.00267/full")],
        "mne": [("MNE read_raw_edf", "https://mne.tools/stable/generated/mne.io.read_raw_edf.html"), ("MNE read_raw_fif", "https://mne.tools/stable/generated/mne.io.read_raw_fif.html"), ("MNE Raw", "https://mne.tools/stable/generated/mne.io.Raw.html")],
    },
    "Filtering": {
        "summary": "Filtering changes the frequency content of the EEG. A high-pass attenuates very slow activity below its cutoff; a low-pass attenuates fast activity above its cutoff; using both forms a band-pass. A notch targets a narrow frequency such as mains interference.",
        "what": "EEG contains activity at many frequencies. A high-pass filter is commonly used to reduce slow drift, while a low-pass filter reduces high-frequency activity/noise. The cutoff is not a brick wall: practical digital filters have a transition band. Filters also have an impulse response, so settings can change waveform shape and can create temporal ringing or distortion when chosen poorly.",
        "why": "Filtering can improve signal-to-noise ratio and stabilize later procedures such as ICA, but aggressive high-pass or low-pass choices can change ERP amplitudes, latencies and apparent morphology. The appropriate band therefore depends on the scientific question rather than on a universal preset.",
        "implementation": "ERP Workbench rebuilds filtering from the imported Raw instead of repeatedly filtering an already-filtered signal. It calls MNE Raw.filter for high/low cutoffs and Raw.notch_filter when notch filtering is enabled. The exact parameters are saved in provenance. The separate temporary ICA fitting high-pass, when needed, is applied only to an ICA fit copy and does not replace the analysis EEG.",
        "watch": "Inspect the unfiltered and filtered signal, avoid treating a cutoff as an absolute frequency boundary, and report filter settings. Very aggressive high-pass filtering is especially capable of producing artifactual ERP effects.",
        "science": [("Tanner et al. (2015) — high-pass filtering can create ERP artifacts, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC4506207/"), ("Zhang et al. — Optimal Filters for ERP Research, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC10245912/")],
        "mne": [("MNE background information on filtering", "https://mne.tools/stable/auto_tutorials/preprocessing/25_background_filtering.html"), ("MNE filtering and resampling tutorial", "https://mne.tools/stable/auto_tutorials/preprocessing/30_filtering_resampling.html"), ("MNE Raw.filter", "https://mne.tools/stable/generated/mne.io.Raw.html#mne.io.Raw.filter")],
    },
    "Bad-channel interpolation": {
        "summary": "Interpolation replaces a bad electrode's time series with a spatial estimate derived from the remaining good electrodes; it is repair, not recovery of the lost original signal.",
        "what": "A malfunctioning or persistently noisy electrode can contaminate averages, references, artifact rejection and decomposition methods. For EEG, MNE's standard bad-channel interpolation uses spherical splines: electrode positions are represented on a sphere and the spatial voltage field at the bad location is estimated from good sensors.",
        "why": "Interpolation can preserve a common channel layout across subjects instead of deleting a different electrode in every participant. Its quality depends on having trustworthy sensor locations and enough surrounding good channels.",
        "implementation": "ERP Workbench marks only the user-selected channels bad on a copy, ensures usable montage information when possible, and calls MNE interpolate_bads. In the structural pipeline interpolation precedes re-reference so a known bad channel does not contribute its corrupted signal to the new reference before repair.",
        "watch": "Do not interpolate a channel merely because one short epoch is noisy; bad-channel interpolation is intended for channels judged unreliable over the relevant recording. Large numbers or clusters of bad electrodes reduce confidence in the spatial estimate.",
        "science": [("Dong et al. (2021) — scalp EEG interpolation methods, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC8195908/")],
        "mne": [("MNE handling bad channels", "https://mne.tools/stable/auto_tutorials/preprocessing/15_handling_bad_channels.html"), ("MNE interpolation example", "https://mne.tools/stable/auto_examples/preprocessing/interpolate_bad_channels.html"), ("MNE interpolation implementation details", "https://mne.tools/stable/documentation/implementation.html")],
    },
    "EEG re-reference": {
        "summary": "EEG measures voltage differences, so changing the reference changes every channel waveform. Average reference subtracts the instantaneous mean of the selected EEG electrodes; a custom reference subtracts the chosen electrode(s).",
        "what": "There is no absolute scalp voltage measured independently of a reference. Re-referencing linearly expresses the same recorded potential differences relative to another reference. An average reference approximates a common reference by using the average across available EEG electrodes; a custom reference uses named channels.",
        "why": "Reference choice changes scalp amplitudes and topographies and can therefore influence component appearance and interpretation. The reference should be methodologically justified and consistently applied across subjects.",
        "implementation": "ERP Workbench calls MNE set_eeg_reference. Average reference uses ref_channels='average'; custom reference uses the selected existing channels. Re-reference is rebuilt deterministically after interpolation when both are enabled.",
        "watch": "A bad channel can contaminate an average reference, which is why bad-channel handling should be resolved first. Reference choice should be included in protocol/provenance and not changed casually between subjects.",
        "science": [("Yao et al. (2019) — Which reference should we use for EEG and ERP practice?, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6592976/"), ("Dong et al. (2019) — comparison of EEG references, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6798171/")],
        "mne": [("MNE set_eeg_reference", "https://mne.tools/stable/generated/mne.set_eeg_reference.html")],
    },
    "ICA (BETA) and eye-blink removal": {
        "summary": "Independent Component Analysis decomposes multichannel EEG into spatially fixed component patterns with time-varying activations. ERP Workbench keeps ICA BETA and requires deliberate component selection/removal.",
        "what": "ICA seeks statistically independent source activations whose weighted mixtures reproduce the sensor EEG. An ocular component often has a stereotyped blink time course and frontal scalp distribution. Removing a component means setting that source contribution to zero and reconstructing sensor-space EEG from the remaining components; it does not simply erase samples around each blink.",
        "why": "ICA can attenuate recurrent blink contamination while retaining surrounding EEG, but a wrong component choice can remove neural signal. Component identity should therefore be judged from morphology, timing and topography rather than from one automatic label alone.",
        "implementation": "ICA is fitted with MNE preprocessing.ICA on a temporary fit copy. User-marked gross-artifact spans are excluded from fitting only. When needed, a temporary approximately 1 Hz high-pass copy is used for ICA estimation; the fitted decomposition is applied back to the full-resolution pre-ICA processed EEG. ICLabel is a trained machine-learning classifier that assigns probabilities to classes such as brain, eye and muscle; ERP Workbench displays it as evidence, not as an automatic removal command. The frontal blink-correlation column is another screening aid, not a diagnosis. Reconstruction is explicitly started with Remove selected components and pre/post versions remain separate.",
        "watch": "Keep representative blinks in the ICA fitting data if blink removal is the goal; exclude gross, unrepresentative motion/electrode disturbances that could dominate the decomposition. Confirm a suspected blink component with repeated time-domain morphology and its topographic map before removal.",
        "science": [("Pion-Tonachini et al. (2019) — ICLabel original paper, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6592775/"), ("Jiang et al. (2019) — EEG artifact-removal review, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6427454/")],
        "mne": [("MNE ICA", "https://mne.tools/stable/generated/mne.preprocessing.ICA.html"), ("MNE ICA artifact-correction tutorial", "https://mne.tools/stable/auto_tutorials/preprocessing/40_artifact_correction_ica.html"), ("MNE-ICALabel documentation", "https://mne.tools/mne-icalabel/stable/index.html")],
    },
    "ICA component identification aids": {
        "summary": "Blink correlation and ICLabel are screening aids for deciding what to inspect; neither is allowed to remove a component automatically.",
        "what": "The Blink corr. value is ERP Workbench's absolute Pearson correlation between an ICA source time course and available frontal EEG channels after a 1–10 Hz screening band. ICLabel is different: it is a trained automated classifier that uses component features to estimate labels such as brain, eye, muscle, heart, line noise, channel noise and other.",
        "why": "A blink component often covaries strongly with frontal EEG and has a characteristic frontal topography and repeated blink-shaped activation, so correlation can be a useful local cue. A trained classifier can add another independent cue. Neither cue proves component identity, especially when montage, preprocessing, ICA algorithm or recording population differs from the data used to develop the classifier.",
        "implementation": "ERP Workbench calculates Blink corr. without changing ICA exclusions and places it before ICLabel in the component table. ICLabel is run through MNE-ICALabel when installed. Checkboxes remain manual. The recommended decision is agreement among repeated time-domain morphology, scalp topography, blink correlation and—when credible—the classifier output.",
        "watch": "Correlation can also be high for a non-ocular component that shares frontal activity, and a blink component can have a modest score if frontal channels are noisy or absent. ICLabel probabilities are model outputs, not calibrated guarantees for every dataset.",
        "science": [("Pion-Tonachini et al. (2019) — ICLabel original paper, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6592775/")],
        "mne": [("MNE-ICALabel documentation", "https://mne.tools/mne-icalabel/stable/index.html"), ("MNE ICA artifact-correction tutorial", "https://mne.tools/stable/auto_tutorials/preprocessing/40_artifact_correction_ica.html")],
    },
    "Annotations, events and condition grouping": {
        "summary": "An event marks when something happened; an annotation also carries a text description and optionally duration. ERP conditions are created by reproducible mappings from these source markers.",
        "what": "ERP analysis requires a trustworthy relation between stimulus/response timing and continuous EEG samples. Annotation descriptions may be unique filenames even when many belong to one experimental condition, so grouping rules convert those exact source labels into condition names without changing the raw event timeline.",
        "why": "Incorrect grouping changes which trials enter each average. Ambiguous matches should therefore be visible rather than silently assigned.",
        "implementation": "ERP Workbench uses MNE events_from_annotations for annotation events (or MNE stim-channel event discovery when chosen). Literal Contains or Starts-with rules map labels to conditions. If one label matches multiple enabled conditions, epoching is blocked. Exact labels can be excluded from a broad group before preview/cutting while their original annotations remain intact.",
        "watch": "Inspect counts and the exact labels captured by every group. Practice trials or similarly named stimuli are common reasons to prefer Starts-with or exact exclusions.",
        "science": [("Kappenman et al. — ERP CORE, open resource and paper", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE events_from_annotations", "https://mne.tools/stable/generated/mne.events_from_annotations.html"), ("MNE parsing events tutorial", "https://mne.tools/stable/auto_tutorials/raw/20_event_arrays.html")],
    },
    "Epoching and baseline correction": {
        "summary": "Epoching cuts fixed time windows around selected events. Baseline correction subtracts a chosen pre/post-event interval mean from each channel in each epoch.",
        "what": "The continuous recording is converted into trials aligned to an event. A -200 to +800 ms epoch, for example, contains 200 ms before the event and 800 ms after it. Baseline correction estimates the mean voltage in a designated baseline interval and subtracts that value from every sample of the epoch channel.",
        "why": "Epoch boundaries define which temporal activity is available for rejection, averaging and measurement. Baseline correction removes constant epoch-wise offsets, but a contaminated or physiologically active baseline can propagate bias through the whole epoch.",
        "implementation": "ERP Workbench previews marker counts and boundary-incomplete trials before constructing MNE Epochs. The user can define the same window either as pre-stimulus + total duration or as explicit start/end. Baseline values are passed to MNE Epochs. Source event label/code/sample/onset metadata are retained.",
        "watch": "Choose windows and baseline before looking for effects where possible. Trials too close to recording boundaries are excluded explicitly rather than padded or shifted.",
        "science": [("Brooker et al. — Conducting ERP research, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC8136588/"), ("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE Epochs", "https://mne.tools/stable/generated/mne.Epochs.html"), ("MNE overview of EEG/MEG analysis", "https://mne.tools/stable/auto_tutorials/intro/10_overview.html")],
    },
    "Automatic epoch artifact screening": {
        "summary": "Epoch screening flags trials using explicit voltage criteria; final inclusion remains reviewable and manual decisions can override an automatic flag.",
        "what": "Absolute amplitude asks whether any sample exceeds a positive/negative magnitude. Peak-to-peak amplitude is max minus min within a channel over the epoch and therefore detects large swings even when neither extreme alone is compared to zero. A flat criterion detects implausibly little channel variation.",
        "why": "Large transients, motion and electrode problems can dominate an average. Fixed criteria make initial screening reproducible, while visual review prevents a threshold from becoming an unexplained black box.",
        "implementation": "ERP Workbench converts the selected screening channels to µV, evaluates absolute maximum, channel-wise peak-to-peak, and optional flatness independently, stores every triggered reason, then exposes automatic vs manual final decisions in Epoch Review. The scientific Epochs object still retains all channels; the screening-channel list controls only the criteria calculation.",
        "watch": "Absolute and peak-to-peak thresholds can overlap mathematically, so the same epoch may legitimately trigger both. Thresholds should be justified for the acquisition scale and population rather than copied blindly.",
        "science": [("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/"), ("Lopez-Calderon & Luck — ERPLAB, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC3995046/")],
        "mne": [("MNE Epochs", "https://mne.tools/stable/generated/mne.Epochs.html")],
    },
    "Manual epoch review": {
        "summary": "Epoch Review is the deliberate quality-control stage between automatic screening and averaging. It records explicit manual keep/reject decisions rather than silently deleting trials.",
        "what": "An automatic threshold can identify suspicious trials, but it cannot understand every physiological or technical pattern. Visual review lets the operator inspect the full multichannel epoch and decide whether an automatically flagged trial should still be kept, or whether an automatically accepted trial should be rejected.",
        "why": "Keeping the automatic flag separate from the final decision makes the workflow auditable: the software can report what the threshold said and what the reviewer ultimately decided.",
        "implementation": "ERP Workbench stores the automatic result and a manual override separately. R toggles the selected epoch's final decision; manual keep can override an automatic rejection and manual reject can reject an automatically accepted epoch. Decision logs use stable event identity (condition, event sample and source annotation) so they can be exported and replayed without guessing by row number.",
        "watch": "Use consistent review criteria and avoid changing rules after seeing condition-level ERP effects. A manual review is most reproducible when the automatic settings and the rationale for manual overrides are documented.",
        "science": [("Lopez-Calderon & Luck — ERPLAB, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC3995046/"), ("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE visualizing epoched data", "https://mne.tools/stable/auto_tutorials/epochs/20_visualize_epochs.html"), ("MNE Epochs", "https://mne.tools/stable/generated/mne.Epochs.html")],
    },
    "Subject averaging": {
        "summary": "A condition ERP is the sample-by-sample average of finally accepted epochs from one subject, yielding an MNE Evoked object.",
        "what": "Activity that is consistently time-locked and phase-aligned to an event tends to remain in the average, whereas unrelated trial-to-trial activity tends to cancel. More clean trials generally improve the stability of the estimate, although data quality and trial balance still matter.",
        "why": "The subject average is the unit subsequently measured for components and, in this workflow, the unit contributed to the grand average.",
        "implementation": "ERP Workbench combines automatic screening with explicit manual keep/reject overrides, selects finally accepted trials condition-wise, and calls MNE Epochs.average. Accepted counts, review provenance and the actual Evoked waveforms are stored in the .erpavg package.",
        "watch": "Inspect accepted trial counts and morphology per condition. Averaging does not make a systematically contaminated or incorrectly grouped dataset valid.",
        "science": [("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE Epochs.average", "https://mne.tools/stable/generated/mne.Epochs.html#mne.Epochs.average"), ("MNE Evoked", "https://mne.tools/stable/generated/mne.Evoked.html")],
    },
    "ERP component measurement": {
        "summary": "ERP Workbench separates window-constrained automatic measurements from deliberate manual picks and records which method produced every value.",
        "what": "A peak amplitude is the most positive/negative (or largest absolute) sample inside a predefined latency window and therefore has both amplitude and peak latency. Mean amplitude averages all samples in the window and has no single meaningful peak latency. A manual pick records the exact user-selected time and voltage.",
        "why": "Different scoring methods answer different questions and differ in sensitivity to noise. Predefining windows/components improves reproducibility, while manual overrides remain explicit rather than masquerading as automatic detections.",
        "implementation": "Component definitions store name, window, polarity, method and optional channels. Automatic Window peak is constrained to the window; Mean amplitude averages within it. Manual points can be placed anywhere in the displayed epoch and take precedence for the same condition/channel/component. Difference waves remain explicitly labelled as differences.",
        "watch": "A grand-average peak should not be used as a substitute for each subject's measurement in inferential statistics. Mean amplitude does not receive an invented latency value.",
        "science": [("Luck/ERP CORE open methodological resource", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/"), ("How to do Better N400 Studies, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC9381463/")],
        "mne": [("MNE Evoked", "https://mne.tools/stable/generated/mne.Evoked.html")],
    },
    "Difference waves": {
        "summary": "A difference wave subtracts one condition's ERP waveform from another at every channel and time point. It isolates condition differences but is not a new recorded condition.",
        "what": "For A−B, every sample in B is subtracted from the corresponding sample in A. Components in a difference waveform describe the difference between conditions and can have morphology unlike either parent waveform.",
        "why": "Difference waves can simplify visualization of an experimental contrast, but their interpretation depends on the underlying conditions and subtraction direction.",
        "implementation": "ERP Workbench creates differences locally from compatible Evoked objects. They are kept outside the real-condition collection, are never included as inputs to grand averaging, and are exported only when explicitly selected and clearly labelled A−B.",
        "watch": "Always retain the parent-condition waveforms and the subtraction direction. A peak in a difference wave does not by itself identify which parent condition generated the underlying physiological change.",
        "science": [("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE combine_evoked", "https://mne.tools/stable/generated/mne.combine_evoked.html")],
    },
    "Grand averaging": {
        "summary": "Grand averaging averages compatible subject-level Evoked waveforms. In ERP Workbench each subject contributes one equal-weight subject average per condition.",
        "what": "A grand average summarizes the group waveform by averaging each channel/time sample across subjects. It is primarily a descriptive group representation; subject-level values remain the basis for between-subject statistical variability.",
        "why": "Equal subject weighting prevents a participant with more accepted trials from automatically receiving more group weight merely because their subject average contains more epochs.",
        "implementation": "Before MNE grand_average is called, ERP Workbench checks protocol compatibility, conditions, sampling frequency, time axis and channel set. Subject component measurements and grand-average component measurements are exported on separate Excel sheets. Selected grand difference waves remain explicitly labelled.",
        "watch": "A smooth grand average can hide heterogeneous individual subjects. Always inspect subject-level averages and trial counts as well as the group waveform.",
        "science": [("ERP CORE, open access", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7909723/")],
        "mne": [("MNE grand_average", "https://mne.tools/stable/generated/mne.grand_average.html")],
    },
}

class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        # Keep the returned Python object available until the GUI has confirmed
        # receipt. This matters for long operations returning an MNE Raw object:
        # progress can reach 100% before a queued result callback is processed.
        self.result_value = None
        self.succeeded = False

    @Slot()
    def run(self):
        try:
            kwargs = dict(self.kwargs)
            kwargs.setdefault("progress", self.signals.progress.emit)
            result = self.fn(*self.args, **kwargs)
            self.result_value = result
            self.succeeded = True
            self.signals.result.emit(result)
        except Exception:
            self.succeeded = False
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


class StepCard(QFrame):
    """Collapsible preprocessing card with processing enable separate from visibility.

    The checkbox controls whether a preprocessing operation is active. The
    disclosure arrow only opens/closes its settings, so a user can collapse an
    active step without changing the EEG pipeline.
    """

    toggled = Signal(bool)

    def __init__(self, title: str, *, checked: bool = False, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("StepCard")
        self._expanded = bool(expanded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("StepCardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 7, 8, 7)
        header_layout.setSpacing(8)

        self.enable_check = QCheckBox(title)
        self.enable_check.setObjectName("StepCardEnable")
        self.enable_check.setChecked(bool(checked))
        self.enable_check.setSizePolicy(self.enable_check.sizePolicy().horizontalPolicy(), self.enable_check.sizePolicy().verticalPolicy())
        header_layout.addWidget(self.enable_check, 1)

        self.arrow = QToolButton()
        self.arrow.setObjectName("StepCardArrow")
        self.arrow.setAutoRaise(True)
        self.arrow.setFixedSize(28, 28)
        self.arrow.clicked.connect(lambda: self.setExpanded(not self._expanded))
        header_layout.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.body = QWidget()
        self.body.setObjectName("StepCardBody")
        self.body.setVisible(self._expanded)

        outer.addWidget(header)
        outer.addWidget(self.body)

        self.enable_check.toggled.connect(self._checked_changed)
        self._update_arrow()

    def _checked_changed(self, checked: bool):
        # Activating a step should reveal the controls the user now needs.
        # Deactivation collapses it again to keep the sidebar uncluttered.
        self.setExpanded(bool(checked))
        self.toggled.emit(bool(checked))

    def _update_arrow(self):
        self.arrow.setText("▾" if self._expanded else "▸")
        self.arrow.setToolTip("Collapse settings" if self._expanded else "Expand settings")

    def setExpanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self.body.setVisible(self._expanded)
        self._update_arrow()
        self.updateGeometry()

    def isExpanded(self) -> bool:
        return self._expanded

    def isChecked(self) -> bool:
        return self.enable_check.isChecked()

    def setChecked(self, checked: bool):
        checked = bool(checked)
        if self.signalsBlocked():
            old = self.enable_check.blockSignals(True)
            self.enable_check.setChecked(checked)
            self.enable_check.blockSignals(old)
            self.setExpanded(checked)
        else:
            self.enable_check.setChecked(checked)

    def setTitle(self, title: str):
        self.enable_check.setText(title)

    def title(self) -> str:
        return self.enable_check.text()


class BadChannelDialog(QDialog):
    def __init__(self, channels: list[str], selected: list[str], parent=None, title: str = "Select bad channels to interpolate"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(360, 520)
        self.list = QListWidget()
        selected_set = set(selected)
        for ch in channels:
            item = QListWidgetItem(ch)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ch in selected_set else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        quick = QHBoxLayout()
        all_btn = QPushButton("Select all"); none_btn = QPushButton("Clear all")
        all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        none_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        quick.addWidget(all_btn); quick.addWidget(none_btn); quick.addStretch(1)
        layout.addLayout(quick)
        layout.addWidget(buttons)

    def _set_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def selected_channels(self) -> list[str]:
        return [
            self.list.item(i).text()
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class ComponentPlanDialog(QDialog):
    """Edit ERP component definitions stored in a protocol."""

    def __init__(self, components: list[ComponentDefinition], parent=None):
        super().__init__(parent)
        self.setWindowTitle("ERP components in protocol")
        self.resize(820, 520)
        layout=QVBoxLayout(self)
        note=QLabel("These component definitions are saved with the protocol and become the default measurement plan in ERP + Measure. They remain editable during analysis.")
        note.setWordWrap(True); layout.addWidget(note)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(["Component","Start ms","End ms","Polarity","Method","Channels"])
        for c in range(5): self.table.horizontalHeader().setSectionResizeMode(c,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table,1)
        for comp in components:
            self._append(comp)
        row=QHBoxLayout(); add=QPushButton("Add component"); remove=QPushButton("Remove selected")
        add.clicked.connect(lambda: self._append(ComponentDefinition(f"Component{self.table.rowCount()+1}",300,500,"positive","peak",[])))
        remove.clicked.connect(self._remove_selected)
        row.addWidget(add); row.addWidget(remove); row.addStretch(1); layout.addLayout(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _append(self, comp: ComponentDefinition):
        r=self.table.rowCount(); self.table.insertRow(r)
        vals=[comp.name,str(comp.start_ms),str(comp.end_ms),comp.polarity,comp.method,",".join(comp.channels)]
        for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(v))

    def _remove_selected(self):
        rows=self.table.selectionModel().selectedRows()
        if rows: self.table.removeRow(rows[0].row())

    def components(self) -> list[ComponentDefinition]:
        out=[]
        for r in range(self.table.rowCount()):
            try:
                vals=[self.table.item(r,c).text().strip() for c in range(6)]
                name,start,end,polarity,method,channels=vals
                start=float(start); end=float(end); polarity=polarity.lower(); method=method.lower()
                if not name or start>=end: continue
                if polarity not in {"positive","negative","absolute"}: polarity="absolute"
                if method not in {"peak","mean","manual","area"}: method="peak"
                out.append(ComponentDefinition(name,start,end,polarity,method,[x.strip() for x in channels.split(",") if x.strip()]))
            except Exception:
                continue
        return out

    def _validate_accept(self):
        if self.table.rowCount() and not self.components():
            QMessageBox.warning(self,"Invalid component plan","Check component names and ensure each Start ms is smaller than End ms.")
            return
        self.accept()


class StageSelectionDialog(QDialog):
    def __init__(self, stages: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose processed EEG stage")
        self.setModal(True)
        layout = QVBoxLayout(self)
        note = QLabel("Choose which available continuous EEG stage to save. This does not change the active analysis pipeline.")
        note.setWordWrap(True); layout.addWidget(note)
        self.combo = QComboBox()
        for label, key in stages:
            self.combo.addItem(label, key)
        layout.addWidget(self.combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_stage(self) -> str:
        return str(self.combo.currentData() or "")



class HelpTopicDialog(QDialog):
    def __init__(self, topic: str, info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"ERP Workbench Help — {topic}")
        self.resize(760, 560)
        browser=QTextBrowser(); browser.setOpenExternalLinks(True)
        science="".join(f'<li><a href="{url}">{label}</a></li>' for label,url in info.get("science",[]))
        mne_links="".join(f'<li><a href="{url}">{label}</a></li>' for label,url in info.get("mne",[]))
        browser.setHtml(
            f"<h2>{topic}</h2><p><b>{info.get('summary','')}</b></p>"
            f"<h3>What it is</h3><p>{info.get('what','')}</p>"
            f"<h3>Why it matters</h3><p>{info.get('why','')}</p>"
            f"<h3>What ERP Workbench does</h3><p>{info.get('implementation','')}</p>"
            f"<h3>Things to watch</h3><p>{info.get('watch','')}</p>"
            f"<h3>Scientific reading</h3><ul>{science}</ul>"
            f"<h3>MNE implementation documentation</h3><ul>{mne_links}</ul>"
            "<p><i>This section documents the implemented processing path and provides methodological starting points; it does not replace paradigm-specific methodological judgement.</i></p>"
        )
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(self.reject)
        lay=QVBoxLayout(self); lay.addWidget(browser,1); lay.addWidget(close)


class ERPWorkbench(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ERP Workbench 1.0 — MNE (ICA BETA)")
        self.resize(1500, 920)

        self.app_settings = QSettings("ERP Workbench", "ERP Workbench")
        self._console_allocated_by_app = False
        self._shortcut_map = dict(DEFAULT_SHORTCUTS)
        for key in self._shortcut_map:
            saved = self.app_settings.value(f"shortcuts/{key}", self._shortcut_map[key])
            if saved is not None:
                self._shortcut_map[key] = str(saved)
        saved_color = str(self.app_settings.value("display/trace_color", "") or "").strip()
        self._trace_color = saved_color if QColor(saved_color).isValid() else ""
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self._default_protocol_library_dir = Path(documents or (Path.home() / "Documents")) / "ERP Workbench" / "Protocols"
        saved_protocol_dir = str(self.app_settings.value("paths/protocol_library", "") or "").strip()
        self._protocol_library_dir = Path(saved_protocol_dir).expanduser() if saved_protocol_dir else self._default_protocol_library_dir
        try:
            self._protocol_library_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Locked-down or unavailable user folders fall back to local app data.
            app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            self._protocol_library_dir = Path(app_data or (Path.home() / ".erp_workbench")) / "Protocols"
            self._protocol_library_dir.mkdir(parents=True, exist_ok=True)
        self._ica_task_started_at: float | None = None
        self._ica_task_estimate_sec: float | None = None
        self._ica_task_name = ""

        self.thread_pool = QThreadPool.globalInstance()
        self.original_raw = None
        self.processed_raw = None
        self.ica = None
        self.events = np.empty((0, 3), dtype=int)
        self.event_labels: dict[int, str] = {}
        self.epochs = None
        self.review = EpochReviewState()
        self.clean_epochs = None
        self.evokeds = {}
        self.measurements: list[MeasurementResult] = []
        self.preprocessing = PreprocessingSettings()
        self.protocol: ProtocolDefinition = ProtocolDefinition(name="New protocol")
        self.metadata = SessionMetadata()
        self.external_annotation_table = None
        self._native_annotations = None
        self._manual_latency_ms: float | None = None
        self._settings_dirty = False
        self._processing_order: list[str] = []
        self._pipeline_signal_guard = False
        self._event_group_table_guard = False
        self._event_table_guard = False
        self._stimulus_exclusion_guard = False
        self._ica_exclusion_table_guard = False
        self._protocol_combo_guard = False
        self._last_epoch_preflight = None
        # Manual review actions are kept separately from the current snapshot so
        # a replayable decision log can include both the final state and history.
        self._epoch_decision_history: list[dict] = []
        self._epoch_screening_channels: list[str] = []  # empty = all EEG channels
        # v0.7 ERP/averaging state. Difference waves remain intentionally
        # temporary and are never inserted into self.evokeds.
        self._erp_display_channels: list[str] = []
        # Session-wide display-channel preference.  A subset chosen in an earlier
        # waveform stage is carried forward to later ERP views when those channel
        # names exist, until the user explicitly changes the selection.
        self._preferred_display_channels: list[str] = []
        self._difference_evoked = None
        self._difference_label = ""
        self._difference_active = False
        self._loaded_average_counts: dict[str, int] = {}
        # One shared, last-used ERP display configuration. The currently visible
        # time span/pan, sensitivity and polarity are applied to every averaged
        # condition until the user changes them again. This deliberately avoids
        # per-condition display memories.
        self._erp_display_state: dict = {}
        # v0.8 grand-average workspace. These are independent of the currently
        # opened single-subject averages and never overwrite self.evokeds.
        self._grand_average_paths: list[Path] = []
        self.grand_evokeds: dict[str, mne.Evoked] = {}
        self._grand_protocol: dict = {}
        self._grand_subject_count: int = 0
        self._grand_display_channels: list[str] = []
        self._grand_display_state: dict = {}
        self.grand_measurements: list[MeasurementResult] = []
        self._grand_manual_latency_ms: float | None = None
        self._grand_difference_evoked = None
        self._grand_difference_label = ""
        self._grand_difference_active = False
        self._grand_difference_exports: set[tuple[str, str]] = set()
        # ICA BETA keeps the pre-ICA processed recording and the cleaned
        # reconstruction as separate, reversible signal versions. Checking a
        # component never performs an expensive reconstruction; the user starts
        # that operation explicitly with Remove selected components.
        self._ica_applied = False  # cleaned reconstruction has been created
        self._ica_fit_exclusions: list[dict] = []
        self._pre_ica_raw = None
        self._ica_cleaned_raw = None
        self._ica_cleaned_excluded: list[int] = []
        self._ica_reconstruction_worker = None
        self._ica_reconstruction_pending_excluded: list[int] = []
        self._ica_reconstruction_result_received = False
        self._ica_reconstruction_failed = False
        self._epoch_input_mode = "pre_ica"
        self._preproc_commit_inflight = False
        self._preproc_commit_pending = False
        # Appearance follows Windows by default, but can be overridden from View > Appearance.
        self._appearance_mode = "system"
        # OpenGL is enabled by default for waveform rendering on modern PCs.
        # It can be disabled instantly from View > Rendering if a graphics
        # driver behaves poorly. This affects drawing only, never MNE results.
        self._opengl_enabled = True

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.setCentralWidget(self.tabs)

        self._build_continuous_tab()
        self._build_ica_tab()
        self._build_epoch_tab()
        self._build_review_tab()
        self._build_erp_tab()
        self._build_grand_average_tab()
        self._build_settings_tab()
        self._build_help_tab()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._build_menu()
        self._build_statusbar()
        self._apply_style()
        self._apply_rendering_mode(show_message=False)
        self._apply_user_display_settings()

        # Reflow control panels after window resizes instead of letting fixed
        # sidebar widths clip controls on smaller laptop displays.
        self._responsive_timer = QTimer(self)
        self._responsive_timer.setSingleShot(True)
        self._responsive_timer.setInterval(70)
        self._responsive_timer.timeout.connect(self._apply_responsive_layout)
        QTimer.singleShot(0, self._apply_responsive_layout)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._update_processing_preview)

        self.preproc_commit_timer = QTimer(self)
        self.preproc_commit_timer.setSingleShot(True)
        self.preproc_commit_timer.timeout.connect(self._commit_live_preprocessing)

        # Re-apply the theme if Windows changes between light/dark while the app is open.
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        except Exception:
            pass


    # ---------- shell ----------
    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("Open EDF/FIF…", self); open_action.setShortcut("Ctrl+O"); open_action.triggered.connect(self.open_file); file_menu.addAction(open_action)
        new_window_action = QAction("Open new window…", self); new_window_action.setShortcut("Ctrl+Shift+N"); new_window_action.triggered.connect(self.open_new_window); file_menu.addAction(new_window_action)
        attach_annotations = QAction("Attach Annotation.txt…", self); attach_annotations.triggered.connect(self.attach_annotation_file); file_menu.addAction(attach_annotations)
        save_fif = QAction("Save processed EEG…", self); save_fif.setShortcut("Ctrl+Shift+S"); save_fif.triggered.connect(self.save_processed_fif); file_menu.addAction(save_fif)
        export_subject = QAction("Export subject ERP Excel…", self); export_subject.triggered.connect(self.export_excel); file_menu.addAction(export_subject)
        file_menu.addSeparator(); exit_action = QAction("Exit", self); exit_action.triggered.connect(self.close); file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu("&View")
        workflow_action = QAction("ERP workflow", self); workflow_action.setCheckable(True); workflow_action.setChecked(True)
        workflow_action.setToolTip("Show the ERP analysis workflow.")
        workflow_action.triggered.connect(lambda _checked=False: self.tabs.setCurrentIndex(0))
        view_menu.addAction(workflow_action); view_menu.addSeparator()
        appearance_menu = view_menu.addMenu("Appearance")
        self._appearance_actions = QActionGroup(self); self._appearance_actions.setExclusive(True)
        for label, mode in [("Follow system", "system"), ("Light", "light"), ("Dark", "dark")]:
            action = QAction(label, self); action.setCheckable(True); action.setData(mode); action.setChecked(mode == self._appearance_mode)
            action.triggered.connect(lambda checked=False, m=mode: self._set_appearance_mode(m)); self._appearance_actions.addAction(action); appearance_menu.addAction(action)
        rendering_menu = view_menu.addMenu("Rendering")
        self.opengl_action = QAction("OpenGL accelerated waveform rendering", self); self.opengl_action.setCheckable(True); self.opengl_action.setChecked(self._opengl_enabled)
        self.opengl_action.setToolTip("Use Qt/OpenGL for waveform drawing. This does not change MNE processing."); self.opengl_action.toggled.connect(self._set_opengl_enabled); rendering_menu.addAction(self.opengl_action)
        # The public/release UI never exposes a terminal.  For development, set
        # ERP_WORKBENCH_DEV=1 before launching to reveal the diagnostic action.
        # PyInstaller also builds with console=False, so installed users see no
        # command window at startup.
        self.terminal_action = None
        if os.environ.get("ERP_WORKBENCH_DEV", "").strip() == "1":
            view_menu.addSeparator()
            self.terminal_action = QAction("Show diagnostic terminal", self)
            self.terminal_action.setCheckable(True); self.terminal_action.setChecked(False)
            self.terminal_action.setToolTip("Developer diagnostics only.")
            self.terminal_action.toggled.connect(self._toggle_command_prompt)
            if sys.platform != "win32":
                self.terminal_action.setEnabled(False)
                self.terminal_action.setToolTip("Developer diagnostics are available in the Windows build.")
            view_menu.addAction(self.terminal_action)

        tools_menu = self.menuBar().addMenu("ERP &Tools")
        interpolate_action = QAction("Bad-channel interpolation…", self)
        interpolate_action.triggered.connect(lambda _checked=False: self._open_erp_preprocessing_tool("interpolation"))
        tools_menu.addAction(interpolate_action)
        reference_action = QAction("EEG re-reference…", self)
        reference_action.triggered.connect(lambda _checked=False: self._open_erp_preprocessing_tool("reference"))
        tools_menu.addAction(reference_action)
        tools_menu.addSeparator()
        for label, index in [("ICA (BETA)",1),("Epoching",2),("Epoch Review",3),("ERP + Measure",4),("Grand Average",5)]:
            action=QAction(label,self); action.triggered.connect(lambda checked=False, i=index: self.tabs.setCurrentIndex(i)); tools_menu.addAction(action)

        settings_menu = self.menuBar().addMenu("&Settings")
        preferences_action = QAction("Preferences…", self); preferences_action.triggered.connect(self._show_settings_dialog); settings_menu.addAction(preferences_action)

        help_menu = self.menuBar().addMenu("&Help")
        methodology_action = QAction("Methodology & readings…", self); methodology_action.triggered.connect(self._show_help_dialog); help_menu.addAction(methodology_action)

    def _open_erp_preprocessing_tool(self, tool: str):
        """Open the ERP workflow and expose an EEG-specific preprocessing card.

        Filtering intentionally remains in the general continuous-EEG sidebar;
        the ERP Tools menu provides direct access to EEG-specific structural
        operations that are likely to remain ERP/EEG workflow tools as the app
        grows additional analysis modes.
        """
        self.tabs.setCurrentIndex(0)
        card = {
            "interpolation": getattr(self, "interp_group", None),
            "reference": getattr(self, "ref_group", None),
        }.get(str(tool))
        if card is not None and hasattr(card, "setExpanded"):
            card.setExpanded(True)

    def _build_statusbar(self):
        self.status_label = QLabel("Open an EDF or FIF file to begin.")
        self.statusBar().addWidget(self.status_label, 1)
        self.global_task_progress = QProgressBar()
        self.global_task_progress.setRange(0, 0)
        self.global_task_progress.setTextVisible(False)
        self.global_task_progress.setFixedWidth(150)
        self.global_task_progress.setVisible(False)
        self.global_task_eta = QLabel("")
        self.global_task_eta.setVisible(False)
        self.statusBar().addPermanentWidget(self.global_task_progress)
        self.statusBar().addPermanentWidget(self.global_task_eta)
        self._ica_eta_timer = QTimer(self)
        self._ica_eta_timer.setInterval(1000)
        self._ica_eta_timer.timeout.connect(self._update_estimated_time_label)

    @staticmethod
    def _format_eta_seconds(seconds: float) -> str:
        seconds = max(0, int(round(float(seconds))))
        if seconds < 60:
            return f"{seconds}s"
        return f"{seconds // 60}m {seconds % 60:02d}s"

    def _start_estimated_task(self, name: str, estimate_sec: float):
        self._ica_task_name = str(name)
        self._ica_task_started_at = time.monotonic()
        self._ica_task_estimate_sec = max(1.0, float(estimate_sec))
        self.global_task_progress.setVisible(True)
        self.global_task_eta.setVisible(True)
        self._update_estimated_time_label()
        self._ica_eta_timer.start()

    def _update_estimated_time_label(self):
        if self._ica_task_started_at is None or self._ica_task_estimate_sec is None:
            self.global_task_eta.setVisible(False)
            return
        elapsed = max(0.0, time.monotonic() - self._ica_task_started_at)
        remaining = self._ica_task_estimate_sec - elapsed
        if remaining > 0:
            text = f"Estimated time ({self._ica_task_name}): ~{self._format_eta_seconds(remaining)} remaining"
        else:
            text = f"Estimated time ({self._ica_task_name}): initial estimate exceeded · elapsed {self._format_eta_seconds(elapsed)}"
        self.global_task_eta.setText(text)
        local_text = text.replace(f" ({self._ica_task_name})", "")
        if self._ica_task_name == "ICA fit" and hasattr(self, "ica_fit_eta"):
            self.ica_fit_eta.setText(local_text)
        if self._ica_task_name == "ICA reconstruction" and hasattr(self, "ica_remove_eta"):
            self.ica_remove_eta.setText(local_text)

    def _finish_estimated_task(self):
        self._ica_eta_timer.stop()
        self._ica_task_started_at = None
        self._ica_task_estimate_sec = None
        self._ica_task_name = ""
        self.global_task_progress.setVisible(False)
        self.global_task_eta.setVisible(False)

    def _system_prefers_dark(self) -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        try:
            return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            # Fallback for older Qt/PySide6 builds.
            return app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _set_appearance_mode(self, mode: str):
        if mode not in {"system", "light", "dark"}:
            return
        self._appearance_mode = mode
        if hasattr(self, "_appearance_actions"):
            for action in self._appearance_actions.actions():
                action.setChecked(action.data() == mode)
        self._apply_style()

    def _system_theme_changed(self, *_):
        if self._appearance_mode == "system":
            self._apply_style()

    def _apply_style(self):
        """Apply an explicit, high-contrast application palette.

        v0.4 originally forced white panels while Windows dark mode supplied
        white widget text. That produced white-on-white controls. The theme is
        now explicit end-to-end (Qt widgets + PyQtGraph viewers), so Windows
        light and dark modes are both readable.
        """
        dark = self._system_prefers_dark() if self._appearance_mode == "system" else self._appearance_mode == "dark"
        self._dark_mode = dark

        if dark:
            c = {
                "window": "#1d1f23", "panel": "#25282d", "input": "#202328",
                "raised": "#30343a", "hover": "#3a3f46", "border": "#474c55",
                "text": "#eef0f3", "muted": "#aeb4be", "disabled": "#747b86",
                "highlight": "#3b78c8", "highlight_text": "#ffffff",
            }
        else:
            c = {
                "window": "#f5f6f8", "panel": "#ffffff", "input": "#ffffff",
                "raised": "#f0f2f5", "hover": "#e6e9ee", "border": "#d7dbe2",
                "text": "#20242a", "muted": "#626873", "disabled": "#9aa0aa",
                "highlight": "#2f6fb3", "highlight_text": "#ffffff",
            }

        app = QApplication.instance()
        if app is not None:
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
            palette.setColor(QPalette.ColorRole.Base, QColor(c["input"]))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["raised"]))
            palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
            palette.setColor(QPalette.ColorRole.Button, QColor(c["raised"]))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["panel"]))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(c["highlight"]))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["highlight_text"]))
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["disabled"]))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["disabled"]))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["disabled"]))
            app.setPalette(palette)

            app.setStyleSheet(f"""
                QWidget {{ color: {c['text']}; background-color: {c['window']}; }}
                QMainWindow, QDialog {{ background-color: {c['window']}; }}
                QLabel, QCheckBox, QRadioButton {{ background: transparent; color: {c['text']}; }}
                QLabel[muted="true"] {{ color: {c['muted']}; }}
                QGroupBox {{
                    font-weight: 600; border: 1px solid {c['border']}; border-radius: 6px;
                    margin-top: 10px; padding-top: 8px; background: {c['panel']}; color: {c['text']};
                }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; background: transparent; }}
                QFrame#StepCard {{
                    border: 1px solid {c['border']}; border-radius: 7px;
                    background: {c['panel']};
                }}
                QWidget#StepCardHeader {{
                    background: {c['panel']}; border-radius: 7px;
                }}
                QWidget#StepCardBody {{
                    background: {c['panel']}; border-top: 1px solid {c['border']};
                }}
                QCheckBox#StepCardEnable {{
                    background: transparent; font-weight: 600; padding: 2px 0px;
                }}
                QToolButton#StepCardArrow {{
                    border: none; background: transparent; color: {c['muted']};
                    font-size: 16px; font-weight: 700;
                }}
                QToolButton#StepCardArrow:hover {{
                    background: {c['hover']}; color: {c['text']}; border-radius: 4px;
                }}
                QPushButton {{
                    min-height: 28px; padding: 3px 10px; border: 1px solid {c['border']};
                    border-radius: 4px; background: {c['raised']}; color: {c['text']};
                }}
                QPushButton:hover {{ background: {c['hover']}; }}
                QPushButton:pressed {{ background: {c['highlight']}; color: {c['highlight_text']}; }}
                QPushButton:disabled {{ color: {c['disabled']}; background: {c['panel']}; }}
                QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
                QListWidget, QTableWidget {{
                    background: {c['input']}; color: {c['text']}; border: 1px solid {c['border']};
                    border-radius: 3px; selection-background-color: {c['highlight']};
                    selection-color: {c['highlight_text']};
                }}
                QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled,
                QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['disabled']}; background: {c['panel']}; }}
                QComboBox QAbstractItemView {{
                    background: {c['input']}; color: {c['text']};
                    selection-background-color: {c['highlight']}; selection-color: {c['highlight_text']};
                }}
                QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; }}
                QTabBar::tab {{
                    background: {c['raised']}; color: {c['muted']}; border: 1px solid {c['border']};
                    padding: 7px 12px; margin-right: 1px;
                }}
                QTabBar::tab:selected {{ background: {c['panel']}; color: {c['text']}; }}
                QTabBar::tab:disabled {{ color: {c['disabled']}; }}
                QTableWidget {{ gridline-color: {c['border']}; }}
                QHeaderView::section {{
                    background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px;
                }}
                QMenuBar {{ background: {c['window']}; color: {c['text']}; }}
                QMenuBar::item:selected {{ background: {c['hover']}; }}
                QMenu {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; }}
                QMenu::item:selected {{ background: {c['highlight']}; color: {c['highlight_text']}; }}
                QStatusBar {{ background: {c['window']}; color: {c['muted']}; }}
                QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; }}
                QSplitter::handle {{ background: {c['border']}; }}
                QScrollArea {{ background: {c['window']}; border: none; }}
                QScrollArea > QWidget > QWidget {{ background: {c['window']}; }}
                QScrollBar:vertical {{
                    background: {c['window']}; width: 12px; margin: 0px;
                }}
                QScrollBar::handle:vertical {{
                    background: {c['border']}; min-height: 28px; border-radius: 5px;
                }}
                QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
                QScrollBar:horizontal {{ background: {c['window']}; height: 10px; }}
                QScrollBar::handle:horizontal {{ background: {c['border']}; min-width: 28px; border-radius: 4px; }}
            """)

        for viewer_name in ("raw_viewer", "epoch_viewer", "erp_viewer", "grand_viewer", "ica_view", "ica_fit_view", "ica_post_view"):
            viewer = getattr(self, viewer_name, None)
            if viewer is not None and hasattr(viewer, "set_dark_mode"):
                viewer.set_dark_mode(dark)

    def _set_opengl_enabled(self, enabled: bool):
        self._opengl_enabled = bool(enabled)
        self._apply_rendering_mode(show_message=True)

    def _apply_rendering_mode(self, show_message: bool = False):
        """Apply OpenGL/software rendering to all interactive waveform plots.

        PyQtGraph/Qt allows switching the QGraphicsView viewport at runtime.
        Because graphics-driver behaviour varies, failure automatically falls
        back to the normal QWidget renderer rather than preventing EEG review.
        """
        requested = bool(self._opengl_enabled)
        failures = []
        viewers = []
        for viewer_name in ("raw_viewer", "epoch_viewer", "erp_viewer", "grand_viewer", "ica_view", "ica_fit_view", "ica_post_view"):
            viewer = getattr(self, viewer_name, None)
            if viewer is not None and hasattr(viewer, "set_opengl_enabled"):
                viewers.append(viewer)
                try:
                    viewer.set_opengl_enabled(requested)
                except Exception as exc:
                    failures.append(f"{viewer_name}: {exc}")

        if requested and failures:
            self._opengl_enabled = False
            for viewer in viewers:
                try:
                    viewer.set_opengl_enabled(False)
                except Exception:
                    pass
            action = getattr(self, "opengl_action", None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)
            message = "OpenGL could not be enabled on this graphics driver. ERP Workbench reverted to software rendering."
            if hasattr(self, "status_label"):
                self.status_label.setText(message)
            if show_message:
                QMessageBox.warning(self, "OpenGL unavailable", message + "\n\n" + "\n".join(failures[:3]))
            return

        action = getattr(self, "opengl_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(self._opengl_enabled)
            action.blockSignals(False)
        if show_message and hasattr(self, "status_label"):
            mode = "OpenGL accelerated" if self._opengl_enabled else "software"
            self.status_label.setText(f"Waveform rendering switched to {mode} mode.")

    def _set_busy(self, busy: bool, text: str = ""):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor) if busy else QApplication.restoreOverrideCursor()
        if text:
            self.status_label.setText(text)

    def _run_worker(self, fn: Callable, args: tuple, on_result: Callable[[Any], None], label: str):
        self._set_busy(True, label)
        worker = FunctionWorker(fn, *args)
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.error.connect(self._worker_error)
        worker.signals.result.connect(on_result)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self.thread_pool.start(worker)

    def _worker_error(self, details: str):
        self.status_label.setText("Operation failed.")
        QMessageBox.critical(self, "Processing error", details[-5000:])

    # ---------- continuous / preprocessing ----------
    def _build_continuous_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        splitter = QSplitter()
        self.continuous_splitter = splitter

        # Keep preprocessing controls readable on small/short displays.
        # The old layout squeezed every control into the available tab height;
        # this panel now scrolls vertically instead, while the EEG viewer keeps
        # the rest of the window.
        controls_widget = QWidget()
        # The sidebar content is allowed to shrink; QFormLayout rows reflow
        # instead of keeping a hidden fixed-width child behind the scroll area.
        controls_widget.setMinimumWidth(0)
        controls_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.preproc_controls_widget = controls_widget
        self._preproc_responsive_forms = []
        controls = QVBoxLayout(controls_widget)
        controls.setContentsMargins(10, 8, 10, 14)
        controls.setSpacing(14)

        self.preproc_scroll = QScrollArea()
        self.preproc_scroll.setWidgetResizable(True)
        self.preproc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preproc_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preproc_scroll.setMinimumWidth(320)
        self.preproc_scroll.setMaximumWidth(520)
        self.preproc_scroll.setWidget(controls_widget)
        self.preproc_scroll.setToolTip(
            "Preprocessing controls. Use each card's arrow to expand/collapse settings; scroll vertically when needed."
        )

        file_group = QGroupBox("Recording")
        file_form = QFormLayout(file_group)
        file_form.setContentsMargins(12, 14, 12, 12)
        file_form.setHorizontalSpacing(12)
        file_form.setVerticalSpacing(10)
        file_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._preproc_responsive_forms.append(file_form)
        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Optional subject ID")
        self.subject_edit.textChanged.connect(lambda x: setattr(self.metadata, "subject_id", x))
        open_btn = QPushButton("Open EDF / FIF")
        open_btn.clicked.connect(self.open_file)
        self.annotation_label = QLabel("None attached")
        self.annotation_label.setWordWrap(True)
        annotation_btn = QPushButton("Attach event Annotation.txt…")
        annotation_btn.setToolTip(
            "Attach the recorder's tab-separated annotation/event-marker export. "
            "Markers are drawn on continuous EEG and become the annotation event source for epoching."
        )
        annotation_btn.clicked.connect(self.attach_annotation_file)
        file_form.addRow(open_btn)
        file_form.addRow("File", self.file_label)
        file_form.addRow(annotation_btn)
        file_form.addRow("Event annotations", self.annotation_label)
        file_form.addRow("Subject", self.subject_edit)
        controls.addWidget(file_group)

        self.filter_group = StepCard("Filter — order-independent", checked=False, expanded=False)
        fg = QFormLayout(self.filter_group.body)
        fg.setContentsMargins(14, 12, 14, 14)
        fg.setHorizontalSpacing(12)
        fg.setVerticalSpacing(12)
        fg.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._preproc_responsive_forms.append(fg)
        self.hp = ReliableDoubleSpinBox(); self.hp.setRange(0, 200); self.hp.setDecimals(2); self.hp.setValue(0.5); self.hp.setSuffix(" Hz")
        self.lp = ReliableDoubleSpinBox(); self.lp.setRange(0, 1000); self.lp.setDecimals(1); self.lp.setValue(35); self.lp.setSuffix(" Hz")
        self.notch_check = QCheckBox("Enable notch")
        self.notch = ReliableDoubleSpinBox(); self.notch.setRange(1, 500); self.notch.setValue(50); self.notch.setSuffix(" Hz")
        self.live_preview = QCheckBox("Live preview active preprocessing")
        self.live_preview.setChecked(True)
        fg.addRow("High-pass (0=None)", self.hp)
        fg.addRow("Low-pass (0=None)", self.lp)
        fg.addRow(self.notch_check, self.notch)
        fg.addRow(self.live_preview)
        controls.addWidget(self.filter_group)

        self.interp_group = StepCard("Interpolate bad channels", checked=False, expanded=False)
        ig = QFormLayout(self.interp_group.body)
        ig.setContentsMargins(14, 12, 14, 14)
        ig.setHorizontalSpacing(12)
        ig.setVerticalSpacing(12)
        ig.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._preproc_responsive_forms.append(ig)
        self.bad_channels_label = QLabel("None")
        self.bad_channels_label.setWordWrap(True)
        bad_btn = QPushButton("Select channels…")
        bad_btn.clicked.connect(self.select_bad_channels)
        self.montage_combo = QComboBox()
        self.montage_combo.addItems(["standard_1020", "standard_1005", "biosemi32", "easycap-M1"])
        ig.addRow(bad_btn)
        ig.addRow("Selected", self.bad_channels_label)
        ig.addRow("Montage", self.montage_combo)
        controls.addWidget(self.interp_group)

        self.ref_group = StepCard("Re-reference", checked=False, expanded=False)
        rg = QFormLayout(self.ref_group.body)
        rg.setContentsMargins(14, 12, 14, 14)
        rg.setHorizontalSpacing(12)
        rg.setVerticalSpacing(12)
        rg.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._preproc_responsive_forms.append(rg)
        self.ref_mode = QComboBox(); self.ref_mode.addItems(["average", "custom"])
        self.ref_custom = QLineEdit(); self.ref_custom.setPlaceholderText("e.g. M1,M2")
        rg.addRow("Mode", self.ref_mode)
        rg.addRow("Custom channels", self.ref_custom)
        controls.addWidget(self.ref_group)

        self.ica_card = StepCard("ICA (BETA — fit/remove in tab 2)", checked=False, expanded=False)
        self.ica_enabled = self.ica_card.enable_check
        self.ica_enabled.setToolTip(
            "ICA belongs to the ordered preprocessing stack. Upstream preprocessing changes invalidate an ICA fit."
        )
        ica_layout = QVBoxLayout(self.ica_card.body)
        ica_layout.setContentsMargins(14, 12, 14, 14)
        ica_layout.setSpacing(10)
        ica_help = QLabel("Fit, inspect topographies/source time series, and select components in tab 2.")
        ica_help.setWordWrap(True)
        ica_help.setProperty("muted", True)
        go_ica_btn = QPushButton("Go to ICA tab →")
        go_ica_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.go_ica_btn = go_ica_btn
        ica_layout.addWidget(ica_help)
        ica_layout.addWidget(go_ica_btn)
        controls.addWidget(self.ica_card)

        stack_group = QGroupBox("Processing summary")
        stack_layout = QVBoxLayout(stack_group)
        stack_layout.setContentsMargins(12, 14, 12, 12)
        stack_layout.setSpacing(7)
        self.pipeline_label = QLabel("No structural steps active. Filtering is independent of step numbering.")
        self.pipeline_label.setWordWrap(True)
        stack_rule = QLabel("Structural rule: Interpolate → Re-reference → ICA (BETA)")
        stack_rule.setProperty("muted", True)
        stack_rule.setWordWrap(True)
        stack_rule.setToolTip(
            "Unchecking an earlier structural step also undoes later steps in reverse order. "
            "Filtering remains order-independent."
        )
        stack_layout.addWidget(self.pipeline_label)
        stack_layout.addWidget(stack_rule)
        controls.addWidget(stack_group)

        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout(display_group)
        display_layout.setContentsMargins(12, 14, 12, 12)
        display_layout.setSpacing(8)
        self.display_combo = QComboBox()
        self.display_combo.addItems(["Current processed", "Original imported"])
        self.display_combo.currentIndexChanged.connect(self._display_choice_changed)
        display_layout.addWidget(self.display_combo)
        self.preview_state_label = QLabel("")
        self.preview_state_label.setWordWrap(True)
        self.preview_state_label.setProperty("muted", True)
        display_layout.addWidget(self.preview_state_label)
        controls.addWidget(display_group)
        controls.addStretch(1)

        self.raw_viewer = StackedEEGViewer()
        self.raw_viewer.channelSelectionChanged.connect(self._remember_display_channels)
        splitter.addWidget(self.preproc_scroll)
        splitter.addWidget(self.raw_viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setSizes([380, 1120])
        splitter.splitterMoved.connect(lambda *_: self._apply_sidebar_reflow())
        layout.addWidget(splitter)
        self.tabs.addTab(page, "1  Continuous EEG")

        # Toggling a preprocessing step now applies/undoes it automatically.
        self.filter_group.toggled.connect(self._filter_toggled)
        self.interp_group.toggled.connect(lambda checked: self._structural_step_toggled("interpolation", checked))
        self.ref_group.toggled.connect(lambda checked: self._structural_step_toggled("reference", checked))
        self.ica_card.toggled.connect(lambda checked: self._structural_step_toggled("ica", checked))

        for widget in [self.hp, self.lp, self.notch, self.notch_check]:
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(lambda *_: self._active_step_setting_changed("filter"))
            if hasattr(widget, "toggled"):
                widget.toggled.connect(lambda *_: self._active_step_setting_changed("filter"))
        self.montage_combo.currentTextChanged.connect(lambda *_: self._active_step_setting_changed("interpolation"))
        self.ref_mode.currentTextChanged.connect(lambda *_: self._active_step_setting_changed("reference"))
        self.ref_custom.textChanged.connect(lambda *_: self._active_step_setting_changed("reference"))
        self.live_preview.toggled.connect(lambda _: self._schedule_preview())

        self.raw_viewer.position.valueChanged.connect(lambda _: self._schedule_preview())
        self.raw_viewer.duration.valueChanged.connect(lambda _: self._schedule_preview())
        self._update_order_labels()

    def _sync_preprocessing_from_ui(self):
        self.preprocessing.filter.enabled = self.filter_group.isChecked()
        self.preprocessing.filter.l_freq = self.hp.value() if self.hp.value() > 0 else None
        self.preprocessing.filter.h_freq = self.lp.value() if self.lp.value() > 0 else None
        self.preprocessing.filter.notch_enabled = self.notch_check.isChecked()
        self.preprocessing.filter.notch_freq = self.notch.value()
        self.preprocessing.interpolation.enabled = self.interp_group.isChecked()
        self.preprocessing.interpolation.montage = self.montage_combo.currentText()
        self.preprocessing.reference.enabled = self.ref_group.isChecked()
        self.preprocessing.reference.mode = self.ref_mode.currentText()
        self.preprocessing.reference.custom_channels = [x.strip() for x in self.ref_custom.text().split(",") if x.strip()]
        self.preprocessing.ica.enabled = self.ica_enabled.isChecked()
        self.preprocessing.ica.fit_exclude_spans = copy.deepcopy(self._ica_fit_exclusions)
        self.preprocessing.step_order = list(self._processing_order)

    def _set_step_checked_silently(self, step: str, checked: bool):
        widget = {
            "interpolation": self.interp_group,
            "reference": self.ref_group,
            "ica": self.ica_card,
        }[step]
        old = widget.blockSignals(True)
        widget.setChecked(bool(checked))
        widget.blockSignals(old)

    def _clear_ica_state(self):
        self.ica = None
        self._ica_applied = False
        self.preprocessing.ica.excluded_components = []
        self.preprocessing.ica.epoch_input = "pre_ica"
        self._pre_ica_raw = None
        self._ica_cleaned_raw = None
        self._ica_cleaned_excluded = []
        self._epoch_input_mode = "pre_ica"
        if hasattr(self, "ica_view"):
            try:
                self.ica_view.clear_ica()
            except Exception:
                pass
        if hasattr(self, "ica_post_view"):
            try:
                self.ica_post_view.set_raw(None)
            except Exception:
                pass
        if getattr(self, "run_iclabel_btn", None) is not None:
            self.run_iclabel_btn.setEnabled(False)
        if hasattr(self, "remove_ica_components_btn"):
            self.remove_ica_components_btn.setEnabled(False)
        if hasattr(self, "ica_remove_progress"):
            self.ica_remove_progress.setVisible(False)
        if hasattr(self, "iclabel_status"):
            self.iclabel_status.setText("ICLabel: not run.")
        if hasattr(self, "blink_aid_status"):
            self.blink_aid_status.setText("Blink correlation aid: not run.")
        if hasattr(self, "ica_post_status"):
            self.ica_post_status.setText("Fit ICA, inspect the components, select any components to remove, then press Remove selected components.")
        self._refresh_ica_version_controls()

    def _drop_ica_for_upstream_change(self, reason: str):
        if "ica" not in self._processing_order and self.ica is None and not self._ica_applied:
            return
        self._set_step_checked_silently("ica", False)
        self._processing_order = [x for x in self._processing_order if x != "ica"]
        self._clear_ica_state()
        self.preview_state_label.setText(f"{reason} ICA was undone/invalidated and must be fitted again after upstream preprocessing is final.")
        self._sync_preprocessing_from_ui()
        self._update_order_labels()

    def _confirm_later_steps_undo(self, step: str, later: list[str]) -> bool:
        if not later:
            return True
        names = {"interpolation": "Interpolation", "reference": "Re-reference", "ica": "ICA"}
        later_text = " → ".join(names[x] for x in reversed(later))
        answer = QMessageBox.question(
            self,
            "Undo later preprocessing steps?",
            f"{names[step]} is earlier in the preprocessing stack. To undo/change it safely, later steps must also be undone in reverse order:\n\n{later_text}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _structural_step_toggled(self, step: str, checked: bool):
        if self._pipeline_signal_guard:
            return
        rank = {"interpolation": 0, "reference": 1, "ica": 2}

        if checked:
            # Enforce interpolation -> reference -> ICA. If an earlier step is
            # inserted after later steps, undo the later steps first rather than
            # silently creating a physiologically questionable order.
            later = [x for x in self._processing_order if rank[x] > rank[step]]
            if later:
                if not self._confirm_later_steps_undo(step, later):
                    self._set_step_checked_silently(step, False)
                    return
                for name in reversed(later):
                    self._set_step_checked_silently(name, False)
                self._processing_order = [x for x in self._processing_order if x not in later]
                if "ica" in later:
                    self._clear_ica_state()
            if step not in self._processing_order:
                self._processing_order.append(step)
        else:
            if step in self._processing_order:
                idx = self._processing_order.index(step)
                later = self._processing_order[idx + 1:]
                if later and not self._confirm_later_steps_undo(step, later):
                    self._set_step_checked_silently(step, True)
                    return
                removed = self._processing_order[idx:]
                for name in reversed(later):
                    self._set_step_checked_silently(name, False)
                self._processing_order = self._processing_order[:idx]
                if "ica" in removed:
                    self._clear_ica_state()

        self._sync_preprocessing_from_ui()
        self._update_order_labels()

        if step == "ica" and checked:
            self.preview_state_label.setText("ICA added as the final structural step. Fit/review components in the ICA tab.")
            self.tabs.setCurrentIndex(1)
            return

        # Interpolation/reference toggles, and undoing an applied ICA, rebuild
        # directly from the original recording. No Apply button is required.
        self._settings_dirty = True
        self._schedule_preview()
        self._schedule_preproc_commit()

    def _filter_toggled(self, _checked: bool):
        if self._pipeline_signal_guard:
            return
        self._sync_preprocessing_from_ui()
        self._drop_ica_for_upstream_change("Filtering changed.")
        self._settings_dirty = True
        self._update_order_labels()
        self._schedule_preview()
        self._schedule_preproc_commit()

    def _active_step_setting_changed(self, step: str):
        if self._pipeline_signal_guard:
            return
        self._sync_preprocessing_from_ui()
        active = {
            "filter": self.filter_group.isChecked(),
            "interpolation": self.interp_group.isChecked(),
            "reference": self.ref_group.isChecked(),
        }.get(step, False)
        if not active:
            return
        self._drop_ica_for_upstream_change(f"{step.capitalize()} settings changed.")
        self._settings_dirty = True
        self._schedule_preview()
        self._schedule_preproc_commit()

    def _update_order_labels(self):
        numbers = {step: i + 1 for i, step in enumerate(self._processing_order)}
        self.interp_group.setTitle(
            f"{numbers['interpolation']}. Interpolate bad channels" if "interpolation" in numbers else "Interpolate bad channels"
        )
        self.ref_group.setTitle(
            f"{numbers['reference']}. Re-reference" if "reference" in numbers else "Re-reference"
        )
        self.ica_card.setTitle(
            f"{numbers['ica']}. ICA (BETA — fit/remove in tab 2)" if "ica" in numbers else "ICA (BETA — fit/remove in tab 2)"
        )
        names = {"interpolation": "Interpolate", "reference": "Re-reference", "ica": "ICA"}
        if self._processing_order:
            stack = " → ".join(f"{i + 1}. {names[x]}" for i, x in enumerate(self._processing_order))
        else:
            stack = "No structural steps active"
        filter_text = "ON" if self.filter_group.isChecked() else "OFF"
        self.pipeline_label.setText(f"Structural order: {stack}\nFilter: {filter_text} (order-independent)")

    def _pipeline_preview_active(self) -> bool:
        return bool(
            self.filter_group.isChecked()
            or self.interp_group.isChecked()
            or self.ref_group.isChecked()
        )

    def _schedule_preview(self):
        if self.original_raw is None or self.display_combo.currentIndex() != 0:
            return
        if self.live_preview.isChecked() and self._pipeline_preview_active():
            self.preview_timer.start(250)
        elif self.processed_raw is not None:
            self.raw_viewer.clear_manual_segment()
            self.raw_viewer.set_raw(self.processed_raw)

    def _update_processing_preview(self):
        if (
            self.original_raw is None
            or not self.live_preview.isChecked()
            or not self._pipeline_preview_active()
            or self.display_combo.currentIndex() != 0
        ):
            return
        self._sync_preprocessing_from_ui()
        lf, hf = self.preprocessing.filter.l_freq, self.preprocessing.filter.h_freq
        if self.preprocessing.filter.enabled and lf is not None and hf is not None and lf >= hf:
            self.preview_state_label.setText("Preview unavailable: high-pass must be below low-pass.")
            return
        try:
            settings = copy.deepcopy(self.preprocessing)
            order = list(self._processing_order)
            data, names, sfreq = engine.preview_preprocessing_segment(
                self.original_raw,
                self.raw_viewer.start_sec,
                self.raw_viewer.duration_sec,
                settings,
                order,
            )
            self.raw_viewer.set_segment(data, names, sfreq, self.raw_viewer.start_sec, as_preview=True)
            self.preview_state_label.setText(
                "LIVE PREVIEW of all checked preprocessing on this visible segment. Full recording is committed automatically in the background."
            )
        except Exception as exc:
            self.preview_state_label.setText(f"Preview unavailable: {exc}")

    def _schedule_preproc_commit(self):
        if self.original_raw is None:
            return
        if self._preproc_commit_inflight:
            self._preproc_commit_pending = True
            return
        self.preproc_commit_timer.start(500)

    def _set_preproc_controls_enabled(self, enabled: bool):
        for widget in [self.filter_group, self.interp_group, self.ref_group, self.ica_card]:
            widget.setEnabled(enabled)

    def _commit_live_preprocessing(self):
        if self.original_raw is None:
            return
        if self._preproc_commit_inflight:
            self._preproc_commit_pending = True
            return
        self._sync_preprocessing_from_ui()
        lf, hf = self.preprocessing.filter.l_freq, self.preprocessing.filter.h_freq
        if self.preprocessing.filter.enabled and lf is not None and hf is not None and lf >= hf:
            QMessageBox.warning(self, "Invalid filter", "High-pass frequency must be lower than low-pass frequency.")
            return

        settings = copy.deepcopy(self.preprocessing)
        order = list(self._processing_order)
        settings.step_order = order
        self._preproc_commit_inflight = True
        self._preproc_commit_pending = False
        self._set_preproc_controls_enabled(False)
        self.status_label.setText("Applying checked preprocessing to the full recording in background …")

        worker = FunctionWorker(engine.apply_preprocessing, self.original_raw, settings, order)
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.error.connect(self._worker_error)
        worker.signals.result.connect(lambda raw, st=settings: self._live_preprocessing_complete(raw, st))
        worker.signals.finished.connect(self._live_preprocessing_finished)
        self.thread_pool.start(worker)

    def _live_preprocessing_complete(self, raw, settings):
        self.processed_raw = raw
        self.preprocessing = settings
        self.preprocessing.step_order = list(self._processing_order)
        self.metadata.processing_log.append(
            "Auto-applied preprocessing: filter={} | order={}".format(
                "on" if settings.filter.enabled else "off",
                " -> ".join(self._processing_order) or "none",
            )
        )
        self.raw_viewer.clear_manual_segment()
        if self.display_combo.currentIndex() == 0:
            self.raw_viewer.set_raw(raw)
        self._settings_dirty = False
        self.preview_state_label.setText("Checked preprocessing committed automatically to the full recording.")
        self._invalidate_after_raw_change(keep_ica_tab=True, keep_events=True)
        self._refresh_epoch_preflight()
        self._refresh_ica_fit_view()
        self._refresh_epoch_input_status()
        self.status_label.setText("Preprocessing updated; event timeline preserved.")

    def _live_preprocessing_finished(self):
        self._preproc_commit_inflight = False
        self._set_preproc_controls_enabled(True)
        if self._preproc_commit_pending:
            self._preproc_commit_pending = False
            self.preproc_commit_timer.start(50)

    def _display_choice_changed(self):
        self.raw_viewer.clear_manual_segment()
        raw = self.processed_raw if self.display_combo.currentIndex() == 0 else self.original_raw
        if raw is not None:
            self.raw_viewer.set_raw(raw)
        if self.display_combo.currentIndex() == 0:
            self._schedule_preview()

    def select_bad_channels(self):
        if self.original_raw is None:
            QMessageBox.information(self, "No recording", "Load a recording first.")
            return
        eeg_idx = mne.pick_types(self.original_raw.info, eeg=True, exclude=[])
        channels = [self.original_raw.ch_names[i] for i in eeg_idx]
        dialog = BadChannelDialog(channels, self.preprocessing.interpolation.bad_channels, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.preprocessing.interpolation.bad_channels = dialog.selected_channels()
            self.bad_channels_label.setText(", ".join(self.preprocessing.interpolation.bad_channels) or "None")
            if self.interp_group.isChecked():
                self._active_step_setting_changed("interpolation")

    def _reset_pipeline_ui_for_new_recording(self):
        self._pipeline_signal_guard = True
        try:
            self.filter_group.setChecked(False)
            self.interp_group.setChecked(False)
            self.ref_group.setChecked(False)
            self.ica_enabled.setChecked(False)
        finally:
            self._pipeline_signal_guard = False
        self.preprocessing = PreprocessingSettings()
        self._processing_order = []
        self._ica_applied = False
        self._ica_fit_exclusions = []
        self._pre_ica_raw = None
        self._ica_cleaned_raw = None
        self._ica_cleaned_excluded = []
        self._epoch_input_mode = "pre_ica"
        self.bad_channels_label.setText("None")
        self.external_annotation_table = None
        self._native_annotations = None
        if hasattr(self, "annotation_label"):
            self.annotation_label.setText("None attached")
        self._clear_ica_state()
        self._sync_preprocessing_from_ui()
        self._update_order_labels()

    def open_file(self):
        start_dir = str(self.app_settings.value("paths/last_eeg_open_dir", "") or "")
        if not start_dir:
            start_dir = str(self.metadata.input_path.parent) if self.metadata.input_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Open EEG recording", start_dir, "EEG files (*.edf *.fif *.fif.gz);;EDF (*.edf);;FIF (*.fif *.fif.gz)"
        )
        if not path:
            return
        self.app_settings.setValue("paths/last_eeg_open_dir", str(Path(path).parent))
        self._set_busy(True, "Loading recording …")
        try:
            raw = engine.load_raw(path, preload=True)
            montage_info = engine.ensure_eeg_montage(raw)
        except Exception as exc:
            self._set_busy(False)
            QMessageBox.critical(self, "Could not open file", str(exc))
            return
        self._set_busy(False)
        self._reset_pipeline_ui_for_new_recording()
        self.original_raw = raw
        self.processed_raw = raw.copy()
        self._native_annotations = raw.annotations.copy()
        self.external_annotation_table = None
        self.metadata = SessionMetadata(input_path=Path(path), subject_id=self.subject_edit.text().strip())
        self.metadata.processing_log.append("Imported recording")
        if montage_info.get("source") == "existing":
            self.metadata.processing_log.append(
                f"Sensor locations already present ({montage_info.get('matched', 0)}/{montage_info.get('total', 0)} EEG channels positioned)"
            )
        elif montage_info.get("applied"):
            self.metadata.processing_log.append(
                f"Automatically attached {montage_info.get('source')} sensor coordinates "
                f"({montage_info.get('matched', 0)}/{montage_info.get('total', 0)} channel names matched)"
            )
        else:
            self.metadata.processing_log.append(
                "No reliable EEG sensor montage could be inferred automatically; ICA topomaps may require channel-location metadata."
            )
        self.file_label.setText(Path(path).name)
        self.setWindowTitle(f"ERP Workbench 1.0 — {Path(path).name} — ICA BETA")
        self.annotation_label.setText(
            f"No external TXT attached ({len(self._native_annotations)} annotation(s) already embedded in recording)"
            if len(self._native_annotations) else "No external TXT attached"
        )
        self.raw_viewer.set_raw(self.processed_raw)
        self._apply_protocol_default_channels()
        self._refresh_ica_fit_view()
        self.display_combo.setCurrentIndex(0)
        self.preview_state_label.setText(
            "Original recording loaded. Check Filter / Interpolate / Re-reference to apply each step automatically."
        )
        self._invalidate_after_raw_change()
        self._refresh_epoch_input_status()
        total_sec = float(raw.n_times) / float(raw.info["sfreq"])
        h = int(total_sec // 3600)
        m = int((total_sec % 3600) // 60)
        sec = total_sec % 60
        duration_text = f"{h:02d}:{m:02d}:{sec:05.2f}" if h else f"{m:02d}:{sec:05.2f}"
        montage_status = (
            f" Sensor positions: {montage_info.get('source')}."
            if montage_info.get("source") in {"existing", "standard_1020", "standard_1005", "biosemi32"}
            else " Sensor positions could not be inferred automatically."
        )
        self.status_label.setText(
            f"Loaded FULL recording: {Path(path).name} — {len(raw.ch_names)} channels, "
            f"{raw.info['sfreq']:g} Hz, duration {duration_text}. Viewer shows a {self.raw_viewer.duration_sec:g}-s window."
            + montage_status
        )

        # Make the common EDF/FIF + matching *_Annotation.txt workflow one-step.
        companion = engine.find_companion_annotation_file(path)
        if companion is not None:
            self._attach_annotation_path(companion, automatic=True)
        elif len(raw.annotations):
            self.event_source.setCurrentText("annotations")
            self._discover_events_internal(silent=True)

    def attach_annotation_file(self):
        if self.original_raw is None:
            QMessageBox.information(self, "No recording", "Load the matching EDF/FIF recording first.")
            return
        start_dir = str(self.app_settings.value("paths/last_annotation_dir", "") or "")
        if not start_dir:
            start_dir = str(self.metadata.input_path.parent) if self.metadata.input_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Attach recorder event annotations",
            start_dir,
            "Annotation text (*.txt);;Text files (*.txt);;All files (*.*)",
        )
        if path:
            self.app_settings.setValue("paths/last_annotation_dir", str(Path(path).parent))
            self._attach_annotation_path(Path(path), automatic=False)

    def _attach_annotation_path(self, path: str | Path, automatic: bool = False):
        if self.original_raw is None:
            return
        path = Path(path)
        try:
            table = engine.read_annotation_txt(path)
            stats = engine.attach_external_annotations(
                self.original_raw, self._native_annotations, table
            )
            # Preprocessing changes the signal, not the event clock. Keep the same
            # annotation timeline on whichever signal version is currently active.
            if self.processed_raw is not None:
                self.processed_raw.set_annotations(self.original_raw.annotations.copy(), verbose="ERROR")
            if self._pre_ica_raw is not None and self._pre_ica_raw is not self.processed_raw:
                self._pre_ica_raw.set_annotations(self.original_raw.annotations.copy(), verbose="ERROR")
            if self._ica_cleaned_raw is not None:
                self._ica_cleaned_raw.set_annotations(self.original_raw.annotations.copy(), verbose="ERROR")
        except Exception as exc:
            if automatic:
                self.annotation_label.setText(f"Auto-detected {path.name}, but it could not be attached")
                QMessageBox.warning(
                    self, "Annotation auto-attach failed",
                    f"A matching annotation file was found, but could not be attached:\n\n{path.name}\n\n{exc}"
                )
            else:
                QMessageBox.critical(self, "Could not attach annotations", str(exc))
            return

        self.external_annotation_table = table
        self.metadata.annotation_path = path
        self.metadata.annotation_count = stats["attached"]
        self.metadata.annotation_out_of_range = stats["out_of_range"]
        self.metadata.annotation_duplicates_skipped = stats.get("duplicates_skipped", 0)
        self.annotation_label.setText(
            f"{path.name} — {stats['attached']} marker(s) attached"
            + (f"; {stats.get('duplicates_skipped', 0)} exact duplicate(s) skipped" if stats.get("duplicates_skipped", 0) else "")
            + (f"; {stats['out_of_range']} outside recording" if stats["out_of_range"] else "")
        )
        self.metadata.processing_log.append(
            f"Attached event annotations: {path.name} ({stats['attached']} markers"
            + (f", {stats.get('duplicates_skipped', 0)} exact duplicates skipped" if stats.get("duplicates_skipped", 0) else "")
            + (f", {stats['out_of_range']} outside recording" if stats["out_of_range"] else "")
            + ")"
        )
        # Annotation attachment changes event/epoch definitions but does not alter
        # EEG samples or invalidate an already fitted ICA solution.
        self._invalidate_after_raw_change(keep_ica_tab=True)
        current = self.processed_raw if self.display_combo.currentIndex() == 0 else self.original_raw
        if current is not None:
            self.raw_viewer.set_raw(current)
        self.event_source.setCurrentText("annotations")
        # v0.5: attaching the timeline immediately populates Epoching. There is
        # no separate "discover" step required for the normal Annotation.txt workflow.
        self._discover_events_internal(silent=True)

        last = stats["annotation_last_latency_sec"]
        self.status_label.setText(
            f"Attached {stats['attached']} event markers from {path.name}; last marker at {last:.3f} s. "
            "Epoching has been populated automatically from this timeline."
        )

        if stats["out_of_range"]:
            QMessageBox.warning(
                self,
                "Some annotations are outside the recording",
                f"{stats['out_of_range']} of {stats['total']} marker(s) fall outside the loaded recording "
                f"({stats['recording_duration_sec']:.3f} s) and were not attached.\n\n"
                "This can indicate that the TXT belongs to a different recording, or that the exported files have different start/end lengths. "
                "Please verify the pairing before epoching.",
            )

    # Kept as a compatibility hook for older prompts/scripts. In v0.4 the UI
    # has no Apply button; calling this simply commits the currently checked
    # pipeline immediately.
    def apply_preprocessing(self):
        self.preproc_commit_timer.stop()
        self._commit_live_preprocessing()

    def _preprocessing_complete(self, raw):
        # Backwards-compatible alias used by no v0.4 UI path.
        self._live_preprocessing_complete(raw, copy.deepcopy(self.preprocessing))

    def _invalidate_after_raw_change(self, keep_ica_tab=False, keep_events=False):
        if not keep_ica_tab:
            self._clear_ica_state()
        if not keep_events:
            self.events = np.empty((0, 3), dtype=int)
            self.event_labels = {}
        self.epochs = None
        self.review = EpochReviewState()
        self._epoch_decision_history = []
        self.clean_epochs = None
        self.evokeds = {}
        self.measurements = []
        self._refresh_event_table()
        self._refresh_epoch_event_status()
        self._refresh_epoch_screening_channels_label()
        self._refresh_epoch_preflight()
        self._refresh_review_table()
        self._refresh_result_table()
        self.condition_combo.clear()
        self.channel_combo.clear()

    # ---------- ICA ----------
    def _build_ica_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        beta = QLabel("ICA (BETA) — component removal is always an explicit user action.")
        beta.setWordWrap(True)
        beta.setStyleSheet("font-weight: 600; padding: 7px; border: 1px solid #a8752a; border-radius: 5px;")
        layout.addWidget(beta)

        self.ica_method = QComboBox(); self.ica_method.addItems(["fastica", "infomax", "infomax_extended"])
        self.ica_method.setToolTip("infomax_extended uses extended Infomax.")
        self.ica_components = QLineEdit("0.99"); self.ica_components.setMaximumWidth(90)
        self.ica_components.setToolTip(
            "0.99 means: retain enough PCA dimensions to explain >99% of variance, then fit ICA in that subspace. "
            "Enter an integer for a fixed component count; leave blank for MNE's default/rank-aware behavior."
        )
        self.run_ica_btn = QPushButton("Fit ICA"); self.run_ica_btn.clicked.connect(self.run_ica)

        self.ica_workspace_tabs = QTabWidget()
        layout.addWidget(self.ica_workspace_tabs, 1)

        # --- 1. Fit-data selection / artifact-span exclusions ---
        fit_page = QWidget(); fit_outer = QHBoxLayout(fit_page)
        fit_outer.setContentsMargins(2, 2, 2, 2); fit_outer.setSpacing(4)
        self.ica_fit_splitter = QSplitter(Qt.Orientation.Horizontal)
        fit_outer.addWidget(self.ica_fit_splitter)

        side_content = QWidget(); side_content.setMinimumWidth(0); side_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.ica_fit_side_content = side_content
        side_layout = QVBoxLayout(side_content)
        side_layout.setContentsMargins(8, 8, 8, 8); side_layout.setSpacing(10)

        side_header = QHBoxLayout()
        side_header.addWidget(QLabel("ICA fit controls"))
        side_header.addStretch(1)
        self.ica_fit_side_hide_btn = QPushButton("Hide controls")
        self.ica_fit_side_hide_btn.clicked.connect(lambda: self._toggle_ica_fit_side_panel(False))
        side_header.addWidget(self.ica_fit_side_hide_btn)
        side_layout.addLayout(side_header)

        fit_settings = QGroupBox("Fit settings")
        fit_settings_layout = QGridLayout(fit_settings)
        self.ica_fit_settings_layout = fit_settings_layout
        self.ica_method_label = QLabel("Method")
        self.ica_dim_label = QLabel("Dimensionality")
        fit_settings_layout.addWidget(self.ica_method_label, 0, 0); fit_settings_layout.addWidget(self.ica_method, 0, 1)
        fit_settings_layout.addWidget(self.ica_dim_label, 1, 0); fit_settings_layout.addWidget(self.ica_components, 1, 1)
        fit_settings_layout.setColumnStretch(1, 1)
        self.ica_method.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        dim_hint = QLabel("0.99 = enough PCA dimensions for >99% variance; blank = MNE default/rank-aware")
        dim_hint.setWordWrap(True); dim_hint.setProperty("muted", True)
        fit_settings_layout.addWidget(dim_hint, 2, 0, 1, 2)
        fit_settings_layout.addWidget(self.run_ica_btn, 3, 0, 1, 2)
        self.ica_fit_progress = QProgressBar(); self.ica_fit_progress.setRange(0, 0); self.ica_fit_progress.setTextVisible(False); self.ica_fit_progress.setVisible(False)
        self.ica_fit_eta = QLabel(""); self.ica_fit_eta.setWordWrap(True); self.ica_fit_eta.setProperty("muted", True)
        fit_settings_layout.addWidget(self.ica_fit_progress, 4, 0, 1, 2)
        fit_settings_layout.addWidget(self.ica_fit_eta, 5, 0, 1, 2)
        side_layout.addWidget(fit_settings)

        fit_note = QLabel("Marked spans are excluded from ICA fitting only; the analysis EEG and later epoch decisions are unchanged.")
        fit_note.setWordWrap(True); fit_note.setProperty("muted", True)
        side_layout.addWidget(fit_note)

        exclusion_box = QGroupBox("ICA-fit exclusions")
        exclusion_layout = QVBoxLayout(exclusion_box)
        reason_row = QGridLayout()
        self.ica_reason_layout = reason_row
        self.ica_reason_label = QLabel("Reason")
        self.ica_exclusion_reason = QLineEdit("neck movement")
        self.ica_exclusion_reason.setToolTip("Free-text reason stored in preprocessing provenance.")
        self.ica_exclusion_reason.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ica_mark_visible_btn = QPushButton("Exclude visible span"); self.ica_mark_visible_btn.clicked.connect(self._ica_exclude_visible_span)
        reason_row.addWidget(self.ica_reason_label, 0, 0); reason_row.addWidget(self.ica_exclusion_reason, 0, 1); reason_row.addWidget(self.ica_mark_visible_btn, 0, 2)
        reason_row.setColumnStretch(1, 1)
        exclusion_layout.addLayout(reason_row)

        exact_row = QGridLayout()
        self.ica_exact_layout = exact_row
        self.ica_start_label = QLabel("Start")
        self.ica_end_label = QLabel("End")
        self.ica_excl_start = ReliableDoubleSpinBox(); self.ica_excl_start.setRange(0.0, 100000.0); self.ica_excl_start.setDecimals(3); self.ica_excl_start.setSuffix(" s")
        self.ica_excl_end = ReliableDoubleSpinBox(); self.ica_excl_end.setRange(0.0, 100000.0); self.ica_excl_end.setDecimals(3); self.ica_excl_end.setSuffix(" s")
        self.ica_add_exact_btn = QPushButton("Add span"); self.ica_add_exact_btn.clicked.connect(self._ica_add_exact_exclusion)
        exact_row.addWidget(self.ica_start_label, 0, 0); exact_row.addWidget(self.ica_excl_start, 0, 1); exact_row.addWidget(self.ica_end_label, 0, 2); exact_row.addWidget(self.ica_excl_end, 0, 3); exact_row.addWidget(self.ica_add_exact_btn, 0, 4)
        exact_row.setColumnStretch(1, 1); exact_row.setColumnStretch(3, 1)
        exclusion_layout.addLayout(exact_row)

        self.ica_exclusion_table = QTableWidget(0, 3)
        self.ica_exclusion_table.setHorizontalHeaderLabels(["Start (s)", "End (s)", "Reason"])
        self.ica_exclusion_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ica_exclusion_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ica_exclusion_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.ica_exclusion_table.itemChanged.connect(self._ica_exclusion_table_changed)
        self.ica_exclusion_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.ica_exclusion_table.setMinimumHeight(125)
        exclusion_layout.addWidget(self.ica_exclusion_table)
        table_buttons=QGridLayout()
        self.ica_table_buttons_layout = table_buttons
        self.ica_remove_span_btn=QPushButton("Remove selected"); self.ica_remove_span_btn.clicked.connect(self._ica_remove_selected_exclusion)
        self.ica_clear_spans_btn=QPushButton("Clear all"); self.ica_clear_spans_btn.clicked.connect(self._ica_clear_exclusions)
        table_buttons.addWidget(self.ica_remove_span_btn, 0, 0); table_buttons.addWidget(self.ica_clear_spans_btn, 0, 1); table_buttons.setColumnStretch(2, 1)
        exclusion_layout.addLayout(table_buttons)
        side_layout.addWidget(exclusion_box, 1)

        speed = QGroupBox("Fit speed")
        perf = QVBoxLayout(speed)
        fast_row = QGridLayout()
        self.ica_fast_layout = fast_row
        self.ica_fast_fit = QCheckBox("Use every Nth sample while fitting")
        self.ica_decim_label = QLabel("N")
        self.ica_decim = ReliableSpinBox(); self.ica_decim.setRange(2, 20); self.ica_decim.setValue(2); self.ica_decim.setEnabled(False)
        self.ica_fast_fit.toggled.connect(self.ica_decim.setEnabled)
        fast_row.addWidget(self.ica_fast_fit, 0, 0); fast_row.addWidget(self.ica_decim_label, 0, 1); fast_row.addWidget(self.ica_decim, 0, 2); fast_row.setColumnStretch(3, 1)
        perf.addLayout(fast_row)
        fast_note = QLabel("Decimation affects only the temporary ICA fitting operation; component removal is reconstructed on the full-resolution recording.")
        fast_note.setWordWrap(True); fast_note.setProperty("muted", True); perf.addWidget(fast_note)
        side_layout.addWidget(speed)
        side_layout.addStretch(1)

        self.ica_fit_side_scroll = QScrollArea()
        self.ica_fit_side_scroll.setWidgetResizable(True)
        self.ica_fit_side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ica_fit_side_scroll.setWidget(side_content)
        self.ica_fit_side_scroll.setMinimumWidth(280); self.ica_fit_side_scroll.setMaximumWidth(500)

        graph_panel = QWidget(); graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(4, 4, 4, 4); graph_layout.setSpacing(5)
        graph_bar = QHBoxLayout()
        self.ica_fit_side_show_btn = QPushButton("Show controls")
        self.ica_fit_side_show_btn.setVisible(False)
        self.ica_fit_side_show_btn.clicked.connect(lambda: self._toggle_ica_fit_side_panel(True))
        drag_hint = QLabel("Left-drag pans. Right-drag across a gross-artifact span to exclude it from ICA fitting.")
        drag_hint.setWordWrap(True); drag_hint.setStyleSheet("font-weight: 600;")
        graph_bar.addWidget(self.ica_fit_side_show_btn); graph_bar.addWidget(drag_hint, 1)
        graph_layout.addLayout(graph_bar)
        self.ica_fit_view = StackedEEGViewer()
        self.ica_fit_view.channelSelectionChanged.connect(self._remember_display_channels)
        self.ica_fit_view.spanSelected.connect(self._ica_exclude_dragged_span)
        self.ica_fit_view.set_span_selection_enabled(True)
        self.ica_fit_view.setToolTip("Left-drag = move through time. Right-drag = ICA-fit exclusion selection.")
        graph_layout.addWidget(self.ica_fit_view, 1)

        self.ica_fit_splitter.addWidget(self.ica_fit_side_scroll)
        self.ica_fit_splitter.addWidget(graph_panel)
        self.ica_fit_splitter.setStretchFactor(0, 0); self.ica_fit_splitter.setStretchFactor(1, 1)
        self.ica_fit_splitter.setCollapsible(0, True)
        self.ica_fit_splitter.setSizes([400, 1080])
        self.ica_fit_splitter.splitterMoved.connect(lambda *_: self._apply_sidebar_reflow())
        self.ica_workspace_tabs.addTab(fit_page, "1  Fit data / exclusions")

        # --- 2. Components ---
        comp_page = QWidget(); comp_layout = QVBoxLayout(comp_page)
        classifier_row = QHBoxLayout()
        self.ica_auto_label_after_fit = QCheckBox("Auto-label after fit (ICLabel)")
        self.ica_auto_label_after_fit.setChecked(True)
        classifier_row.addWidget(self.ica_auto_label_after_fit); classifier_row.addStretch(1)
        comp_layout.addLayout(classifier_row)
        advisory = QLabel("Confirm automatic suggestions with the component time-domain morphology and topographic map before removal.")
        advisory.setWordWrap(True); advisory.setStyleSheet("font-weight: 600;"); comp_layout.addWidget(advisory)
        self.blink_aid_status = QLabel("Blink correlation: not run.")
        self.blink_aid_status.setWordWrap(True); self.blink_aid_status.setProperty("muted", True); comp_layout.addWidget(self.blink_aid_status)
        self.iclabel_status = QLabel("ICLabel: trained component classifier; not run. Treat its class/probability as a suggestion because performance depends on how closely the recording and ICA decomposition resemble the data on which the model was trained.")
        self.iclabel_status.setWordWrap(True); self.iclabel_status.setProperty("muted", True); comp_layout.addWidget(self.iclabel_status)
        self.ica_view = ICAComponentView()
        self.ica_view.exclusionsChanged.connect(self._ica_exclusions_changed)
        comp_layout.addWidget(self.ica_view, 1)

        removal_row = QHBoxLayout()
        self.remove_ica_components_btn = QPushButton("Remove selected components")
        self.remove_ica_components_btn.setEnabled(False)
        self.remove_ica_components_btn.setToolTip("Checking components is selection-only. This button starts one full-resolution EEG reconstruction using the current selection.")
        self.remove_ica_components_btn.clicked.connect(self.remove_selected_ica_components)
        self.ica_selection_status = QLabel("No ICA fit yet."); self.ica_selection_status.setProperty("muted", True)
        removal_row.addWidget(self.remove_ica_components_btn); removal_row.addWidget(self.ica_selection_status, 1)
        comp_layout.addLayout(removal_row)
        self.ica_remove_progress = QProgressBar(); self.ica_remove_progress.setRange(0, 100); self.ica_remove_progress.setValue(0); self.ica_remove_progress.setTextVisible(True); self.ica_remove_progress.setVisible(False)
        self.ica_remove_eta = QLabel(""); self.ica_remove_eta.setProperty("muted", True)
        comp_layout.addWidget(self.ica_remove_progress); comp_layout.addWidget(self.ica_remove_eta)
        self.ica_workspace_tabs.addTab(comp_page, "2  Components")

        # --- 3. Pre/post ICA comparison + downstream choice ---
        post_page = QWidget(); post_layout = QVBoxLayout(post_page)
        post_controls = QHBoxLayout()
        self.ica_post_display_combo = QComboBox()
        self.ica_post_display_combo.addItem("Pre-ICA processed EEG", "pre_ica")
        self.ica_post_display_combo.currentIndexChanged.connect(self._ica_post_display_changed)
        self.ica_epoch_input_combo = QComboBox()
        self.ica_epoch_input_combo.addItem("Pre-ICA processed EEG", "pre_ica")
        self.ica_epoch_input_combo.currentIndexChanged.connect(self._ica_epoch_input_changed)
        continue_btn = QPushButton("Continue to Epoching →"); continue_btn.clicked.connect(self._continue_after_ica)
        post_controls.addWidget(QLabel("Viewer")); post_controls.addWidget(self.ica_post_display_combo)
        post_controls.addSpacing(12); post_controls.addWidget(QLabel("Use for epoching")); post_controls.addWidget(self.ica_epoch_input_combo)
        post_controls.addWidget(continue_btn); post_controls.addStretch(1)
        post_layout.addLayout(post_controls)
        self.ica_post_status = QLabel("Pre-ICA processed EEG is preserved. After component removal, Post-ICA processed EEG becomes available for both viewing and epoching selection.")
        self.ica_post_status.setWordWrap(True); self.ica_post_status.setProperty("muted", True); post_layout.addWidget(self.ica_post_status)
        self.ica_post_view = StackedEEGViewer()
        self.ica_post_view.channelSelectionChanged.connect(self._remember_display_channels)
        post_layout.addWidget(self.ica_post_view, 1)
        self.ica_workspace_tabs.addTab(post_page, "3  Pre / post ICA EEG")

        self.tabs.addTab(page, "2  ICA (BETA)")

    def _refresh_ica_fit_view(self):
        if not hasattr(self, "ica_fit_view"):
            return
        raw = self.processed_raw
        self.ica_fit_view.set_raw(raw)
        if raw is not None and self._preferred_display_channels:
            self.ica_fit_view.set_selected_channels(self._preferred_display_channels)
        self.ica_fit_view.set_overlay_spans(self._ica_fit_exclusions)
        if raw is not None and raw.n_times:
            total = float(raw.n_times) / float(raw.info["sfreq"])
            for spin in (self.ica_excl_start, self.ica_excl_end):
                spin.setMaximum(max(0.001, total))

    def _refresh_ica_exclusion_table(self):
        if not hasattr(self, "ica_exclusion_table"):
            return
        self._ica_exclusion_table_guard=True
        try:
            self.ica_exclusion_table.setRowCount(len(self._ica_fit_exclusions))
            for r, span in enumerate(self._ica_fit_exclusions):
                start_item=QTableWidgetItem(f"{float(span['start_sec']):.3f}")
                end_item=QTableWidgetItem(f"{float(span['end_sec']):.3f}")
                for item in (start_item,end_item): item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                reason_item=QTableWidgetItem(str(span.get("reason", "artifact")))
                self.ica_exclusion_table.setItem(r,0,start_item); self.ica_exclusion_table.setItem(r,1,end_item); self.ica_exclusion_table.setItem(r,2,reason_item)
        finally:
            self._ica_exclusion_table_guard=False
        self.ica_fit_view.set_overlay_spans(self._ica_fit_exclusions)

    def _ica_exclusion_table_changed(self, item: QTableWidgetItem):
        if getattr(self,"_ica_exclusion_table_guard",False) or item is None or item.column()!=2:
            return
        row=item.row()
        if not (0<=row<len(self._ica_fit_exclusions)):
            return
        reason=item.text().strip() or "artifact"
        self._ica_fit_exclusions[row]["reason"]=reason
        self.preprocessing.ica.fit_exclude_spans=copy.deepcopy(self._ica_fit_exclusions)
        # Editing the reason is metadata only; the fit samples have not changed, so an existing ICA fit remains valid.
        self.ica_fit_view.set_overlay_spans(self._ica_fit_exclusions)

    def _toggle_ica_fit_side_panel(self, visible: bool):
        """Collapse/restore only the ICA-fit control sidebar; the EEG viewer stays in place."""
        if not hasattr(self, "ica_fit_side_scroll"):
            return
        visible = bool(visible)
        self.ica_fit_side_scroll.setVisible(visible)
        if hasattr(self, "ica_fit_side_show_btn"):
            self.ica_fit_side_show_btn.setVisible(not visible)
        if visible and hasattr(self, "ica_fit_splitter"):
            total = max(800, self.ica_fit_splitter.width())
            self.ica_fit_splitter.setSizes([min(420, total // 3), max(500, total - min(420, total // 3))])

    def _invalidate_ica_fit_for_span_change(self):
        self.preprocessing.ica.fit_exclude_spans = copy.deepcopy(self._ica_fit_exclusions)
        if self.ica is not None or self._ica_applied:
            self._clear_ica_state()
            self.status_label.setText("ICA fit exclusions changed — previous ICA was invalidated; fit ICA again.")
        if hasattr(self, "ica_post_view"):
            self.ica_post_view.set_raw(None)
        if hasattr(self, "ica_post_status"):
            self.ica_post_status.setText("ICA fit-data exclusions changed. Fit ICA again before removing components.")
        self._refresh_ica_version_controls()

    def _normalise_ica_exclusions(self):
        cleaned = []
        raw = self.processed_raw
        total = float(raw.n_times) / float(raw.info["sfreq"]) if raw is not None and raw.n_times else 0.0
        for span in self._ica_fit_exclusions:
            try:
                a = max(0.0, float(span.get("start_sec", 0.0)))
                b = min(total, float(span.get("end_sec", a))) if total > 0 else float(span.get("end_sec", a))
            except Exception:
                continue
            if b <= a:
                continue
            cleaned.append({"start_sec": a, "end_sec": b, "reason": str(span.get("reason", "artifact") or "artifact")})
        cleaned.sort(key=lambda x: (x["start_sec"], x["end_sec"]))
        # Merge overlapping/touching spans. Keep combined reason text concise.
        merged = []
        for span in cleaned:
            if merged and span["start_sec"] <= merged[-1]["end_sec"] + 1e-6:
                merged[-1]["end_sec"] = max(merged[-1]["end_sec"], span["end_sec"])
                if span["reason"] not in merged[-1]["reason"]:
                    merged[-1]["reason"] += "; " + span["reason"]
            else:
                merged.append(dict(span))
        self._ica_fit_exclusions = merged

    def _ica_exclude_dragged_span(self, start_sec: float, end_sec: float):
        """Add a right-dragged ICA-fit-only exclusion span."""
        if self.processed_raw is None:
            return
        start = max(0.0, float(min(start_sec, end_sec)))
        end = min(float(self.processed_raw.times[-1]), float(max(start_sec, end_sec)))
        if end - start < 0.010:
            return
        reason = self.ica_exclusion_reason.text().strip() or "gross artifact"
        self._ica_fit_exclusions.append({"start_sec": start, "end_sec": end, "reason": reason})
        self._normalise_ica_exclusions(); self._refresh_ica_exclusion_table(); self._invalidate_ica_fit_for_span_change()
        self.status_label.setText(f"ICA-fit exclusion added by right-drag: {start:.3f}–{end:.3f} s ({reason}).")

    def _ica_exclude_visible_span(self):
        if self.processed_raw is None:
            QMessageBox.information(self, "No recording", "Load/preprocess a recording first."); return
        start, end = self.ica_fit_view.visible_time_range()
        if end <= start:
            return
        self._ica_fit_exclusions.append({
            "start_sec": float(start), "end_sec": float(end),
            "reason": self.ica_exclusion_reason.text().strip() or "artifact",
        })
        self._normalise_ica_exclusions(); self._refresh_ica_exclusion_table(); self._invalidate_ica_fit_for_span_change()
        self.status_label.setText(f"Marked {start:.3f}–{end:.3f} s to be excluded from ICA fitting only.")

    def _ica_add_exact_exclusion(self):
        if self.processed_raw is None:
            QMessageBox.information(self, "No recording", "Load/preprocess a recording first."); return
        start, end = float(self.ica_excl_start.value()), float(self.ica_excl_end.value())
        if end <= start:
            QMessageBox.warning(self, "Invalid ICA exclusion", "End time must be greater than start time."); return
        self._ica_fit_exclusions.append({
            "start_sec": start, "end_sec": end,
            "reason": self.ica_exclusion_reason.text().strip() or "artifact",
        })
        self._normalise_ica_exclusions(); self._refresh_ica_exclusion_table(); self._invalidate_ica_fit_for_span_change()

    def _ica_remove_selected_exclusion(self):
        rows = self.ica_exclusion_table.selectionModel().selectedRows() if hasattr(self, "ica_exclusion_table") else []
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._ica_fit_exclusions):
            self._ica_fit_exclusions.pop(idx)
            self._refresh_ica_exclusion_table(); self._invalidate_ica_fit_for_span_change()

    def _ica_clear_exclusions(self):
        if not self._ica_fit_exclusions:
            return
        self._ica_fit_exclusions = []
        self._refresh_ica_exclusion_table(); self._invalidate_ica_fit_for_span_change()

    def _refresh_ica_version_controls(self):
        """Refresh pre/post ICA selectors without discarding either signal version."""
        if not hasattr(self, "ica_post_display_combo") or not hasattr(self, "ica_epoch_input_combo"):
            return
        cleaned_available = self._ica_cleaned_raw is not None
        display_wanted = self.ica_post_display_combo.currentData() if self.ica_post_display_combo.count() else "pre_ica"
        epoch_wanted = self._epoch_input_mode
        for combo, wanted in (
            (self.ica_post_display_combo, display_wanted),
            (self.ica_epoch_input_combo, epoch_wanted),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Pre-ICA processed EEG", "pre_ica")
            if cleaned_available:
                combo.addItem("Post-ICA processed EEG", "ica_cleaned")
            idx = combo.findData(wanted)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        if not cleaned_available and self._epoch_input_mode != "pre_ica":
            self._epoch_input_mode = "pre_ica"
            self.preprocessing.ica.epoch_input = "pre_ica"
        self._ica_post_display_changed()
        self._refresh_epoch_input_status()

    def _ica_post_display_changed(self, _index=None):
        if not hasattr(self, "ica_post_view"):
            return
        mode = self.ica_post_display_combo.currentData() if hasattr(self, "ica_post_display_combo") else "pre_ica"
        if mode == "ica_cleaned" and self._ica_cleaned_raw is not None:
            raw = self._ica_cleaned_raw
        else:
            raw = self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw
        self.ica_post_view.set_raw(raw)
        if raw is not None and self._preferred_display_channels:
            self.ica_post_view.set_selected_channels(self._preferred_display_channels)

    def _ica_epoch_input_changed(self, _index=None):
        if not hasattr(self, "ica_epoch_input_combo"):
            return
        mode = str(self.ica_epoch_input_combo.currentData() or "pre_ica")
        if mode == "ica_cleaned" and self._ica_cleaned_raw is None:
            mode = "pre_ica"
        if mode == self._epoch_input_mode:
            self._refresh_epoch_input_status()
            return
        self._epoch_input_mode = mode
        self.preprocessing.ica.epoch_input = mode
        self.metadata.processing_log.append(
            "Epoching input changed to " + ("Post-ICA processed EEG" if mode == "ica_cleaned" else "Pre-ICA processed EEG")
        )
        self._invalidate_after_ica()
        self._refresh_epoch_input_status()
        self._refresh_epoch_preflight()
        self.status_label.setText(
            "Epoching will use " + ("the Post-ICA processed EEG." if mode == "ica_cleaned" else "the Pre-ICA processed EEG.")
        )

    def _epoching_raw(self):
        """Return the deliberately selected continuous signal for epoch cutting."""
        if self._epoch_input_mode == "ica_cleaned" and self._ica_cleaned_raw is not None:
            return self._ica_cleaned_raw
        return self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw

    def _ica_exclusions_changed(self, excluded):
        """Checkbox changes are selection-only; never reconstruct EEG implicitly."""
        selected = list(excluded or [])
        if hasattr(self, "remove_ica_components_btn"):
            self.remove_ica_components_btn.setEnabled(self.ica is not None and bool(selected))
        if hasattr(self, "ica_selection_status"):
            if self._ica_cleaned_raw is None:
                self.ica_selection_status.setText(
                    f"Selected for removal: {selected or 'none'}. Press Remove selected components to reconstruct the EEG."
                )
            elif selected == list(self._ica_cleaned_excluded):
                self.ica_selection_status.setText(
                    f"Current cleaned EEG already corresponds to removed components {selected or 'none'}."
                )
            else:
                self.ica_selection_status.setText(
                    f"Checked selection is now {selected or 'none'}; current cleaned EEG still reflects {self._ica_cleaned_excluded or 'none'}. "
                    "Press Remove selected components to rebuild it."
                )
        if hasattr(self, "ica_post_status") and self._ica_cleaned_raw is not None and selected != list(self._ica_cleaned_excluded):
            self.ica_post_status.setText(
                f"The saved ICA-cleaned EEG still removes {self._ica_cleaned_excluded or 'no components'}. "
                f"Current checks are {selected or 'none'}; rebuild only if you want the new selection."
            )

    def remove_selected_ica_components(self):
        if self.ica is None:
            QMessageBox.information(self, "ICA not fitted", "Fit ICA first.")
            return
        base = self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw
        if base is None:
            QMessageBox.information(self, "No recording", "No pre-ICA processed EEG is available.")
            return
        excluded = self.ica_view.excluded_components()
        if not excluded:
            QMessageBox.information(self, "No components selected", "Check at least one ICA component to remove first.")
            return
        self.remove_ica_components_btn.setEnabled(False)
        self.run_ica_btn.setEnabled(False)
        self.ica_remove_progress.setRange(0,100); self.ica_remove_progress.setValue(0); self.ica_remove_progress.setVisible(True)
        duration_sec = float(base.n_times) / float(base.info["sfreq"]) if base.n_times else 0.0
        n_eeg = max(1, len(mne.pick_types(base.info, eeg=True, exclude=[])))
        estimate_sec = max(3.0, (duration_sec * n_eeg / 32.0) * 0.012)
        self._start_estimated_task("ICA reconstruction", estimate_sec)
        self.global_task_progress.setRange(0,100); self.global_task_progress.setValue(0); self.global_task_progress.setTextVisible(True)
        self.ica_selection_status.setText(f"Reconstructing full EEG after removing {excluded or 'no components'} …")
        self.ica_post_status.setText("Reconstruction is running in the background. Progress remains visible in the status bar if you change tabs.")
        worker = FunctionWorker(engine.apply_ica, base, self.ica, excluded)
        # Retain the long-running worker and its Raw result until the GUI has
        # registered the reconstruction. A local-only QRunnable plus an
        # anonymous result callback could leave progress at 100% without
        # exposing Post-ICA EEG if the queued handoff was not processed.
        self._ica_reconstruction_worker = worker
        self._ica_reconstruction_pending_excluded = list(excluded)
        self._ica_reconstruction_result_received = False
        self._ica_reconstruction_failed = False
        worker.signals.progress.connect(self._ica_reconstruction_progress)
        worker.signals.result.connect(self._ica_reconstruction_result)
        worker.signals.error.connect(self._ica_removal_error)
        worker.signals.finished.connect(self._ica_removal_finished)
        self.thread_pool.start(worker)

    # Compatibility alias for older internal scripts/tests. The v1.0 UI exposes
    # only the explicit Remove selected components action.
    def apply_ica(self):
        self.remove_selected_ica_components()

    def preview_ica_cleaned(self):
        self.remove_selected_ica_components()

    def _ica_reconstruction_progress(self, text: str):
        text=str(text or "")
        if not text.startswith("ICA_RECON_PROGRESS|"):
            self.status_label.setText(text)
            return
        try:
            _prefix,pct_text,message=text.split("|",2); pct=max(0,min(100,int(pct_text)))
        except Exception:
            self.status_label.setText(text); return
        self.ica_remove_progress.setValue(pct)
        self.global_task_progress.setRange(0,100); self.global_task_progress.setValue(pct)
        self.status_label.setText(message)
        if self._ica_task_started_at is not None and pct>0:
            elapsed=max(0.001,time.monotonic()-self._ica_task_started_at)
            total_est=elapsed*100.0/pct
            self._ica_task_estimate_sec = max(elapsed, total_est)
            remaining=max(0.0,total_est-elapsed)
            eta=f"Estimated time: ~{self._format_eta_seconds(remaining)} remaining" if pct<100 else "Estimated time: complete"
            self.ica_remove_eta.setText(eta); self.global_task_eta.setText(eta)

    def _ica_removal_error(self, details: str):
        self._ica_reconstruction_failed = True
        self.status_label.setText("ICA reconstruction failed; the pre-ICA EEG and any previous cleaned reconstruction were left unchanged.")
        if hasattr(self,"ica_post_status"):
            self.ica_post_status.setText("Post-ICA reconstruction failed. Pre-ICA processed EEG remains available for viewing and epoching.")
        QMessageBox.critical(self,"ICA reconstruction failed",str(details)[-5000:])

    def _ica_reconstruction_result(self, raw):
        """Register a returned post-ICA Raw and surface GUI handoff errors."""
        try:
            if raw is None:
                raise RuntimeError("ICA reconstruction returned no EEG data.")
            self._ica_removal_complete(
                raw,
                list(self._ica_reconstruction_pending_excluded),
            )
            self._ica_reconstruction_result_received = True
        except Exception:
            self._ica_reconstruction_failed = True
            details = (
                "The ICA calculation completed and its returned EEG was retained, but part of the interface refresh failed. "
                "Post-ICA EEG may already be available in the selectors.\n\n"
                + traceback.format_exc()
            )
            self.status_label.setText(
                "ICA reconstruction completed, but part of the post-ICA interface refresh failed."
            )
            if hasattr(self, "ica_post_status"):
                self.ica_post_status.setText(
                    "The reconstructed EEG was retained. Review the error, then check the Post-ICA selectors."
                )
            QMessageBox.critical(self, "Post-ICA interface refresh failed", details[-5000:])

    def _ica_removal_finished(self):
        # The result signal is normally delivered immediately before finished.
        # Keep a direct fallback to the worker's Python result so a completed
        # reconstruction cannot be discarded by a queued GUI handoff.
        worker = self._ica_reconstruction_worker
        if (
            not self._ica_reconstruction_result_received
            and not self._ica_reconstruction_failed
            and worker is not None
            and bool(getattr(worker, "succeeded", False))
        ):
            self._ica_reconstruction_result(getattr(worker, "result_value", None))
        if hasattr(self, "ica_remove_progress"):
            self.ica_remove_progress.setVisible(False)
        if hasattr(self, "ica_remove_eta"):
            self.ica_remove_eta.setText("")
        self._finish_estimated_task()
        self.global_task_progress.setRange(0,0); self.global_task_progress.setTextVisible(False)
        if hasattr(self, "remove_ica_components_btn"):
            self.remove_ica_components_btn.setEnabled(
                self.ica is not None and bool(self.ica_view.excluded_components())
            )
        if hasattr(self, "run_ica_btn"):
            self.run_ica_btn.setEnabled(True)
        self._ica_reconstruction_worker = None
        self._ica_reconstruction_pending_excluded = []

    def _ica_removal_complete(self, raw, excluded):
        self._ica_cleaned_raw = raw
        self._ica_cleaned_excluded = list(excluded)
        self._ica_applied = True
        self.preprocessing.ica.enabled = True
        self.preprocessing.ica.excluded_components = list(excluded)
        self.preprocessing.ica.fit_exclude_spans = copy.deepcopy(self._ica_fit_exclusions)
        self._epoch_input_mode = "ica_cleaned"
        self.preprocessing.ica.epoch_input = "ica_cleaned"
        self.preprocessing.step_order = list(self._processing_order)
        self.metadata.processing_log.append(
            f"ICA BETA cleaned reconstruction created; removed components: {list(excluded)}; epoch input set to ICA-cleaned EEG"
        )
        # Expose and select the returned signal before refreshing downstream
        # epoch/ERP state. Even if a separate downstream widget later fails to
        # refresh, the expensive reconstructed EEG remains reachable.
        self._refresh_ica_version_controls()
        # After a deliberate removal, show the result and use it downstream by
        # default. The user can switch back to pre-ICA EEG at any time.
        for combo in (self.ica_post_display_combo, self.ica_epoch_input_combo):
            idx = combo.findData("ica_cleaned")
            if idx >= 0:
                combo.blockSignals(True); combo.setCurrentIndex(idx); combo.blockSignals(False)
        self._ica_post_display_changed()
        self._refresh_epoch_input_status()
        self._invalidate_after_ica()
        self.ica_workspace_tabs.setCurrentIndex(2)
        self.ica_selection_status.setText(
            f"Cleaned EEG ready — removed {list(excluded) or 'no components'}. Component checks will not rebuild it unless you press Remove selected components again."
        )
        self.ica_post_status.setText(
            f"ICA-cleaned EEG ready — removed {list(excluded) or 'no components'}. Pre-ICA EEG is still preserved. "
            "Use the Viewer selector to compare them and Use for epoching to choose the downstream signal."
        )
        self.status_label.setText(
            f"ICA reconstruction complete. Epoching input is ICA-cleaned EEG; removed components: {list(excluded) or 'none'}."
        )

    def _continue_after_ica(self):
        self._refresh_epoch_input_status()
        self.tabs.setCurrentIndex(2)

    def _parse_n_components(self):
        text = self.ica_components.text().strip()
        if not text:
            return None
        value = float(text)
        if 0.0 < value < 1.0:
            return value
        if value >= 1 and float(value).is_integer():
            return int(value)
        raise ValueError("ICA dimensionality must be blank, a fraction between 0 and 1, or a positive integer.")

    def run_ica(self):
        if self.processed_raw is None:
            QMessageBox.information(self, "No recording", "Load and preprocess a recording first.")
            return
        if self._preproc_commit_inflight or self.preproc_commit_timer.isActive():
            QMessageBox.information(
                self,
                "Preprocessing is updating",
                "Wait for the checked preprocessing steps to finish updating before fitting ICA.",
            )
            return
        try:
            n_components = self._parse_n_components()
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid components",
                "Enter an integer component count, a variance fraction such as 0.99, or leave blank.",
            )
            return

        if not self.ica_enabled.isChecked():
            self.ica_enabled.setChecked(True)
        self.preprocessing.ica.enabled = True
        self.preprocessing.ica.method = self.ica_method.currentText()
        self.preprocessing.ica.n_components = n_components
        decim = self.ica_decim.value() if self.ica_fast_fit.isChecked() else None
        self.preprocessing.ica.fit_decim = decim
        self.preprocessing.step_order = list(self._processing_order)

        self.run_ica_btn.setEnabled(False)
        self.remove_ica_components_btn.setEnabled(False)
        self._set_preproc_controls_enabled(False)
        self.status_label.setText("Fitting ICA …")
        self.ica_fit_progress.setVisible(True)
        duration_sec = float(self.processed_raw.n_times) / float(self.processed_raw.info["sfreq"]) if self.processed_raw.n_times else 0.0
        n_eeg = max(1, len(mne.pick_types(self.processed_raw.info, eeg=True, exclude=[])))
        decim_factor = max(1, int(decim or 1))
        # Deliberately labelled as an estimate: FastICA/Infomax convergence can vary substantially.
        estimate_sec = max(8.0, (duration_sec * n_eeg / 32.0) * 0.075 / math.sqrt(decim_factor))
        self._start_estimated_task("ICA fit", estimate_sec)
        worker = FunctionWorker(
            engine.run_ica,
            self.processed_raw,
            self.preprocessing.ica.method,
            n_components,
            self.preprocessing.ica.random_state,
            decim,
            copy.deepcopy(self._ica_fit_exclusions),
        )
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.error.connect(self._worker_error)
        worker.signals.result.connect(self._ica_fit_complete)
        worker.signals.finished.connect(self._ica_fit_finished)
        self.thread_pool.start(worker)

    def _ica_fit_finished(self):
        self.run_ica_btn.setEnabled(True)
        self.remove_ica_components_btn.setEnabled(
            self.ica is not None and bool(self.ica_view.excluded_components())
        )
        self._set_preproc_controls_enabled(True)
        if hasattr(self, "ica_fit_progress"):
            self.ica_fit_progress.setVisible(False)
        if hasattr(self, "ica_fit_eta"):
            self.ica_fit_eta.setText("")
        self._finish_estimated_task()

    def _ica_fit_complete(self, ica):
        self.ica = ica
        self._ica_applied = False
        self._pre_ica_raw = self.processed_raw
        self._ica_cleaned_raw = None
        self._ica_cleaned_excluded = []
        self._epoch_input_mode = "pre_ica"
        self.preprocessing.ica.epoch_input = "pre_ica"
        self.preprocessing.ica.excluded_components = []
        self.ica_view.set_ica(ica, self._pre_ica_raw, [])
        decim_text = (
            f"; fit decimation N={self.preprocessing.ica.fit_decim}"
            if self.preprocessing.ica.fit_decim
            else "; all samples used"
        )
        excluded_time = sum(float(x["end_sec"]) - float(x["start_sec"]) for x in self._ica_fit_exclusions)
        exclusion_text = (
            f"; omitted {len(self._ica_fit_exclusions)} marked fit span(s), {excluded_time:.2f} s total"
            if self._ica_fit_exclusions else "; no custom fit spans omitted"
        )
        self.status_label.setText(
            f"ICA fit complete — actual ICA count {ica.n_components_}, {ica.n_samples_} fit samples{decim_text}{exclusion_text}. Check components to remove."
        )
        self.preprocessing.ica.fit_exclude_spans = copy.deepcopy(self._ica_fit_exclusions)
        self.metadata.processing_log.append(
            f"ICA BETA fit: method={self.preprocessing.ica.method}, requested n_components={self.preprocessing.ica.n_components}, "
            f"actual={ica.n_components_}, fit exclusions={len(self._ica_fit_exclusions)} ({excluded_time:.3f} s)"
        )
        self.remove_ica_components_btn.setEnabled(False)
        self.ica_selection_status.setText("Selected for removal: none. Check a component first; checking alone does not reconstruct the EEG.")
        self.ica_workspace_tabs.setCurrentIndex(1)
        self.iclabel_status.setText("ICLabel: not run.")
        self.blink_aid_status.setText("Frontal blink correlation: calculating …")
        self._refresh_ica_version_controls()
        QTimer.singleShot(0, self.run_ica_blink_aid)

    def run_ica_blink_aid(self):
        raw = self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw
        if self.ica is None or raw is None:
            return
        self.blink_aid_status.setText("Blink correlation aid: calculating …")
        worker = FunctionWorker(
            engine.blink_component_correlations, raw, self.ica, copy.deepcopy(self._ica_fit_exclusions)
        )
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.result.connect(self._ica_blink_aid_complete)
        worker.signals.error.connect(self._ica_blink_aid_error)
        self.thread_pool.start(worker)

    def _ica_blink_aid_complete(self, result: dict):
        scores = result.get("scores", [])
        refs = result.get("reference_channel", [])
        self.ica_view.set_blink_scores(scores, refs)
        used = result.get("reference_channels", [])
        note = result.get("note", "")
        if used:
            best_text = ""
            finite = []
            for i, value in enumerate(scores):
                try:
                    value = float(value)
                except Exception:
                    continue
                if math.isfinite(value):
                    finite.append((value, i, str(refs[i]) if i < len(refs) else ""))
            if finite:
                best_value, best_comp, best_ref = max(finite, key=lambda x: x[0])
                best_text = (
                    f" Highest: ICA{best_comp:03d} |r|={best_value:.2f}"
                    + (f" with {best_ref}." if best_ref else ".")
                )
            self.blink_aid_status.setText(
                "Frontal blink correlation: " + ", ".join(used) + "." + best_text
            )
        else:
            self.blink_aid_status.setText(note or "Frontal blink correlation unavailable: no suitable frontal EEG channels were found.")
        self.status_label.setText("ICA component aids ready.")
        if self.ica_auto_label_after_fit.isChecked():
            QTimer.singleShot(0, self.run_ica_autolabel)

    def _ica_blink_aid_error(self, traceback_text: str):
        last_line = traceback_text.strip().splitlines()[-1] if traceback_text.strip() else "Blink correlation aid failed."
        self.blink_aid_status.setText(last_line)
        if self.ica_auto_label_after_fit.isChecked():
            QTimer.singleShot(0, self.run_ica_autolabel)

    def run_ica_autolabel(self):
        raw = self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw
        if self.ica is None or raw is None:
            QMessageBox.information(self, "ICA not fitted", "Fit ICA before running automatic component labeling.")
            return
        self.iclabel_status.setText("ICLabel is classifying components …")
        self.status_label.setText("Running ICLabel automatic ICA classification …")
        worker = FunctionWorker(engine.auto_label_ica_components, raw, self.ica)
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.result.connect(self._ica_autolabel_complete)
        worker.signals.error.connect(self._ica_autolabel_error)
        self.thread_pool.start(worker)

    def _ica_autolabel_complete(self, result: dict):
        labels = result.get("labels", [])
        probabilities = result.get("probabilities", [])
        self.ica_view.set_component_labels(labels, probabilities)
        counts = {}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        compatibility = [str(x) for x in result.get("warnings", []) if str(x).strip()]
        self.iclabel_status.setText("ICLabel complete" + (f" — {summary}." if summary else "."))
        self.iclabel_status.setToolTip(
            "ICLabel is a trained-model prediction and remains advisory. "
            "Always confirm candidate blink components with time-domain morphology and topographic distribution."
            + (("\n\nCompatibility notes:\n• " + "\n• ".join(compatibility)) if compatibility else "")
        )
        self.status_label.setText("ICLabel classification complete." + (" Compatibility notes are available on the ICLabel status tooltip." if compatibility else ""))

    def _ica_autolabel_error(self, traceback_text: str):
        last_line = traceback_text.strip().splitlines()[-1] if traceback_text.strip() else "ICLabel failed."
        self.iclabel_status.setText(last_line)
        self.status_label.setText("ICLabel classification failed; manual ICA review is still available.")
        if "update_dependencies_windows.bat" in traceback_text or "ICLabel support is not installed" in traceback_text:
            QMessageBox.information(
                self,
                "Install ICLabel support",
                "ICLabel support is unavailable in this development installation. Install the optional MNE-ICALabel dependency, then reopen ERP Workbench. Manual ICA review remains available.",
            )
        else:
            QMessageBox.warning(self, "ICLabel failed", last_line)

    def _invalidate_after_ica(self):
        # ICA changes EEG samples, not event timing. Keep the discovered event
        # timeline and only invalidate downstream epoch/ERP results.
        self.epochs = None
        self.review = EpochReviewState()
        self._epoch_decision_history = []
        self.clean_epochs = None
        self.evokeds = {}
        self.measurements = []
        self._refresh_event_table()
        self._refresh_epoch_event_status()
        self._refresh_epoch_screening_channels_label()
        self._refresh_epoch_preflight()
        self._refresh_review_table()
        self._refresh_result_table()

    # ---------- Epoching ----------
    def _refresh_epoch_input_status(self):
        if not hasattr(self, "epoch_input_status"):
            return
        raw = self._epoching_raw()
        if raw is None:
            self.epoch_input_status.setText("Epoching input: no processed EEG available yet.")
        elif self._epoch_input_mode == "ica_cleaned" and self._ica_cleaned_raw is not None:
            self.epoch_input_status.setText(
                f"Epoching input: ICA-cleaned EEG (BETA; removed components {self._ica_cleaned_excluded or 'none'}). "
                "The pre-ICA processed EEG is still preserved and can be selected in the ICA tab."
            )
        elif self._ica_cleaned_raw is not None:
            self.epoch_input_status.setText(
                "Epoching input: pre-ICA processed EEG. An ICA-cleaned reconstruction also exists but is not currently selected."
            )
        elif self.ica is not None:
            self.epoch_input_status.setText(
                "Epoching input: pre-ICA processed EEG. ICA is fitted, but no cleaned reconstruction has been created yet."
            )
        else:
            self.epoch_input_status.setText("Epoching input: current processed EEG; ICA has not been used.")

    def _build_epoch_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(6)

        protocol_grid = QGridLayout()
        protocol_grid.setHorizontalSpacing(8); protocol_grid.setVerticalSpacing(6)
        self.protocol_library_combo = QComboBox(); self.protocol_library_combo.setMinimumWidth(210)
        self.protocol_library_combo.currentIndexChanged.connect(self._protocol_library_selected)
        self.protocol_name_edit = QLineEdit(self.protocol.name); self.protocol_name_edit.setMinimumWidth(180)
        self.protocol_name_edit.setPlaceholderText("Protocol name")
        save_library_btn = QPushButton("Save to library"); save_library_btn.clicked.connect(self._save_protocol_to_library)
        load_btn = QPushButton("Load JSON…"); load_btn.clicked.connect(self.load_protocol_dialog)
        save_btn = QPushButton("Save JSON…"); save_btn.clicked.connect(self.save_protocol_dialog)
        protocol_channels_btn=QPushButton("Default channels…"); protocol_channels_btn.clicked.connect(self._select_protocol_display_channels)
        protocol_components_btn=QPushButton("ERP components…"); protocol_components_btn.clicked.connect(self._edit_protocol_components)
        protocol_grid.addWidget(QLabel("Protocol"),0,0); protocol_grid.addWidget(self.protocol_library_combo,0,1)
        protocol_grid.addWidget(QLabel("Name"),0,2); protocol_grid.addWidget(self.protocol_name_edit,0,3)
        protocol_grid.addWidget(save_library_btn,0,4)
        protocol_grid.addWidget(protocol_channels_btn,1,1); protocol_grid.addWidget(protocol_components_btn,1,2)
        protocol_grid.addWidget(load_btn,1,3); protocol_grid.addWidget(save_btn,1,4)
        protocol_grid.setColumnStretch(1,2); protocol_grid.setColumnStretch(3,2); protocol_grid.setColumnStretch(5,1)
        outer.addLayout(protocol_grid)
        protocol_meta=QHBoxLayout()
        self.protocol_channels_summary=QLabel("Default channels: all")
        self.protocol_components_summary=QLabel("ERP components: none")
        for label in (self.protocol_channels_summary,self.protocol_components_summary): label.setProperty("muted",True)
        protocol_meta.addWidget(self.protocol_channels_summary); protocol_meta.addSpacing(18); protocol_meta.addWidget(self.protocol_components_summary); protocol_meta.addStretch(1)
        outer.addLayout(protocol_meta)

        self.epoch_input_status = QLabel("Epoching input: no processed EEG available yet.")
        self.epoch_input_status.setWordWrap(True); self.epoch_input_status.setProperty("muted", True)
        outer.addWidget(self.epoch_input_status)
        self.epoch_event_status = QLabel(
            "No event timeline loaded. Attach the matching Annotation.txt in Continuous EEG; events will be discovered automatically."
        )
        self.epoch_event_status.setWordWrap(True); self.epoch_event_status.setObjectName("EpochEventStatus")
        outer.addWidget(self.epoch_event_status)

        self.epoch_subtabs = QTabWidget(); self.epoch_subtabs.setUsesScrollButtons(True)
        outer.addWidget(self.epoch_subtabs, 1)

        def scroll_page(content: QWidget) -> QScrollArea:
            area = QScrollArea(); area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setWidget(content)
            return area

        # ---- 1. Epoch timing / baseline / rejection only ----
        setup_content = QWidget(); setup = QVBoxLayout(setup_content)
        setup.setContentsMargins(8, 8, 8, 8); setup.setSpacing(12)
        cards = QHBoxLayout(); cards.setSpacing(14)

        window_group = QGroupBox("Epoch window + baseline")
        window_layout = QVBoxLayout(window_group)
        mode_row=QHBoxLayout()
        self.epoch_window_mode=QComboBox(); self.epoch_window_mode.addItem("Pre-stimulus + epoch duration", "pre_duration"); self.epoch_window_mode.addItem("Epoch start + epoch end", "start_end")
        mode_row.addWidget(QLabel("Define window as")); mode_row.addWidget(self.epoch_window_mode); mode_row.addStretch(1)
        window_layout.addLayout(mode_row)
        self.window_mode_stack=QStackedWidget()

        pre_duration_page=QWidget(); pd=QFormLayout(pre_duration_page)
        self.pre_stimulus=ReliableDoubleSpinBox(); self.pre_stimulus.setRange(0,5000); self.pre_stimulus.setValue(200); self.pre_stimulus.setSuffix(" ms"); self.pre_stimulus.setMinimumWidth(130)
        self.epoch_duration=ReliableDoubleSpinBox(); self.epoch_duration.setRange(1,15000); self.epoch_duration.setValue(1200); self.epoch_duration.setSuffix(" ms"); self.epoch_duration.setMinimumWidth(130)
        pd.addRow("Pre-stimulus", self.pre_stimulus); pd.addRow("Epoch duration", self.epoch_duration)
        self.window_mode_stack.addWidget(pre_duration_page)

        start_end_page=QWidget(); se=QFormLayout(start_end_page)
        self.tmin = ReliableDoubleSpinBox(); self.tmin.setRange(-5000, 5000); self.tmin.setValue(-200); self.tmin.setSuffix(" ms"); self.tmin.setMinimumWidth(130)
        self.tmax = ReliableDoubleSpinBox(); self.tmax.setRange(-5000, 10000); self.tmax.setValue(1000); self.tmax.setSuffix(" ms"); self.tmax.setMinimumWidth(130)
        se.addRow("Epoch start", self.tmin); se.addRow("Epoch end", self.tmax)
        self.window_mode_stack.addWidget(start_end_page)
        window_layout.addWidget(self.window_mode_stack)

        self.baseline_check = QCheckBox("Baseline correction"); self.baseline_check.setChecked(True)
        window_layout.addWidget(self.baseline_check)
        base = QWidget(); bl=QHBoxLayout(base); bl.setContentsMargins(0,0,0,0); bl.setSpacing(6)
        self.baseline_start = ReliableDoubleSpinBox(); self.baseline_start.setRange(-5000,5000); self.baseline_start.setValue(-200); self.baseline_start.setSuffix(" ms")
        self.baseline_end = ReliableDoubleSpinBox(); self.baseline_end.setRange(-5000,5000); self.baseline_end.setValue(0); self.baseline_end.setSuffix(" ms")
        bl.addWidget(QLabel("Baseline")); bl.addWidget(self.baseline_start); bl.addWidget(QLabel("to")); bl.addWidget(self.baseline_end); bl.addStretch(1)
        window_layout.addWidget(base)

        rejection_group = QGroupBox("Automatic artifact screening")
        rejection_grid = QGridLayout(rejection_group); rejection_grid.setHorizontalSpacing(12); rejection_grid.setVerticalSpacing(9)
        self.abs_reject_check = QCheckBox("Absolute amplitude"); self.abs_reject_check.setChecked(True)
        self.abs_threshold = ReliableDoubleSpinBox(); self.abs_threshold.setRange(1,5000); self.abs_threshold.setValue(75); self.abs_threshold.setSuffix(" µV"); self.abs_threshold.setMinimumWidth(125)
        self.p2p_check = QCheckBox("Peak-to-peak")
        self.p2p_threshold = ReliableDoubleSpinBox(); self.p2p_threshold.setRange(1,5000); self.p2p_threshold.setValue(150); self.p2p_threshold.setSuffix(" µV"); self.p2p_threshold.setMinimumWidth(125)
        self.flat_check = QCheckBox("Flat channel")
        self.flat_threshold = ReliableDoubleSpinBox(); self.flat_threshold.setRange(0.001,100); self.flat_threshold.setDecimals(3); self.flat_threshold.setValue(0.5); self.flat_threshold.setSuffix(" µV p-p"); self.flat_threshold.setMinimumWidth(125)
        rejection_grid.addWidget(self.abs_reject_check,0,0); rejection_grid.addWidget(self.abs_threshold,0,1)
        rejection_grid.addWidget(self.p2p_check,1,0); rejection_grid.addWidget(self.p2p_threshold,1,1)
        rejection_grid.addWidget(self.flat_check,2,0); rejection_grid.addWidget(self.flat_threshold,2,1)
        self.screen_channels_label = QLabel("All EEG channels"); self.screen_channels_label.setWordWrap(True)
        screen_channels_btn = QPushButton("Screening channels…"); screen_channels_btn.clicked.connect(self._select_epoch_screening_channels)
        sr=QWidget(); sl=QHBoxLayout(sr); sl.setContentsMargins(0,0,0,0); sl.addWidget(screen_channels_btn); sl.addWidget(self.screen_channels_label,1)
        rejection_grid.addWidget(QLabel("Channels"),3,0); rejection_grid.addWidget(sr,3,1)
        note=QLabel("These criteria flag epochs for review; they do not permanently delete them. Peak-to-peak is max−min within each channel over the epoch.")
        note.setWordWrap(True); note.setProperty("muted",True); rejection_grid.addWidget(note,4,0,1,2)
        self.p2p_relation_note=QLabel(""); self.p2p_relation_note.setWordWrap(True); self.p2p_relation_note.setProperty("muted",True); rejection_grid.addWidget(self.p2p_relation_note,5,0,1,2)

        cards.addWidget(window_group,1); cards.addWidget(rejection_group,1)
        setup.addLayout(cards); setup.addStretch(1)
        self.epoch_subtabs.addTab(scroll_page(setup_content), "1  Window + rejection")

        # ---- 2. Event source / annotation groups / raw names ----
        events_content=QWidget(); events_layout=QVBoxLayout(events_content); events_layout.setContentsMargins(8,8,8,8); events_layout.setSpacing(10)
        source_group=QGroupBox("Event source")
        source_form=QFormLayout(source_group)
        self.event_source=QComboBox(); self.event_source.addItems(["annotations","stim"]); self.event_source.setMinimumWidth(145)
        self.stim_channel=QLineEdit(); self.stim_channel.setPlaceholderText("blank = MNE auto-detect")
        source_form.addRow("Source", self.event_source); source_form.addRow("Stim channel", self.stim_channel)
        events_layout.addWidget(source_group)

        grouping=QGroupBox("ERP conditions from annotation names")
        grouping_layout=QVBoxLayout(grouping)
        group_entry=QHBoxLayout()
        self.group_pattern=QLineEdit(); self.group_pattern.setPlaceholderText("e.g. Neu")
        self.group_condition=QLineEdit(); self.group_condition.setPlaceholderText("e.g. Neutral")
        self.group_case_sensitive=QCheckBox("Case sensitive")
        self.group_starts_with=QCheckBox("Starts with")
        preview_group_btn=QPushButton("Preview"); preview_group_btn.clicked.connect(self._preview_event_group)
        add_group_btn=QPushButton("Add / update"); add_group_btn.clicked.connect(self._add_event_group)
        remove_group_btn=QPushButton("Remove"); remove_group_btn.clicked.connect(self._remove_event_group)
        group_entry.addWidget(QLabel("Find")); group_entry.addWidget(self.group_pattern,2); group_entry.addWidget(QLabel("→")); group_entry.addWidget(self.group_condition,2)
        group_entry.addWidget(self.group_case_sensitive); group_entry.addWidget(self.group_starts_with); group_entry.addWidget(preview_group_btn); group_entry.addWidget(add_group_btn); group_entry.addWidget(remove_group_btn)
        grouping_layout.addLayout(group_entry)
        self.group_table=QTableWidget(0,7); self.group_table.setHorizontalHeaderLabels(["Use","Common string","Condition","Case sensitive","Starts with","Unique names","Markers"])
        self.group_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); self.group_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.group_table.verticalHeader().setVisible(False); self.group_table.setMinimumHeight(180); self.group_table.itemChanged.connect(self._event_group_table_changed)
        grouping_layout.addWidget(self.group_table,1); events_layout.addWidget(grouping,1)

        raw_events_group=QGroupBox("Raw annotation names / manual mapping")
        raw_layout=QVBoxLayout(raw_events_group)
        self.event_table=QTableWidget(0,5); self.event_table.setHorizontalHeaderLabels(["Use","Event code","File label","Markers","Condition name"])
        self.event_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch); self.event_table.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch)
        self.event_table.verticalHeader().setVisible(False); self.event_table.setMinimumHeight(220); self.event_table.itemChanged.connect(self._event_table_changed)
        raw_layout.addWidget(self.event_table,1); events_layout.addWidget(raw_events_group,1)
        self.epoch_subtabs.addTab(scroll_page(events_content), "2  ERP events")

        # ---- 3. Per-group stimulus exclusions + preview / cut ----
        preview_content=QWidget(); preview_layout=QVBoxLayout(preview_content); preview_layout.setContentsMargins(8,8,8,8); preview_layout.setSpacing(10)
        summary_group=QGroupBox("Current epoch protocol preview")
        summary_layout=QVBoxLayout(summary_group)
        self.epoch_preview_summary=QLabel(""); self.epoch_preview_summary.setWordWrap(True)
        summary_layout.addWidget(self.epoch_preview_summary)
        preview_layout.addWidget(summary_group)

        stimulus_box=QGroupBox("Stimuli included within a grouped condition")
        stimulus_layout=QVBoxLayout(stimulus_box)
        stim_row=QHBoxLayout()
        self.preview_group_combo=QComboBox(); self.preview_group_combo.currentIndexChanged.connect(self._refresh_stimulus_exclusion_table)
        stim_row.addWidget(QLabel("Condition group")); stim_row.addWidget(self.preview_group_combo,1)
        stimulus_layout.addLayout(stim_row)
        stim_note=QLabel("Uncheck an exact stimulus/annotation label to exclude it from epoch preview and cutting while keeping the broad string-group rule unchanged.")
        stim_note.setWordWrap(True); stim_note.setProperty("muted",True); stimulus_layout.addWidget(stim_note)
        self.stimulus_exclusion_table=QTableWidget(0,3); self.stimulus_exclusion_table.setHorizontalHeaderLabels(["Include","Stimulus / annotation label","Markers"])
        self.stimulus_exclusion_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); self.stimulus_exclusion_table.verticalHeader().setVisible(False)
        self.stimulus_exclusion_table.setMinimumHeight(180); self.stimulus_exclusion_table.itemChanged.connect(self._stimulus_exclusion_changed)
        stimulus_layout.addWidget(self.stimulus_exclusion_table)
        preview_layout.addWidget(stimulus_box)

        plan_group=QGroupBox("Epoch cutting preview")
        plan_layout=QVBoxLayout(plan_group)
        plan_buttons=QHBoxLayout()
        rescan_btn=QPushButton("Re-scan events"); rescan_btn.clicked.connect(self.discover_events)
        refresh_plan_btn=QPushButton("Refresh preview"); refresh_plan_btn.clicked.connect(self._refresh_epoch_preflight)
        create_btn=QPushButton("Create epochs + auto-screen"); create_btn.clicked.connect(self.create_epochs)
        plan_buttons.addWidget(rescan_btn); plan_buttons.addWidget(refresh_plan_btn); plan_buttons.addWidget(create_btn); plan_buttons.addStretch(1)
        plan_layout.addLayout(plan_buttons)
        self.epoch_plan_status=QLabel("No ERP conditions selected yet."); self.epoch_plan_status.setWordWrap(True); plan_layout.addWidget(self.epoch_plan_status)
        self.epoch_plan_table=QTableWidget(0,4); self.epoch_plan_table.setHorizontalHeaderLabels(["Condition","Selected markers","Can be cut","Boundary excluded"])
        self.epoch_plan_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for col in (1,2,3): self.epoch_plan_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        self.epoch_plan_table.verticalHeader().setVisible(False); self.epoch_plan_table.setMinimumHeight(230)
        plan_layout.addWidget(self.epoch_plan_table,1); preview_layout.addWidget(plan_group,1)
        self.epoch_subtabs.addTab(scroll_page(preview_content), "3  Preview + cut")

        self._epoch_window_sync_guard=False
        self.epoch_window_mode.currentIndexChanged.connect(self._epoch_window_mode_changed)
        self.pre_stimulus.valueChanged.connect(self._pre_duration_changed)
        self.epoch_duration.valueChanged.connect(self._pre_duration_changed)
        self.tmin.valueChanged.connect(self._start_end_changed); self.tmax.valueChanged.connect(self._start_end_changed)
        for widget in (self.baseline_start,self.baseline_end):
            widget.valueChanged.connect(lambda _=None: self._refresh_epoch_preflight())
        self.baseline_check.toggled.connect(lambda _=None: self._refresh_epoch_preflight())
        self.event_source.currentTextChanged.connect(self._event_source_changed)
        self.abs_reject_check.toggled.connect(self._update_p2p_relationship_note); self.p2p_check.toggled.connect(self._update_p2p_relationship_note)
        self.abs_threshold.valueChanged.connect(lambda _=None: self._update_p2p_relationship_note()); self.p2p_threshold.valueChanged.connect(lambda _=None: self._update_p2p_relationship_note())
        for w in (self.abs_reject_check,self.abs_threshold,self.p2p_check,self.p2p_threshold,self.flat_check,self.flat_threshold):
            if hasattr(w,"toggled"): w.toggled.connect(lambda *_: self._refresh_epoch_preflight())
            if hasattr(w,"valueChanged"): w.valueChanged.connect(lambda *_: self._refresh_epoch_preflight())
        self._update_p2p_relationship_note()

        self.tabs.addTab(page,"3  Epoching")
        self._refresh_protocol_library(select_name=self.protocol.name)
        self._load_protocol_into_ui(self.protocol)
        self._refresh_epoch_input_status()

    def _epoch_window_mode_changed(self, index: int):
        if hasattr(self, "window_mode_stack"):
            self.window_mode_stack.setCurrentIndex(max(0, min(1, int(index))))
        self._refresh_epoch_preflight()

    def _pre_duration_changed(self, *_):
        if getattr(self, "_epoch_window_sync_guard", False):
            return
        self._epoch_window_sync_guard = True
        try:
            pre = float(self.pre_stimulus.value())
            duration = float(self.epoch_duration.value())
            self.tmin.setValue(-pre)
            self.tmax.setValue(-pre + duration)
        finally:
            self._epoch_window_sync_guard = False
        self._refresh_epoch_preflight()

    def _start_end_changed(self, *_):
        if getattr(self, "_epoch_window_sync_guard", False):
            return
        self._epoch_window_sync_guard = True
        try:
            start = float(self.tmin.value()); end = float(self.tmax.value())
            self.pre_stimulus.setValue(max(0.0, -start))
            self.epoch_duration.setValue(max(1.0, end - start))
        finally:
            self._epoch_window_sync_guard = False
        self._refresh_epoch_preflight()

    @staticmethod
    def _safe_protocol_filename(name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).strip()).strip("._")
        return safe or "ERP_protocol"

    def _refresh_protocol_library(self, select_name: str | None = None):
        if not hasattr(self, "protocol_library_combo"):
            return
        combo = self.protocol_library_combo
        self._protocol_combo_guard = True
        try:
            combo.clear()
            combo.addItem("New protocol", {"kind": "new"})
            saved_entries=[]
            for path in sorted(self._protocol_library_dir.glob("*.json"), key=lambda x: x.name.casefold()):
                try:
                    loaded=load_protocol(path); display=loaded.name or path.stem
                except Exception:
                    display=path.stem
                saved_entries.append((display,path))
            for display,path in saved_entries:
                combo.addItem(f"Saved · {display}", {"kind": "saved", "path": str(path), "name": display})
            target=-1
            if select_name:
                for i in range(combo.count()):
                    data=combo.itemData(i) or {}
                    if data.get("kind")=="saved" and data.get("name")==select_name:
                        target=i; break
            combo.setCurrentIndex(target if target>=0 else 0)
        finally:
            self._protocol_combo_guard = False

    def _protocol_library_selected(self, _index: int):
        if getattr(self, "_protocol_combo_guard", False):
            return
        data=self.protocol_library_combo.currentData() or {"kind":"new"}
        kind=data.get("kind")
        try:
            if kind=="saved":
                self.protocol=load_protocol(Path(str(data.get("path"))))
            else:
                self.protocol=ProtocolDefinition(name="New protocol")
            self._load_protocol_into_ui(self.protocol)
            self.status_label.setText(f"Protocol selected: {self.protocol.name}")
        except Exception as exc:
            QMessageBox.critical(self,"Protocol load failed",str(exc))

    def _save_protocol_to_library(self):
        self._sync_protocol_from_ui()
        name=self.protocol_name_edit.text().strip() or self.protocol.name or "ERP protocol"
        self.protocol.name=name
        path=self._protocol_library_dir / f"{self._safe_protocol_filename(name)}.json"
        if not self._confirm_overwrite(path):
            return
        try:
            save_protocol(self.protocol,path)
        except Exception as exc:
            QMessageBox.critical(self,"Protocol save failed",str(exc)); return
        self._refresh_protocol_library(select_name=name)
        self.status_label.setText(f"Saved protocol to library: {path.name}")

    def _refresh_stimulus_exclusion_groups(self):
        if not hasattr(self,"preview_group_combo"):
            return
        current=str(self.preview_group_combo.currentData() or "")
        conditions=[]
        for rule in self._event_group_rules_from_table():
            if rule.enabled and rule.condition.strip() and rule.condition.strip() not in conditions:
                conditions.append(rule.condition.strip())
        self.preview_group_combo.blockSignals(True)
        self.preview_group_combo.clear()
        for condition in conditions:
            self.preview_group_combo.addItem(condition,condition)
        idx=self.preview_group_combo.findData(current) if current else -1
        self.preview_group_combo.setCurrentIndex(idx if idx>=0 else (0 if conditions else -1))
        self.preview_group_combo.blockSignals(False)
        self._refresh_stimulus_exclusion_table()

    def _refresh_stimulus_exclusion_table(self, *_):
        if not hasattr(self,"stimulus_exclusion_table"):
            return
        condition=str(self.preview_group_combo.currentData() or "") if hasattr(self,"preview_group_combo") else ""
        rules=self._event_group_rules_from_table()
        counts=engine.event_code_counts(self.events)
        by_label={}
        if condition:
            for code,label in self.event_labels.items():
                if engine.resolve_event_group_condition(label,rules)==condition:
                    by_label[str(label)]=by_label.get(str(label),0)+int(counts.get(int(code),0))
        excluded=set(str(x) for x in self.protocol.excluded_event_labels.get(condition,[]))
        self._stimulus_exclusion_guard=True
        try:
            self.stimulus_exclusion_table.setRowCount(len(by_label))
            for row,(label,count) in enumerate(sorted(by_label.items(), key=lambda kv: kv[0].casefold())):
                use=QTableWidgetItem(); use.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsSelectable)
                use.setCheckState(Qt.CheckState.Unchecked if label in excluded else Qt.CheckState.Checked)
                self.stimulus_exclusion_table.setItem(row,0,use)
                label_item=QTableWidgetItem(label); label_item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable); self.stimulus_exclusion_table.setItem(row,1,label_item)
                count_item=QTableWidgetItem(str(count)); count_item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable); self.stimulus_exclusion_table.setItem(row,2,count_item)
        finally:
            self._stimulus_exclusion_guard=False

    def _stimulus_exclusion_changed(self, item=None):
        if getattr(self,"_stimulus_exclusion_guard",False) or not hasattr(self,"preview_group_combo"):
            return
        condition=str(self.preview_group_combo.currentData() or "")
        if not condition:
            return
        current_labels=set()
        unchecked=set()
        for row in range(self.stimulus_exclusion_table.rowCount()):
            use=self.stimulus_exclusion_table.item(row,0); lab=self.stimulus_exclusion_table.item(row,1)
            if not lab: continue
            label=lab.text(); current_labels.add(label)
            if use and use.checkState()!=Qt.CheckState.Checked:
                unchecked.add(label)
        old=set(str(x) for x in self.protocol.excluded_event_labels.get(condition,[]))
        preserved=old-current_labels
        new=sorted(preserved|unchecked)
        if new:
            self.protocol.excluded_event_labels[condition]=new
        else:
            self.protocol.excluded_event_labels.pop(condition,None)
        self._refresh_epoch_preflight()

    def _update_epoch_preview_summary(self):
        if not hasattr(self,"epoch_preview_summary"):
            return
        start=float(self.tmin.value()); end=float(self.tmax.value()); duration=end-start
        baseline=(f"{self.baseline_start.value():g} to {self.baseline_end.value():g} ms" if self.baseline_check.isChecked() else "off")
        criteria=[]
        if self.abs_reject_check.isChecked(): criteria.append(f"absolute ±{self.abs_threshold.value():g} µV")
        if self.p2p_check.isChecked(): criteria.append(f"p-p {self.p2p_threshold.value():g} µV")
        if self.flat_check.isChecked(): criteria.append(f"flat <{self.flat_threshold.value():g} µV p-p")
        screening=(", ".join(self._epoch_screening_channels) if self._epoch_screening_channels else "all EEG channels")
        active_conditions={r.condition.strip() for r in self._event_group_rules_from_table() if r.enabled and r.condition.strip()}
        excluded=sum(len(v) for k,v in self.protocol.excluded_event_labels.items() if str(k) in active_conditions)
        self.epoch_preview_summary.setText(
            f"Epoch: {start:g} to {end:g} ms ({duration:g} ms total) · baseline: {baseline} · "
            f"automatic screening: {', '.join(criteria) if criteria else 'off'} · screening channels: {screening} · "
            f"exact grouped stimulus labels excluded: {excluded}."
        )

    def _update_p2p_relationship_note(self):
        if not hasattr(self, "p2p_relation_note"):
            return
        if self.abs_reject_check.isChecked() and self.p2p_check.isChecked():
            a = float(self.abs_threshold.value()); p = float(self.p2p_threshold.value())
            if p >= 2.0 * a:
                self.p2p_relation_note.setText(
                    f"Note: with absolute ±{a:g} µV and peak-to-peak {p:g} µV, a p-p exceedance cannot be unique: "
                    f"p-p > {p:g} µV necessarily implies at least one sample beyond ±{a:g} µV. Both criteria are still evaluated and logged."
                )
                return
        self.p2p_relation_note.setText("Peak-to-peak is evaluated independently whenever its checkbox is enabled.")

    def _available_epoch_eeg_channels(self) -> list[str]:
        raw = self._epoching_raw() if self._epoching_raw() is not None else self.original_raw
        if raw is None:
            return []
        picks = mne.pick_types(raw.info, eeg=True, exclude=[])
        return [raw.ch_names[int(i)] for i in picks]

    def _refresh_protocol_summaries(self):
        if hasattr(self, "protocol_channels_summary"):
            channels=list(getattr(self.protocol,"display_channels",[]) or [])
            self.protocol_channels_summary.setText("Default channels: " + (", ".join(channels) if channels else "all"))
        if hasattr(self, "protocol_components_summary"):
            comps=list(getattr(self.protocol,"components",[]) or [])
            self.protocol_components_summary.setText("ERP components: " + (", ".join(c.name for c in comps) if comps else "none"))

    def _select_protocol_display_channels(self):
        channels=self._available_epoch_eeg_channels()
        if not channels:
            QMessageBox.information(self,"No EEG channels","Load a recording before choosing protocol display channels. Existing channel names stored in a loaded protocol are preserved.")
            return
        configured=[ch for ch in (getattr(self.protocol,"display_channels",[]) or []) if ch in channels]
        initial=configured or channels
        dialog=BadChannelDialog(channels,initial,self,title="Default channels for this protocol")
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        selected=dialog.selected_channels()
        if not selected:
            QMessageBox.information(self,"Choose at least one channel","At least one display channel must be selected.")
            return
        self.protocol.display_channels=[] if len(selected)==len(channels) else list(selected)
        self._apply_protocol_default_channels()
        self._refresh_protocol_summaries()

    def _edit_protocol_components(self):
        dialog=ComponentPlanDialog(list(getattr(self.protocol,"components",[]) or []),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        self.protocol.components=dialog.components()
        if hasattr(self,"component_table"):
            self._load_components_table(self.protocol.components)
        if hasattr(self,"grand_component_table") and not self.grand_evokeds:
            self._load_grand_components_table(self.protocol.components)
        self._refresh_protocol_summaries()

    def _apply_protocol_default_channels(self):
        configured=list(getattr(self.protocol,"display_channels",[]) or [])
        available=self._available_epoch_eeg_channels()
        if configured:
            chosen=[ch for ch in configured if not available or ch in available]
            if available and not chosen:
                # Preserve the protocol names but do not silently hide every channel on an incompatible file.
                chosen=list(available)
        else:
            chosen=list(available)
        if chosen:
            self._preferred_display_channels=list(chosen)
            for viewer_name in ("raw_viewer","ica_fit_view","ica_post_view","epoch_viewer"):
                viewer=getattr(self,viewer_name,None)
                if viewer is not None and hasattr(viewer,"set_selected_channels"):
                    try: viewer.set_selected_channels(chosen)
                    except Exception: pass
            self._erp_display_channels=list(chosen)
            if self.grand_evokeds: self._grand_display_channels=list(chosen)
        self._refresh_protocol_summaries()

    def _refresh_epoch_screening_channels_label(self):
        if not hasattr(self, "screen_channels_label"):
            return
        available = self._available_epoch_eeg_channels()
        selected = [ch for ch in self._epoch_screening_channels if ch in available]
        if not self._epoch_screening_channels:
            self.screen_channels_label.setText("All EEG channels" if available else "All EEG channels (recording not loaded)")
        else:
            self.screen_channels_label.setText(
                f"{len(selected)} selected: " + ", ".join(selected)
                if selected else "Configured channels are not present in this recording"
            )

    def _select_epoch_screening_channels(self):
        channels = self._available_epoch_eeg_channels()
        if not channels:
            QMessageBox.information(self, "No EEG channels", "Load an EDF/FIF recording before choosing epoch-screening channels.")
            return
        initial = self._epoch_screening_channels or channels
        dialog = BadChannelDialog(
            channels, initial, self,
            title="Select channels for automatic epoch screening",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_channels()
        if not selected:
            QMessageBox.information(self, "Choose at least one channel", "Automatic screening needs at least one EEG channel. Nothing was changed.")
            return
        # Store [] when all are selected so protocols remain portable across
        # recordings that have the same paradigm but a different channel count.
        self._epoch_screening_channels = [] if len(selected) == len(channels) else selected
        self.protocol.epoch.rejection_channels = list(self._epoch_screening_channels)
        self._refresh_epoch_screening_channels_label()
        self._refresh_epoch_preflight()
        self.status_label.setText(
            "Automatic epoch screening: all EEG channels" if not self._epoch_screening_channels
            else f"Automatic epoch screening: {len(self._epoch_screening_channels)} selected channel(s)."
        )

    def _builtin_protocol_selected(self, name: str):
        # Built-in analysis protocols were removed from 1.0; protocols are user-defined JSON/library entries.
        return

    def _load_protocol_into_ui(self, protocol: ProtocolDefinition):
        ep = protocol.epoch
        self._epoch_window_sync_guard = True
        try:
            self.tmin.setValue(ep.tmin_ms); self.tmax.setValue(ep.tmax_ms)
            self.pre_stimulus.setValue(max(0.0, -float(ep.tmin_ms)))
            self.epoch_duration.setValue(max(1.0, float(ep.tmax_ms) - float(ep.tmin_ms)))
        finally:
            self._epoch_window_sync_guard = False
        self.protocol_name_edit.setText(protocol.name)
        self.baseline_check.setChecked(ep.baseline_enabled)
        self.baseline_start.setValue(ep.baseline_start_ms if ep.baseline_start_ms is not None else ep.tmin_ms)
        self.baseline_end.setValue(ep.baseline_end_ms if ep.baseline_end_ms is not None else 0)
        self.event_source.setCurrentText(ep.event_source)
        self.stim_channel.setText(ep.stim_channel)
        self.abs_reject_check.setChecked(ep.absolute_threshold_uv is not None)
        if ep.absolute_threshold_uv is not None: self.abs_threshold.setValue(ep.absolute_threshold_uv)
        self.p2p_check.setChecked(ep.p2p_threshold_uv is not None)
        if ep.p2p_threshold_uv is not None: self.p2p_threshold.setValue(ep.p2p_threshold_uv)
        self.flat_check.setChecked(ep.flat_threshold_uv is not None)
        if ep.flat_threshold_uv is not None: self.flat_threshold.setValue(ep.flat_threshold_uv)
        self._epoch_screening_channels = list(getattr(ep, "rejection_channels", []) or [])
        self._refresh_epoch_screening_channels_label()
        self._load_components_table(protocol.components)
        self._apply_protocol_default_channels()
        self._refresh_protocol_summaries()
        self._load_event_groups_table(protocol.event_groups)
        self._refresh_event_table()
        self._refresh_stimulus_exclusion_groups()
        self._refresh_epoch_preflight()

    def _sync_protocol_from_ui(self):
        self.protocol.name = self.protocol_name_edit.text().strip() or self.protocol.name or "ERP protocol"
        ep = self.protocol.epoch
        ep.tmin_ms = self.tmin.value(); ep.tmax_ms = self.tmax.value()
        ep.baseline_enabled = self.baseline_check.isChecked()
        ep.baseline_start_ms = self.baseline_start.value() if ep.baseline_enabled else None
        ep.baseline_end_ms = self.baseline_end.value() if ep.baseline_enabled else None
        ep.event_source = self.event_source.currentText(); ep.stim_channel = self.stim_channel.text().strip()
        ep.absolute_threshold_uv = self.abs_threshold.value() if self.abs_reject_check.isChecked() else None
        ep.p2p_threshold_uv = self.p2p_threshold.value() if self.p2p_check.isChecked() else None
        ep.flat_threshold_uv = self.flat_threshold.value() if self.flat_check.isChecked() else None
        ep.rejection_channels = list(self._epoch_screening_channels)
        self.protocol.event_groups = self._event_group_rules_from_table()
        active_group_conditions = {r.condition.strip() for r in self.protocol.event_groups if r.enabled and r.condition.strip()}
        self.protocol.excluded_event_labels = {
            str(condition): list(labels)
            for condition, labels in self.protocol.excluded_event_labels.items()
            if str(condition) in active_group_conditions and labels
        }
        event_map = {}
        for row in range(self.event_table.rowCount()):
            use = self.event_table.item(row, 0)
            code_item = self.event_table.item(row, 1)
            label_item = self.event_table.item(row, 2)
            cond_item = self.event_table.item(row, 4)
            if not (use and use.checkState() == Qt.CheckState.Checked and code_item and cond_item and cond_item.text().strip()):
                continue
            label = label_item.text() if label_item else ""
            # A code assigned by a reusable string group does not need a fragile
            # per-recording integer mapping saved into the protocol.
            if engine.resolve_event_group_condition(label, self.protocol.event_groups):
                continue
            event_map[code_item.text().strip()] = cond_item.text().strip()
        self.protocol.event_map = event_map
        self.protocol.components = self._components_from_table()

    def _event_group_rules_from_table(self) -> list[EventGroupRule]:
        if not hasattr(self, "group_table"):
            return list(getattr(self.protocol, "event_groups", []))
        rules: list[EventGroupRule] = []
        for row in range(self.group_table.rowCount()):
            use = self.group_table.item(row, 0)
            pattern = self.group_table.item(row, 1)
            condition = self.group_table.item(row, 2)
            case_item = self.group_table.item(row, 3)
            starts_item = self.group_table.item(row, 4)
            if not pattern or not condition or not pattern.text().strip() or not condition.text().strip():
                continue
            rules.append(EventGroupRule(
                pattern=pattern.text().strip(),
                condition=condition.text().strip(),
                case_sensitive=(case_item.text().strip().lower() == "yes") if case_item else False,
                starts_with=(starts_item.text().strip().lower() == "yes") if starts_item else False,
                enabled=(use.checkState() == Qt.CheckState.Checked) if use else True,
            ))
        return rules

    def _load_event_groups_table(self, rules: list[EventGroupRule]):
        if not hasattr(self, "group_table"):
            return
        self._event_group_table_guard = True
        try:
            self.group_table.setRowCount(len(rules))
            for row, rule in enumerate(rules):
                use = QTableWidgetItem()
                use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                use.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
                self.group_table.setItem(row, 0, use)
                self.group_table.setItem(row, 1, QTableWidgetItem(rule.pattern))
                self.group_table.setItem(row, 2, QTableWidgetItem(rule.condition))
                case_item = QTableWidgetItem("Yes" if rule.case_sensitive else "No")
                case_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.group_table.setItem(row, 3, case_item)
                starts_item = QTableWidgetItem("Yes" if getattr(rule, "starts_with", False) else "No")
                starts_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.group_table.setItem(row, 4, starts_item)
                for col in (5, 6):
                    item = QTableWidgetItem("0")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.group_table.setItem(row, col, item)
        finally:
            self._event_group_table_guard = False
        self._refresh_event_group_counts()
        self._refresh_stimulus_exclusion_groups()

    def _event_group_table_changed(self, _item=None):
        if self._event_group_table_guard:
            return
        self.protocol.event_groups = self._event_group_rules_from_table()
        self._refresh_event_group_counts()
        self._refresh_event_table()
        self._refresh_stimulus_exclusion_groups()
        self._refresh_epoch_preflight()

    def _refresh_event_group_counts(self):
        if not hasattr(self, "group_table"):
            return
        rules = self._event_group_rules_from_table()
        stats = engine.event_group_stats(self.event_labels, self.events, rules)
        self._event_group_table_guard = True
        try:
            for row, stat in enumerate(stats):
                if row >= self.group_table.rowCount():
                    break
                self.group_table.item(row, 5).setText(str(stat["unique_labels"]))
                self.group_table.item(row, 6).setText(str(stat["markers"]))
        finally:
            self._event_group_table_guard = False

    def _preview_event_group(self):
        pattern = self.group_pattern.text().strip()
        if not pattern:
            QMessageBox.information(self, "Common string", "Type a string to search for, for example Neu_Red.")
            return
        rule = EventGroupRule(
            pattern=pattern,
            condition=self.group_condition.text().strip() or pattern,
            case_sensitive=self.group_case_sensitive.isChecked(),
            starts_with=self.group_starts_with.isChecked(),
            enabled=True,
        )
        stat = engine.event_group_stats(self.event_labels, self.events, [rule])[0]
        examples = [
            label for _, label in self.event_labels.items() if engine.event_group_match(label, rule)
        ][:5]
        example_text = " | ".join(examples) if examples else "No current event names match."
        mode_text = "starts with" if rule.starts_with else "contains"
        self.group_match_label.setText(
            f"Match preview ({mode_text}): {stat['unique_labels']} unique event name(s), {stat['markers']} marker(s). "
            f"Examples: {example_text}"
        )

    def _add_event_group(self):
        pattern = self.group_pattern.text().strip()
        condition = self.group_condition.text().strip() or pattern
        if not pattern:
            QMessageBox.information(self, "Common string", "Type the common string that identifies this condition.")
            return
        rules = self._event_group_rules_from_table()
        replacement = EventGroupRule(
            pattern=pattern, condition=condition,
            case_sensitive=self.group_case_sensitive.isChecked(),
            starts_with=self.group_starts_with.isChecked(), enabled=True,
        )
        updated = False
        for i, rule in enumerate(rules):
            if (
                rule.pattern.casefold() == pattern.casefold()
                and rule.case_sensitive == replacement.case_sensitive
                and getattr(rule, "starts_with", False) == replacement.starts_with
            ):
                rules[i] = replacement
                updated = True
                break
        if not updated:
            rules.append(replacement)
        self.protocol.event_groups = rules
        self._load_event_groups_table(rules)
        self._refresh_event_table()
        self._refresh_epoch_preflight()
        self._preview_event_group()
        match_wording = "starting with" if replacement.starts_with else "containing"
        self.status_label.setText(
            f"String group {'updated' if updated else 'added'}: names {match_wording} {pattern!r} → {condition!r}."
        )

    def _remove_event_group(self):
        row = self.group_table.currentRow() if hasattr(self, "group_table") else -1
        if row < 0:
            QMessageBox.information(self, "Remove group", "Select a string-group row first.")
            return
        rules = self._event_group_rules_from_table()
        if row < len(rules):
            removed = rules.pop(row)
            self.protocol.event_groups = rules
            self._load_event_groups_table(rules)
            self._refresh_event_table()
            self._refresh_epoch_preflight()
            self.status_label.setText(f"Removed string group {removed.pattern!r}.")

    def _event_source_changed(self, _text=None):
        # Annotation.txt is the normal ERP path. Switching source re-scans so the
        # user never has to wonder why an old event table is still shown.
        if self._epoching_raw() is not None:
            self._discover_events_internal(silent=True)

    def _event_table_changed(self, _item=None):
        if self._event_table_guard:
            return
        self._refresh_epoch_preflight()

    def _discover_events_internal(self, silent: bool = False) -> bool:
        raw = self._epoching_raw()
        if raw is None:
            if not silent:
                QMessageBox.information(self, "No recording", "Load a recording first.")
            self._refresh_epoch_event_status()
            return False
        try:
            self.events, self.event_labels = engine.discover_events(
                raw, self.event_source.currentText(), self.stim_channel.text()
            )
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Event detection failed", str(exc))
            self.events = np.empty((0, 3), dtype=int)
            self.event_labels = {}
            self._refresh_event_table()
            self._refresh_epoch_event_status(error=str(exc))
            return False

        self._refresh_event_group_counts()
        self._refresh_event_table()
        self._refresh_epoch_event_status()
        self._refresh_epoch_preflight()
        grouped = sum(
            1 for _, label in self.event_labels.items()
            if engine.resolve_event_group_condition(label, self.protocol.event_groups)
        )
        self.status_label.setText(
            f"Event timeline ready: {len(self.events)} marker(s), {len(self.event_labels)} unique event name(s)"
            + (f"; {grouped} unique name(s) currently matched to ERP groups." if grouped else ".")
        )
        return bool(len(self.events))

    def discover_events(self):
        self._discover_events_internal(silent=False)

    def _refresh_epoch_event_status(self, error: str = ""):
        if not hasattr(self, "epoch_event_status"):
            return
        if error:
            self.epoch_event_status.setText(f"Event discovery failed: {error}")
            return
        raw = self._epoching_raw()
        if raw is None:
            self.epoch_event_status.setText(
                "No recording loaded. Attach the matching Annotation.txt in Continuous EEG; events will be discovered automatically."
            )
            return
        if len(self.events) == 0:
            ann_count = len(raw.annotations)
            if ann_count:
                self.epoch_event_status.setText(
                    f"{ann_count} annotation marker(s) are attached to the EEG, but no events are currently listed. "
                    "The app will re-scan automatically; use Re-scan event timeline if needed."
                )
            else:
                self.epoch_event_status.setText("No annotation events are attached to this recording.")
            return
        source_name = "Annotation.txt / embedded annotations" if self.event_source.currentText() == "annotations" else "stim channel"
        self.epoch_event_status.setText(
            f"✓ Event timeline ready from {source_name}: {len(self.events)} marker(s), "
            f"{len(self.event_labels)} unique event name(s). Define condition groups below, then inspect the epoch cutting preview."
        )

    def _refresh_event_table(self):
        if not hasattr(self, "event_table"):
            return
        existing = dict(self.protocol.event_map)
        counts = engine.event_code_counts(self.events)
        codes = sorted(self.event_labels)
        self._event_table_guard = True
        try:
            self.event_table.setRowCount(len(codes))
            for row, code in enumerate(codes):
                use = QTableWidgetItem()
                use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                label = self.event_labels.get(code, str(code))
                group_condition = engine.resolve_event_group_condition(label, self.protocol.event_groups)
                pre = group_condition or existing.get(str(code), "")
                use.setCheckState(Qt.CheckState.Checked if pre else Qt.CheckState.Unchecked)
                self.event_table.setItem(row, 0, use)
                code_item = QTableWidgetItem(str(code)); code_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.event_table.setItem(row, 1, code_item)
                label_item = QTableWidgetItem(label); label_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.event_table.setItem(row, 2, label_item)
                count_item = QTableWidgetItem(str(counts.get(int(code), 0)))
                count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.event_table.setItem(row, 3, count_item)
                condition_item = QTableWidgetItem(pre or label)
                if group_condition:
                    condition_item.setToolTip("Assigned automatically by a common-string event group. Edit the group above to change it.")
                    condition_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.event_table.setItem(row, 4, condition_item)
            self.event_table.resizeColumnToContents(0)
            self.event_table.resizeColumnToContents(1)
            self.event_table.resizeColumnToContents(3)
        finally:
            self._event_table_guard = False

    def _allowed_epoch_event_codes(self) -> set[int]:
        return {
            int(self.event_table.item(row, 1).text())
            for row in range(self.event_table.rowCount())
            if self.event_table.item(row, 0)
            and self.event_table.item(row, 0).checkState() == Qt.CheckState.Checked
            and self.event_table.item(row, 1)
        }

    def _refresh_epoch_preflight(self):
        if not hasattr(self, "epoch_plan_table"):
            return
        self._update_epoch_preview_summary()
        self._last_epoch_preflight = None
        self.epoch_plan_table.setRowCount(0)
        raw = self._epoching_raw()
        if raw is None or len(self.events) == 0:
            self.epoch_plan_status.setText("No event timeline is ready yet.")
            return
        # Copy only the epoch/group/map fields currently visible in the UI. This
        # avoids mutating the saved protocol merely because the user is previewing.
        preview_protocol = copy.deepcopy(self.protocol)
        ep = preview_protocol.epoch
        ep.tmin_ms = self.tmin.value(); ep.tmax_ms = self.tmax.value()
        ep.baseline_enabled = self.baseline_check.isChecked()
        ep.baseline_start_ms = self.baseline_start.value() if ep.baseline_enabled else None
        ep.baseline_end_ms = self.baseline_end.value() if ep.baseline_enabled else None
        preview_protocol.event_groups = self._event_group_rules_from_table()
        event_map = {}
        for row in range(self.event_table.rowCount()):
            use = self.event_table.item(row, 0)
            code_item = self.event_table.item(row, 1)
            label_item = self.event_table.item(row, 2)
            cond_item = self.event_table.item(row, 4)
            if not (use and use.checkState() == Qt.CheckState.Checked and code_item and cond_item and cond_item.text().strip()):
                continue
            label = label_item.text() if label_item else ""
            if engine.resolve_event_group_condition(label, preview_protocol.event_groups):
                continue
            event_map[code_item.text().strip()] = cond_item.text().strip()
        preview_protocol.event_map = event_map
        try:
            plan = engine.epoch_preflight(
                raw,
                self.events,
                preview_protocol,
                event_labels=self.event_labels,
                allowed_event_codes=self._allowed_epoch_event_codes(),
            )
        except Exception as exc:
            self.epoch_plan_status.setText(f"Epoch preview unavailable: {exc}")
            return
        self._last_epoch_preflight = plan
        self.epoch_plan_table.setRowCount(len(plan["conditions"]))
        for row, stat in enumerate(plan["conditions"]):
            for col, value in enumerate((stat["condition"], stat["selected"], stat["in_bounds"], stat["boundary_drop"])):
                item = QTableWidgetItem(str(value))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.epoch_plan_table.setItem(row, col, item)

        if plan["conflicts"]:
            example = plan["conflicts"][0]
            self.epoch_plan_status.setText(
                f"⚠ {len(plan['conflicts'])} ambiguous event name(s) match more than one condition. "
                f"Example: {example['label']} → {', '.join(example['conditions'])}. "
                "Resolve overlapping common strings before epoching."
            )
        elif plan["selected_total"] == 0:
            self.epoch_plan_status.setText(
                "No ERP markers are selected yet. Add a common-string group (for example a shared stimulus filename prefix) or manually map an event below."
            )
        else:
            self.epoch_plan_status.setText(
                f"Ready to cut: {plan['in_bounds_total']} of {plan['selected_total']} selected marker(s) fit completely inside "
                f"the {self.tmin.value():g} to {self.tmax.value():g} ms epoch window. "
                f"{plan['boundary_drop_total']} marker(s) would be excluded only because the full epoch extends beyond the recording boundary."
            )

    def create_epochs(self):
        raw = self._epoching_raw()
        if raw is None:
            QMessageBox.information(self, "No recording", "Load a recording first.")
            return
        if len(self.events) == 0 and not self._discover_events_internal(silent=False):
            return
        self._sync_protocol_from_ui()
        if self.protocol.epoch.tmin_ms >= self.protocol.epoch.tmax_ms:
            QMessageBox.warning(self, "Invalid epoch", "Epoch start must be earlier than epoch end.")
            return
        allowed_event_codes = self._allowed_epoch_event_codes()
        plan = engine.epoch_preflight(
            raw, self.events, self.protocol,
            event_labels=self.event_labels, allowed_event_codes=allowed_event_codes,
        )
        self._last_epoch_preflight = plan
        self._refresh_epoch_preflight()
        if plan["conflicts"]:
            QMessageBox.warning(
                self, "Ambiguous event grouping",
                "Some event names match more than one active common-string condition. "
                "Epoching is blocked so a trial cannot silently enter the wrong average. Resolve the overlap shown in the Epoch cutting preview first."
            )
            return
        if plan["in_bounds_total"] == 0:
            QMessageBox.information(
                self, "No epochs to create",
                "No selected event markers can produce a complete epoch with the current time window. "
                "Define at least one condition group and check the epoch cutting preview."
            )
            return
        try:
            epochs = engine.create_epochs(
                raw, self.events, self.protocol,
                event_labels=self.event_labels, allowed_event_codes=allowed_event_codes,
            )
            review = engine.auto_review_epochs(epochs, self.protocol)
        except Exception as exc:
            QMessageBox.critical(self, "Epoching failed", str(exc))
            return
        self.epochs = epochs
        self.review = review
        self._epoch_decision_history = []
        self.clean_epochs = None
        self.evokeds = {}; self.measurements = []
        boundary_dropped = int(plan.get("boundary_drop_total", 0))
        criterion_counts = {"abs": 0, "p2p": 0, "flat": 0}
        for reason in review.auto_reason:
            if "abs>" in reason: criterion_counts["abs"] += 1
            if "p2p>" in reason: criterion_counts["p2p"] += 1
            if "flat<" in reason: criterion_counts["flat"] += 1
        self.metadata.processing_log.append(
            f"Epoching: selected {plan['selected_total']} markers; created {len(epochs)} complete epochs; "
            f"boundary excluded {boundary_dropped}; auto-screen flagged {int(review.auto_bad.sum())}; "
            f"criterion hits abs={criterion_counts['abs']}, p2p={criterion_counts['p2p']}, flat={criterion_counts['flat']}; "
            f"screening channels: {', '.join(self.protocol.epoch.rejection_channels) if self.protocol.epoch.rejection_channels else 'ALL EEG'}"
        )
        self._refresh_review_table()
        self.status_label.setText(
            f"Created {len(epochs)} epochs; auto-screen flagged {int(review.auto_bad.sum())} "
            f"(abs {criterion_counts['abs']}, p-p {criterion_counts['p2p']}, flat {criterion_counts['flat']}); "
            f"{boundary_dropped} boundary exclusion(s)."
        )
        self.tabs.setCurrentIndex(3)

    # ---------- epoch review ----------
    def _build_review_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        bar = QHBoxLayout()
        self.review_filter = QComboBox()
        self.review_filter.addItems([
            "All", "Auto flagged", "Manual rejected", "Manual kept",
            "Final rejected", "Final accepted",
        ])
        self.review_filter.setToolTip("Filter epochs by review/rejection state.")
        self.review_filter.currentTextChanged.connect(self._refresh_review_table)

        self.review_condition_filter = QComboBox()
        self.review_condition_filter.addItem("All conditions", None)
        self.review_condition_filter.setMinimumContentsLength(18)
        self.review_condition_filter.setToolTip(
            "Show only epochs belonging to one event-category/condition group, "
            "for example Neu_Red, NH_Green, etc."
        )
        self.review_condition_filter.currentIndexChanged.connect(self._refresh_review_table)

        self.keep_btn = QPushButton("Keep"); self.keep_btn.clicked.connect(lambda: self._set_manual_review(1))
        self.reject_btn = QPushButton("Reject"); self.reject_btn.clicked.connect(lambda: self._set_manual_review(-1))
        self.reset_review_btn = QPushButton("Auto decision"); self.reset_review_btn.clicked.connect(lambda: self._set_manual_review(0))
        self.export_review_log_btn = QPushButton("Export log…"); self.export_review_log_btn.clicked.connect(self._export_epoch_review_log)
        self.apply_review_log_btn = QPushButton("Apply log…"); self.apply_review_log_btn.clicked.connect(self._apply_epoch_review_log)
        self.export_review_log_btn.setToolTip("Export every epoch's final accepted/rejected state plus stable event identifiers to a replayable JSON log.")
        self.apply_review_log_btn.setToolTip("Apply a previously exported JSON review log to reproduce the same final accepted/rejected epoch set.")
        self.review_stats = QLabel("No epochs")
        bar.addWidget(QLabel("Show")); bar.addWidget(self.review_filter)
        bar.addWidget(QLabel("Condition")); bar.addWidget(self.review_condition_filter)
        bar.addWidget(self.keep_btn); bar.addWidget(self.reject_btn); bar.addWidget(self.reset_review_btn)
        bar.addWidget(self.export_review_log_btn); bar.addWidget(self.apply_review_log_btn)
        bar.addStretch(1); bar.addWidget(self.review_stats)
        layout.addLayout(bar)

        splitter = QSplitter()
        self.review_splitter = splitter
        self.review_table = QTableWidget(0, 5)
        self.review_table.setHorizontalHeaderLabels(["Epoch", "Condition", "Auto", "Reason(s)", "Final"])
        self.review_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.review_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.review_table.itemSelectionChanged.connect(self._review_selection_changed)

        self.epoch_viewer = StackedEEGViewer()
        self.epoch_viewer.channelSelectionChanged.connect(self._remember_display_channels)
        # Unlike v0.5, the epoch time-scale control is intentionally enabled.
        # It changes only the visible portion of the already-created epoch; it
        # never changes tmin/tmax or recuts the underlying MNE Epochs object.
        self.epoch_viewer.duration.setEnabled(True)
        self.epoch_viewer.set_standalone_max_zoom_out_factor(4.0)
        self.epoch_viewer.duration.setToolTip(
            "Display time scale only. + zooms in. - zooms out; after the complete epoch is visible, "
            "further '-' presses keep the epoch start fixed at the left and add blank space to the right until the epoch occupies 25% of the plot width. "
            "Epoch boundaries/data are unchanged."
        )
        self.epoch_viewer.position.setEnabled(False)
        self.epoch_viewer.shortcut_help.setText(
            "Epoch Review: ←/→ previous/next epoch in the current filters   |   R toggle manual keep/reject   |   "
            "+ zoom in   − zoom out/shrink epoch to 25% width   |   * increase sensitivity   / decrease sensitivity   |   "
            "Positive ↑/↓ changes display polarity only"
        )

        # The review page owns these shortcuts so they still work while the
        # table has focus. Disable the viewer-local copies to avoid ambiguous
        # duplicate QShortcuts when the plot itself has focus.
        for shortcut in getattr(self.epoch_viewer, "_shortcuts", []):
            shortcut.setEnabled(False)

        # Shortcuts are scoped to the table/viewer area so navigation does not
        # hijack the condition/rejection filter combo boxes above.
        self._review_shortcuts = []
        self._install_review_shortcuts()

        splitter.addWidget(self.review_table); splitter.addWidget(self.epoch_viewer)
        splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(page, "4  Epoch Review")

    def _install_review_shortcuts(self):
        """Epoch Review shortcuts are routed at the main-window level.

        A WindowShortcut is intentionally used instead of attaching shortcuts to
        the review splitter.  On Windows, QTableWidget/QComboBox focus could
        otherwise consume Left/Right/R or make the shortcuts appear inactive.
        The dispatcher below only gives these actions to Epoch Review while that
        tab is active, so the keys do not affect hidden viewers.
        """
        for shortcut in getattr(self, "_review_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._review_shortcuts = []
        if hasattr(self, "epoch_viewer"):
            m = self._shortcut_map
            self.epoch_viewer.shortcut_help.setText(
                f"Epoch Review: {m.get('previous','')}/{m.get('next','')} previous/next epoch · "
                f"{m.get('toggle_reject','')} toggle manual decision · "
                f"{m.get('time_in','')}/{m.get('time_out','')} time scale · "
                f"{m.get('sensitivity_up','')}/{m.get('sensitivity_down','')} sensitivity · polarity is display-only"
            )

    def _update_review_condition_filter(self):
        """Populate Epoch Review's condition filter from the actual MNE epochs."""
        if not hasattr(self, "review_condition_filter"):
            return
        previous = self.review_condition_filter.currentData()
        self.review_condition_filter.blockSignals(True)
        self.review_condition_filter.clear()
        self.review_condition_filter.addItem("All conditions", None)
        if self.epochs is not None:
            event_codes = np.asarray(self.epochs.events[:, 2], dtype=int) if len(self.epochs) else np.asarray([], dtype=int)
            # Preserve event_id insertion order: this normally reflects the
            # protocol/group order rather than arbitrarily alphabetising it.
            for condition, code in self.epochs.event_id.items():
                count = int(np.sum(event_codes == int(code)))
                self.review_condition_filter.addItem(f"{condition} ({count})", str(condition))
        restore = self.review_condition_filter.findData(previous)
        self.review_condition_filter.setCurrentIndex(restore if restore >= 0 else 0)
        self.review_condition_filter.blockSignals(False)

    def _refresh_review_table(self, *_):
        if not hasattr(self, "review_table"):
            return
        previous_idx = self._selected_epoch_index()
        previous_row = self.review_table.currentRow()
        self.review_table.blockSignals(True)
        self.review_table.setRowCount(0)
        if self.epochs is None or self.review.auto_bad.size != len(self.epochs):
            self._update_review_condition_filter()
            self.review_stats.setText("No epochs")
            self.review_table.blockSignals(False)
            return

        self._update_review_condition_filter()
        accepted = self.review.accepted_mask()
        reverse_id = {v: k for k, v in self.epochs.event_id.items()}
        mode = self.review_filter.currentText()
        selected_condition = self.review_condition_filter.currentData()
        indices = []
        for i in range(len(self.epochs)):
            condition = reverse_id.get(int(self.epochs.events[i, 2]), str(self.epochs.events[i, 2]))
            if selected_condition is not None and condition != selected_condition:
                continue

            final_reject = not accepted[i]
            manual = int(self.review.manual_decision[i])
            include = (
                mode == "All" or
                (mode == "Auto flagged" and self.review.auto_bad[i]) or
                (mode == "Manual rejected" and manual == -1) or
                (mode == "Manual kept" and manual == 1) or
                (mode == "Final rejected" and final_reject) or
                (mode == "Final accepted" and accepted[i])
            )
            if include:
                indices.append(i)

        self.review_table.setRowCount(len(indices))
        for row, i in enumerate(indices):
            item = QTableWidgetItem(str(i + 1)); item.setData(Qt.ItemDataRole.UserRole, i)
            self.review_table.setItem(row, 0, item)
            condition = reverse_id.get(int(self.epochs.events[i, 2]), str(self.epochs.events[i, 2]))
            self.review_table.setItem(row, 1, QTableWidgetItem(condition))
            self.review_table.setItem(row, 2, QTableWidgetItem("FLAG" if self.review.auto_bad[i] else "OK"))
            self.review_table.setItem(row, 3, QTableWidgetItem(self.review.auto_reason[i]))
            manual = self.review.manual_decision[i]
            final = "KEEP (manual)" if manual == 1 else "REJECT (manual)" if manual == -1 else "KEEP" if accepted[i] else "REJECT"
            self.review_table.setItem(row, 4, QTableWidgetItem(final))

        n_accept = int(accepted.sum()); n_reject = len(accepted) - n_accept
        selected_name = selected_condition if selected_condition is not None else "all conditions"
        self.review_stats.setText(
            f"Showing {len(indices)}/{len(self.epochs)} ({selected_name})  |  "
            f"Accepted {n_accept}  |  Rejected {n_reject}  |  Auto flagged {int(self.review.auto_bad.sum())}"
        )
        self.review_table.blockSignals(False)
        if self.review_table.rowCount() > 0:
            restored = False
            if previous_idx is not None:
                for row in range(self.review_table.rowCount()):
                    item = self.review_table.item(row, 0)
                    if item is not None and item.data(Qt.ItemDataRole.UserRole) == previous_idx:
                        self.review_table.selectRow(row)
                        restored = True
                        break
            if not restored:
                fallback = min(max(previous_row, 0), self.review_table.rowCount() - 1)
                self.review_table.selectRow(fallback)
        else:
            self.epoch_viewer.set_rejected_visual(False)
            self.epoch_viewer.clear_manual_segment()

    def _selected_epoch_index(self):
        rows = self.review_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.review_table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _review_selection_changed(self):
        idx = self._selected_epoch_index()
        if idx is None or self.epochs is None:
            return
        data = self.epochs.get_data()[idx]
        # Keep the review display scale while stepping through epochs so rapid
        # comparison does not continually snap back to full-width display.
        self.epoch_viewer.set_segment(
            data, self.epochs.ch_names, self.epochs.info["sfreq"], self.epochs.tmin, preserve_view=True
        )
        # Carry a previously chosen channel subset forward into Epoch Review.
        # If the user changes the subset here, channelSelectionChanged updates
        # the same preference for the later ERP-average stage.
        if self._preferred_display_channels:
            self.epoch_viewer.set_selected_channels(self._preferred_display_channels)
        accepted = self.review.accepted_mask()
        manual = int(self.review.manual_decision[idx])
        rejected = not bool(accepted[idx])
        if manual == -1:
            reject_label = "REJECTED — manual override"
        elif rejected:
            reject_label = "REJECTED — automatic flag"
        else:
            reject_label = ""
        self.epoch_viewer.set_rejected_visual(rejected, reject_label)

    def _navigate_review_epoch(self, direction: int):
        """Move to previous/next epoch in the currently filtered table."""
        rows = self.review_table.rowCount()
        if rows <= 0:
            return
        current = self.review_table.currentRow()
        if current < 0:
            current = 0
        target = min(rows - 1, max(0, current + int(direction)))
        if target != current or self.review_table.currentRow() < 0:
            self.review_table.selectRow(target)
            self.review_table.scrollToItem(self.review_table.item(target, 0))

    def _toggle_selected_epoch_rejection(self):
        """R toggles the current final decision as an explicit manual override."""
        idx = self._selected_epoch_index()
        if idx is None or self.epochs is None:
            return
        currently_accepted = bool(self.review.accepted_mask()[idx])
        # If auto-screen rejected it, R means manually KEEP it; if currently
        # accepted, R means manually REJECT it.  Either direction is therefore
        # a documented operator override rather than a hidden state change.
        self._set_manual_review(-1 if currently_accepted else 1)

    def _set_manual_review(self, decision: int):
        idx = self._selected_epoch_index()
        if idx is None:
            return
        previous_final = bool(self.review.accepted_mask()[idx]) if self.review.auto_bad.size else None
        self.review.manual_decision[idx] = decision
        new_final = bool(self.review.accepted_mask()[idx]) if self.review.auto_bad.size else None
        if self.epochs is not None:
            row = engine.epoch_review_rows(self.epochs, self.review)[idx]
            self._epoch_decision_history.append({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "action": "use_auto" if decision == 0 else "manual_keep" if decision == 1 else "manual_reject",
                "epoch_number": int(idx + 1),
                "condition": row.get("condition", ""),
                "event_sample": row.get("event_sample"),
                "source_event_label": row.get("source_event_label", ""),
                "previous_final_accepted": previous_final,
                "new_final_accepted": new_final,
            })
        self.clean_epochs = None; self.evokeds = {}; self.measurements = []
        self._refresh_review_table()

    def _export_epoch_review_log(self):
        if self.epochs is None or self.review.auto_bad.size != len(self.epochs):
            QMessageBox.information(self, "No epochs", "Create epochs before exporting a review decision log.")
            return
        self._sync_protocol_from_ui()
        base = self.metadata.input_path.stem if self.metadata.input_path else "recording"
        folder = self.metadata.input_path.parent if self.metadata.input_path else Path.home()
        suggested = str(folder / f"{base}_epoch_review.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export epoch review decisions", suggested, "ERP review log (*.json);;JSON (*.json)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() != ".json":
            p = p.with_suffix(".json")
        if not self._confirm_overwrite(p):
            return
        try:
            engine.export_epoch_review_log(
                p, self.metadata, self.protocol, self.epochs, self.review, self._epoch_decision_history
            )
        except Exception as exc:
            QMessageBox.critical(self, "Review log export failed", str(exc))
            return
        self.status_label.setText(f"Exported replayable epoch review log: {p.name}")

    def _apply_epoch_review_log(self):
        if self.epochs is None or self.review.auto_bad.size != len(self.epochs):
            QMessageBox.information(self, "No epochs", "Create the matching epochs before applying a review decision log.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Apply epoch review decisions", "", "ERP review log (*.json);;JSON (*.json)")
        if not path:
            return
        try:
            stats = engine.apply_epoch_review_log(path, self.epochs, self.review)
        except Exception as exc:
            QMessageBox.critical(self, "Review log apply failed", str(exc))
            return
        self.clean_epochs = None; self.evokeds = {}; self.measurements = []
        self._epoch_decision_history.append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "action": "apply_review_log",
            "source": str(path),
            "matched": int(stats["matched"]),
            "unmatched": int(stats["unmatched"]),
        })
        self._refresh_review_table()
        self.status_label.setText(
            f"Applied epoch review log: {stats['matched']}/{stats['logged']} logged epoch(s) matched; "
            f"{stats['unmatched']} unmatched."
        )
        if stats["unmatched"]:
            QMessageBox.warning(
                self, "Review log partially matched",
                f"Matched {stats['matched']} of {stats['logged']} logged epoch(s). "
                f"{stats['unmatched']} could not be uniquely matched to the current epoch set. "
                "No guess was made for unmatched epochs."
            )

    def _remember_display_channels(self, channels):
        """Remember a user's explicit display subset for use in later stages."""
        chosen = [str(ch) for ch in (channels or []) if str(ch)]
        if chosen:
            self._preferred_display_channels = chosen

    # ---------- ERP / subject averaging ----------
    def _build_erp_tab(self):
        page = QWidget(); outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)

        # Row 1: subject-average lifecycle.
        bar = QHBoxLayout()
        average_btn = QPushButton("Average accepted epochs")
        average_btn.clicked.connect(self.make_averages)
        load_avg_btn = QPushButton("Open averaged subject…")
        load_avg_btn.clicked.connect(self.load_average_subject_package)
        save_avg_btn = QPushButton("Save averaged subject…")
        save_avg_btn.clicked.connect(self.save_average_subject_package)
        subject_excel_btn = QPushButton("Export subject Excel…")
        subject_excel_btn.clicked.connect(self.export_excel)
        bar.addWidget(average_btn)
        bar.addWidget(load_avg_btn)
        bar.addWidget(save_avg_btn)
        bar.addWidget(subject_excel_btn)
        bar.addSpacing(16)

        prev_cond = QPushButton("◀")
        prev_cond.setFixedWidth(34)
        prev_cond.setToolTip("Previous averaged condition (Left arrow)")
        prev_cond.clicked.connect(lambda: self._step_erp_condition(-1))
        next_cond = QPushButton("▶")
        next_cond.setFixedWidth(34)
        next_cond.setToolTip("Next averaged condition (Right arrow)")
        next_cond.clicked.connect(lambda: self._step_erp_condition(1))
        self.condition_combo = QComboBox()
        self.condition_combo.setMinimumWidth(150)
        self.condition_combo.currentTextChanged.connect(self._condition_changed)
        bar.addWidget(QLabel("Condition")); bar.addWidget(prev_cond); bar.addWidget(self.condition_combo); bar.addWidget(next_cond)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(90)
        self.channel_combo.currentTextChanged.connect(self._primary_erp_channel_changed)
        display_channels_btn = QPushButton("Display channels…")
        display_channels_btn.clicked.connect(self.select_erp_display_channels)
        self.erp_channel_summary = QLabel("None")
        self.erp_channel_summary.setProperty("muted", True)
        self.erp_channel_summary.setMaximumWidth(210)
        bar.addSpacing(10)
        bar.addWidget(QLabel("Primary")); bar.addWidget(self.channel_combo)
        bar.addWidget(display_channels_btn); bar.addWidget(self.erp_channel_summary)
        self.erp_view_mode = QComboBox()
        self.erp_view_mode.addItems(["Butterfly", "Stacked"])
        self.erp_view_mode.setToolTip(
            "Butterfly overlays selected channels. Stacked arranges selected channel averages vertically like the EEG/epoch viewers."
        )
        self.erp_view_mode.currentTextChanged.connect(self._erp_view_mode_changed)
        bar.addSpacing(10); bar.addWidget(QLabel("View")); bar.addWidget(self.erp_view_mode)
        bar.addStretch(1)
        outer.addLayout(bar)

        # Row 2: measurement actions + explicitly temporary difference wave.
        actions = QHBoxLayout()
        measure_btn = QPushButton("Measure selected component")
        measure_btn.clicked.connect(self.measure_current)
        detect_current = QPushButton("Auto-detect current condition")
        detect_current.clicked.connect(self.measure_current_condition_automatic)
        detect_all = QPushButton("Auto-detect all conditions")
        detect_all.clicked.connect(self.measure_all_automatic)
        self.auto_measure_mode = QComboBox()
        self.auto_measure_mode.addItem("Use component setting", "component")
        self.auto_measure_mode.addItem("Window peak", "peak")
        self.auto_measure_mode.addItem("Mean amplitude", "mean")
        self.auto_measure_mode.setToolTip(
            "Choose how automatic measurement is performed. Window peak finds the configured polarity extremum "
            "inside each component window; Mean amplitude averages voltage across the window. "
            "Use component setting follows each row's Method column."
        )
        actions.addWidget(measure_btn)
        actions.addSpacing(8); actions.addWidget(QLabel("Auto method")); actions.addWidget(self.auto_measure_mode)
        actions.addWidget(detect_current); actions.addWidget(detect_all)
        actions.addSpacing(18)
        actions.addWidget(QLabel("Temporary difference"))
        self.diff_a_combo = QComboBox(); self.diff_b_combo = QComboBox()
        self.diff_a_combo.setMinimumWidth(120); self.diff_b_combo.setMinimumWidth(120)
        diff_btn = QPushButton("View A − B")
        diff_btn.clicked.connect(self._show_difference_wave)
        clear_diff_btn = QPushButton("Return to condition")
        clear_diff_btn.clicked.connect(self._clear_difference_wave)
        actions.addWidget(self.diff_a_combo); actions.addWidget(QLabel("−")); actions.addWidget(self.diff_b_combo)
        actions.addWidget(diff_btn); actions.addWidget(clear_diff_btn)
        actions.addStretch(1)
        outer.addLayout(actions)

        # Component definition editor can be collapsed without affecting the protocol.
        upper = QSplitter(Qt.Orientation.Horizontal)
        self.erp_upper_splitter = upper
        self.component_panel = QFrame(); comp_outer = QVBoxLayout(self.component_panel)
        comp_outer.setContentsMargins(0, 0, 0, 0); comp_outer.setSpacing(4)
        comp_head = QHBoxLayout()
        self.component_toggle = QToolButton(); self.component_toggle.setText("▾ Components")
        self.component_toggle.setToolTip("Collapse/expand component definitions. This does not change any component.")
        self.component_toggle.clicked.connect(self._toggle_component_panel)
        add_component = QPushButton("+"); add_component.setFixedWidth(30); add_component.setToolTip("Add component")
        add_component.clicked.connect(self._add_component_row)
        remove_component = QPushButton("−"); remove_component.setFixedWidth(30); remove_component.setToolTip("Remove selected component")
        remove_component.clicked.connect(self._remove_component_row)
        comp_head.addWidget(self.component_toggle); comp_head.addStretch(1); comp_head.addWidget(add_component); comp_head.addWidget(remove_component)
        comp_outer.addLayout(comp_head)

        self.component_body = QWidget(); comp_body_layout = QVBoxLayout(self.component_body)
        comp_body_layout.setContentsMargins(0, 0, 0, 0)
        self.component_table = QTableWidget(0, 6)
        self.component_table.setHorizontalHeaderLabels(["Component", "Start ms", "End ms", "Polarity", "Method", "Channels"])
        self.component_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.component_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.component_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.component_table.itemSelectionChanged.connect(self._component_selection_changed)
        self.component_table.itemChanged.connect(self._component_definition_edited)
        comp_body_layout.addWidget(self.component_table)
        component_hint = QLabel("Automatic peak detection stays inside the defined window; mean = mean voltage across the window. Manual clicks may be placed anywhere in the displayed ERP. In Stacked view the clicked waveform chooses the electrode. Right-click a marker to remove or relabel it.")
        component_hint.setProperty("muted", True); component_hint.setWordWrap(True)
        comp_body_layout.addWidget(component_hint)
        comp_outer.addWidget(self.component_body, 1)
        self.component_panel.setMinimumWidth(360)

        self.erp_viewer = ERPViewer(); self.erp_viewer.pointClicked.connect(self._erp_clicked)
        self.erp_viewer.markerContextRequested.connect(self._erp_marker_context_requested)
        self.erp_viewer.previousRequested.connect(lambda: self._step_erp_condition(-1))
        self.erp_viewer.nextRequested.connect(lambda: self._step_erp_condition(1))
        upper.addWidget(self.component_panel); upper.addWidget(self.erp_viewer)
        upper.setStretchFactor(0, 0); upper.setStretchFactor(1, 1)
        upper.setSizes([420, 1080])
        outer.addWidget(upper, 3)

        # Measurements are a collapsible, filterable, sortable result browser.
        measure_panel = QFrame(); self.measure_panel = measure_panel
        measure_outer = QVBoxLayout(measure_panel)
        measure_outer.setContentsMargins(0, 0, 0, 0); measure_outer.setSpacing(4)
        measure_head = QHBoxLayout()
        self.measurements_toggle = QToolButton(); self.measurements_toggle.setText("▾ Measurements")
        self.measurements_toggle.clicked.connect(self._toggle_measurements_panel)
        measure_head.addWidget(self.measurements_toggle)
        measure_head.addSpacing(10)
        self.measure_filter_condition = QComboBox(); self.measure_filter_channel = QComboBox()
        self.measure_filter_component = QComboBox(); self.measure_filter_method = QComboBox()
        for combo in (self.measure_filter_condition, self.measure_filter_channel, self.measure_filter_component, self.measure_filter_method):
            combo.setMinimumWidth(100); combo.currentTextChanged.connect(self._refresh_result_table)
        measure_head.addWidget(QLabel("Condition")); measure_head.addWidget(self.measure_filter_condition)
        measure_head.addWidget(QLabel("Channel")); measure_head.addWidget(self.measure_filter_channel)
        measure_head.addWidget(QLabel("Component")); measure_head.addWidget(self.measure_filter_component)
        measure_head.addWidget(QLabel("Method")); measure_head.addWidget(self.measure_filter_method)
        measure_head.addStretch(1)
        measure_outer.addLayout(measure_head)

        self.measurements_body = QWidget(); measure_body_layout = QVBoxLayout(self.measurements_body)
        measure_body_layout.setContentsMargins(0, 0, 0, 0)
        self.result_table = QTableWidget(0, 10)
        self.result_table.setHorizontalHeaderLabels([
            "Condition", "Channel", "Component", "Method", "Start ms", "End ms",
            "Amplitude (µV*)", "Latency ms", "Epochs", "Notes"
        ])
        self.result_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setSortingEnabled(True)
        measure_body_layout.addWidget(self.result_table)
        foot = QLabel("Filter/sort this table without changing measurements. Channels in component definitions may be a comma-separated list or ALL. *Area, if used, is signed µV·ms.")
        foot.setProperty("muted", True); foot.setWordWrap(True); measure_body_layout.addWidget(foot)
        measure_outer.addWidget(self.measurements_body, 1)
        outer.addWidget(measure_panel, 2)
        self.erp_outer_layout = outer
        # bar/actions are items 0/1; the actual scientific workspace and the
        # measurements browser are items 2/3.  These stretch factors are
        # dynamically changed when a panel is collapsed so no blank area is
        # reserved for hidden content.
        outer.setStretch(2, 3); outer.setStretch(3, 2)

        self.tabs.addTab(page, "5  ERP + Measure")
        self._load_components_table(self.protocol.components)
        self.erp_viewer.clear_view("Average accepted epochs or open an averaged-subject file to begin.")

    def _toggle_component_panel(self):
        visible = not self.component_body.isVisible()
        self.component_body.setVisible(visible)
        self.component_toggle.setText("▾ Components" if visible else "▸ Components")
        splitter = getattr(self, "erp_upper_splitter", None)
        total = max(int(splitter.width()) if splitter is not None else 1200, 700)
        if visible:
            self.component_panel.setMinimumWidth(360); self.component_panel.setMaximumWidth(16777215)
            if splitter is not None:
                left = min(420, max(360, int(total * 0.30)))
                splitter.setSizes([left, max(1, total - left)])
        else:
            # Keep only the compact header and immediately give the released
            # horizontal space to the ERP plot.
            self.component_panel.setMinimumWidth(135); self.component_panel.setMaximumWidth(165)
            if splitter is not None:
                splitter.setSizes([150, max(1, total - 150)])

    def _toggle_measurements_panel(self):
        visible = not self.measurements_body.isVisible()
        self.measurements_body.setVisible(visible)
        self.measurements_toggle.setText("▾ Measurements" if visible else "▸ Measurements")
        panel = getattr(self, "measure_panel", None)
        outer = getattr(self, "erp_outer_layout", None)
        if panel is not None:
            if visible:
                panel.setMaximumHeight(16777215)
                panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            else:
                # Leave only the filter/header row.  The ERP/component splitter
                # grows vertically into all of the released space.
                panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                panel.setMaximumHeight(max(42, self.measurements_toggle.sizeHint().height() + 18))
        if outer is not None:
            outer.setStretch(2, 3 if visible else 1)
            outer.setStretch(3, 2 if visible else 0)
        if hasattr(self, "erp_viewer"):
            self.erp_viewer.updateGeometry()

    def _add_component_row(self):
        r = self.component_table.rowCount(); self.component_table.insertRow(r)
        values = [f"Component{r + 1}", "300", "500", "positive", "peak", ""]
        for c, value in enumerate(values):
            self.component_table.setItem(r, c, QTableWidgetItem(value))
        self.component_table.selectRow(r)
        self.protocol.components = self._components_from_table()
        self._refresh_protocol_summaries()

    def _remove_component_row(self):
        rows = self.component_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        name_item = self.component_table.item(row, 0)
        removed_name = name_item.text().strip() if name_item is not None else ""
        self.component_table.removeRow(row)
        self.protocol.components = self._components_from_table()
        self._refresh_protocol_summaries()

        # A removed component is no longer part of the active measurement plan.
        # Remove its persisted measurements as well so old auto-detected labels do
        # not remain as visual clutter or silently survive into exports.
        if removed_name:
            self.measurements = [m for m in self.measurements if m.component != removed_name]
            self._refresh_measurement_filters()
            self._refresh_result_table()

        if self.component_table.rowCount():
            self.component_table.selectRow(min(row, self.component_table.rowCount() - 1))
        else:
            self.erp_viewer.set_window(0, 0)
        self._draw_current_measurement_markers()

    def _load_components_table(self, components: list[ComponentDefinition]):
        if not hasattr(self, "component_table"):
            return
        self.component_table.blockSignals(True)
        self.component_table.setRowCount(len(components))
        for r, c in enumerate(components):
            vals = [c.name, str(c.start_ms), str(c.end_ms), c.polarity, c.method, ",".join(c.channels)]
            for col, val in enumerate(vals):
                self.component_table.setItem(r, col, QTableWidgetItem(val))
        self.component_table.blockSignals(False)
        if self.component_table.rowCount(): self.component_table.selectRow(0)

    def _components_from_table(self) -> list[ComponentDefinition]:
        if not hasattr(self, "component_table"):
            return list(self.protocol.components)
        out = []
        for r in range(self.component_table.rowCount()):
            try:
                name_item = self.component_table.item(r, 0); start_item = self.component_table.item(r, 1)
                end_item = self.component_table.item(r, 2); pol_item = self.component_table.item(r, 3)
                method_item = self.component_table.item(r, 4); channels_item = self.component_table.item(r, 5)
                if not all((name_item, start_item, end_item, pol_item, method_item, channels_item)):
                    continue
                name = name_item.text().strip(); start = float(start_item.text()); end = float(end_item.text())
                polarity = pol_item.text().strip().lower(); method = method_item.text().strip().lower()
                channels = [x.strip() for x in channels_item.text().split(",") if x.strip()]
                if polarity not in {"positive", "negative", "absolute"}:
                    polarity = "absolute"
                if method not in {"peak", "mean", "manual", "area"}:
                    method = "peak"
                if name and start < end:
                    out.append(ComponentDefinition(name, start, end, polarity, method, channels))
            except Exception:
                continue
        return out

    def _selected_component(self) -> ComponentDefinition | None:
        rows = self.component_table.selectionModel().selectedRows()
        if not rows: return None
        r = rows[0].row()
        try:
            return ComponentDefinition(
                self.component_table.item(r, 0).text().strip(),
                float(self.component_table.item(r, 1).text()),
                float(self.component_table.item(r, 2).text()),
                self.component_table.item(r, 3).text().strip().lower(),
                self.component_table.item(r, 4).text().strip().lower(),
                [x.strip() for x in self.component_table.item(r, 5).text().split(",") if x.strip()],
            )
        except Exception:
            return None

    def _populate_average_controls(self):
        conditions = list(self.evokeds)
        old_condition = self.condition_combo.currentText()
        self.condition_combo.blockSignals(True); self.condition_combo.clear(); self.condition_combo.addItems(conditions)
        if old_condition in conditions: self.condition_combo.setCurrentText(old_condition)
        self.condition_combo.blockSignals(False)
        for combo in (self.diff_a_combo, self.diff_b_combo):
            old = combo.currentText(); combo.blockSignals(True); combo.clear(); combo.addItems(conditions)
            if old in conditions: combo.setCurrentText(old)
            combo.blockSignals(False)
        if len(conditions) > 1 and self.diff_b_combo.currentIndex() == self.diff_a_combo.currentIndex():
            self.diff_b_combo.setCurrentIndex(1)

        channels = []
        if conditions:
            first = self.evokeds[conditions[0]]
            eeg_idx = mne.pick_types(first.info, eeg=True, exclude=[])
            channels = [first.ch_names[i] for i in eeg_idx]
        old_channel = self.channel_combo.currentText()
        self.channel_combo.blockSignals(True); self.channel_combo.clear(); self.channel_combo.addItems(channels)
        if old_channel in channels: self.channel_combo.setCurrentText(old_channel)
        self.channel_combo.blockSignals(False)
        if not self._erp_display_channels:
            preferred = [ch for ch in self._preferred_display_channels if ch in channels]
            if preferred:
                self._erp_display_channels = preferred
            elif self.channel_combo.currentText():
                self._erp_display_channels = [self.channel_combo.currentText()]
        else:
            self._erp_display_channels = [ch for ch in self._erp_display_channels if ch in channels]
            if not self._erp_display_channels:
                preferred = [ch for ch in self._preferred_display_channels if ch in channels]
                if preferred:
                    self._erp_display_channels = preferred
                elif self.channel_combo.currentText():
                    self._erp_display_channels = [self.channel_combo.currentText()]
        self._update_erp_channel_summary()

    def make_averages(self):
        if self.epochs is None:
            QMessageBox.information(self, "No epochs", "Create and review epochs first.")
            return
        try:
            self.clean_epochs = engine.clean_epochs(self.epochs, self.review)
            if len(self.clean_epochs) == 0:
                raise ValueError("All epochs are rejected.")
            self.evokeds = engine.condition_averages(self.clean_epochs)
            self._loaded_average_counts = engine.accepted_condition_counts(self.clean_epochs)
        except Exception as exc:
            QMessageBox.critical(self, "Averaging failed", str(exc)); return
        self._difference_active = False; self._difference_evoked = None; self._difference_label = ""
        # Keep the last-used ERP display configuration across re-averaging.
        # Existing measurements were derived from an older accepted-epoch set.
        self.measurements = []
        self.protocol.components = self._components_from_table()
        summary = ", ".join(f"{k}={v}" for k, v in self._loaded_average_counts.items())
        self.metadata.processing_log.append(f"Averaged accepted epochs: {summary}")
        self._populate_average_controls(); self._refresh_measurement_filters(); self._refresh_erp_plot()
        self.status_label.setText(f"Averaged {len(self.clean_epochs)} accepted epochs across {len(self.evokeds)} conditions.")

    def select_erp_display_channels(self):
        if not self.evokeds:
            QMessageBox.information(self, "No averages", "Average accepted epochs first.")
            return
        condition = self.condition_combo.currentText() or next(iter(self.evokeds))
        evoked = self.evokeds[condition]
        eeg_idx = mne.pick_types(evoked.info, eeg=True, exclude=[])
        channels = [evoked.ch_names[i] for i in eeg_idx]
        selected = self._erp_display_channels or ([self.channel_combo.currentText()] if self.channel_combo.currentText() else [])
        dlg = BadChannelDialog(channels, selected, self, title="Select ERP channels to display")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dlg.selected_channels()
        if not chosen:
            QMessageBox.information(self, "No display channels", "Select at least one channel to display.")
            return
        self._capture_current_erp_display_state()
        self._erp_display_channels = chosen
        self._preferred_display_channels = list(chosen)
        if self.channel_combo.currentText() not in chosen:
            self.channel_combo.setCurrentText(chosen[0])
        self._update_erp_channel_summary(); self._refresh_erp_plot()

    def _update_erp_channel_summary(self):
        if not hasattr(self, "erp_channel_summary"):
            return
        ch = list(self._erp_display_channels)
        if not ch: text = "None"
        elif len(ch) <= 3: text = ", ".join(ch)
        else: text = f"{len(ch)} channels"
        self.erp_channel_summary.setText(text)
        self.erp_channel_summary.setToolTip(", ".join(ch))

    def _primary_erp_channel_changed(self, channel: str):
        if not channel:
            return
        self._capture_current_erp_display_state()
        if not self._erp_display_channels:
            self._erp_display_channels = [channel]
        elif len(self._erp_display_channels) == 1:
            self._erp_display_channels = [channel]
        elif channel not in self._erp_display_channels:
            self._erp_display_channels.insert(0, channel)
        if len(self._erp_display_channels) == 1:
            self._preferred_display_channels = list(self._erp_display_channels)
        self._update_erp_channel_summary(); self._refresh_erp_plot()

    def _capture_current_erp_display_state(self):
        """Capture the current real-condition view as the shared last-used setup.

        This is intentionally global across averaged conditions: whatever the user
        last configured (time scale/pan, vertical sensitivity and polarity) is
        inherited by NH_Green, NH_Red, PL, Neu, etc. until changed again.
        Temporary difference waves never overwrite this shared configuration.
        """
        if not hasattr(self, "erp_viewer") or self._difference_active:
            return
        if not self.evokeds or not self.erp_viewer.has_data():
            return
        self._erp_display_state = dict(self.erp_viewer.get_display_state())

    def _erp_view_mode_changed(self, text: str):
        if not hasattr(self, "erp_viewer"):
            return
        # Save the old view's compatible state first. A fresh render prevents a
        # stacked row-index Y range from being reused as a butterfly µV range.
        self._capture_current_erp_display_state()
        self.erp_viewer.set_display_mode("stacked" if str(text).lower().startswith("stack") else "butterfly")
        self._refresh_erp_plot()

    def _condition_changed(self, text=""):
        # currentTextChanged arrives after the combo has changed, so save the
        # condition that was previously displayed using our explicit last key.
        self._capture_current_erp_display_state()
        self._difference_active = False; self._difference_evoked = None; self._difference_label = ""
        self._refresh_erp_plot()

    def _step_erp_condition(self, delta: int):
        if self.condition_combo.count() == 0:
            return
        i = self.condition_combo.currentIndex()
        self.condition_combo.setCurrentIndex((i + int(delta)) % self.condition_combo.count())

    def _show_difference_wave(self):
        # Preserve the real condition's display state before temporarily replacing
        # it with a difference wave.
        self._capture_current_erp_display_state()
        a = self.diff_a_combo.currentText(); b = self.diff_b_combo.currentText()
        if not a or not b or a not in self.evokeds or b not in self.evokeds:
            QMessageBox.information(self, "Difference wave", "Average data and select two valid conditions first.")
            return
        if a == b:
            QMessageBox.information(self, "Difference wave", "Choose two different conditions.")
            return
        try:
            self._difference_label = f"{a} − {b}"
            self._difference_evoked = engine.difference_evoked(self.evokeds[a], self.evokeds[b], self._difference_label)
            self._difference_active = True
        except Exception as exc:
            QMessageBox.warning(self, "Difference wave failed", str(exc)); return
        self._refresh_erp_plot()
        self.status_label.setText(f"Viewing temporary difference wave {self._difference_label}. It will NOT be saved as a subject condition.")

    def _clear_difference_wave(self):
        self._difference_active = False; self._difference_evoked = None; self._difference_label = ""
        self._refresh_erp_plot()

    def _current_display_evoked(self):
        if self._difference_active and self._difference_evoked is not None:
            return self._difference_evoked, self._difference_label, True
        condition = self.condition_combo.currentText()
        if condition in self.evokeds:
            return self.evokeds[condition], condition, False
        return None, "", False

    def _refresh_erp_plot(self, *_):
        if not hasattr(self, "erp_viewer"):
            return
        evoked, label, temporary = self._current_display_evoked()
        if evoked is None:
            self.erp_viewer.clear_view("Average accepted epochs or open an averaged-subject file to begin.")
            return
        valid = [ch for ch in self._erp_display_channels if ch in evoked.ch_names]
        primary = self.channel_combo.currentText()
        if not valid and primary in evoked.ch_names:
            valid = [primary]
        if not valid:
            eeg_idx = mne.pick_types(evoked.info, eeg=True, exclude=[])
            if len(eeg_idx): valid = [evoked.ch_names[int(eeg_idx[0])]]
        series = {ch: evoked.data[evoked.ch_names.index(ch)] * 1e6 for ch in valid}
        title = f"{label}" + ("  [temporary difference]" if temporary else "")
        if hasattr(self, "erp_view_mode"):
            self.erp_viewer.set_display_mode("stacked" if self.erp_view_mode.currentText().lower().startswith("stack") else "butterfly")
        self.erp_viewer.set_evoked_multi(evoked.times * 1000, series, title)
        # Apply the same last-used display configuration to every condition.
        # Temporary difference waves inherit it for convenient inspection, but
        # _capture_current_erp_display_state() deliberately ignores differences.
        if self._erp_display_state:
            self.erp_viewer.apply_display_state(self._erp_display_state)
        self._manual_latency_ms = None
        self._component_selection_changed()
        self._draw_current_measurement_markers()

    def _component_definition_edited(self, *_):
        # Keep the protocol and plot annotations synchronized while the user edits
        # names/windows. Stale measurements whose component name is no longer
        # defined remain in the package for provenance, but are not drawn.
        self.protocol.components = self._components_from_table()
        self._refresh_protocol_summaries()
        self._component_selection_changed()
        self._draw_current_measurement_markers()

    def _component_selection_changed(self):
        component = self._selected_component()
        if component is not None and hasattr(self, "erp_viewer"):
            method_label = "mean voltage" if component.method == "mean" else component.method
            self.erp_viewer.set_window(component.start_ms, component.end_ms, f"{component.name}: {method_label}")

    def _erp_clicked(self, latency_ms: float, clicked_channel: str = ""):
        """Create a manual ERP point at the clicked latency/channel.

        Automatic peak detection remains window-constrained, but a manual pick is
        intentionally allowed anywhere inside the recorded epoch. In stacked mode
        the viewer reports the waveform that was clicked, so the operator is not
        restricted to the single Primary-channel combo box.
        """
        component = self._selected_component()
        ev, condition, temporary = self._current_display_evoked()
        if component is None or ev is None:
            return

        channel = clicked_channel if clicked_channel in ev.ch_names else self.channel_combo.currentText()
        if channel not in ev.ch_names:
            return
        times_ms = ev.times * 1000.0
        if not len(times_ms) or latency_ms < float(times_ms[0]) or latency_ms > float(times_ms[-1]):
            return

        self._manual_latency_ms = float(latency_ms)
        y = float(np.interp(latency_ms, times_ms, ev.data[ev.ch_names.index(channel)] * 1e6))
        outside = not (component.start_ms <= latency_ms <= component.end_ms)
        suffix = " (outside configured window)" if outside else ""

        if temporary:
            # Difference-wave picks are local inspection aids only and are never
            # inserted into the persistent subject measurement collection.
            self.erp_viewer.add_marker(
                latency_ms, y, component.name,
                channel if len(self._erp_display_channels) > 1 else "",
            )
            self.status_label.setText(
                f"Temporary manual point {component.name} / {channel}: "
                f"{latency_ms:.1f} ms, {y:.3f} µV{suffix}. Not saved."
            )
            return

        # A manual assignment is an explicit override for this component/channel.
        # Remove any previous automatic/manual result for the same component so a
        # single physiological label cannot leave two competing points on-screen.
        self.measurements = [
            m for m in self.measurements
            if not (m.condition == condition and m.channel == channel and m.component == component.name)
        ]
        n_epochs = self._n_epochs_for_condition(condition)
        note = "Manual waveform click"
        if outside:
            note += "; selected outside configured automatic-detection window"
        self.measurements.append(MeasurementResult(
            condition=condition,
            channel=channel,
            component=component.name,
            method="manual",
            window_start_ms=float(component.start_ms),
            window_end_ms=float(component.end_ms),
            amplitude_uv=y,
            latency_ms=float(latency_ms),
            n_epochs=n_epochs,
            notes=note,
        ))
        self._refresh_measurement_filters()
        self._refresh_result_table()
        self._draw_current_measurement_markers()
        self.status_label.setText(
            f"Manual point {component.name} / {channel}: {latency_ms:.1f} ms, {y:.3f} µV{suffix}."
        )

    def _n_epochs_for_condition(self, condition: str) -> int:
        if condition in self._loaded_average_counts:
            return int(self._loaded_average_counts[condition])
        if self.clean_epochs is not None and condition in self.clean_epochs.event_id:
            return int(len(self.clean_epochs[condition]))
        if condition in self.evokeds:
            return int(getattr(self.evokeds[condition], "nave", 0) or 0)
        return 0

    def _upsert_measurement(self, result: MeasurementResult) -> bool:
        """Insert one authoritative measurement for condition/channel/component.

        A manual pick is an explicit human override and therefore wins over later
        automatic detection. Automatic re-detection replaces older automatic
        results, but never silently replaces a manual point.
        """
        identity = (result.condition, result.channel, result.component)
        same = [m for m in self.measurements if (m.condition, m.channel, m.component) == identity]

        if result.method == "manual":
            self.measurements = [
                m for m in self.measurements
                if (m.condition, m.channel, m.component) != identity
            ]
            self.measurements.append(result)
            return True

        # Manual selection remains authoritative until the user removes/relabels it.
        # Also clean any stale automatic duplicate that may have been loaded from
        # an older v0.7 package where both were allowed to coexist.
        if any(m.method == "manual" for m in same):
            self.measurements = [
                m for m in self.measurements
                if (m.condition, m.channel, m.component) != identity or m.method == "manual"
            ]
            return False

        # There should be only one active automatic result for a component at a
        # channel, even if its configured method was changed peak <-> mean/area.
        self.measurements = [
            m for m in self.measurements
            if (m.condition, m.channel, m.component) != identity
        ]
        self.measurements.append(result)
        return True

    def _normalize_measurement_authority(self):
        """Collapse legacy duplicate measurements to one result per ERP identity."""
        chosen: dict[tuple[str, str, str], MeasurementResult] = {}
        for m in self.measurements:
            key = (m.condition, m.channel, m.component)
            old = chosen.get(key)
            if old is None or m.method == "manual" or old.method != "manual":
                chosen[key] = m
        self.measurements = list(chosen.values())

    def measure_current(self):
        ev, condition, temporary = self._current_display_evoked()
        channel = self.channel_combo.currentText(); component = self._selected_component()
        if ev is None or not channel or component is None or channel not in ev.ch_names:
            QMessageBox.information(self, "Nothing to measure", "Average epochs and select a component/channel first."); return
        n_epochs = 0 if temporary else self._n_epochs_for_condition(condition)
        try:
            result = engine.measure_evoked(
                ev, condition, channel, component, n_epochs,
                self._manual_latency_ms if component.method == "manual" else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Measurement failed", str(exc)); return
        if temporary:
            self._draw_current_measurement_markers()
            if result.latency_ms is not None:
                self.erp_viewer.add_marker(result.latency_ms, result.amplitude_uv, result.component, channel)
            self.status_label.setText(
                f"Temporary difference measurement: {result.component} = {result.amplitude_uv:.3f} µV"
                + (f" at {result.latency_ms:.1f} ms" if result.latency_ms is not None else "")
                + ". Not added to saved subject measurements."
            )
            return
        changed = self._upsert_measurement(result)
        self._refresh_measurement_filters(); self._refresh_result_table(); self._draw_current_measurement_markers()
        if changed:
            self.status_label.setText(f"Measured {result.component} / {result.condition} / {result.channel}.")
        else:
            self.status_label.setText(
                f"Kept existing manual {result.component} / {result.channel}; automatic measurement did not overwrite it."
            )

    def _channels_for_component(self, component: ComponentDefinition, available: list[str] | None = None) -> list[str]:
        if available is None:
            available = list(self.channel_combo.itemText(i) for i in range(self.channel_combo.count()))
        if not component.channels:
            # For automatic detection with no explicit protocol channel, use the
            # selected ERP display set; this makes multi-channel inspection useful
            # without silently running every scalp electrode.
            selected = [ch for ch in self._erp_display_channels if ch in available]
            return selected or ([self.channel_combo.currentText()] if self.channel_combo.currentText() else [])
        if len(component.channels) == 1 and component.channels[0].upper() == "ALL":
            return available
        return [ch for ch in component.channels if ch in available]

    def _automatic_component_definition(self, component: ComponentDefinition) -> ComponentDefinition | None:
        """Return the component definition that automatic measurement should use.

        The toolbar can override the per-component method with a window peak or
        mean-amplitude strategy without permanently rewriting the protocol row.
        Manual components are skipped when following component settings.
        """
        mode = "component"
        if hasattr(self, "auto_measure_mode"):
            mode = str(self.auto_measure_mode.currentData() or "component")
        if mode == "component":
            if component.method == "manual":
                return None
            return copy.deepcopy(component)
        if mode in {"peak", "mean"}:
            c = copy.deepcopy(component)
            c.method = mode
            return c
        return copy.deepcopy(component)

    def measure_current_condition_automatic(self):
        ev, condition, temporary = self._current_display_evoked()
        if ev is None:
            QMessageBox.information(self, "No average", "Average accepted epochs first."); return
        self.protocol.components = self._components_from_table()
        available = [ch for ch in ev.ch_names if ch in set(self.channel_combo.itemText(i) for i in range(self.channel_combo.count()))]
        added = 0; errors = []
        # Temporary difference: show window-peak markers locally when latency exists,
        # but deliberately never persist any result.
        if temporary:
            self.erp_viewer.clear_markers()
            for component in self.protocol.components:
                auto_component = self._automatic_component_definition(component)
                if auto_component is None:
                    continue
                for ch in self._channels_for_component(auto_component, available):
                    try:
                        result = engine.measure_evoked(ev, condition, ch, auto_component, 0)
                        if result.latency_ms is not None:
                            self.erp_viewer.add_marker(result.latency_ms, result.amplitude_uv, result.component, ch if len(self._erp_display_channels) > 1 else "")
                        added += 1
                    except Exception as exc:
                        errors.append(f"{component.name}/{ch}: {exc}")
            self.status_label.setText(f"Computed {added} temporary difference-wave automatic result(s); none were saved.")
            return

        n_epochs = self._n_epochs_for_condition(condition)
        manual_preserved = 0
        for component in self.protocol.components:
            auto_component = self._automatic_component_definition(component)
            if auto_component is None:
                continue
            channels = self._channels_for_component(auto_component, available)
            for ch in channels:
                try:
                    result = engine.measure_evoked(ev, condition, ch, auto_component, n_epochs)
                    if self._upsert_measurement(result):
                        added += 1
                    else:
                        manual_preserved += 1
                except Exception as exc:
                    errors.append(f"{component.name}/{ch}: {exc}")
        self._refresh_measurement_filters(); self._refresh_result_table(); self._draw_current_measurement_markers()
        mode_text = self.auto_measure_mode.currentText() if hasattr(self, "auto_measure_mode") else "component setting"
        msg = f"Measured/updated {added} automatic result(s) for {condition} using {mode_text}."
        if manual_preserved:
            msg += f" Preserved {manual_preserved} manual override(s)."
        self.status_label.setText(msg)
        if errors:
            QMessageBox.information(self, "Measurement summary", f"{len(errors)} result(s) were skipped.\n\n" + "\n".join(errors[:20]))

    def measure_all_automatic(self):
        if not self.evokeds:
            QMessageBox.information(self, "No averages", "Average accepted epochs first."); return
        self.protocol.components = self._components_from_table()
        available = list(self.channel_combo.itemText(i) for i in range(self.channel_combo.count()))
        added = 0; errors = []; manual_preserved = 0
        for condition, evoked in self.evokeds.items():
            n_epochs = self._n_epochs_for_condition(condition)
            for component in self.protocol.components:
                auto_component = self._automatic_component_definition(component)
                if auto_component is None:
                    continue
                channels = self._channels_for_component(auto_component, available)
                if not channels:
                    errors.append(f"{component.name}: no valid channels")
                    continue
                for ch in channels:
                    try:
                        result = engine.measure_evoked(evoked, condition, ch, auto_component, n_epochs)
                        if self._upsert_measurement(result):
                            added += 1
                        else:
                            manual_preserved += 1
                    except Exception as exc:
                        errors.append(f"{condition}/{component.name}/{ch}: {exc}")
        self._refresh_measurement_filters(); self._refresh_result_table(); self._draw_current_measurement_markers()
        mode_text = self.auto_measure_mode.currentText() if hasattr(self, "auto_measure_mode") else "component setting"
        msg = f"Measured/updated {added} automatic result(s) across real subject conditions using {mode_text}."
        if manual_preserved:
            msg += f" Preserved {manual_preserved} manual override(s)."
        self.status_label.setText(msg)
        if errors:
            QMessageBox.information(self, "Measurement summary", f"{len(errors)} result(s) were skipped.\n\n" + "\n".join(errors[:20]))

    def _draw_current_measurement_markers(self):
        if not hasattr(self, "erp_viewer"):
            return
        self.erp_viewer.clear_markers()
        if self._difference_active:
            return
        condition = self.condition_combo.currentText()
        display = set(self._erp_display_channels)
        multi = len(display) > 1
        # Only active component definitions are rendered. This makes deleting a
        # component immediately declutter the waveform even if an older package
        # happened to contain stale measurements with that name.
        active_components = {c.name for c in self._components_from_table()}
        for x in self.measurements:
            if (
                x.condition != condition or x.latency_ms is None or
                x.channel not in display or x.component not in active_components
            ):
                continue
            key = (x.condition, x.channel, x.component, x.method)
            self.erp_viewer.add_marker(
                x.latency_ms,
                x.amplitude_uv,
                x.component,
                x.channel if multi else "",
                measurement_key=key,
            )

    def _find_measurement_by_key(self, key):
        if not key:
            return None, None
        try:
            wanted = tuple(key)
        except TypeError:
            return None, None
        for i, m in enumerate(self.measurements):
            current = (m.condition, m.channel, m.component, m.method)
            if current == wanted:
                return i, m
        return None, None

    def _erp_marker_context_requested(self, marker_def: dict):
        """Right-click menu for a plotted ERP measurement marker."""
        idx, measurement = self._find_measurement_by_key(marker_def.get("measurement_key"))
        if measurement is None:
            return

        menu = QMenu(self)
        title = menu.addAction(f"{measurement.component} · {measurement.channel} · {measurement.latency_ms:.1f} ms")
        title.setEnabled(False)
        menu.addSeparator()
        remove_action = menu.addAction("Remove marker / measurement")

        reassign_menu = menu.addMenu("Change component label")
        component_names = [c.name for c in self._components_from_table() if c.name]
        for name in component_names:
            action = reassign_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == measurement.component)
            action.triggered.connect(lambda _checked=False, n=name, k=marker_def.get("measurement_key"): self._reassign_erp_marker(k, n))

        chosen = menu.exec(QCursor.pos())
        if chosen is remove_action:
            # Re-resolve after the menu closes in case another action changed data.
            idx, measurement = self._find_measurement_by_key(marker_def.get("measurement_key"))
            if idx is not None:
                removed = self.measurements.pop(idx)
                self._refresh_measurement_filters()
                self._refresh_result_table()
                self._draw_current_measurement_markers()
                self.status_label.setText(
                    f"Removed {removed.component} marker / {removed.channel} / {removed.condition}."
                )

    def _reassign_erp_marker(self, measurement_key, new_component_name: str):
        idx, measurement = self._find_measurement_by_key(measurement_key)
        if measurement is None:
            return
        target = next((c for c in self._components_from_table() if c.name == new_component_name), None)
        if target is None:
            return
        if measurement.component == new_component_name:
            return

        old_name = measurement.component
        # Reassignment is a human decision, so the result becomes a manual
        # measurement even when the original point came from automatic detection.
        replacement = MeasurementResult(
            condition=measurement.condition,
            channel=measurement.channel,
            component=new_component_name,
            method="manual",
            window_start_ms=float(target.start_ms),
            window_end_ms=float(target.end_ms),
            amplitude_uv=float(measurement.amplitude_uv),
            latency_ms=None if measurement.latency_ms is None else float(measurement.latency_ms),
            n_epochs=int(measurement.n_epochs),
            notes=(measurement.notes + "; " if measurement.notes else "") + f"Relabeled manually from {old_name}",
        )

        # Do not leave two markers with the same condition/channel/component after
        # reassignment. The newly relabeled point is the explicit manual choice.
        self.measurements = [
            m for j, m in enumerate(self.measurements)
            if j == idx or not (
                m.condition == replacement.condition and
                m.channel == replacement.channel and
                m.component == replacement.component
            )
        ]
        # The original index may have shifted if duplicates before it were removed;
        # locate the original object by identity when possible, then replace it.
        replaced = False
        for j, m in enumerate(self.measurements):
            if m is measurement:
                self.measurements[j] = replacement
                replaced = True
                break
        if not replaced:
            self.measurements.append(replacement)

        self._refresh_measurement_filters()
        self._refresh_result_table()
        self._draw_current_measurement_markers()
        self.status_label.setText(
            f"Changed marker {old_name} → {new_component_name} on {replacement.channel} at {replacement.latency_ms:.1f} ms."
        )

    def _refresh_measurement_filters(self):
        if not hasattr(self, "measure_filter_condition"):
            return
        specs = [
            (self.measure_filter_condition, sorted({x.condition for x in self.measurements})),
            (self.measure_filter_channel, sorted({x.channel for x in self.measurements})),
            (self.measure_filter_component, sorted({x.component for x in self.measurements})),
            (self.measure_filter_method, sorted({x.method for x in self.measurements})),
        ]
        for combo, values in specs:
            old = combo.currentText() or "All"
            combo.blockSignals(True); combo.clear(); combo.addItem("All"); combo.addItems(values)
            if old in ["All"] + values: combo.setCurrentText(old)
            combo.blockSignals(False)

    def _refresh_result_table(self, *_):
        if not hasattr(self, "result_table"): return
        filters = {
            "condition": self.measure_filter_condition.currentText() if hasattr(self, "measure_filter_condition") else "All",
            "channel": self.measure_filter_channel.currentText() if hasattr(self, "measure_filter_channel") else "All",
            "component": self.measure_filter_component.currentText() if hasattr(self, "measure_filter_component") else "All",
            "method": self.measure_filter_method.currentText() if hasattr(self, "measure_filter_method") else "All",
        }
        visible = []
        for x in self.measurements:
            if filters["condition"] not in {"", "All"} and x.condition != filters["condition"]: continue
            if filters["channel"] not in {"", "All"} and x.channel != filters["channel"]: continue
            if filters["component"] not in {"", "All"} and x.component != filters["component"]: continue
            if filters["method"] not in {"", "All"} and x.method != filters["method"]: continue
            visible.append(x)
        was_sorting = self.result_table.isSortingEnabled(); self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(visible))
        for r, x in enumerate(visible):
            vals = [
                x.condition, x.channel, x.component, x.method, f"{x.window_start_ms:g}", f"{x.window_end_ms:g}",
                f"{x.amplitude_uv:.6g}", "" if x.latency_ms is None else f"{x.latency_ms:.3f}", str(x.n_epochs), x.notes,
            ]
            for c, v in enumerate(vals): self.result_table.setItem(r, c, QTableWidgetItem(v))
        self.result_table.setSortingEnabled(was_sorting)

    def save_average_subject_package(self):
        if not self.evokeds:
            QMessageBox.information(self, "No averages", "Average accepted epochs first.")
            return
        self._sync_protocol_from_ui()
        default = (self.metadata.subject_id.strip() or (Path(self.metadata.input_path).stem if self.metadata.input_path else "subject")) + ".erpavg"
        path, _ = QFileDialog.getSaveFileName(self, "Save averaged subject", default, "ERP Workbench averaged subject (*.erpavg)")
        if not path: return
        p = Path(path)
        if p.suffix.lower() != ".erpavg": p = p.with_suffix(".erpavg")
        if not self._confirm_overwrite(p): return
        counts = self._loaded_average_counts or ({k: self._n_epochs_for_condition(k) for k in self.evokeds})
        self._normalize_measurement_authority()
        try:
            engine.save_average_package(
                p, self.evokeds, self.metadata, self.preprocessing, self.protocol, self.measurements,
                condition_counts=counts, epochs=self.epochs, review=self.review if self.epochs is not None else None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Average save failed", str(exc)); return
        self.status_label.setText(f"Saved averaged-subject package: {p.name}. Temporary difference waves were excluded.")

    def load_average_subject_package(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open averaged subject", "", "ERP Workbench averaged subject (*.erpavg)")
        if not path: return
        try:
            payload = engine.load_average_package(path)
            manifest = payload["manifest"]; evokeds = payload["evokeds"]
            if not evokeds: raise ValueError("The package contains no averaged conditions.")
            self.evokeds = evokeds
            self.clean_epochs = None
            self._loaded_average_counts = {str(k): int(v) for k, v in manifest.get("condition_counts", {}).items()}
            self.measurements = [MeasurementResult(**x) for x in manifest.get("measurements", [])]
            self._normalize_measurement_authority()
            if manifest.get("protocol"):
                self.protocol = ProtocolDefinition.from_dict(manifest["protocol"])
                self._load_protocol_into_ui(self.protocol)
            self.metadata.subject_id = str(manifest.get("subject_id", ""))
            self._difference_active = False; self._difference_evoked = None; self._difference_label = ""
            # Keep the last-used ERP display configuration across subjects.
            # Keep the user's display-channel preference and intersect it with
            # the newly opened subject instead of resetting to one electrode.
            self._erp_display_channels = [ch for ch in self._erp_display_channels if any(ch in ev.ch_names for ev in evokeds.values())]
            self._populate_average_controls(); self._refresh_measurement_filters(); self._refresh_result_table(); self._refresh_erp_plot()
            self.tabs.setCurrentIndex(4)
        except Exception as exc:
            QMessageBox.critical(self, "Open averaged subject failed", str(exc)); return
        self.status_label.setText(
            f"Opened averaged subject {Path(path).name}: {len(self.evokeds)} condition(s). "
            "Averaged-subject package opened successfully."
        )

    # ---------- v0.8 grand averaging ----------
    def _build_grand_average_tab(self):
        page = QWidget(); outer = QVBoxLayout(page)
        self.grand_outer_layout = outer
        outer.setContentsMargins(8, 8, 8, 8); outer.setSpacing(7)

        # Collapsible subject-file/validation panel. Once the grand average is
        # computed this can shrink to one header line and release the space to
        # the scientific waveform workspace below.
        self.grand_files_panel = QFrame(); files_outer = QVBoxLayout(self.grand_files_panel)
        files_outer.setContentsMargins(0, 0, 0, 0); files_outer.setSpacing(4)
        file_head = QHBoxLayout()
        self.grand_files_toggle = QToolButton(); self.grand_files_toggle.setText("▾ Averaged subjects (.erpavg)")
        self.grand_files_toggle.setToolTip("Collapse/expand the averaged-subject selection and protocol-validation area.")
        self.grand_files_toggle.clicked.connect(self._toggle_grand_files_panel)
        self.grand_files_summary = QLabel("No files selected")
        self.grand_files_summary.setProperty("muted", True)
        file_head.addWidget(self.grand_files_toggle); file_head.addWidget(self.grand_files_summary); file_head.addStretch(1)
        files_outer.addLayout(file_head)

        self.grand_files_body = QWidget(); fl = QVBoxLayout(self.grand_files_body)
        fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(4)
        buttons = QHBoxLayout()
        add_files = QPushButton("Add averaged files…"); add_files.clicked.connect(self._grand_add_files)
        add_folder = QPushButton("Add folder recursively…"); add_folder.clicked.connect(self._grand_add_folder_recursive)
        remove = QPushButton("Remove selected"); remove.clicked.connect(self._grand_remove_selected)
        clear = QPushButton("Clear"); clear.clicked.connect(self._grand_clear_files)
        validate = QPushButton("Validate + grand average"); validate.clicked.connect(self._compute_grand_average)
        buttons.addWidget(add_files); buttons.addWidget(add_folder); buttons.addWidget(remove); buttons.addWidget(clear)
        buttons.addSpacing(16); buttons.addWidget(validate); buttons.addStretch(1)
        fl.addLayout(buttons)

        self.grand_file_table = QTableWidget(0, 6)
        self.grand_file_table.setHorizontalHeaderLabels(["File", "Subject", "Protocol", "Conditions", "Protocol ID", "Status"])
        self.grand_file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grand_file_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grand_file_table.verticalHeader().setVisible(False)
        self.grand_file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4, 5):
            self.grand_file_table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.grand_file_table.setMaximumHeight(210)
        fl.addWidget(self.grand_file_table)
        self.grand_validation_label = QLabel(
            "Select at least two .erpavg files. Validation is strict: saved protocol, condition set, time axis, sampling rate and electrode set must agree."
        )
        self.grand_validation_label.setProperty("muted", True); self.grand_validation_label.setWordWrap(True)
        fl.addWidget(self.grand_validation_label)
        files_outer.addWidget(self.grand_files_body)
        outer.addWidget(self.grand_files_panel, 0)

        # Row 1: condition/channel/view controls.
        controls = QHBoxLayout()
        prev_btn = QPushButton("◀"); prev_btn.setFixedWidth(34); prev_btn.clicked.connect(lambda: self._step_grand_condition(-1))
        next_btn = QPushButton("▶"); next_btn.setFixedWidth(34); next_btn.clicked.connect(lambda: self._step_grand_condition(1))
        self.grand_condition_combo = QComboBox(); self.grand_condition_combo.setMinimumWidth(150)
        self.grand_condition_combo.currentTextChanged.connect(self._grand_condition_changed)
        self.grand_channel_combo = QComboBox(); self.grand_channel_combo.setMinimumWidth(90)
        self.grand_channel_combo.currentTextChanged.connect(self._grand_primary_channel_changed)
        choose_ch = QPushButton("Display channels…"); choose_ch.clicked.connect(self._select_grand_display_channels)
        self.grand_channel_summary = QLabel("None"); self.grand_channel_summary.setProperty("muted", True)
        self.grand_view_mode = QComboBox(); self.grand_view_mode.addItems(["Butterfly", "Stacked"])
        self.grand_view_mode.currentTextChanged.connect(self._grand_view_mode_changed)
        controls.addWidget(QLabel("Condition")); controls.addWidget(prev_btn); controls.addWidget(self.grand_condition_combo); controls.addWidget(next_btn)
        controls.addSpacing(12); controls.addWidget(QLabel("Primary")); controls.addWidget(self.grand_channel_combo)
        controls.addWidget(choose_ch); controls.addWidget(self.grand_channel_summary)
        controls.addSpacing(12); controls.addWidget(QLabel("View")); controls.addWidget(self.grand_view_mode); controls.addStretch(1)
        outer.addLayout(controls)

        # Row 2: same measurement workflow as the subject-average tab, plus
        # explicitly exportable-but-nonpermanent difference waves.
        actions = QHBoxLayout()
        measure = QPushButton("Measure selected component"); measure.clicked.connect(self._grand_measure_current)
        self.grand_auto_measure_mode = QComboBox()
        self.grand_auto_measure_mode.addItem("Use component setting", "component")
        self.grand_auto_measure_mode.addItem("Window peak", "peak")
        self.grand_auto_measure_mode.addItem("Mean amplitude", "mean")
        auto_current = QPushButton("Auto-detect current condition"); auto_current.clicked.connect(self._grand_measure_current_automatic)
        auto_all = QPushButton("Auto-detect all conditions"); auto_all.clicked.connect(self._grand_measure_all_automatic)
        actions.addWidget(measure); actions.addSpacing(8); actions.addWidget(QLabel("Auto method")); actions.addWidget(self.grand_auto_measure_mode)
        actions.addWidget(auto_current); actions.addWidget(auto_all)
        actions.addSpacing(18); actions.addWidget(QLabel("Difference"))
        self.grand_diff_a_combo = QComboBox(); self.grand_diff_b_combo = QComboBox()
        self.grand_diff_a_combo.setMinimumWidth(120); self.grand_diff_b_combo.setMinimumWidth(120)
        self.grand_diff_a_combo.currentTextChanged.connect(self._grand_difference_pair_changed)
        self.grand_diff_b_combo.currentTextChanged.connect(self._grand_difference_pair_changed)
        diff_btn = QPushButton("View A − B"); diff_btn.clicked.connect(self._show_grand_difference_wave)
        return_btn = QPushButton("Return to condition"); return_btn.clicked.connect(self._clear_grand_difference_wave)
        self.grand_diff_export_check = QCheckBox("Include this difference in Excel")
        self.grand_diff_export_check.setToolTip(
            "Difference waves remain local analysis objects and never become grand-average conditions. "
            "Checking this only includes the selected A−B pair in the exported workbook."
        )
        self.grand_diff_export_check.toggled.connect(self._grand_difference_export_toggled)
        actions.addWidget(self.grand_diff_a_combo); actions.addWidget(QLabel("−")); actions.addWidget(self.grand_diff_b_combo)
        actions.addWidget(diff_btn); actions.addWidget(return_btn); actions.addWidget(self.grand_diff_export_check)
        actions.addStretch(1)
        export_grand = QPushButton("Export grand-average Excel…"); export_grand.clicked.connect(self._export_grand_average_excel)
        actions.addWidget(export_grand)
        outer.addLayout(actions)

        # Components + plot use a splitter so collapsing Components returns width
        # directly to the waveform.
        upper = QSplitter(Qt.Orientation.Horizontal); self.grand_upper_splitter = upper
        self.grand_component_panel = QFrame(); gc_outer = QVBoxLayout(self.grand_component_panel)
        gc_outer.setContentsMargins(0, 0, 0, 0); gc_outer.setSpacing(4)
        gc_head = QHBoxLayout()
        self.grand_component_toggle = QToolButton(); self.grand_component_toggle.setText("▾ Components")
        self.grand_component_toggle.clicked.connect(self._toggle_grand_component_panel)
        add_comp = QPushButton("+"); add_comp.setFixedWidth(30); add_comp.setToolTip("Add component")
        rem_comp = QPushButton("−"); rem_comp.setFixedWidth(30); rem_comp.setToolTip("Remove selected component")
        add_comp.clicked.connect(self._grand_add_component_row); rem_comp.clicked.connect(self._grand_remove_component_row)
        gc_head.addWidget(self.grand_component_toggle); gc_head.addStretch(1); gc_head.addWidget(add_comp); gc_head.addWidget(rem_comp)
        gc_outer.addLayout(gc_head)
        self.grand_component_body = QWidget(); gcb = QVBoxLayout(self.grand_component_body); gcb.setContentsMargins(0, 0, 0, 0)
        self.grand_component_table = QTableWidget(0, 6)
        self.grand_component_table.setHorizontalHeaderLabels(["Component", "Start ms", "End ms", "Polarity", "Method", "Channels"])
        for c in range(5): self.grand_component_table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.grand_component_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.grand_component_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grand_component_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grand_component_table.itemSelectionChanged.connect(self._grand_component_selection_changed)
        self.grand_component_table.itemChanged.connect(self._grand_component_definition_edited)
        gcb.addWidget(self.grand_component_table)
        hint = QLabel(
            "Grand-average component plan. Automatic peaks stay inside the configured window; mean = mean voltage across the window. "
            "Manual clicks may be placed anywhere. In Stacked view the clicked waveform chooses the electrode. Right-click a marker to remove or relabel it."
        )
        hint.setProperty("muted", True); hint.setWordWrap(True); gcb.addWidget(hint)
        gc_outer.addWidget(self.grand_component_body, 1); self.grand_component_panel.setMinimumWidth(360)

        self.grand_viewer = ERPViewer()
        self.grand_viewer.pointClicked.connect(self._grand_erp_clicked)
        self.grand_viewer.markerContextRequested.connect(self._grand_marker_context_requested)
        self.grand_viewer.previousRequested.connect(lambda: self._step_grand_condition(-1))
        self.grand_viewer.nextRequested.connect(lambda: self._step_grand_condition(1))
        self.grand_viewer.clear_view("Select compatible averaged-subject files, then run Validate + grand average.")
        upper.addWidget(self.grand_component_panel); upper.addWidget(self.grand_viewer)
        upper.setStretchFactor(0, 0); upper.setStretchFactor(1, 1); upper.setSizes([420, 1080])
        outer.addWidget(upper, 4)

        # Collapsible/filterable grand measurements browser.
        self.grand_measure_panel = QFrame(); gm_outer = QVBoxLayout(self.grand_measure_panel)
        gm_outer.setContentsMargins(0, 0, 0, 0); gm_outer.setSpacing(4)
        gm_head = QHBoxLayout()
        self.grand_measurements_toggle = QToolButton(); self.grand_measurements_toggle.setText("▾ Measurements")
        self.grand_measurements_toggle.clicked.connect(self._toggle_grand_measurements_panel)
        self.grand_measure_filter_condition = QComboBox(); self.grand_measure_filter_channel = QComboBox()
        self.grand_measure_filter_component = QComboBox(); self.grand_measure_filter_method = QComboBox()
        for combo in (self.grand_measure_filter_condition, self.grand_measure_filter_channel, self.grand_measure_filter_component, self.grand_measure_filter_method):
            combo.setMinimumWidth(100); combo.currentTextChanged.connect(self._refresh_grand_measurement_table)
        gm_head.addWidget(self.grand_measurements_toggle); gm_head.addSpacing(10)
        gm_head.addWidget(QLabel("Condition")); gm_head.addWidget(self.grand_measure_filter_condition)
        gm_head.addWidget(QLabel("Channel")); gm_head.addWidget(self.grand_measure_filter_channel)
        gm_head.addWidget(QLabel("Component")); gm_head.addWidget(self.grand_measure_filter_component)
        gm_head.addWidget(QLabel("Method")); gm_head.addWidget(self.grand_measure_filter_method); gm_head.addStretch(1)
        gm_outer.addLayout(gm_head)
        self.grand_measurements_body = QWidget(); gmb = QVBoxLayout(self.grand_measurements_body); gmb.setContentsMargins(0, 0, 0, 0)
        self.grand_result_table = QTableWidget(0, 10)
        self.grand_result_table.setHorizontalHeaderLabels([
            "Condition / difference", "Channel", "Component", "Method", "Start ms", "End ms",
            "Amplitude (µV*)", "Latency ms", "Subjects", "Notes"
        ])
        self.grand_result_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        self.grand_result_table.verticalHeader().setVisible(False); self.grand_result_table.setSortingEnabled(True)
        gmb.addWidget(self.grand_result_table)
        foot = QLabel(
            "Grand measurements can include real conditions and local A−B difference waves. Difference rows are exported only when that pair is checked above."
        )
        foot.setProperty("muted", True); foot.setWordWrap(True); gmb.addWidget(foot)
        gm_outer.addWidget(self.grand_measurements_body, 1)
        outer.addWidget(self.grand_measure_panel, 2)
        outer.setStretch(3, 4); outer.setStretch(4, 2)

        method_note = QLabel(
            "Grand averaging is subject-level: each .erpavg contributes one condition Evoked with equal subject weight. "
            "Difference waves are never inserted into the real condition collection."
        )
        method_note.setProperty("muted", True); method_note.setWordWrap(True); outer.addWidget(method_note)
        self.tabs.addTab(page, "6  Grand Average")
        self._load_grand_components_table(self.protocol.components)


    # ---------- Settings / Help ----------
    def _build_settings_tab(self):
        page = QWidget(); outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12); outer.setSpacing(10)

        title = QLabel("<h2>Settings</h2>"); title.setTextFormat(Qt.TextFormat.RichText)
        intro = QLabel(
            "Shortcut and display preferences are stored for this Windows user account and do not change the EEG data."
        )
        intro.setWordWrap(True); intro.setProperty("muted", True)
        outer.addWidget(title); outer.addWidget(intro)

        shortcut_box = QGroupBox("Keyboard shortcuts")
        sg = QGridLayout(shortcut_box)
        sg.addWidget(QLabel("Action"), 0, 0); sg.addWidget(QLabel("Shortcut"), 0, 1)
        self.shortcut_editors: dict[str, QKeySequenceEdit] = {}
        row = 1
        for key, label in SHORTCUT_LABELS.items():
            sg.addWidget(QLabel(label), row, 0)
            editor = QKeySequenceEdit(QKeySequence(self._shortcut_map.get(key, DEFAULT_SHORTCUTS[key])))
            editor.setMaximumSequenceLength(1)
            self.shortcut_editors[key] = editor
            sg.addWidget(editor, row, 1)
            row += 1
        shortcut_buttons = QHBoxLayout()
        save_shortcuts = QPushButton("Apply shortcuts")
        save_shortcuts.clicked.connect(self._save_shortcut_settings)
        reset_shortcuts = QPushButton("Restore shortcut defaults")
        reset_shortcuts.clicked.connect(self._reset_shortcut_settings)
        shortcut_buttons.addWidget(save_shortcuts); shortcut_buttons.addWidget(reset_shortcuts); shortcut_buttons.addStretch(1)
        sg.addLayout(shortcut_buttons, row, 0, 1, 2)
        shortcut_note = QLabel("Shortcut remapping applies to waveform viewers. The Epoch Review reject toggle is remappable separately as R by default.")
        shortcut_note.setWordWrap(True); shortcut_note.setProperty("muted", True)
        sg.addWidget(shortcut_note, row + 1, 0, 1, 2)
        outer.addWidget(shortcut_box)

        color_box = QGroupBox("Non-butterfly waveform trace color")
        cg = QGridLayout(color_box)
        self.trace_color_hex = QLineEdit(self._trace_color or "")
        self.trace_color_hex.setPlaceholderText("Default theme color, or e.g. #66B3FF")
        self.trace_color_r = ReliableSpinBox(); self.trace_color_g = ReliableSpinBox(); self.trace_color_b = ReliableSpinBox()
        for spin in (self.trace_color_r, self.trace_color_g, self.trace_color_b):
            spin.setRange(0, 255)
        initial = QColor(self._trace_color) if self._trace_color else QColor("#66B3FF")
        self.trace_color_r.setValue(initial.red()); self.trace_color_g.setValue(initial.green()); self.trace_color_b.setValue(initial.blue())
        self._color_settings_guard = False
        self.trace_color_hex.editingFinished.connect(self._hex_color_edited)
        for spin in (self.trace_color_r, self.trace_color_g, self.trace_color_b):
            spin.valueChanged.connect(self._rgb_color_edited)
        self.trace_color_swatch = QLabel("      ")
        self.trace_color_swatch.setMinimumWidth(70); self.trace_color_swatch.setFrameShape(QFrame.Shape.Box)
        self._update_trace_color_swatch(self._trace_color)
        palette_btn = QPushButton("Choose color…"); palette_btn.clicked.connect(self._choose_trace_color)
        apply_color_btn = QPushButton("Apply color"); apply_color_btn.clicked.connect(self._apply_trace_color_from_settings)
        default_color_btn = QPushButton("Use theme default"); default_color_btn.clicked.connect(self._reset_trace_color)
        cg.addWidget(QLabel("Hex"), 0, 0); cg.addWidget(self.trace_color_hex, 0, 1, 1, 3); cg.addWidget(palette_btn, 0, 4)
        cg.addWidget(QLabel("RGB"), 1, 0); cg.addWidget(QLabel("R"), 1, 1); cg.addWidget(self.trace_color_r, 1, 2)
        cg.addWidget(QLabel("G"), 1, 3); cg.addWidget(self.trace_color_g, 1, 4)
        cg.addWidget(QLabel("B"), 1, 5); cg.addWidget(self.trace_color_b, 1, 6); cg.addWidget(self.trace_color_swatch, 1, 7)
        cb = QHBoxLayout(); cb.addWidget(apply_color_btn); cb.addWidget(default_color_btn); cb.addStretch(1)
        cg.addLayout(cb, 2, 0, 1, 8)
        color_note = QLabel(
            "This color is used for stacked/continuous traces and ICA time-domain traces. "
            "ERP Butterfly mode deliberately keeps separate channel colors so overlapping electrodes remain distinguishable."
        )
        color_note.setWordWrap(True); color_note.setProperty("muted", True); cg.addWidget(color_note, 3, 0, 1, 8)
        outer.addWidget(color_box)

        path_box = QGroupBox("Local data folders")
        pl = QFormLayout(path_box)
        protocol_path_row = QWidget(); ppr = QHBoxLayout(protocol_path_row); ppr.setContentsMargins(0,0,0,0)
        self.settings_protocol_library_path_label = QLabel(str(self._protocol_library_dir))
        self.settings_protocol_library_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.settings_protocol_library_path_label.setWordWrap(True)
        change_protocol_path = QPushButton("Change…")
        change_protocol_path.clicked.connect(self._choose_protocol_library_dir)
        reset_protocol_path = QPushButton("Restore default")
        reset_protocol_path.clicked.connect(self._reset_protocol_library_dir)
        ppr.addWidget(self.settings_protocol_library_path_label, 1)
        ppr.addWidget(change_protocol_path)
        ppr.addWidget(reset_protocol_path)
        pl.addRow("Protocol library", protocol_path_row)
        path_note = QLabel("Protocols in this folder populate the Epoching protocol dropdown. Load JSON / Save JSON can still access a protocol anywhere else.")
        path_note.setWordWrap(True); path_note.setProperty("muted", True); pl.addRow(path_note)
        outer.addWidget(path_box)
        outer.addStretch(1)
        self.settings_page = page

    def _build_help_tab(self):
        page = QWidget(); outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12); outer.setSpacing(10)
        heading = QLabel("<h2>Methodology & readings</h2>"); heading.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(heading)
        note = QLabel(
            "Open any processing step for a local explanation of the method, why it matters, what ERP Workbench does, cautions, scientific reading and MNE documentation."
        )
        note.setWordWrap(True); note.setProperty("muted", True); outer.addWidget(note)

        reading = QGroupBox("Processing-step reading")
        rl = QVBoxLayout(reading)
        self.help_topic_list = QListWidget()
        for topic in HELP_TOPICS:
            self.help_topic_list.addItem(topic)
        self.help_topic_list.itemDoubleClicked.connect(lambda item: self._open_help_topic(item.text()))
        rl.addWidget(self.help_topic_list, 1)
        buttons = QHBoxLayout()
        open_topic = QPushButton("Open selected explanation…")
        open_topic.clicked.connect(self._open_selected_help_topic)
        buttons.addWidget(open_topic); buttons.addStretch(1)
        rl.addLayout(buttons)
        outer.addWidget(reading, 1)
        self.help_page = page

    def _show_settings_dialog(self):
        if not hasattr(self,"_settings_dialog") or self._settings_dialog is None:
            dlg=QDialog(self); dlg.setWindowTitle("ERP Workbench settings"); dlg.resize(760,680)
            lay=QVBoxLayout(dlg); lay.addWidget(self.settings_page,1)
            close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(dlg.hide); lay.addWidget(close)
            dlg.finished.connect(lambda *_: None); self._settings_dialog=dlg
        self._settings_dialog.show(); self._settings_dialog.raise_(); self._settings_dialog.activateWindow()

    def _show_help_dialog(self):
        if not hasattr(self,"_help_dialog") or self._help_dialog is None:
            dlg=QDialog(self); dlg.setWindowTitle("ERP Workbench — Methodology & readings"); dlg.resize(820,700)
            lay=QVBoxLayout(dlg); lay.addWidget(self.help_page,1)
            close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(dlg.hide); lay.addWidget(close)
            self._help_dialog=dlg
        self._help_dialog.show(); self._help_dialog.raise_(); self._help_dialog.activateWindow()

    def _open_selected_help_topic(self):
        item = self.help_topic_list.currentItem() if hasattr(self, "help_topic_list") else None
        if item is None:
            QMessageBox.information(self, "Help", "Select a processing step first.")
            return
        self._open_help_topic(item.text())

    def _open_help_topic(self, topic: str):
        info = HELP_TOPICS.get(str(topic))
        if not info:
            return
        dlg = HelpTopicDialog(str(topic), info, self)
        dlg.exec()

    def _update_trace_color_swatch(self, color: str):
        if not hasattr(self, "trace_color_swatch"):
            return
        q = QColor(color)
        if not q.isValid():
            self.trace_color_swatch.setStyleSheet("")
            self.trace_color_swatch.setText("Default")
            return
        self.trace_color_swatch.setText("")
        self.trace_color_swatch.setStyleSheet(f"background-color: {q.name()}; border: 1px solid #777;")

    def _rgb_color_edited(self, *_):
        if getattr(self, "_color_settings_guard", False):
            return
        color = QColor(self.trace_color_r.value(), self.trace_color_g.value(), self.trace_color_b.value())
        self._color_settings_guard = True
        try:
            self.trace_color_hex.setText(color.name().upper())
            self._update_trace_color_swatch(color.name())
        finally:
            self._color_settings_guard = False

    def _hex_color_edited(self):
        if getattr(self, "_color_settings_guard", False):
            return
        text = self.trace_color_hex.text().strip()
        if not text:
            self._update_trace_color_swatch("")
            return
        color = QColor(text)
        if not color.isValid():
            self._update_trace_color_swatch("")
            return
        self._color_settings_guard = True
        try:
            self.trace_color_hex.setText(color.name().upper())
            self.trace_color_r.setValue(color.red()); self.trace_color_g.setValue(color.green()); self.trace_color_b.setValue(color.blue())
            self._update_trace_color_swatch(color.name())
        finally:
            self._color_settings_guard = False

    def _choose_trace_color(self):
        current = QColor(self._trace_color) if self._trace_color else QColor(self.trace_color_r.value(), self.trace_color_g.value(), self.trace_color_b.value())
        chosen = QColorDialog.getColor(current, self, "Choose waveform trace color")
        if not chosen.isValid():
            return
        self.trace_color_hex.setText(chosen.name().upper())
        for spin, value in ((self.trace_color_r, chosen.red()), (self.trace_color_g, chosen.green()), (self.trace_color_b, chosen.blue())):
            old = spin.blockSignals(True); spin.setValue(value); spin.blockSignals(old)
        self._update_trace_color_swatch(chosen.name())

    def _apply_trace_color_from_settings(self):
        entered = self.trace_color_hex.text().strip()
        if entered:
            color = QColor(entered)
        else:
            color = QColor(self.trace_color_r.value(), self.trace_color_g.value(), self.trace_color_b.value())
        if not color.isValid():
            QMessageBox.warning(self, "Invalid color", "Enter a valid HTML hex color such as #66B3FF, or choose RGB values from 0 to 255.")
            return
        self._trace_color = color.name().upper()
        self.trace_color_hex.setText(self._trace_color)
        self.trace_color_r.setValue(color.red()); self.trace_color_g.setValue(color.green()); self.trace_color_b.setValue(color.blue())
        self.app_settings.setValue("display/trace_color", self._trace_color)
        self._update_trace_color_swatch(self._trace_color)
        self._apply_user_display_settings()
        self.status_label.setText(f"Waveform trace color set to {self._trace_color}. Butterfly channel colors are unchanged.")

    def _reset_trace_color(self):
        self._trace_color = ""
        self.app_settings.remove("display/trace_color")
        if hasattr(self, "trace_color_hex"):
            self.trace_color_hex.clear()
        self._update_trace_color_swatch("")
        self._apply_user_display_settings()
        self.status_label.setText("Waveform trace color restored to the theme default. Butterfly channel colors are unchanged.")

    def _save_shortcut_settings(self):
        proposed = {}
        for key, editor in self.shortcut_editors.items():
            value = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
            if not value:
                QMessageBox.warning(self, "Shortcut required", f"Choose a shortcut for: {SHORTCUT_LABELS[key]}")
                return
            proposed[key] = value
        inverse = {}
        duplicates = []
        for key, value in proposed.items():
            norm = value.casefold()
            if norm in inverse:
                duplicates.append((SHORTCUT_LABELS[inverse[norm]], SHORTCUT_LABELS[key], value))
            inverse[norm] = key
        if duplicates:
            details = "\n".join(f"{a} and {b}: {value}" for a, b, value in duplicates)
            QMessageBox.warning(self, "Duplicate shortcuts", "Each action needs a distinct shortcut.\n\n" + details)
            return
        self._shortcut_map = proposed
        for key, value in proposed.items():
            self.app_settings.setValue(f"shortcuts/{key}", value)
        self._apply_user_display_settings()
        self.status_label.setText("Shortcut settings applied.")

    def _reset_shortcut_settings(self):
        self._shortcut_map = dict(DEFAULT_SHORTCUTS)
        for key, editor in self.shortcut_editors.items():
            editor.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS[key]))
            self.app_settings.remove(f"shortcuts/{key}")
        self._apply_user_display_settings()
        self.status_label.setText("Shortcuts restored to defaults.")

    def _apply_user_display_settings(self):
        viewers = (
            "raw_viewer", "ica_fit_view", "ica_post_view", "epoch_viewer",
            "erp_viewer", "grand_viewer", "ica_view",
        )
        for name in viewers:
            viewer = getattr(self, name, None)
            if viewer is None:
                continue
            if hasattr(viewer, "set_shortcut_map"):
                try:
                    viewer.set_shortcut_map(self._shortcut_map)
                except Exception:
                    pass
            if hasattr(viewer, "set_trace_color"):
                try:
                    viewer.set_trace_color(self._trace_color or None)
                except Exception:
                    pass

        # Embedded viewers are all alive even when their tab is hidden. Their
        # local QShortcuts can therefore become ambiguous if a remapped key is
        # shared. Main-window routing below activates only the currently visible
        # waveform stage. The ICA pop-out browser remains independent and keeps
        # its own local shortcuts. Epoch Review retains its dedicated shortcuts.
        for name in ("raw_viewer", "ica_fit_view", "ica_post_view", "epoch_viewer", "erp_viewer", "grand_viewer", "ica_view"):
            viewer = getattr(self, name, None)
            for shortcut in getattr(viewer, "_shortcuts", []) if viewer is not None else []:
                shortcut.setEnabled(False)
        self._install_main_waveform_shortcuts()
        if hasattr(self, "_install_review_shortcuts"):
            try:
                self._install_review_shortcuts()
            except Exception:
                pass

    def _install_main_waveform_shortcuts(self):
        for shortcut in getattr(self, "_main_waveform_shortcuts", []):
            shortcut.setEnabled(False); shortcut.deleteLater()
        self._main_waveform_shortcuts = []
        for name in ("previous", "next", "time_in", "time_out", "sensitivity_up", "sensitivity_down", "toggle_reject"):
            key = str(self._shortcut_map.get(name, "") or "").strip()
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda n=name: self._dispatch_waveform_shortcut(n))
            self._main_waveform_shortcuts.append(shortcut)

    def _dispatch_waveform_shortcut(self, name: str):
        tab = self.tabs.currentIndex() if hasattr(self, "tabs") else -1

        # Epoch Review intentionally owns its navigation keys across the entire
        # page, including when the epoch table or filter combo has focus. This
        # avoids Qt child widgets swallowing Left/Right/R on Windows.
        if tab == 3:
            viewer = getattr(self, "epoch_viewer", None)
            if name == "previous":
                self._navigate_review_epoch(-1)
            elif name == "next":
                self._navigate_review_epoch(1)
            elif name == "toggle_reject":
                self._toggle_selected_epoch_rejection()
            elif viewer is not None and name == "time_in":
                viewer.zoom_time_in()
            elif viewer is not None and name == "time_out":
                viewer.zoom_time_out()
            elif viewer is not None and name == "sensitivity_up":
                viewer.increase_sensitivity()
            elif viewer is not None and name == "sensitivity_down":
                viewer.decrease_sensitivity()
            return

        # On other pages, do not steal normal editing/navigation keys while the
        # operator is typing into a value, name, table or combo box.
        focus = QApplication.focusWidget()
        editing = isinstance(
            focus,
            (QLineEdit, QKeySequenceEdit, QAbstractSpinBox, QComboBox, QTableWidget, QListWidget, QTextEdit, QTextBrowser),
        )
        if editing:
            return
        if name == "toggle_reject":
            return

        if tab == 0:
            viewer = getattr(self, "raw_viewer", None)
            self._dispatch_stacked_viewer_shortcut(viewer, name)
            return
        if tab == 1:
            sub = self.ica_workspace_tabs.currentIndex() if hasattr(self, "ica_workspace_tabs") else 0
            if sub == 0:
                self._dispatch_stacked_viewer_shortcut(getattr(self, "ica_fit_view", None), name)
            elif sub == 1:
                viewer = getattr(self, "ica_view", None)
                if viewer is not None:
                    actions = {
                        "previous": lambda: viewer.step_source(-1), "next": lambda: viewer.step_source(1),
                        "time_in": viewer.zoom_time_in, "time_out": viewer.zoom_time_out,
                        "sensitivity_up": viewer.increase_y_sensitivity, "sensitivity_down": viewer.decrease_y_sensitivity,
                    }
                    callback = actions.get(name)
                    if callback: callback()
            else:
                self._dispatch_stacked_viewer_shortcut(getattr(self, "ica_post_view", None), name)
            return
        if tab == 4:
            viewer = getattr(self, "erp_viewer", None)
            if viewer is None: return
            if name == "previous": self._step_erp_condition(-1)
            elif name == "next": self._step_erp_condition(1)
            elif name == "time_in": viewer.zoom_time_in()
            elif name == "time_out": viewer.zoom_time_out()
            elif name == "sensitivity_up": viewer.increase_sensitivity()
            elif name == "sensitivity_down": viewer.decrease_sensitivity()
            return
        if tab == 5:
            viewer = getattr(self, "grand_viewer", None)
            if viewer is None: return
            if name == "previous": self._step_grand_condition(-1)
            elif name == "next": self._step_grand_condition(1)
            elif name == "time_in": viewer.zoom_time_in()
            elif name == "time_out": viewer.zoom_time_out()
            elif name == "sensitivity_up": viewer.increase_sensitivity()
            elif name == "sensitivity_down": viewer.decrease_sensitivity()

    @staticmethod
    def _dispatch_stacked_viewer_shortcut(viewer, name: str):
        if viewer is None:
            return
        actions = {
            "previous": lambda: viewer.step_time(-1), "next": lambda: viewer.step_time(1),
            "time_in": viewer.zoom_time_in, "time_out": viewer.zoom_time_out,
            "sensitivity_up": viewer.increase_sensitivity, "sensitivity_down": viewer.decrease_sensitivity,
        }
        callback = actions.get(name)
        if callback: callback()

    # ---------- application windows / diagnostics ----------
    def open_new_window(self):
        child = ERPWorkbench()
        child.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        OPEN_WORKBENCH_WINDOWS.append(child)
        child.destroyed.connect(lambda *_args, w=child: OPEN_WORKBENCH_WINDOWS.remove(w) if w in OPEN_WORKBENCH_WINDOWS else None)
        child.show()
        child.raise_(); child.activateWindow()

    def _set_protocol_library_dir(self, path: Path, *, persist: bool = True):
        path = Path(path).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "Protocol folder unavailable", f"Could not use this protocol folder:\n{path}\n\n{exc}")
            return False
        self._protocol_library_dir = path
        if persist:
            self.app_settings.setValue("paths/protocol_library", str(path))
        if hasattr(self, "settings_protocol_library_path_label"):
            self.settings_protocol_library_path_label.setText(str(path))
        if hasattr(self, "protocol_library_combo"):
            self._refresh_protocol_library(select_name=self.protocol.name)
        return True

    def _choose_protocol_library_dir(self):
        start = str(self._protocol_library_dir)
        chosen = QFileDialog.getExistingDirectory(self, "Choose protocol library folder", start)
        if chosen:
            self._set_protocol_library_dir(Path(chosen), persist=True)

    def _reset_protocol_library_dir(self):
        self.app_settings.remove("paths/protocol_library")
        if self._set_protocol_library_dir(self._default_protocol_library_dir, persist=False):
            self.app_settings.remove("paths/protocol_library")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        timer = getattr(self, "_responsive_timer", None)
        if timer is not None:
            timer.start()

    def _set_form_compact(self, form: QFormLayout, compact: bool):
        """Stack form labels above fields when a sidebar becomes narrow."""
        form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapAllRows if compact
            else QFormLayout.RowWrapPolicy.DontWrapRows
        )
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _remove_widgets_from_grid(layout: QGridLayout, widgets):
        for widget in widgets:
            if widget is not None:
                layout.removeWidget(widget)

    def _reflow_ica_controls(self, compact: bool):
        """Rearrange ICA control rows instead of clipping them behind the sidebar."""
        if not hasattr(self, "ica_fit_settings_layout"):
            return

        # Fit settings
        grid = self.ica_fit_settings_layout
        fit_widgets = [self.ica_method_label, self.ica_method, self.ica_dim_label, self.ica_components,
                       self.run_ica_btn, self.ica_fit_progress, self.ica_fit_eta]
        self._remove_widgets_from_grid(grid, fit_widgets)
        # dim_hint is left in the same grid; find and temporarily remove it.
        dim_hint = None
        for i in range(grid.count()):
            item = grid.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, QLabel) and "enough PCA dimensions" in w.text():
                dim_hint = w
                break
        if dim_hint is not None:
            grid.removeWidget(dim_hint)
        if compact:
            grid.addWidget(self.ica_method_label, 0, 0, 1, 2)
            grid.addWidget(self.ica_method, 1, 0, 1, 2)
            grid.addWidget(self.ica_dim_label, 2, 0, 1, 2)
            grid.addWidget(self.ica_components, 3, 0, 1, 2)
            if dim_hint is not None:
                grid.addWidget(dim_hint, 4, 0, 1, 2)
            grid.addWidget(self.run_ica_btn, 5, 0, 1, 2)
            grid.addWidget(self.ica_fit_progress, 6, 0, 1, 2)
            grid.addWidget(self.ica_fit_eta, 7, 0, 1, 2)
        else:
            grid.addWidget(self.ica_method_label, 0, 0); grid.addWidget(self.ica_method, 0, 1)
            grid.addWidget(self.ica_dim_label, 1, 0); grid.addWidget(self.ica_components, 1, 1)
            if dim_hint is not None:
                grid.addWidget(dim_hint, 2, 0, 1, 2)
            grid.addWidget(self.run_ica_btn, 3, 0, 1, 2)
            grid.addWidget(self.ica_fit_progress, 4, 0, 1, 2)
            grid.addWidget(self.ica_fit_eta, 5, 0, 1, 2)

        # Exclusion reason row
        rg = self.ica_reason_layout
        self._remove_widgets_from_grid(rg, [self.ica_reason_label, self.ica_exclusion_reason, self.ica_mark_visible_btn])
        if compact:
            rg.addWidget(self.ica_reason_label, 0, 0)
            rg.addWidget(self.ica_exclusion_reason, 0, 1)
            rg.addWidget(self.ica_mark_visible_btn, 1, 0, 1, 2)
        else:
            rg.addWidget(self.ica_reason_label, 0, 0)
            rg.addWidget(self.ica_exclusion_reason, 0, 1)
            rg.addWidget(self.ica_mark_visible_btn, 0, 2)
        rg.setColumnStretch(1, 1)

        # Exact start/end entry
        eg = self.ica_exact_layout
        exact_widgets = [self.ica_start_label, self.ica_excl_start, self.ica_end_label, self.ica_excl_end, self.ica_add_exact_btn]
        self._remove_widgets_from_grid(eg, exact_widgets)
        if compact:
            eg.addWidget(self.ica_start_label, 0, 0); eg.addWidget(self.ica_excl_start, 0, 1)
            eg.addWidget(self.ica_end_label, 1, 0); eg.addWidget(self.ica_excl_end, 1, 1)
            eg.addWidget(self.ica_add_exact_btn, 2, 0, 1, 2)
        else:
            eg.addWidget(self.ica_start_label, 0, 0); eg.addWidget(self.ica_excl_start, 0, 1)
            eg.addWidget(self.ica_end_label, 0, 2); eg.addWidget(self.ica_excl_end, 0, 3)
            eg.addWidget(self.ica_add_exact_btn, 0, 4)
        eg.setColumnStretch(1, 1)
        if not compact:
            eg.setColumnStretch(3, 1)

        # Fit-speed row
        fg = self.ica_fast_layout
        self._remove_widgets_from_grid(fg, [self.ica_fast_fit, self.ica_decim_label, self.ica_decim])
        if compact:
            fg.addWidget(self.ica_fast_fit, 0, 0, 1, 2)
            fg.addWidget(self.ica_decim_label, 1, 0); fg.addWidget(self.ica_decim, 1, 1)
        else:
            fg.addWidget(self.ica_fast_fit, 0, 0); fg.addWidget(self.ica_decim_label, 0, 1); fg.addWidget(self.ica_decim, 0, 2)

    def _apply_sidebar_reflow(self):
        """Reflow controls according to the *actual* sidebar viewport width."""
        if hasattr(self, "preproc_scroll"):
            vw = max(1, self.preproc_scroll.viewport().width())
            compact = vw < 335
            for form in getattr(self, "_preproc_responsive_forms", []):
                self._set_form_compact(form, compact)
            if hasattr(self, "preproc_controls_widget"):
                self.preproc_controls_widget.setMinimumWidth(0)
                self.preproc_controls_widget.setMaximumWidth(16777215)

        if hasattr(self, "ica_fit_side_scroll") and self.ica_fit_side_scroll.isVisible():
            vw = max(1, self.ica_fit_side_scroll.viewport().width())
            self._reflow_ica_controls(vw < 370)
            if hasattr(self, "ica_fit_side_content"):
                self.ica_fit_side_content.setMinimumWidth(0)
                self.ica_fit_side_content.setMaximumWidth(16777215)

    def _apply_responsive_layout(self):
        """Resize side/control panels and reflow their contents to available space."""
        width = max(700, int(self.centralWidget().width() if self.centralWidget() else self.width()))

        # Continuous preprocessing sidebar: never force a hidden fixed-width child.
        if hasattr(self, "preproc_scroll"):
            side = max(320, min(440, int(width * (0.31 if width < 1100 else 0.27))))
            self.preproc_scroll.setMinimumWidth(320)
            self.preproc_scroll.setMaximumWidth(520)
            sp = getattr(self, "continuous_splitter", None)
            if sp is not None and sp.width() > 0:
                total = max(1, sp.width())
                left = min(side, max(320, total - 360))
                sp.setSizes([left, max(360, total - left)])

        # ICA fit sidebar follows the same rule.
        if hasattr(self, "ica_fit_side_scroll") and self.ica_fit_side_scroll.isVisible():
            side = max(285, min(410, int(width * (0.30 if width < 1100 else 0.25))))
            self.ica_fit_side_scroll.setMinimumWidth(280)
            self.ica_fit_side_scroll.setMaximumWidth(500)
            if hasattr(self, "ica_fit_splitter") and self.ica_fit_splitter.width() > 0:
                total = max(1, self.ica_fit_splitter.width())
                left = min(side, max(280, total - 360))
                self.ica_fit_splitter.setSizes([left, max(360, total - left)])

        # Epoch review table remains scrollable, with plot priority on laptops.
        if hasattr(self, "review_splitter") and self.review_splitter.width() > 0:
            total = self.review_splitter.width()
            left = max(280, min(520, int(total * (0.37 if total < 1200 else 0.32))))
            self.review_splitter.setSizes([left, max(360, total - left)])

        # Subject and grand-average component tables already have internal scrolling;
        # resize only the panel, never its scientific display state.
        for splitter_name, panel_name, body_name in (
            ("erp_upper_splitter", "component_panel", "component_body"),
            ("grand_upper_splitter", "grand_component_panel", "grand_component_body"),
        ):
            sp = getattr(self, splitter_name, None); panel = getattr(self, panel_name, None); body = getattr(self, body_name, None)
            if sp is None or panel is None or sp.width() <= 0:
                continue
            total = sp.width()
            if body is not None and body.isVisible():
                left = max(280, min(420, int(total * (0.31 if total < 1200 else 0.28))))
                panel.setMinimumWidth(270); panel.setMaximumWidth(max(300, left))
                sp.setSizes([left, max(360, total - left)])

        self._apply_sidebar_reflow()

    def _toggle_command_prompt(self, checked: bool):
        """Show/hide this process's Windows console rather than launching a dummy shell.

        The frozen release is built without a console, so the first Show action
        allocates one and redirects Python stdout/stderr to it. Hiding merely
        hides the console window so it can be shown again later.
        """
        if sys.platform != "win32":
            return
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            hwnd = kernel32.GetConsoleWindow()
            if checked:
                if not hwnd:
                    if not kernel32.AllocConsole():
                        raise OSError("Windows could not allocate a console window.")
                    self._console_allocated_by_app = True
                    # Reconnect Python's streams for a PyInstaller --windowed build.
                    try:
                        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
                        sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
                        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    hwnd = kernel32.GetConsoleWindow()
                kernel32.SetConsoleTitleW("ERP Workbench diagnostics")
                if hwnd:
                    user32.ShowWindow(hwnd, 5)  # SW_SHOW
                    user32.SetForegroundWindow(hwnd)
                try:
                    print("ERP Workbench diagnostic console is active.")
                    print("Closing this console window directly may close console I/O; use View > Show command prompt terminal to hide it.")
                except Exception:
                    pass
            else:
                if hwnd:
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception as exc:
            if getattr(self, "terminal_action", None) is not None:
                self.terminal_action.blockSignals(True); self.terminal_action.setChecked(False); self.terminal_action.blockSignals(False)
            QMessageBox.warning(self, "Could not show diagnostic terminal", str(exc))

    def closeEvent(self, event):
        # Any allocated diagnostic console belongs to this process and will close
        # naturally with the application. Child ERP Workbench windows are
        # independent QMainWindows and are not forced closed here.
        super().closeEvent(event)

    def _toggle_grand_files_panel(self):
        visible = not self.grand_files_body.isVisible()
        self.grand_files_body.setVisible(visible)
        self.grand_files_toggle.setText("▾ Averaged subjects (.erpavg)" if visible else "▸ Averaged subjects (.erpavg)")
        if visible:
            self.grand_files_panel.setMaximumHeight(16777215)
            self.grand_files_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.grand_files_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.grand_files_panel.setMaximumHeight(max(40, self.grand_files_toggle.sizeHint().height() + 16))
        self.grand_viewer.updateGeometry()

    def _toggle_grand_component_panel(self):
        visible = not self.grand_component_body.isVisible()
        self.grand_component_body.setVisible(visible)
        self.grand_component_toggle.setText("▾ Components" if visible else "▸ Components")
        total = max(int(self.grand_upper_splitter.width()), 700)
        if visible:
            self.grand_component_panel.setMinimumWidth(360); self.grand_component_panel.setMaximumWidth(16777215)
            left = min(420, max(360, int(total * 0.30))); self.grand_upper_splitter.setSizes([left, max(1, total-left)])
        else:
            self.grand_component_panel.setMinimumWidth(135); self.grand_component_panel.setMaximumWidth(165)
            self.grand_upper_splitter.setSizes([150, max(1, total-150)])

    def _toggle_grand_measurements_panel(self):
        visible = not self.grand_measurements_body.isVisible()
        self.grand_measurements_body.setVisible(visible)
        self.grand_measurements_toggle.setText("▾ Measurements" if visible else "▸ Measurements")
        if visible:
            self.grand_measure_panel.setMaximumHeight(16777215)
            self.grand_measure_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.grand_outer_layout.setStretch(3, 4); self.grand_outer_layout.setStretch(4, 2)
        else:
            self.grand_measure_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.grand_measure_panel.setMaximumHeight(max(42, self.grand_measurements_toggle.sizeHint().height() + 18))
            self.grand_outer_layout.setStretch(3, 1); self.grand_outer_layout.setStretch(4, 0)
        self.grand_viewer.updateGeometry()

    def _grand_add_paths(self, paths):
        added = 0; problems = []
        existing = {str(Path(p).resolve()).lower() for p in self._grand_average_paths}
        for value in paths:
            p = Path(value)
            try: resolved = str(p.resolve()).lower()
            except Exception: resolved = str(p.absolute()).lower()
            if resolved in existing: continue
            try:
                engine.load_average_manifest(p)
                self._grand_average_paths.append(p); existing.add(resolved); added += 1
            except Exception as exc:
                problems.append(f"{p.name}: {exc}")
        self._grand_reset_computed("File selection changed. Run Validate + grand average again.")
        self._refresh_grand_file_table()
        if problems: QMessageBox.warning(self, "Some files were not added", "\n".join(problems[:20]))
        self.status_label.setText(f"Added {added} averaged-subject file(s); {len(self._grand_average_paths)} selected for grand averaging.")

    def _grand_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select averaged subjects", "", "ERP Workbench averaged subject (*.erpavg)")
        if paths: self._grand_add_paths(paths)

    def _grand_add_folder_recursive(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder containing averaged-subject files")
        if not folder: return
        paths = sorted([p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() == ".erpavg"])
        if not paths:
            QMessageBox.information(self, "No averaged files found", f"No .erpavg files were found recursively under:\n{folder}"); return
        self._grand_add_paths(paths)

    def _grand_remove_selected(self):
        rows = sorted({idx.row() for idx in self.grand_file_table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._grand_average_paths): self._grand_average_paths.pop(row)
        self._grand_reset_computed("File selection changed. Run Validate + grand average again.")
        self._refresh_grand_file_table()

    def _grand_clear_files(self):
        self._grand_average_paths = []
        self._grand_reset_computed("Select compatible averaged-subject files, then run Validate + grand average.")
        self._refresh_grand_file_table()

    def _grand_reset_computed(self, message: str):
        self.grand_evokeds = {}; self._grand_protocol = {}; self._grand_subject_count = 0
        self.grand_measurements = []
        self._grand_difference_evoked = None; self._grand_difference_label = ""; self._grand_difference_active = False
        self._grand_difference_exports = set()
        if hasattr(self, "grand_viewer"): self.grand_viewer.clear_view(message)
        if hasattr(self, "grand_result_table"):
            self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table()
        self._update_grand_files_summary()

    def _update_grand_files_summary(self):
        if not hasattr(self, "grand_files_summary"): return
        n = len(self._grand_average_paths)
        if self._grand_subject_count:
            self.grand_files_summary.setText(f"{self._grand_subject_count} validated subjects · {len(self.grand_evokeds)} conditions")
        else:
            self.grand_files_summary.setText(f"{n} file(s) selected" if n else "No files selected")

    def _refresh_grand_file_table(self):
        if not hasattr(self, "grand_file_table"): return
        self.grand_file_table.setRowCount(len(self._grand_average_paths))
        ref_hash = ""; manifests = []
        for p in self._grand_average_paths:
            try: manifests.append(engine.load_average_manifest(p))
            except Exception: manifests.append(None)
        for m in manifests:
            if m is not None:
                ref_hash = engine.protocol_fingerprint(m.get("protocol", {}) or {}); break
        for r, (p, manifest) in enumerate(zip(self._grand_average_paths, manifests)):
            if manifest is None:
                vals = [str(p), "", "", "", "", "Invalid package"]
            else:
                proto = manifest.get("protocol", {}) or {}; ph = engine.protocol_fingerprint(proto)
                conditions = list(map(str, manifest.get("conditions", []) or [])); status = "Ready" if not ref_hash or ph == ref_hash else "Protocol differs"
                vals = [str(p), str(manifest.get("subject_id", "") or ""), str(proto.get("name", "") or ""), str(len(conditions)), ph[:10], status]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(str(val)); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable); item.setToolTip(str(val)); self.grand_file_table.setItem(r, c, item)
        self._update_grand_files_summary()

    def _compute_grand_average(self):
        if len(self._grand_average_paths) < 2:
            QMessageBox.information(self, "Need more subjects", "Select at least two .erpavg files."); return
        try: result = engine.grand_average_packages(self._grand_average_paths)
        except Exception as exc:
            QMessageBox.critical(self, "Grand average stopped", str(exc)); return
        self.grand_evokeds = result["evokeds"]; self._grand_protocol = result["protocol"]
        self._grand_subject_count = int(result["subject_count"]); self.grand_measurements = []
        self._grand_difference_evoked = None; self._grand_difference_label = ""; self._grand_difference_active = False
        self._grand_difference_exports = set()
        try:
            proto = ProtocolDefinition.from_dict(self._grand_protocol)
            self._load_grand_components_table(proto.components)
        except Exception:
            self._load_grand_components_table([])
        self._populate_grand_controls(); self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._refresh_grand_plot()
        ph = str(result.get("protocol_hash", ""))[:10]
        self.grand_validation_label.setText(
            f"✓ Validated {self._grand_subject_count} subject package(s); protocol ID {ph}; computed {len(self.grand_evokeds)} condition grand average(s)."
        )
        self._update_grand_files_summary()
        self.status_label.setText(f"Grand average complete: {self._grand_subject_count} subjects × {len(self.grand_evokeds)} conditions.")

    def _populate_grand_controls(self):
        conditions = list(self.grand_evokeds)
        old_cond = self.grand_condition_combo.currentText()
        self.grand_condition_combo.blockSignals(True); self.grand_condition_combo.clear(); self.grand_condition_combo.addItems(conditions)
        if old_cond in conditions: self.grand_condition_combo.setCurrentText(old_cond)
        self.grand_condition_combo.blockSignals(False)
        for combo in (self.grand_diff_a_combo, self.grand_diff_b_combo):
            old = combo.currentText(); combo.blockSignals(True); combo.clear(); combo.addItems(conditions)
            if old in conditions: combo.setCurrentText(old)
            combo.blockSignals(False)
        if len(conditions) > 1 and self.grand_diff_b_combo.currentIndex() == self.grand_diff_a_combo.currentIndex(): self.grand_diff_b_combo.setCurrentIndex(1)
        channels = []
        if conditions:
            ev = self.grand_evokeds[conditions[0]]; picks = mne.pick_types(ev.info, eeg=True, exclude=[]); channels = [ev.ch_names[i] for i in picks]
        old_ch = self.grand_channel_combo.currentText()
        self.grand_channel_combo.blockSignals(True); self.grand_channel_combo.clear(); self.grand_channel_combo.addItems(channels)
        if old_ch in channels: self.grand_channel_combo.setCurrentText(old_ch)
        elif channels: self.grand_channel_combo.setCurrentIndex(0)
        self.grand_channel_combo.blockSignals(False)
        keep = [ch for ch in self._grand_display_channels if ch in channels]
        if not keep: keep = [ch for ch in self._preferred_display_channels if ch in channels]
        if not keep and self.grand_channel_combo.currentText(): keep = [self.grand_channel_combo.currentText()]
        self._grand_display_channels = keep; self._update_grand_channel_summary(); self._grand_difference_pair_changed()

    def _capture_grand_display_state(self):
        if hasattr(self, "grand_viewer") and self.grand_viewer.has_data(): self._grand_display_state = dict(self.grand_viewer.get_display_state())

    def _grand_primary_channel_changed(self, channel: str):
        if not channel: return
        self._capture_grand_display_state()
        if not self._grand_display_channels or len(self._grand_display_channels) == 1: self._grand_display_channels = [channel]
        elif channel not in self._grand_display_channels: self._grand_display_channels.insert(0, channel)
        self._update_grand_channel_summary(); self._refresh_grand_plot()

    def _select_grand_display_channels(self):
        if not self.grand_evokeds:
            QMessageBox.information(self, "No grand average", "Compute a grand average first."); return
        condition = self.grand_condition_combo.currentText() or next(iter(self.grand_evokeds)); ev = self.grand_evokeds[condition]
        picks = mne.pick_types(ev.info, eeg=True, exclude=[]); channels = [ev.ch_names[i] for i in picks]
        dlg = BadChannelDialog(channels, self._grand_display_channels, self, title="Select grand-average channels to display")
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        chosen = dlg.selected_channels()
        if not chosen:
            QMessageBox.information(self, "No display channels", "Select at least one channel."); return
        self._capture_grand_display_state(); self._grand_display_channels = chosen; self._preferred_display_channels = list(chosen)
        if self.grand_channel_combo.currentText() not in chosen: self.grand_channel_combo.setCurrentText(chosen[0])
        self._update_grand_channel_summary(); self._refresh_grand_plot()

    def _update_grand_channel_summary(self):
        if not hasattr(self, "grand_channel_summary"): return
        ch = list(self._grand_display_channels); text = "None" if not ch else (", ".join(ch) if len(ch) <= 3 else f"{len(ch)} channels")
        self.grand_channel_summary.setText(text); self.grand_channel_summary.setToolTip(", ".join(ch))

    def _grand_view_mode_changed(self, text: str):
        if not hasattr(self, "grand_viewer"): return
        self._capture_grand_display_state(); self.grand_viewer.set_display_mode("stacked" if str(text).lower().startswith("stack") else "butterfly"); self._refresh_grand_plot()

    def _grand_condition_changed(self, *_):
        self._capture_grand_display_state()
        self._grand_difference_active = False; self._grand_difference_evoked = None; self._grand_difference_label = ""
        self._refresh_grand_plot()

    def _step_grand_condition(self, delta: int):
        if self.grand_condition_combo.count() == 0: return
        self.grand_condition_combo.setCurrentIndex((self.grand_condition_combo.currentIndex() + int(delta)) % self.grand_condition_combo.count())

    def _grand_difference_pair(self):
        return str(self.grand_diff_a_combo.currentText()), str(self.grand_diff_b_combo.currentText())

    def _grand_difference_pair_changed(self, *_):
        if not hasattr(self, "grand_diff_export_check"): return
        pair = self._grand_difference_pair(); checked = pair in getattr(self, "_grand_difference_exports", set())
        old = self.grand_diff_export_check.blockSignals(True); self.grand_diff_export_check.setChecked(checked); self.grand_diff_export_check.blockSignals(old)

    def _grand_difference_export_toggled(self, checked: bool):
        a, b = self._grand_difference_pair()
        if not a or not b or a == b:
            if checked:
                old = self.grand_diff_export_check.blockSignals(True); self.grand_diff_export_check.setChecked(False); self.grand_diff_export_check.blockSignals(old)
            return
        pair = (a, b)
        if checked: self._grand_difference_exports.add(pair)
        else: self._grand_difference_exports.discard(pair)
        self.status_label.setText(
            f"Difference {a} − {b} {'will' if checked else 'will not'} be included in the grand-average Excel export."
        )

    def _show_grand_difference_wave(self):
        self._capture_grand_display_state(); a, b = self._grand_difference_pair()
        if not a or not b or a not in self.grand_evokeds or b not in self.grand_evokeds:
            QMessageBox.information(self, "Difference wave", "Compute a grand average and select two valid conditions first."); return
        if a == b:
            QMessageBox.information(self, "Difference wave", "Choose two different conditions."); return
        try:
            self._grand_difference_label = f"{a} − {b}"
            self._grand_difference_evoked = engine.difference_evoked(self.grand_evokeds[a], self.grand_evokeds[b], self._grand_difference_label)
            self._grand_difference_active = True
        except Exception as exc:
            QMessageBox.warning(self, "Difference wave failed", str(exc)); return
        self._refresh_grand_plot()
        self.status_label.setText(
            f"Viewing grand-average difference {self._grand_difference_label}. It remains separate from real conditions; check Include this difference in Excel if required."
        )

    def _clear_grand_difference_wave(self):
        self._grand_difference_active = False; self._grand_difference_evoked = None; self._grand_difference_label = ""; self._refresh_grand_plot()

    def _current_grand_display_evoked(self):
        if self._grand_difference_active and self._grand_difference_evoked is not None:
            return self._grand_difference_evoked, self._grand_difference_label, True
        condition = self.grand_condition_combo.currentText()
        if condition in self.grand_evokeds: return self.grand_evokeds[condition], condition, False
        return None, "", False

    def _refresh_grand_plot(self):
        if not hasattr(self, "grand_viewer"): return
        ev, label, is_difference = self._current_grand_display_evoked()
        if ev is None:
            if not self.grand_evokeds: self.grand_viewer.clear_view("Select compatible averaged-subject files, then run Validate + grand average.")
            return
        self._capture_grand_display_state()
        available = set(ev.ch_names); channels = [ch for ch in self._grand_display_channels if ch in available]
        if not channels:
            primary = self.grand_channel_combo.currentText(); channels = [primary] if primary in available else [ev.ch_names[0]]; self._grand_display_channels = channels
        series = {ch: ev.data[ev.ch_names.index(ch)] * 1e6 for ch in channels}
        self.grand_viewer.set_display_mode("stacked" if self.grand_view_mode.currentText().lower().startswith("stack") else "butterfly")
        title = f"Grand average — {label} — N={self._grand_subject_count}" + ("  [difference]" if is_difference else "")
        self.grand_viewer.set_evoked_multi(ev.times * 1000.0, series, title)
        if self._grand_display_state: self.grand_viewer.apply_display_state(self._grand_display_state)
        self._grand_manual_latency_ms = None
        self._grand_component_selection_changed(); self._draw_grand_measurement_markers(); self._update_grand_channel_summary()

    # ----- grand component plan -----
    def _load_grand_components_table(self, components: list[ComponentDefinition]):
        if not hasattr(self, "grand_component_table"): return
        self.grand_component_table.blockSignals(True); self.grand_component_table.setRowCount(len(components))
        for r, c in enumerate(components):
            for col, value in enumerate([c.name, str(c.start_ms), str(c.end_ms), c.polarity, c.method, ",".join(c.channels)]):
                self.grand_component_table.setItem(r, col, QTableWidgetItem(value))
        self.grand_component_table.blockSignals(False)
        if self.grand_component_table.rowCount(): self.grand_component_table.selectRow(0)

    def _grand_components_from_table(self) -> list[ComponentDefinition]:
        if not hasattr(self, "grand_component_table"): return []
        out = []
        for r in range(self.grand_component_table.rowCount()):
            try:
                vals = [self.grand_component_table.item(r, c) for c in range(6)]
                if not all(vals): continue
                name = vals[0].text().strip(); start = float(vals[1].text()); end = float(vals[2].text())
                polarity = vals[3].text().strip().lower(); method = vals[4].text().strip().lower()
                channels = [x.strip() for x in vals[5].text().split(",") if x.strip()]
                if polarity not in {"positive", "negative", "absolute"}: polarity = "absolute"
                if method not in {"peak", "mean", "manual", "area"}: method = "peak"
                if name and start < end: out.append(ComponentDefinition(name, start, end, polarity, method, channels))
            except Exception: continue
        return out

    def _selected_grand_component(self):
        rows = self.grand_component_table.selectionModel().selectedRows()
        if not rows: return None
        r = rows[0].row()
        try:
            return ComponentDefinition(
                self.grand_component_table.item(r, 0).text().strip(), float(self.grand_component_table.item(r, 1).text()),
                float(self.grand_component_table.item(r, 2).text()), self.grand_component_table.item(r, 3).text().strip().lower(),
                self.grand_component_table.item(r, 4).text().strip().lower(),
                [x.strip() for x in self.grand_component_table.item(r, 5).text().split(",") if x.strip()],
            )
        except Exception: return None

    def _grand_add_component_row(self):
        r = self.grand_component_table.rowCount(); self.grand_component_table.insertRow(r)
        for c, value in enumerate([f"Component{r+1}", "300", "500", "positive", "peak", ""]): self.grand_component_table.setItem(r, c, QTableWidgetItem(value))
        self.grand_component_table.selectRow(r)

    def _grand_remove_component_row(self):
        rows = self.grand_component_table.selectionModel().selectedRows()
        if not rows: return
        r = rows[0].row(); item = self.grand_component_table.item(r, 0); name = item.text().strip() if item else ""
        self.grand_component_table.removeRow(r)
        if name: self.grand_measurements = [m for m in self.grand_measurements if m.component != name]
        if self.grand_component_table.rowCount(): self.grand_component_table.selectRow(min(r, self.grand_component_table.rowCount()-1))
        else: self.grand_viewer.set_window(0, 0)
        self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()

    def _grand_component_definition_edited(self, *_):
        self._grand_component_selection_changed(); self._draw_grand_measurement_markers()

    def _grand_component_selection_changed(self):
        component = self._selected_grand_component()
        if component is not None and hasattr(self, "grand_viewer"):
            method_label = "mean voltage" if component.method == "mean" else component.method
            self.grand_viewer.set_window(component.start_ms, component.end_ms, f"{component.name}: {method_label}")

    # ----- grand measurements / markers -----
    def _grand_channels_for_component(self, component: ComponentDefinition, available: list[str]):
        if not component.channels:
            selected = [ch for ch in self._grand_display_channels if ch in available]
            return selected or ([self.grand_channel_combo.currentText()] if self.grand_channel_combo.currentText() in available else [])
        if len(component.channels) == 1 and component.channels[0].upper() == "ALL": return list(available)
        return [ch for ch in component.channels if ch in available]

    def _grand_automatic_component_definition(self, component: ComponentDefinition):
        mode = str(self.grand_auto_measure_mode.currentData() or "component")
        if mode == "component":
            if component.method == "manual": return None
            return copy.deepcopy(component)
        c = copy.deepcopy(component); c.method = mode if mode in {"peak", "mean"} else c.method; return c

    def _grand_upsert_measurement(self, result: MeasurementResult) -> bool:
        identity = (result.condition, result.channel, result.component)
        same = [m for m in self.grand_measurements if (m.condition, m.channel, m.component) == identity]
        if result.method == "manual":
            self.grand_measurements = [m for m in self.grand_measurements if (m.condition, m.channel, m.component) != identity]
            self.grand_measurements.append(result); return True
        if any(m.method == "manual" for m in same):
            self.grand_measurements = [m for m in self.grand_measurements if (m.condition, m.channel, m.component) != identity or m.method == "manual"]
            return False
        self.grand_measurements = [m for m in self.grand_measurements if (m.condition, m.channel, m.component) != identity]
        self.grand_measurements.append(result); return True

    def _grand_erp_clicked(self, latency_ms: float, clicked_channel: str = ""):
        component = self._selected_grand_component(); ev, condition, is_difference = self._current_grand_display_evoked()
        if component is None or ev is None: return
        channel = clicked_channel if clicked_channel in ev.ch_names else self.grand_channel_combo.currentText()
        if channel not in ev.ch_names: return
        times_ms = ev.times * 1000.0
        if not len(times_ms) or latency_ms < float(times_ms[0]) or latency_ms > float(times_ms[-1]): return
        y = float(np.interp(latency_ms, times_ms, ev.data[ev.ch_names.index(channel)] * 1e6))
        outside = not (component.start_ms <= latency_ms <= component.end_ms)
        note = "Manual grand-average waveform click"
        if is_difference: note += "; difference wave"
        if outside: note += "; selected outside configured automatic-detection window"
        result = MeasurementResult(
            condition=condition, channel=channel, component=component.name, method="manual",
            window_start_ms=float(component.start_ms), window_end_ms=float(component.end_ms), amplitude_uv=y,
            latency_ms=float(latency_ms), n_epochs=int(self._grand_subject_count), notes=note,
        )
        self._grand_upsert_measurement(result); self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()
        self.status_label.setText(
            f"Manual grand point {component.name} / {channel}: {latency_ms:.1f} ms, {y:.3f} µV" + (" (difference wave)." if is_difference else ".")
        )

    def _grand_measure_current(self):
        ev, condition, _ = self._current_grand_display_evoked(); component = self._selected_grand_component(); channel = self.grand_channel_combo.currentText()
        if ev is None or component is None or channel not in ev.ch_names:
            QMessageBox.information(self, "Nothing to measure", "Compute a grand average and select a component/channel first."); return
        try:
            result = engine.measure_evoked(ev, condition, channel, component, int(self._grand_subject_count), self._grand_manual_latency_ms if component.method == "manual" else None)
        except Exception as exc:
            QMessageBox.warning(self, "Measurement failed", str(exc)); return
        changed = self._grand_upsert_measurement(result); self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()
        self.status_label.setText("Grand measurement updated." if changed else "Existing manual grand measurement kept; automation did not overwrite it.")

    def _grand_measure_current_automatic(self):
        ev, condition, _ = self._current_grand_display_evoked()
        if ev is None:
            QMessageBox.information(self, "No grand average", "Compute a grand average first."); return
        changed = 0; skipped_manual = 0; components = self._grand_components_from_table(); available = list(ev.ch_names)
        for base in components:
            component = self._grand_automatic_component_definition(base)
            if component is None: continue
            for ch in self._grand_channels_for_component(base, available):
                try: result = engine.measure_evoked(ev, condition, ch, component, int(self._grand_subject_count))
                except Exception: continue
                if self._grand_upsert_measurement(result): changed += 1
                else: skipped_manual += 1
        self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()
        self.status_label.setText(f"Grand auto-detection updated {changed} result(s)" + (f"; kept {skipped_manual} manual override(s)." if skipped_manual else "."))

    def _grand_measure_all_automatic(self):
        if not self.grand_evokeds:
            QMessageBox.information(self, "No grand average", "Compute a grand average first."); return
        # All means real grand-average conditions. Difference waves are measured
        # explicitly when the operator views the desired A−B pair.
        self._grand_difference_active = False; self._grand_difference_evoked = None; self._grand_difference_label = ""
        changed = 0; skipped = 0; components = self._grand_components_from_table()
        for condition, ev in self.grand_evokeds.items():
            available = list(ev.ch_names)
            for base in components:
                component = self._grand_automatic_component_definition(base)
                if component is None: continue
                for ch in self._grand_channels_for_component(base, available):
                    try: result = engine.measure_evoked(ev, condition, ch, component, int(self._grand_subject_count))
                    except Exception: continue
                    if self._grand_upsert_measurement(result): changed += 1
                    else: skipped += 1
        self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._refresh_grand_plot()
        self.status_label.setText(f"Measured/updated {changed} grand component/channel result(s) across all conditions" + (f"; kept {skipped} manual override(s)." if skipped else "."))

    def _draw_grand_measurement_markers(self):
        if not hasattr(self, "grand_viewer"): return
        self.grand_viewer.clear_markers()
        ev, condition, _ = self._current_grand_display_evoked()
        if ev is None: return
        display = [ch for ch in self._grand_display_channels if ch in ev.ch_names]
        if not display and self.grand_channel_combo.currentText() in ev.ch_names: display = [self.grand_channel_combo.currentText()]
        active = {c.name for c in self._grand_components_from_table()}; multi = len(display) > 1
        for m in self.grand_measurements:
            if m.condition != condition or m.latency_ms is None or m.channel not in display or m.component not in active: continue
            key = (m.condition, m.channel, m.component, m.method)
            self.grand_viewer.add_marker(m.latency_ms, m.amplitude_uv, m.component, m.channel if multi else "", measurement_key=key)

    def _find_grand_measurement_by_key(self, key):
        if not key: return None, None
        wanted = tuple(key)
        for i, m in enumerate(self.grand_measurements):
            if (m.condition, m.channel, m.component, m.method) == wanted: return i, m
        return None, None

    def _grand_marker_context_requested(self, marker_def: dict):
        idx, measurement = self._find_grand_measurement_by_key(marker_def.get("measurement_key"))
        if measurement is None: return
        menu = QMenu(self); title = menu.addAction(f"{measurement.component} · {measurement.channel} · {measurement.latency_ms:.1f} ms"); title.setEnabled(False)
        menu.addSeparator(); remove_action = menu.addAction("Remove marker / measurement")
        reassign = menu.addMenu("Change component label")
        for name in [c.name for c in self._grand_components_from_table() if c.name]:
            action = reassign.addAction(name); action.setCheckable(True); action.setChecked(name == measurement.component)
            action.triggered.connect(lambda _checked=False, n=name, k=marker_def.get("measurement_key"): self._reassign_grand_marker(k, n))
        chosen = menu.exec(QCursor.pos())
        if chosen is remove_action:
            idx, measurement = self._find_grand_measurement_by_key(marker_def.get("measurement_key"))
            if idx is not None:
                removed = self.grand_measurements.pop(idx); self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()
                self.status_label.setText(f"Removed grand {removed.component} marker / {removed.channel} / {removed.condition}.")

    def _reassign_grand_marker(self, measurement_key, new_component_name: str):
        idx, measurement = self._find_grand_measurement_by_key(measurement_key)
        if measurement is None: return
        target = next((c for c in self._grand_components_from_table() if c.name == new_component_name), None)
        if target is None or measurement.component == new_component_name: return
        replacement = MeasurementResult(
            condition=measurement.condition, channel=measurement.channel, component=new_component_name, method="manual",
            window_start_ms=float(target.start_ms), window_end_ms=float(target.end_ms), amplitude_uv=float(measurement.amplitude_uv),
            latency_ms=None if measurement.latency_ms is None else float(measurement.latency_ms), n_epochs=int(measurement.n_epochs),
            notes=(measurement.notes + "; " if measurement.notes else "") + f"Relabeled manually from {measurement.component}",
        )
        self.grand_measurements.pop(idx); self._grand_upsert_measurement(replacement)
        self._refresh_grand_measurement_filters(); self._refresh_grand_measurement_table(); self._draw_grand_measurement_markers()

    def _refresh_grand_measurement_filters(self):
        if not hasattr(self, "grand_measure_filter_condition"): return
        specs = [
            (self.grand_measure_filter_condition, sorted({m.condition for m in self.grand_measurements})),
            (self.grand_measure_filter_channel, sorted({m.channel for m in self.grand_measurements})),
            (self.grand_measure_filter_component, sorted({m.component for m in self.grand_measurements})),
            (self.grand_measure_filter_method, sorted({m.method for m in self.grand_measurements})),
        ]
        for combo, values in specs:
            old = combo.currentText() or "All"; combo.blockSignals(True); combo.clear(); combo.addItem("All"); combo.addItems(values)
            if old in ["All"] + values: combo.setCurrentText(old)
            combo.blockSignals(False)

    def _refresh_grand_measurement_table(self, *_):
        if not hasattr(self, "grand_result_table"): return
        filters = {
            "condition": self.grand_measure_filter_condition.currentText() if hasattr(self, "grand_measure_filter_condition") else "All",
            "channel": self.grand_measure_filter_channel.currentText() if hasattr(self, "grand_measure_filter_channel") else "All",
            "component": self.grand_measure_filter_component.currentText() if hasattr(self, "grand_measure_filter_component") else "All",
            "method": self.grand_measure_filter_method.currentText() if hasattr(self, "grand_measure_filter_method") else "All",
        }
        visible = []
        for m in self.grand_measurements:
            if filters["condition"] not in {"", "All"} and m.condition != filters["condition"]: continue
            if filters["channel"] not in {"", "All"} and m.channel != filters["channel"]: continue
            if filters["component"] not in {"", "All"} and m.component != filters["component"]: continue
            if filters["method"] not in {"", "All"} and m.method != filters["method"]: continue
            visible.append(m)
        sorting = self.grand_result_table.isSortingEnabled(); self.grand_result_table.setSortingEnabled(False); self.grand_result_table.setRowCount(len(visible))
        for r, m in enumerate(visible):
            vals = [m.condition, m.channel, m.component, m.method, f"{m.window_start_ms:g}", f"{m.window_end_ms:g}", f"{m.amplitude_uv:.6g}", "" if m.latency_ms is None else f"{m.latency_ms:.3f}", str(m.n_epochs), m.notes]
            for c, v in enumerate(vals): self.grand_result_table.setItem(r, c, QTableWidgetItem(v))
        self.grand_result_table.setSortingEnabled(sorting)

    def _export_grand_average_excel(self):
        if not self.grand_evokeds or self._grand_subject_count < 2:
            QMessageBox.information(self, "No grand average", "Validate and compute the grand average first."); return
        components = self._grand_components_from_table()
        if not components:
            QMessageBox.information(self, "No components", "Define at least one component to export."); return
        base_dir = self._grand_average_paths[0].parent if self._grand_average_paths else Path.home()
        suggested = str(base_dir / "Grand_Average_ERP_results.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export grand-average ERP workbook", suggested, "Excel workbook (*.xlsx)")
        if not path: return
        p = Path(path)
        if p.suffix.lower() != ".xlsx": p = p.with_suffix(".xlsx")
        if not self._confirm_overwrite(p): return
        try:
            summary = engine.export_grand_average_excel(
                p,
                self._grand_average_paths,
                self.grand_evokeds,
                self._grand_protocol,
                components,
                self.grand_measurements,
                auto_mode=str(self.grand_auto_measure_mode.currentData() or "component"),
                default_channels=list(self._grand_display_channels),
                difference_pairs=sorted(self._grand_difference_exports),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Grand-average Excel export failed", str(exc)); return
        self.status_label.setText(
            f"Exported grand-average workbook: {p.name} · {summary['subjects']} subjects · "
            f"{summary['subject_rows']} subject rows · {summary['grand_rows']} grand rows."
        )

    def _on_tab_changed(self, index: int):
        if index == 1:
            self._refresh_ica_fit_view()
            self._refresh_ica_exclusion_table()
        if index == 2:
            if self._epoching_raw() is not None and len(self.events) == 0:
                self._discover_events_internal(silent=True)
            self._refresh_epoch_event_status()
            self._refresh_epoch_preflight()

    # ---------- file / subject exports ----------
    def _confirm_overwrite(self, path: Path) -> bool:
        if not path.exists(): return True
        reply = QMessageBox.question(
            self, "File already exists",
            f"{path.name} already exists. Overwrite it?\n\nChoose No to return and save under a different name.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _available_processed_save_stages(self) -> list[tuple[str, str]]:
        """Return user-visible continuous EEG stages that currently exist.

        Intermediate preprocessing stages are rebuilt deterministically from the
        imported Raw when selected for saving. The temporary ICA fitting copy is
        intentionally never exposed as an analysis/save stage.
        """
        if self.original_raw is None:
            return []
        self._sync_preprocessing_from_ui()
        stages: list[tuple[str, str]] = [("Original imported EEG", "original")]
        if self.preprocessing.filter.enabled:
            stages.append(("After filtering", "filtered"))
        if self.preprocessing.interpolation.enabled:
            stages.append(("After interpolation", "interpolated"))
        if self.preprocessing.reference.enabled:
            stages.append(("After re-reference", "referenced"))
        if self.ica is not None or self._pre_ica_raw is not None or "ica" in self._processing_order:
            stages.append(("Pre-ICA processed EEG", "pre_ica"))
        if self._ica_cleaned_raw is not None:
            stages.append(("Post-ICA processed EEG", "post_ica"))
        return stages

    def _raw_for_processed_save_stage(self, stage: str, progress=None):
        if self.original_raw is None:
            raise RuntimeError("No recording is loaded.")
        stage = str(stage)
        if stage == "original":
            return self.original_raw.copy().load_data()
        if stage == "post_ica":
            if self._ica_cleaned_raw is None:
                raise RuntimeError("Post-ICA processed EEG is not available yet.")
            return self._ica_cleaned_raw.copy().load_data()
        if stage == "pre_ica":
            raw = self._pre_ica_raw if self._pre_ica_raw is not None else self.processed_raw
            if raw is None:
                raise RuntimeError("Pre-ICA processed EEG is not available.")
            return raw.copy().load_data()

        settings = copy.deepcopy(self.preprocessing)
        # ICA is always external to deterministic preprocessing rebuilds.
        settings.ica.enabled = False
        settings.ica.excluded_components = []
        settings.ica.fit_exclude_spans = []
        order: list[str] = []
        if stage == "filtered":
            settings.interpolation.enabled = False
            settings.reference.enabled = False
        elif stage == "interpolated":
            settings.reference.enabled = False
            if settings.interpolation.enabled:
                order.append("interpolation")
        elif stage == "referenced":
            if settings.interpolation.enabled:
                order.append("interpolation")
            if settings.reference.enabled:
                order.append("reference")
        else:
            raise ValueError(f"Unknown EEG save stage: {stage}")
        settings.step_order = list(order)
        return engine.apply_preprocessing(self.original_raw, settings, order, progress=progress)

    def save_processed_fif(self):
        if self.original_raw is None:
            QMessageBox.information(self, "No recording", "Load a recording first.")
            return
        stages = self._available_processed_save_stages()
        chooser = StageSelectionDialog(stages, self)
        # Prefer the signal deliberately selected for downstream analysis when
        # ICA versions exist; otherwise default to the latest available stage.
        preferred_stage = "post_ica" if self._epoch_input_mode == "ica_cleaned" and self._ica_cleaned_raw is not None else "pre_ica"
        preferred_index = chooser.combo.findData(preferred_stage)
        if preferred_index >= 0:
            chooser.combo.setCurrentIndex(preferred_index)
        elif chooser.combo.count():
            chooser.combo.setCurrentIndex(chooser.combo.count() - 1)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return
        stage = chooser.selected_stage()
        stage_label = chooser.combo.currentText()

        base = self.metadata.input_path.stem if self.metadata.input_path else "recording"
        safe_stage = re.sub(r"[^A-Za-z0-9]+", "_", stage_label).strip("_")
        start_dir = str(self.app_settings.value("paths/last_processed_save_dir", "") or "")
        if not start_dir:
            start_dir = str(self.metadata.input_path.parent) if self.metadata.input_path else str(Path.home())
        suggested = str(Path(start_dir) / f"{base}_{safe_stage}_raw.fif")
        path, _ = QFileDialog.getSaveFileName(self, f"Save {stage_label}", suggested, "MNE FIF (*.fif)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() != ".fif":
            p = p.with_suffix(".fif")
        if not self._confirm_overwrite(p):
            return
        self.app_settings.setValue("paths/last_processed_save_dir", str(p.parent))

        def build_and_save(progress=None):
            if progress:
                progress(f"Preparing {stage_label} …")
            raw = self._raw_for_processed_save_stage(stage, progress=progress)
            if progress:
                progress(f"Saving {stage_label} …")
            raw.save(p, overwrite=True, verbose="ERROR")
            return str(p)

        worker = FunctionWorker(build_and_save)
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.error.connect(self._worker_error)
        worker.signals.result.connect(lambda saved: self.status_label.setText(f"Saved {stage_label}: {Path(saved).name}"))
        self.thread_pool.start(worker)
        self.status_label.setText(f"Preparing {stage_label} for save in background …")

    def export_excel(self):
        if self.original_raw is None:
            QMessageBox.information(self, "No recording", "Load a recording first."); return
        self._sync_preprocessing_from_ui(); self._sync_protocol_from_ui()
        base = self.metadata.input_path.stem if self.metadata.input_path else "recording"
        suggested = str((self.metadata.input_path.parent if self.metadata.input_path else Path.home()) / f"{base}_ERP_results.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export ERP results", suggested, "Excel workbook (*.xlsx)")
        if not path: return
        p = Path(path)
        if p.suffix.lower() != ".xlsx": p = p.with_suffix(".xlsx")
        if not self._confirm_overwrite(p): return
        try:
            engine.export_excel(
                p, self.metadata, self.preprocessing, self.protocol, self.epochs, self.review, self.measurements,
                annotation_table=self.external_annotation_table,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Excel export failed", str(exc)); return
        self.status_label.setText(f"Exported ERP workbook: {p.name}")

    # ---------- protocol files ----------
    def load_protocol_dialog(self):
        start_dir = str(self.app_settings.value("paths/last_protocol_json_dir", "") or "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Load ERP protocol", start_dir, "ERP protocol (*.json);;JSON (*.json)")
        if not path:
            return
        self.app_settings.setValue("paths/last_protocol_json_dir", str(Path(path).parent))
        try:
            self.protocol = load_protocol(path)
            self._load_protocol_into_ui(self.protocol)
            self._refresh_protocol_library(select_name=self.protocol.name)
            self.status_label.setText(f"Loaded protocol JSON: {self.protocol.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Protocol load failed", str(exc))

    def save_protocol_dialog(self):
        self._sync_protocol_from_ui()
        start_dir = str(self.app_settings.value("paths/last_protocol_json_dir", "") or "") or str(Path.home())
        suggested = str(Path(start_dir) / f"{self._safe_protocol_filename(self.protocol.name)}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save ERP protocol JSON", suggested, "ERP protocol (*.json)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() != ".json":
            p = p.with_suffix(".json")
        if not self._confirm_overwrite(p):
            return
        try:
            save_protocol(self.protocol, p)
        except Exception as exc:
            QMessageBox.critical(self, "Protocol save failed", str(exc)); return
        self.app_settings.setValue("paths/last_protocol_json_dir", str(p.parent))
        self.status_label.setText(f"Saved protocol JSON: {p.name}")
