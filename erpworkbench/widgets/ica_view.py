from __future__ import annotations

import math

import mne
import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)




def _topomap_info_with_inferred_montage(raw, channel_names):
    """Return Info with usable EEG positions, inferring a standard montage if needed."""
    work = raw.copy()
    work.pick(channel_names)

    def positioned_count(info):
        count = 0
        for ch in info["chs"]:
            loc = np.asarray(ch.get("loc", np.zeros(12))[:3], dtype=float)
            if np.all(np.isfinite(loc)) and float(np.linalg.norm(loc)) > 1e-6:
                count += 1
        return count

    if positioned_count(work.info) >= max(3, int(math.ceil(len(channel_names) * 0.6))):
        return work.info, "recording sensor positions"

    channel_lower = {str(ch).lower() for ch in channel_names}
    best = None
    for montage_name in ("standard_1020", "standard_1005", "biosemi32"):
        montage = mne.channels.make_standard_montage(montage_name)
        montage_names = {str(ch).lower() for ch in montage.ch_names}
        matched = sum(ch in montage_names for ch in channel_lower)
        if best is None or matched > best[0]:
            best = (matched, montage_name, montage)
    matched, montage_name, montage = best
    threshold = max(3, int(math.ceil(len(channel_names) * 0.6)))
    if matched < threshold:
        return work.info, f"no standard montage matched enough channels ({matched}/{len(channel_names)})"
    work.set_montage(montage, match_case=False, on_missing="ignore", verbose="ERROR")
    return work.info, f"inferred {montage_name} ({matched}/{len(channel_names)} names matched)"

from ..ui_utils import ReliableDoubleSpinBox


class ICAHorizontalViewBox(pg.ViewBox):
    """Horizontal-only drag navigation for the ICA source trace."""

    panRequested = Signal(float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_scene_pos = None
        self.setMouseEnabled(x=False, y=False)

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        if ev.isStart() or self._last_scene_pos is None:
            self._last_scene_pos = ev.scenePos()
            return
        current = ev.scenePos()
        delta_px = float(current.x() - self._last_scene_pos.x())
        self._last_scene_pos = current
        width_px = max(float(self.sceneBoundingRect().width()), 1.0)
        x0, x1 = self.viewRange()[0]
        span = max(float(x1 - x0), 1e-12)
        shift = -(delta_px / width_px) * span
        if math.isfinite(shift) and abs(shift) > 0:
            self.panRequested.emit(shift)
        if ev.isFinish():
            self._last_scene_pos = None

    def wheelEvent(self, ev, axis=None):  # noqa: N802
        ev.ignore()


class ICAAllComponentsWindow(QDialog):
    """Vertically stacked browser for screening all ICA component sources.

    Each row gets a fixed robust scale when the window is opened (or when the
    user explicitly presses ``Auto-scale rows``). Horizontal navigation never
    recalculates those scales. This avoids the visually misleading rescaling
    that makes blink-like transients appear to change size from one time window
    to the next.
    """

    componentActivated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ICA components — time domain (BETA)")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(1300, 820)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.ica = None
        self.raw = None
        self._start_sec = 0.0
        self._duration_sec = 10.0
        self._total_duration = 0.0
        self._dark_mode = False
        self._opengl_enabled = False
        self._row_scales: dict[int, float] = {}
        self._sensitivity = 1.0
        self._selected_component = 0
        self._trace_color_override: str | None = None
        self._shortcut_map = {
            "previous": "Left", "next": "Right", "time_in": "+", "time_out": "-",
            "sensitivity_up": "*", "sensitivity_down": "/",
        }
        self._labels: dict[int, str] = {}
        self._blink_scores: dict[int, float] = {}
        self._blink_refs: dict[int, str] = {}
        self._curve_items = []
        self._pending_pan_delta = 0.0
        self._pan_timer = QTimer(self)
        self._pan_timer.setSingleShot(True)
        self._pan_timer.setInterval(30)
        self._pan_timer.timeout.connect(self._flush_pending_pan)

        self._vb = ICAHorizontalViewBox()
        self._vb.panRequested.connect(self._pan_source)
        self.plot = pg.PlotWidget(viewBox=self._vb)
        self.plot.setBackground("#ffffff")
        self.plot.showGrid(x=True, y=False, alpha=0.12)
        self.plot.setLabel("bottom", "Recording time", units="s")
        self.plot.setLabel("left", "ICA components")
        self.plot.setMenuEnabled(False)
        self.plot.setAntialiasing(False)
        try:
            self.plot.scene().sigMouseClicked.connect(self._plot_clicked)
        except Exception:
            pass

        self.prev_btn = QPushButton("◀")
        self.prev_btn.clicked.connect(lambda: self.step_source(-1))
        self.next_btn = QPushButton("▶")
        self.next_btn.clicked.connect(lambda: self.step_source(1))
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.valueChanged.connect(self._position_changed)
        self.time_window = ReliableDoubleSpinBox()
        self.time_window.setRange(0.10, 7200.0)
        self.time_window.setDecimals(2)
        self.time_window.setValue(10.0)
        self.time_window.setSuffix(" s")
        self.time_window.valueChanged.connect(self._duration_changed)
        self.full_btn = QPushButton("Full duration")
        self.full_btn.clicked.connect(self.show_full_duration)
        self.auto_rows_btn = QPushButton("Auto-scale rows")
        self.auto_rows_btn.setToolTip(
            "Recalculate one robust scale per component from the currently visible segment. "
            "Those scales then remain fixed while you scroll."
        )
        self.auto_rows_btn.clicked.connect(self.auto_scale_rows)
        self.time_label = QLabel("00:00.000 – 00:00.000")
        self.total_label = QLabel("Total: 00:00.000")

        nav = QHBoxLayout()
        nav.addWidget(self.prev_btn)
        nav.addWidget(QLabel("Position"))
        nav.addWidget(self.position, 1)
        nav.addWidget(self.next_btn)
        nav.addWidget(QLabel("Visible"))
        nav.addWidget(self.time_window)
        nav.addWidget(self.full_btn)
        nav.addWidget(self.auto_rows_btn)
        nav.addWidget(self.time_label)
        nav.addWidget(self.total_label)

        self.help_label = QLabel(
            "All ICA sources share the same time axis and keep fixed row scales while you scroll. Auto-scale rows recalculates only when requested. "
            "ICA source amplitudes are component activations in arbitrary/scaled component units, not scalp voltage (µV); use morphology and timing rather than absolute EEG voltage. "
            "Click a row to select that component in the main ICA panel."
        )
        self.help_label.setWordWrap(True)
        self.help_label.setProperty("muted", True)

        lay = QVBoxLayout(self)
        lay.addWidget(self.plot, 1)
        lay.addLayout(nav)
        lay.addWidget(self.help_label)

        self._shortcuts = []
        self._install_shortcuts()
        self._update_help_label()
        self._update_navigation()

    def _install_shortcuts(self):
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        callbacks = {
            "previous": lambda: self.step_source(-1),
            "next": lambda: self.step_source(1),
            "time_in": self.zoom_time_in,
            "time_out": self.zoom_time_out,
            "sensitivity_up": self.increase_y_sensitivity,
            "sensitivity_down": self.decrease_y_sensitivity,
        }
        self._shortcuts = []
        for name, callback in callbacks.items():
            key = str(self._shortcut_map.get(name, "") or "").strip()
            if not key:
                continue
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(callback)
            self._shortcuts.append(sc)

    def _update_help_label(self):
        if not hasattr(self, "help_label"):
            return
        m = self._shortcut_map
        self.help_label.setText(
            "All ICA sources share the same time axis and keep fixed row scales while you scroll. "
            "Auto-scale rows recalculates only when requested. "
            f"{m.get('previous','')}/{m.get('next','')} move one page · {m.get('time_in','')}/{m.get('time_out','')} change time scale · "
            f"{m.get('sensitivity_up','')}/{m.get('sensitivity_down','')} change vertical sensitivity. "
            "ICA source amplitudes are component activations in arbitrary/scaled component units, not scalp voltage (µV); "
            "use morphology and timing rather than absolute EEG voltage. Click a row to select that component in the main ICA panel."
        )

    def set_shortcut_map(self, mapping: dict):
        self._shortcut_map.update({k: str(v) for k, v in dict(mapping or {}).items() if k in self._shortcut_map})
        self._install_shortcuts()
        self._update_help_label()

    def set_trace_color(self, color: str | None):
        self._trace_color_override = str(color) if color else None
        self._refresh_sources()

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    def set_opengl_enabled(self, enabled: bool):
        self._opengl_enabled = bool(enabled)
        self.plot.useOpenGL(self._opengl_enabled)
        self.plot.setAntialiasing(False)
        self.plot.viewport().update()

    def set_dark_mode(self, dark: bool):
        self._dark_mode = bool(dark)
        bg = "#17191d" if self._dark_mode else "#ffffff"
        fg = "#e8ebef" if self._dark_mode else "#20242a"
        self.plot.setBackground(bg)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        self._refresh_sources()

    def _theme_curve(self):
        return self._trace_color_override or ("#8fd3ff" if self._dark_mode else "#1769aa")

    def set_context(
        self,
        ica,
        raw,
        *,
        labels: dict[int, str] | None = None,
        blink_scores: dict[int, float] | None = None,
        blink_refs: dict[int, str] | None = None,
        selected_component: int = 0,
    ):
        same_fit = self.ica is ica and self.raw is raw
        self.ica = ica
        self.raw = raw
        self._labels = dict(labels or {})
        self._blink_scores = dict(blink_scores or {})
        self._blink_refs = dict(blink_refs or {})
        self._selected_component = int(selected_component)
        self._total_duration = (
            float(raw.n_times) / float(raw.info["sfreq"]) if raw is not None and raw.n_times else 0.0
        )
        if not same_fit:
            self._row_scales = {}
            self._sensitivity = 1.0
            self._start_sec = 0.0
            self._duration_sec = min(10.0, self._total_duration) if self._total_duration else 10.0
        else:
            self._duration_sec = min(max(0.10, self._duration_sec), max(self._total_duration, 0.10))
            self._start_sec = min(self._max_start(), max(0.0, self._start_sec))
        self.time_window.blockSignals(True)
        self.time_window.setMaximum(max(0.10, self._total_duration or 7200.0))
        self.time_window.setValue(max(0.10, self._duration_sec))
        self.time_window.blockSignals(False)
        self._configure_slider()
        self._refresh_sources()

    def set_selected_component(self, component_index: int):
        self._selected_component = int(component_index)
        self._refresh_sources()

    def set_labels_and_scores(self, labels=None, blink_scores=None, blink_refs=None):
        if labels is not None:
            self._labels = dict(labels)
        if blink_scores is not None:
            self._blink_scores = dict(blink_scores)
        if blink_refs is not None:
            self._blink_refs = dict(blink_refs)
        self._refresh_sources()

    def _max_start(self) -> float:
        return max(0.0, self._total_duration - self._duration_sec)

    def _configure_slider(self):
        max_ms = int(round(self._max_start() * 1000.0))
        self.position.blockSignals(True)
        self.position.setRange(0, max(0, max_ms))
        self.position.setValue(min(max_ms, int(round(self._start_sec * 1000.0))))
        self.position.setPageStep(max(1, int(round(self._duration_sec * 1000.0))))
        self.position.blockSignals(False)

    def _update_navigation(self):
        end = min(self._total_duration, self._start_sec + self._duration_sec)
        self.time_label.setText(f"{self._format_time(self._start_sec)} – {self._format_time(end)}")
        self.total_label.setText(f"Total: {self._format_time(self._total_duration)}")
        self.prev_btn.setEnabled(self._start_sec > 0.0005)
        self.next_btn.setEnabled(self._start_sec < self._max_start() - 0.0005)
        self.position.setEnabled(self.raw is not None and self._total_duration > self._duration_sec)

    def _visible_sources(self):
        if self.ica is None or self.raw is None:
            return None, None
        sfreq = float(self.raw.info["sfreq"])
        start = max(0, int(round(self._start_sec * sfreq)))
        stop = min(self.raw.n_times, start + int(round(self._duration_sec * sfreq)) + 1)
        sources = self.ica.get_sources(self.raw, start=start, stop=stop)
        data = np.asarray(sources.get_data(), dtype=float)
        t = self._start_sec + np.arange(data.shape[1], dtype=float) / sfreq
        return t, data

    @staticmethod
    def _robust_scale(row: np.ndarray) -> float:
        arr = np.asarray(row, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return 1.0
        center = float(np.median(arr))
        scale = float(np.percentile(np.abs(arr - center), 98.0))
        if not math.isfinite(scale) or scale <= 1e-12:
            scale = float(np.std(arr))
        return max(scale if math.isfinite(scale) else 1.0, 1e-12)

    def _row_label(self, comp: int) -> str:
        text = f"ICA{comp:03d}"
        label = self._labels.get(comp, "")
        if label:
            text += f" · {label}"
        score = self._blink_scores.get(comp, float("nan"))
        if math.isfinite(float(score)):
            text += f" · blink {float(score):.2f}"
        return text

    def _refresh_sources(self):
        if self.ica is None or self.raw is None:
            self.plot.clear(); self._curve_items = []
            self._update_navigation()
            return
        try:
            t, data = self._visible_sources()
            n_comp = int(data.shape[0])
            baselines = {i: float(n_comp - 1 - i) for i in range(n_comp)}
            if not self._row_scales:
                self._row_scales = {i: self._robust_scale(data[i]) for i in range(n_comp)}
            if len(self._curve_items) != n_comp:
                self.plot.clear(); self._curve_items = []
                for _ in range(n_comp):
                    item = self.plot.plot([], [], pen=pg.mkPen(self._theme_curve(), width=1.0))
                    item.setDownsampling(auto=True, method="peak")
                    item.setClipToView(True)
                    self._curve_items.append(item)
            for i, item in enumerate(self._curve_items):
                scale = max(float(self._row_scales.get(i, self._robust_scale(data[i]))), 1e-12)
                y = baselines[i] + (data[i] / scale) * 0.32 * float(self._sensitivity)
                width = 2.0 if i == self._selected_component else 1.0
                item.setPen(pg.mkPen(self._theme_curve(), width=width))
                item.setData(t, y)
            if t is not None and t.size:
                self.plot.setXRange(float(t[0]), float(t[-1]), padding=0.0)
            self.plot.setYRange(-0.75, max(0.75, float(n_comp) - 0.25), padding=0.0)
            ticks = [(baselines[i], self._row_label(i)) for i in range(n_comp)]
            self.plot.getAxis("left").setTicks([ticks])
        except Exception as exc:
            self.plot.setTitle(f"Could not display ICA components: {exc}")
        self._update_navigation()

    def auto_scale_rows(self):
        if self.ica is None or self.raw is None:
            return
        try:
            _t, data = self._visible_sources()
            self._row_scales = {i: self._robust_scale(data[i]) for i in range(int(data.shape[0]))}
            self._refresh_sources()
        except Exception:
            pass

    def _plot_clicked(self, event):
        if self.ica is None or self.raw is None:
            return
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return
            pos = self._vb.mapSceneToView(event.scenePos())
            n_comp = int(self.ica.n_components_)
            comp = int(round((n_comp - 1) - float(pos.y())))
            if 0 <= comp < n_comp:
                self._selected_component = comp
                self.componentActivated.emit(comp)
        except Exception:
            pass

    def _position_changed(self, value: int):
        self._start_sec = min(self._max_start(), max(0.0, float(value) / 1000.0))
        self._refresh_sources()

    def _duration_changed(self, value: float):
        old = self._duration_sec
        center = self._start_sec + old / 2.0
        self._duration_sec = min(max(0.10, float(value)), max(self._total_duration, 0.10))
        self._start_sec = min(self._max_start(), max(0.0, center - self._duration_sec / 2.0))
        self._configure_slider()
        self._refresh_sources()

    def _pan_source(self, delta_seconds: float):
        self._pending_pan_delta += float(delta_seconds)
        if not self._pan_timer.isActive():
            self._pan_timer.start()

    def _flush_pending_pan(self):
        delta = float(self._pending_pan_delta)
        self._pending_pan_delta = 0.0
        if not math.isfinite(delta) or abs(delta) <= 0.0:
            return
        target = min(self._max_start(), max(0.0, self._start_sec + delta))
        self.position.setValue(int(round(target * 1000.0)))

    def step_source(self, direction: int):
        target = self._start_sec + float(direction) * self._duration_sec
        target = min(self._max_start(), max(0.0, target))
        self.position.setValue(int(round(target * 1000.0)))

    def zoom_time_in(self):
        self.time_window.setValue(max(self.time_window.minimum(), self._duration_sec / 1.25))

    def zoom_time_out(self):
        self.time_window.setValue(min(self.time_window.maximum(), self._duration_sec * 1.25))

    def increase_y_sensitivity(self):
        self._sensitivity = min(8.0, self._sensitivity * 1.25)
        self._refresh_sources()

    def decrease_y_sensitivity(self):
        self._sensitivity = max(0.125, self._sensitivity / 1.25)
        self._refresh_sources()

    def show_full_duration(self):
        if self._total_duration <= 0:
            return
        self._start_sec = 0.0
        self.time_window.setValue(self._total_duration)


class ICAComponentView(QWidget):
    exclusionsChanged = Signal(object)
    """ICA component table + topomap + stable-scale source viewer/pop-out browser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark_mode = False
        self.ica = None
        self.raw = None
        self._updating = False
        self._component_index = 0
        self._start_sec = 0.0
        self._duration_sec = 10.0
        self._total_duration = 0.0
        self._opengl_enabled = False
        self._auto_labels = {}
        self._auto_probabilities = {}
        self._blink_scores: dict[int, float] = {}
        self._blink_refs: dict[int, str] = {}
        self._component_y_ranges: dict[int, tuple[float, float]] = {}
        self._trace_color_override: str | None = None
        self._source_curve = None
        self._pending_pan_delta = 0.0
        self._pan_timer = QTimer(self)
        self._pan_timer.setSingleShot(True)
        self._pan_timer.setInterval(30)
        self._pan_timer.timeout.connect(self._flush_pending_pan)
        self._shortcut_map = {
            "previous": "Left", "next": "Right", "time_in": "+", "time_out": "-",
            "sensitivity_up": "*", "sensitivity_down": "/",
        }
        self._all_window = None

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Remove", "Component", "Blink corr.", "ICLabel", "Confidence"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._item_changed)

        self.figure = Figure(figsize=(4, 4), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)

        self._source_vb = ICAHorizontalViewBox()
        self._source_vb.panRequested.connect(self._pan_source)
        self.source_plot = pg.PlotWidget(viewBox=self._source_vb)
        self.source_plot.setBackground("#ffffff")
        self.source_plot.showGrid(x=True, y=True, alpha=0.15)
        self.source_plot.setLabel("bottom", "Recording time", units="s")
        self.source_plot.setLabel("left", "ICA source (a.u.)")
        self.source_plot.setMenuEnabled(False)
        self.source_plot.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.source_plot.setToolTip(
            "The entire recording is available. Drag horizontally or use Left/Right arrows to move; +/- changes the visible time span."
        )
        self.source_plot.setAntialiasing(False)

        self.prev_btn = QPushButton("◀")
        self.prev_btn.setToolTip("Previous ICA source window (Left arrow)")
        self.prev_btn.clicked.connect(lambda: self.step_source(-1))
        self.next_btn = QPushButton("▶")
        self.next_btn.setToolTip("Next ICA source window (Right arrow)")
        self.next_btn.clicked.connect(lambda: self.step_source(1))

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.valueChanged.connect(self._position_changed)

        self.time_window = ReliableDoubleSpinBox()
        self.time_window.setRange(0.10, 7200.0)
        self.time_window.setDecimals(2)
        self.time_window.setValue(10.0)
        self.time_window.setSuffix(" s")
        self.time_window.valueChanged.connect(self._duration_changed)

        self.full_btn = QPushButton("Full duration")
        self.full_btn.setToolTip("Show the complete ICA source recording at once (may take longer for long files).")
        self.full_btn.clicked.connect(self.show_full_duration)
        self.lock_y = QCheckBox("Lock Y scale")
        self.lock_y.setChecked(True)
        self.lock_y.setToolTip("Keep the same vertical scale while moving horizontally through the component source.")
        self.lock_y.toggled.connect(self._lock_y_changed)
        self.auto_y_btn = QPushButton("Auto-scale Y")
        self.auto_y_btn.setToolTip("Recalculate the vertical scale from the currently visible source segment, then keep it fixed.")
        self.auto_y_btn.clicked.connect(self.auto_scale_y)
        self.open_all_btn = QPushButton("Open time-domain component viewer…")
        self.open_all_btn.setToolTip("Open all fitted ICA component time courses in one large vertically separated browser.")
        self.open_all_btn.clicked.connect(self.open_all_components_window)

        self.time_label = QLabel("00:00.000 – 00:00.000")
        self.total_label = QLabel("Total: 00:00.000")

        nav = QHBoxLayout()
        nav.addWidget(self.prev_btn)
        nav.addWidget(QLabel("ICA source position"))
        nav.addWidget(self.position, 1)
        nav.addWidget(self.next_btn)
        nav.addWidget(QLabel("Visible"))
        nav.addWidget(self.time_window)
        nav.addWidget(self.full_btn)
        nav.addWidget(self.lock_y)
        nav.addWidget(self.auto_y_btn)
        nav.addWidget(self.open_all_btn)
        nav.addWidget(self.time_label)
        nav.addWidget(self.total_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Selected component topography"))
        self.classification_label = QLabel("ICLabel: not run")
        self.classification_label.setProperty("muted", True)
        right_layout.addWidget(self.classification_label)
        self.blink_aid_label = QLabel("Blink correlation aid: not run")
        self.blink_aid_label.setProperty("muted", True)
        self.blink_aid_label.setWordWrap(True)
        right_layout.addWidget(self.blink_aid_label)
        self.topomap_status = QLabel("Sensor positions: not checked")
        self.topomap_status.setProperty("muted", True)
        right_layout.addWidget(self.topomap_status)
        right_layout.addWidget(self.canvas, 1)
        topo_note = QLabel("Topomap = spatial distribution of this independent component across the recorded scalp electrodes.")
        topo_note.setWordWrap(True); topo_note.setProperty("muted", True)
        right_layout.addWidget(topo_note)
        right_layout.addWidget(QLabel("Selected component source across the recording"))
        right_layout.addWidget(self.source_plot, 1)
        right_layout.addLayout(nav)

        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._install_shortcuts()
        self._update_navigation()

    def set_opengl_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self.source_plot.useOpenGL(enabled)
        self.source_plot.setAntialiasing(False)
        self._opengl_enabled = enabled
        self.source_plot.viewport().update()
        if self._all_window is not None:
            try:
                self._all_window.set_opengl_enabled(enabled)
            except RuntimeError:
                self._all_window = None

    def set_dark_mode(self, dark: bool):
        self._dark_mode = bool(dark)
        bg = "#17191d" if self._dark_mode else "#ffffff"
        fg = "#e8ebef" if self._dark_mode else "#20242a"
        self.source_plot.setBackground(bg)
        for axis_name in ("left", "bottom"):
            axis = self.source_plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        # Keep the topomap canvas as a light scientific panel: MNE's sensor
        # outlines/topomap annotations are authored for a light Matplotlib face.
        self.figure.set_facecolor("white")
        if self.ica is not None and self.raw is not None:
            self.show_component(self._component_index)
        else:
            self.canvas.draw_idle()
        if self._all_window is not None:
            try:
                self._all_window.set_dark_mode(self._dark_mode)
            except RuntimeError:
                self._all_window = None

    def _theme_curve(self):
        return self._trace_color_override or ("#8fd3ff" if self._dark_mode else "#1769aa")

    def _install_shortcuts(self):
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        callbacks = {
            "previous": lambda: self.step_source(-1),
            "next": lambda: self.step_source(1),
            "time_in": self.zoom_time_in,
            "time_out": self.zoom_time_out,
            "sensitivity_up": self.increase_y_sensitivity,
            "sensitivity_down": self.decrease_y_sensitivity,
        }
        self._shortcuts = []
        for name, callback in callbacks.items():
            key = str(self._shortcut_map.get(name, "") or "").strip()
            if not key:
                continue
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(callback)
            self._shortcuts.append(sc)

    def set_shortcut_map(self, mapping: dict):
        self._shortcut_map.update({k: str(v) for k, v in dict(mapping or {}).items() if k in self._shortcut_map})
        self._install_shortcuts()
        if self._all_window is not None:
            try:
                self._all_window.set_shortcut_map(self._shortcut_map)
            except RuntimeError:
                self._all_window = None

    def set_trace_color(self, color: str | None):
        self._trace_color_override = str(color) if color else None
        self._refresh_source()
        if self._all_window is not None:
            try:
                self._all_window.set_trace_color(self._trace_color_override)
            except RuntimeError:
                self._all_window = None

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    def clear_ica(self):
        self.ica = None
        self.raw = None
        self._component_index = 0
        self._start_sec = 0.0
        self._total_duration = 0.0
        self.table.setRowCount(0)
        self._auto_labels = {}
        self._auto_probabilities = {}
        self._blink_scores = {}
        self._blink_refs = {}
        self._component_y_ranges = {}
        if self._all_window is not None:
            try:
                self._all_window.close()
            except Exception:
                pass
            self._all_window = None
        self.classification_label.setText("ICLabel: not run")
        self.blink_aid_label.setText("Blink correlation aid: not run")
        self.topomap_status.setText("Sensor positions: not checked")
        self.figure.clear()
        self.canvas.draw_idle()
        self.source_plot.clear(); self._source_curve = None
        self._configure_slider()
        self._update_navigation()

    def set_ica(self, ica, raw, excluded: list[int] | None = None):
        self.ica = ica
        self.raw = raw
        self._total_duration = (
            float(raw.n_times) / float(raw.info["sfreq"]) if raw is not None and raw.n_times else 0.0
        )
        self._start_sec = 0.0
        self._duration_sec = min(10.0, self._total_duration) if self._total_duration else 10.0
        self.time_window.blockSignals(True)
        self.time_window.setMaximum(max(0.10, self._total_duration or 7200.0))
        self.time_window.setValue(max(0.10, self._duration_sec))
        self.time_window.blockSignals(False)
        self._configure_slider()

        excluded = set(excluded or [])
        self._updating = True
        self.table.setRowCount(int(ica.n_components_))
        self._auto_labels = {}
        self._auto_probabilities = {}
        self._blink_scores = {}
        self._blink_refs = {}
        self._component_y_ranges = {}
        for i in range(int(ica.n_components_)):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if i in excluded else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, check)
            item = QTableWidgetItem(f"ICA{i:03d}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 1, item)
            blink_item = QTableWidgetItem("—")
            blink_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            blink_item.setToolTip("Advisory maximum absolute correlation with available frontal EEG reference channels (1–10 Hz).")
            self.table.setItem(i, 2, blink_item)
            label_item = QTableWidgetItem("—")
            label_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 3, label_item)
            prob_item = QTableWidgetItem("—")
            prob_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 4, prob_item)
        self._updating = False
        self.table.resizeColumnsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)
            self.show_component(0)
        self._update_navigation()

    def set_component_labels(self, labels, probabilities=None):
        """Display advisory component classifications without changing removal checks."""
        probabilities = list(probabilities or [])
        self._auto_labels = {}
        self._auto_probabilities = {}
        for i in range(min(self.table.rowCount(), len(labels))):
            label = str(labels[i])
            prob = float(probabilities[i]) if i < len(probabilities) else float("nan")
            self._auto_labels[i] = label
            self._auto_probabilities[i] = prob
            label_item = self.table.item(i, 3)
            if label_item is None:
                label_item = QTableWidgetItem()
                label_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(i, 3, label_item)
            label_item.setText(label)
            label_item.setToolTip("ICLabel is a trained classifier prediction. Confirm with morphology and topography before removal; performance can change when data/preprocessing differ from its training distribution.")
            prob_item = self.table.item(i, 4)
            if prob_item is None:
                prob_item = QTableWidgetItem()
                prob_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(i, 4, prob_item)
            prob_item.setText(f"{prob * 100:.1f}%" if math.isfinite(prob) else "—")
        self.table.resizeColumnsToContents()
        self._update_component_classification_label()
        self._sync_all_components_window()

    def set_blink_scores(self, scores, reference_channels=None):
        """Display an advisory frontal blink-correlation score per component.

        The score is informational only and never checks a component for removal.
        """
        refs = list(reference_channels or [])
        self._blink_scores = {}
        self._blink_refs = {}
        for i in range(min(self.table.rowCount(), len(scores or []))):
            try:
                score = float(scores[i])
            except Exception:
                score = float("nan")
            ref = str(refs[i]) if i < len(refs) else ""
            self._blink_scores[i] = score
            self._blink_refs[i] = ref
            item = self.table.item(i, 2)
            if item is None:
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(i, 2, item)
            if math.isfinite(score):
                item.setText(f"{score:.2f}" + (f" · {ref}" if ref else ""))
                item.setToolTip(
                    "Advisory only: maximum absolute 1–10 Hz Pearson correlation between this ICA source "
                    f"and the available frontal EEG references. Best reference: {ref or '—'}. "
                    "A high value supports blink suspicion but does not prove that the component should be removed."
                )
            else:
                item.setText("—")
                item.setToolTip("Blink-correlation aid unavailable for this component.")
        self.table.resizeColumnsToContents()
        self._update_component_classification_label()
        self._sync_all_components_window()

    def _update_component_classification_label(self):
        idx = int(self._component_index)
        if idx not in self._auto_labels:
            self.classification_label.setText("ICLabel: not run / unavailable")
        else:
            label = self._auto_labels[idx]
            prob = self._auto_probabilities.get(idx, float("nan"))
            conf = f" ({prob * 100:.1f}%)" if math.isfinite(prob) else ""
            self.classification_label.setText(
                f"ICLabel: {label}{conf} — trained classifier suggestion; confirm with morphology and topography."
            )

        score = self._blink_scores.get(idx, float("nan"))
        ref = self._blink_refs.get(idx, "")
        if math.isfinite(float(score)):
            self.blink_aid_label.setText(
                f"Blink correlation aid: |r|={float(score):.2f}"
                + (f" with {ref}" if ref else "")
                + ". Advisory only — confirm repeated blink morphology and frontal topography."
            )
        else:
            self.blink_aid_label.setText("Blink correlation aid: unavailable / not run")

    def excluded_components(self) -> list[int]:
        out = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                out.append(i)
        return out

    def _item_changed(self, _item):
        if self._updating or self.ica is None:
            return
        self.exclusionsChanged.emit(self.excluded_components())

    def _selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.show_component(rows[0].row())

    def show_component(self, index: int):
        if self.ica is None or self.raw is None:
            return
        self._component_index = int(index)
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        weights = self.ica.get_components()[:, index]
        try:
            info, position_source = _topomap_info_with_inferred_montage(self.raw, self.ica.ch_names)
            mne.viz.plot_topomap(weights, info, axes=ax, show=False, contours=6, sensors=True)
            ax.set_title(f"ICA{index:03d}")
            self.topomap_status.setText(f"Sensor positions: {position_source}")
        except Exception as exc:
            ax.bar(np.arange(len(weights)), weights)
            ax.set_title(f"ICA{index:03d} — topomap unavailable")
            ax.set_xlabel("Channel index")
            ax.set_ylabel("Weight")
            ax.text(0.02, 0.02, str(exc)[:160], transform=ax.transAxes, fontsize=7)
            self.topomap_status.setText(f"Topomap unavailable: {str(exc)[:180]}")
        self._update_component_classification_label()
        self.canvas.draw_idle()
        self._refresh_source()
        if self._all_window is not None:
            try:
                self._all_window.set_selected_component(int(self._component_index))
            except RuntimeError:
                self._all_window = None

    def _max_start(self) -> float:
        return max(0.0, self._total_duration - self._duration_sec)

    def _configure_slider(self):
        max_ms = int(round(self._max_start() * 1000.0))
        self.position.blockSignals(True)
        self.position.setRange(0, max(0, max_ms))
        self.position.setValue(min(max_ms, int(round(self._start_sec * 1000.0))))
        self.position.setPageStep(max(1, int(round(self._duration_sec * 1000.0))))
        self.position.blockSignals(False)

    def _update_navigation(self):
        end = min(self._total_duration, self._start_sec + self._duration_sec)
        self.time_label.setText(f"{self._format_time(self._start_sec)} – {self._format_time(end)}")
        self.total_label.setText(f"Total: {self._format_time(self._total_duration)}")
        self.prev_btn.setEnabled(self._start_sec > 0.0005)
        self.next_btn.setEnabled(self._start_sec < self._max_start() - 0.0005)
        self.position.setEnabled(self.raw is not None and self._total_duration > self._duration_sec)

    @staticmethod
    def _range_for_data(data: np.ndarray) -> tuple[float, float]:
        arr = np.asarray(data, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return (-1.0, 1.0)
        lo, hi = float(np.min(arr)), float(np.max(arr))
        if math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-12):
            pad = max(abs(lo) * 0.2, 1.0)
        else:
            pad = 0.08 * (hi - lo)
        return (lo - pad, hi + pad)

    def _visible_source(self):
        if self.ica is None or self.raw is None:
            return None, None
        sfreq = float(self.raw.info["sfreq"])
        start = max(0, int(round(self._start_sec * sfreq)))
        stop = min(self.raw.n_times, start + int(round(self._duration_sec * sfreq)) + 1)
        sources = self.ica.get_sources(self.raw, start=start, stop=stop)
        src = sources.get_data(picks=[self._component_index])[0]
        t = self._start_sec + np.arange(src.size, dtype=float) / sfreq
        return t, src

    def _refresh_source(self):
        if self.ica is None or self.raw is None:
            self.source_plot.clear(); self._source_curve = None
            self._update_navigation()
            return
        try:
            t, src = self._visible_source()
            if self._source_curve is None:
                self.source_plot.clear()
                self._source_curve = self.source_plot.plot([], [], pen=pg.mkPen(self._theme_curve(), width=1))
                self._source_curve.setDownsampling(auto=True, method="peak")
                self._source_curve.setClipToView(True)
            self._source_curve.setPen(pg.mkPen(self._theme_curve(), width=1))
            self._source_curve.setData(t, src)
            if t is not None and t.size:
                self.source_plot.setXRange(float(t[0]), float(t[-1]), padding=0.0)
            if self.lock_y.isChecked():
                yr = self._component_y_ranges.get(self._component_index)
                if yr is None:
                    yr = self._range_for_data(src)
                    self._component_y_ranges[self._component_index] = yr
                self.source_plot.setYRange(float(yr[0]), float(yr[1]), padding=0.0)
            else:
                self.source_plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            self.source_plot.setTitle(f"ICA{self._component_index:03d}")
        except Exception as exc:
            self.source_plot.setTitle(f"Could not display source: {exc}")
        self._update_navigation()

    def auto_scale_y(self):
        if self.ica is None or self.raw is None:
            return
        try:
            _t, src = self._visible_source()
            yr = self._range_for_data(src)
            self._component_y_ranges[self._component_index] = yr
            self.lock_y.setChecked(True)
            self.source_plot.setYRange(float(yr[0]), float(yr[1]), padding=0.0)
        except Exception:
            pass

    def _lock_y_changed(self, checked: bool):
        if checked:
            if self._component_index not in self._component_y_ranges:
                self.auto_scale_y()
            else:
                yr = self._component_y_ranges[self._component_index]
                self.source_plot.setYRange(float(yr[0]), float(yr[1]), padding=0.0)
        else:
            self.source_plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

    def _scale_y(self, factor: float):
        if self.ica is None or self.raw is None:
            return
        if self._component_index not in self._component_y_ranges:
            self.auto_scale_y()
        yr = self._component_y_ranges.get(self._component_index)
        if not yr:
            return
        center = (float(yr[0]) + float(yr[1])) / 2.0
        half = max((float(yr[1]) - float(yr[0])) / 2.0 * float(factor), 1e-12)
        new = (center - half, center + half)
        self._component_y_ranges[self._component_index] = new
        self.lock_y.setChecked(True)
        self.source_plot.setYRange(*new, padding=0.0)

    def increase_y_sensitivity(self):
        self._scale_y(0.8)

    def decrease_y_sensitivity(self):
        self._scale_y(1.25)

    def open_all_components_window(self):
        if self.ica is None or self.raw is None:
            return
        if self._all_window is None:
            self._all_window = ICAAllComponentsWindow(self)
            self._all_window.destroyed.connect(lambda *_: setattr(self, "_all_window", None))
            self._all_window.componentActivated.connect(self._all_window_component_selected)
            self._all_window.set_dark_mode(self._dark_mode)
            self._all_window.set_opengl_enabled(self._opengl_enabled)
            self._all_window.set_shortcut_map(self._shortcut_map)
            self._all_window.set_trace_color(self._trace_color_override)
        self._sync_all_components_window()
        self._all_window.show()
        self._all_window.raise_()
        self._all_window.activateWindow()

    def _all_window_component_selected(self, component_index: int):
        if not (0 <= int(component_index) < self.table.rowCount()):
            return
        self.table.selectRow(int(component_index))
        self.show_component(int(component_index))

    def _sync_all_components_window(self):
        if self._all_window is None or self.ica is None or self.raw is None:
            return
        try:
            self._all_window.set_context(
                self.ica, self.raw,
                labels=self._auto_labels,
                blink_scores=self._blink_scores,
                blink_refs=self._blink_refs,
                selected_component=int(self._component_index),
            )
        except RuntimeError:
            self._all_window = None

    def _position_changed(self, value: int):
        self._start_sec = min(self._max_start(), max(0.0, float(value) / 1000.0))
        self._refresh_source()

    def _duration_changed(self, value: float):
        old = self._duration_sec
        center = self._start_sec + old / 2.0
        self._duration_sec = min(max(0.10, float(value)), max(self._total_duration, 0.10))
        self._start_sec = min(self._max_start(), max(0.0, center - self._duration_sec / 2.0))
        self._configure_slider()
        self._refresh_source()

    def _pan_source(self, delta_seconds: float):
        self._pending_pan_delta += float(delta_seconds)
        if not self._pan_timer.isActive():
            self._pan_timer.start()

    def _flush_pending_pan(self):
        delta = float(self._pending_pan_delta)
        self._pending_pan_delta = 0.0
        if not math.isfinite(delta) or abs(delta) <= 0.0:
            return
        target = min(self._max_start(), max(0.0, self._start_sec + delta))
        self.position.setValue(int(round(target * 1000.0)))

    def step_source(self, direction: int):
        # One full visible page. This makes the arrow buttons predictable for
        # long recordings and matches the continuous EEG browser.
        target = self._start_sec + float(direction) * self._duration_sec
        target = min(self._max_start(), max(0.0, target))
        self.position.setValue(int(round(target * 1000.0)))

    def zoom_time_in(self):
        self.time_window.setValue(max(self.time_window.minimum(), self._duration_sec / 1.25))

    def zoom_time_out(self):
        self.time_window.setValue(min(self.time_window.maximum(), self._duration_sec * 1.25))

    def show_full_duration(self):
        if self._total_duration <= 0:
            return
        self._start_sec = 0.0
        self.time_window.setValue(self._total_duration)
