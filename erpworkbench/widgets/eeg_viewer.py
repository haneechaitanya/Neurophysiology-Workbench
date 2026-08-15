from __future__ import annotations

import math
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..ui_utils import ReliableDoubleSpinBox, ReliableSpinBox


class HorizontalDragViewBox(pg.ViewBox):
    """ViewBox that reserves left-drag for time navigation.

    It emits a requested horizontal shift in the same units as the x axis. The
    parent viewer decides whether this means loading another part of a Raw
    recording or panning within an already supplied epoch.
    """

    panRequested = Signal(float)
    spanSelectionStarted = Signal(float)
    spanSelectionUpdated = Signal(float, float)
    spanSelectionFinished = Signal(float, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_scene_pos = None
        self._right_drag_start_x = None
        self.spanSelectionEnabled = False
        self.setMouseEnabled(x=False, y=False)

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 - Qt/pyqtgraph API name
        if ev.button() == Qt.MouseButton.RightButton:
            if not self.spanSelectionEnabled:
                ev.ignore()
                return
            ev.accept()
            x = float(self.mapSceneToView(ev.scenePos()).x())
            if ev.isStart() or self._right_drag_start_x is None:
                self._right_drag_start_x = x
                self.spanSelectionStarted.emit(x)
            else:
                self.spanSelectionUpdated.emit(float(self._right_drag_start_x), x)
            if ev.isFinish():
                start_x = float(self._right_drag_start_x if self._right_drag_start_x is not None else x)
                self.spanSelectionFinished.emit(start_x, x)
                self._right_drag_start_x = None
            return

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

        rect = self.sceneBoundingRect()
        width_px = max(float(rect.width()), 1.0)
        x_range = self.viewRange()[0]
        x_span = max(float(x_range[1] - x_range[0]), 1e-12)
        shift = -(delta_px / width_px) * x_span
        if math.isfinite(shift) and abs(shift) > 0:
            self.panRequested.emit(shift)

        if ev.isFinish():
            self._last_scene_pos = None

    def wheelEvent(self, ev, axis=None):  # noqa: N802
        # Time and amplitude zoom are deliberately keyboard-controlled so the
        # mouse wheel cannot accidentally alter scientific display scaling.
        ev.ignore()


class DisplayChannelDialog(QDialog):
    """Simple checkbox channel picker for waveform display only."""

    def __init__(self, channels: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select channels to display")
        self.resize(360, 560)
        layout = QVBoxLayout(self)
        note = QLabel("Checked channels are shown in this waveform viewer. This changes display only, not the EEG data.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        selected_set = set(selected) if selected else set(channels)
        for ch in channels:
            item = QListWidgetItem(ch)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ch in selected_set else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Clear all")
        all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        none_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        row.addWidget(all_btn); row.addWidget(none_btn); row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
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


class StackedEEGViewer(QWidget):
    """Embedded stacked EEG viewer used for continuous data and epoch review.

    Display conventions
    -------------------
    * Left-drag waveform: pan horizontally in time.
    * + : time zoom in (less time visible).
    * - : time zoom out (more time visible).
    * * : increase amplitude sensitivity (larger deflections).
    * / : decrease amplitude sensitivity (smaller deflections).

    A vertical calibration bar is drawn below the lowest channel. Its physical
    height equals one channel-row spacing and its label gives the corresponding
    voltage in microvolts.
    """

    channelSelectionChanged = Signal(object)
    spanSelected = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark_mode = False
        self._duration = 10.0
        self._total_duration = 0.0
        self._start = 0.0
        self._raw = None
        self._opengl_enabled = False
        self._pending_pan_delta = 0.0

        # Coalesce very frequent mouse-drag events so long EEG traces redraw at
        # at most about 60 frames/s instead of rebuilding the scene for every
        # individual mouse-move event.
        self._pan_timer = QTimer(self)
        self._pan_timer.setSingleShot(True)
        # Around 30 fps is visually smooth for drag navigation and avoids queueing
        # more redraws than a long multichannel EEG scene can present.
        self._pan_timer.setInterval(30)
        self._pan_timer.timeout.connect(self._flush_pending_pan)
        self._trace_items = []
        self._overlay_items = []
        self._last_trace_names: tuple[str, ...] = ()

        self._manual_data = None
        self._manual_names: list[str] = []
        self._manual_sfreq = 1.0
        self._manual_x0 = 0.0
        self._manual_is_preview = False
        self._manual_view_start = 0.0
        self._manual_view_duration = 0.0
        # Standalone segments (notably Epoch Review) may be displayed with
        # blank margins around the complete segment.  A factor of 4 means the
        # epoch can occupy as little as 25% of the plot width, matching the
        # compact review convention used in BESS-like viewers.
        self._standalone_max_zoom_out_factor = 1.0
        self._polarity_inverted = False
        self._rejected_visual = False
        self._rejected_visual_label = "REJECTED"
        # Empty means show all available channels (subject to Max rows). A
        # non-empty list is a display-only subset selected by the user.
        self._selected_channel_names: list[str] = []
        # Optional display-only spans, used by the ICA-beta fit-data editor.
        # They never modify Raw.annotations or downstream epoch rejection.
        self._overlay_spans: list[tuple[float, float, str]] = []
        self._trace_color_override: str | None = None
        self._shortcut_map = {
            "previous": "Left", "next": "Right", "time_in": "+", "time_out": "-",
            "sensitivity_up": "*", "sensitivity_down": "/",
        }

        self._view_box = HorizontalDragViewBox()
        self._view_box.panRequested.connect(self._pan_requested)
        self._selection_region = None
        self._view_box.spanSelectionStarted.connect(self._span_selection_started)
        self._view_box.spanSelectionUpdated.connect(self._span_selection_updated)
        self._view_box.spanSelectionFinished.connect(self._span_selection_finished)
        self.plot = pg.PlotWidget(viewBox=self._view_box)
        self.plot.setBackground("#ffffff")
        self.plot.showGrid(x=True, y=False, alpha=0.15)
        self.plot.getAxis("left").setStyle(tickTextWidth=90)
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setMenuEnabled(False)
        self.plot.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.plot.setToolTip("Left-drag = move through time. Right-drag can select a span when the parent workflow enables it. +/− = time scale; */ = amplitude sensitivity.")
        self.plot.setAntialiasing(False)

        # Slider coordinate is milliseconds, giving intuitive random access to
        # ordinary EEG recordings without pretending the visible window is the
        # complete file.
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.valueChanged.connect(self._slider_changed)

        self.prev_window = QPushButton("◀ Previous")
        self.prev_window.setToolTip("Move backward by one displayed window")
        self.prev_window.clicked.connect(lambda: self.step_windows(-1))
        self.next_window = QPushButton("Next ▶")
        self.next_window.setToolTip("Move forward by one displayed window")
        self.next_window.clicked.connect(lambda: self.step_windows(1))

        self.time_label = QLabel("00:00.000 – 00:00.000")
        self.time_label.setMinimumWidth(190)
        self.total_label = QLabel("Total: 00:00.000")
        self.total_label.setMinimumWidth(130)

        self.duration = ReliableDoubleSpinBox()
        self.duration.setRange(0.10, 120.0)
        self.duration.setDecimals(2)
        self.duration.setSingleStep(1.0)
        self.duration.setValue(10.0)
        self.duration.setSuffix(" s")
        self.duration.setToolTip("Amount of time visible at once. It never crops the loaded recording.")
        self.duration.valueChanged.connect(self._duration_changed)

        self.scale = ReliableDoubleSpinBox()
        self.scale.setRange(1.0, 5000.0)
        self.scale.setDecimals(1)
        self.scale.setSingleStep(10.0)
        self.scale.setValue(100.0)
        self.scale.setSuffix(" µV/row")
        self.scale.setToolTip(
            "Vertical display scale. One channel-row spacing equals this many µV. "
            "Press * for greater sensitivity and / for lower sensitivity."
        )
        self.scale.valueChanged.connect(lambda _: self.refresh())

        self.channel_limit = ReliableSpinBox()
        self.channel_limit.setRange(1, 128)
        self.channel_limit.setValue(32)
        self.channel_limit.valueChanged.connect(lambda _: self.refresh())

        self.channel_select_btn = QPushButton("Select channels…")
        self.channel_select_btn.setToolTip("Choose a specific subset of channels for this display. Display-only; underlying EEG is unchanged.")
        self.channel_select_btn.clicked.connect(self._choose_display_channels)

        self.show_annotations = QCheckBox("Event markers")
        self.show_annotations.setChecked(True)
        self.show_annotations.setToolTip(
            "Show MNE annotations, including an attached recorder Annotation.txt file, directly on continuous EEG."
        )
        self.show_annotations.toggled.connect(lambda _: self.refresh())
        self.show_annotation_labels = QCheckBox("Marker labels")
        self.show_annotation_labels.setChecked(True)
        self.show_annotation_labels.setToolTip(
            "Show event names when space permits. Overlapping labels are automatically hidden; zoom in (+) to reveal them."
        )
        self.show_annotation_labels.toggled.connect(lambda _: self.refresh())

        self.polarity_btn = QPushButton("Positive ↑")
        self.polarity_btn.setCheckable(True)
        self.polarity_btn.setToolTip(
            "Display polarity only. Default is positive-up; enable for positive-down. "
            "This never changes the underlying EEG samples."
        )
        self.polarity_btn.toggled.connect(self._polarity_toggled)

        nav = QHBoxLayout()
        nav.addWidget(self.prev_window)
        self.position_caption = QLabel("Recording position")
        nav.addWidget(self.position_caption)
        nav.addWidget(self.position, 1)
        nav.addWidget(self.next_window)
        nav.addWidget(self.time_label)
        nav.addWidget(self.total_label)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Time scale"))
        controls.addWidget(self.duration)
        controls.addWidget(QLabel("Vertical scale"))
        controls.addWidget(self.scale)
        controls.addWidget(QLabel("Max rows"))
        controls.addWidget(self.channel_limit)
        controls.addWidget(self.channel_select_btn)
        controls.addWidget(self.show_annotations)
        controls.addWidget(self.show_annotation_labels)
        controls.addWidget(self.polarity_btn)
        controls.addStretch(1)

        self.shortcut_help = QLabel(
            "Mouse: drag waveform horizontally to move in time   |   ←/→ move one page   |   "
            "+ time zoom in   − time zoom out   |   * increase sensitivity   / decrease sensitivity"
        )
        self.shortcut_help.setWordWrap(True)
        self.shortcut_help.setProperty("muted", True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot, 1)
        layout.addLayout(nav)
        layout.addLayout(controls)
        layout.addWidget(self.shortcut_help)

        self._install_shortcuts()
        self._update_navigation_ui()

    def _span_selection_started(self, x: float):
        if self._selection_region is not None:
            try:
                self.plot.removeItem(self._selection_region)
            except Exception:
                pass
        self._selection_region = pg.LinearRegionItem(values=(x, x), movable=False, brush=pg.mkBrush(180, 70, 70, 45), pen=pg.mkPen("#cc6666"))
        self._selection_region.setZValue(50)
        self.plot.addItem(self._selection_region)

    def _span_selection_updated(self, a: float, b: float):
        if self._selection_region is not None:
            self._selection_region.setRegion((a, b))

    def _span_selection_finished(self, a: float, b: float):
        if self._selection_region is not None:
            self._selection_region.setRegion((a, b))
            self.plot.removeItem(self._selection_region)
            self._selection_region = None
        lo, hi = sorted((float(a), float(b)))
        if hi - lo >= 0.010:
            self.spanSelected.emit(lo, hi)

    def set_span_selection_enabled(self, enabled: bool):
        self._view_box.spanSelectionEnabled = bool(enabled)

    def set_opengl_enabled(self, enabled: bool):
        """Use Qt/OpenGL as the PlotWidget viewport when requested.

        PyQtGraph exposes this per GraphicsView.  It accelerates drawing only;
        MNE filtering/ICA/epoch calculations are unaffected.
        """
        enabled = bool(enabled)
        self.plot.useOpenGL(enabled)
        self.plot.setAntialiasing(False)
        self._opengl_enabled = enabled
        self.plot.viewport().update()

    def set_dark_mode(self, dark: bool):
        """Synchronize the scientific viewer with the application theme."""
        self._dark_mode = bool(dark)
        bg = "#17191d" if self._dark_mode else "#ffffff"
        fg = "#e8ebef" if self._dark_mode else "#20242a"
        self.plot.setBackground(bg)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        self.refresh()

    def _theme_fg(self):
        return "#e8ebef" if self._dark_mode else "#20242a"

    def _install_shortcuts(self):
        # WidgetWithChildrenShortcut makes the keys work while the user is
        # interacting with this viewer, without making them global application
        # shortcuts that could interfere with unrelated text entry.
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        callbacks = {
            "previous": lambda: self.step_time(-1),
            "next": lambda: self.step_time(1),
            "time_in": self.zoom_time_in,
            "time_out": self.zoom_time_out,
            "sensitivity_up": self.increase_sensitivity,
            "sensitivity_down": self.decrease_sensitivity,
        }
        self._shortcuts = []
        for name, callback in callbacks.items():
            key = str(self._shortcut_map.get(name, "") or "").strip()
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        if hasattr(self, "shortcut_help"):
            m = self._shortcut_map
            self.shortcut_help.setText(
                f"Mouse: drag waveform horizontally to move in time   |   {m.get('previous','')}/{m.get('next','')} move one page   |   "
                f"{m.get('time_in','')} time zoom in   {m.get('time_out','')} time zoom out   |   "
                f"{m.get('sensitivity_up','')} increase sensitivity   {m.get('sensitivity_down','')} decrease sensitivity"
            )

    def set_shortcut_map(self, mapping: dict):
        self._shortcut_map.update({k: str(v) for k, v in dict(mapping or {}).items() if k in self._shortcut_map})
        self._install_shortcuts()

    def set_trace_color(self, color: str | None):
        self._trace_color_override = str(color) if color else None
        self.refresh()

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    @staticmethod
    def _format_relative_time(seconds: float) -> str:
        return f"{float(seconds):+.3f} s"

    @property
    def start_sec(self) -> float:
        return self._start

    @property
    def duration_sec(self) -> float:
        return self._duration

    @property
    def total_duration_sec(self) -> float:
        return self._total_duration

    def set_standalone_max_zoom_out_factor(self, factor: float):
        """Allow a standalone segment to occupy only part of the plot width.

        ``factor=4`` permits an epoch of duration D to be shown inside an x
        range of 4D, i.e. the complete epoch occupies 25% of the plot width.
        This is display-only and never pads or changes the supplied data.
        """
        self._standalone_max_zoom_out_factor = max(1.0, float(factor))
        if self._is_standalone_segment():
            self._set_manual_view_duration(self._manual_view_duration)

    def set_rejected_visual(self, rejected: bool, label: str = "REJECTED"):
        """Dim a standalone epoch to make the current final rejection obvious."""
        self._rejected_visual = bool(rejected)
        self._rejected_visual_label = str(label or "REJECTED")
        self.refresh()

    def _polarity_toggled(self, checked: bool):
        self._polarity_inverted = bool(checked)
        self.polarity_btn.setText("Positive ↓" if checked else "Positive ↑")
        self.refresh()

    def _available_channel_names(self) -> list[str]:
        if self._manual_data is not None and self._manual_names:
            return list(self._manual_names)
        if self._raw is not None:
            return list(self._raw.ch_names)
        return []

    def selected_channels(self) -> list[str]:
        """Return explicit display subset; empty means all available channels."""
        return list(self._selected_channel_names)

    def set_selected_channels(self, channels: list[str] | None):
        available = self._available_channel_names()
        requested = list(channels or [])
        self._selected_channel_names = [ch for ch in requested if ch in available]
        self._update_channel_button_text()
        self.refresh()

    def _update_channel_button_text(self):
        available = self._available_channel_names()
        if not available:
            self.channel_select_btn.setText("Select channels…")
        elif self._selected_channel_names:
            self.channel_select_btn.setText(f"Channels… ({len(self._selected_channel_names)}/{len(available)})")
        else:
            self.channel_select_btn.setText(f"Channels… (all {len(available)})")

    def _choose_display_channels(self):
        available = self._available_channel_names()
        if not available:
            return
        initial = self._selected_channel_names or available
        dialog = DisplayChannelDialog(available, initial, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_channels()
        if not selected:
            # An empty display is rarely useful; interpret Clear-all + OK as all
            # rather than leaving the scientific viewer blank.
            self._selected_channel_names = []
        elif len(selected) == len(available):
            self._selected_channel_names = []
        else:
            self._selected_channel_names = selected
            if self.channel_limit.value() < len(selected):
                self.channel_limit.setValue(min(self.channel_limit.maximum(), len(selected)))
        self._update_channel_button_text()
        # Share the effective display selection with the parent workstation so
        # later stages can reuse the same electrodes as a convenience.  An
        # empty internal list means "all", therefore emit the actual available
        # channel list in that case.
        effective = list(self._selected_channel_names) if self._selected_channel_names else list(available)
        self.channelSelectionChanged.emit(effective)
        self.refresh()

    def _display_indices(self, names: list[str]) -> list[int]:
        if self._selected_channel_names:
            wanted = set(self._selected_channel_names)
            indices = [i for i, name in enumerate(names) if name in wanted]
        else:
            indices = list(range(len(names)))
        return indices[: max(1, int(self.channel_limit.value()))]

    def _reset_plot_item_cache(self):
        """Clear the plot and every cached reference to removed graphics items."""
        self.plot.clear()
        self._trace_items = []
        self._overlay_items = []
        self._last_trace_names = ()
        self._selection_region = None

    def set_raw(self, raw):
        if raw is not self._raw:
            # set_raw is a stage/recording change, not horizontal navigation.
            # Rebuild its curves so references removed by an earlier clear can
            # never be mistaken for items that are still attached to the plot.
            self._reset_plot_item_cache()
        self._raw = raw
        self._manual_data = None
        self._manual_is_preview = False
        if raw is not None and raw.n_times:
            self._total_duration = float(raw.n_times) / float(raw.info["sfreq"])
        else:
            self._total_duration = 0.0
        self._start = min(self._start, self._max_start())
        if self._selected_channel_names:
            available = set(raw.ch_names) if raw is not None else set()
            self._selected_channel_names = [ch for ch in self._selected_channel_names if ch in available]
        self._update_channel_button_text()
        self._configure_slider()
        self._update_navigation_ui()
        self.refresh()

    def set_segment(
        self,
        data_volts: np.ndarray,
        names: list[str],
        sfreq: float,
        x0: float = 0.0,
        *,
        as_preview: bool = False,
        preserve_view: bool = False,
    ):
        """Display supplied data.

        ``as_preview=True`` is used for a live-filter preview of the current Raw
        window. Navigation therefore remains attached to the full recording.
        ``False`` is used for standalone segments such as one epoch.
        """
        old_standalone = self._is_standalone_segment()
        old_view_duration = self._manual_view_duration
        old_view_start = self._manual_view_start

        self._manual_data = np.asarray(data_volts)
        self._manual_names = list(names)
        self._manual_sfreq = float(sfreq)
        self._manual_x0 = float(x0)
        self._manual_is_preview = bool(as_preview)
        if self._selected_channel_names:
            available = set(self._manual_names)
            self._selected_channel_names = [ch for ch in self._selected_channel_names if ch in available]
        self._update_channel_button_text()

        if self._manual_data.ndim == 2 and self._manual_sfreq > 0:
            seg_duration = self._manual_data.shape[1] / self._manual_sfreq
        else:
            seg_duration = 0.0

        if as_preview:
            self._start = float(x0)
        else:
            minimum = max(1.0 / max(self._manual_sfreq, 1.0), 0.001)
            if preserve_view and old_standalone and old_view_duration > 0:
                max_view = max(seg_duration, minimum) * self._standalone_max_zoom_out_factor
                self._manual_view_duration = min(max_view, max(minimum, old_view_duration))
                if self._manual_view_duration >= seg_duration:
                    # BESS-like compact review: keep the epoch's left boundary
                    # fixed and let extra display space open only to the right.
                    self._manual_view_start = float(x0)
                else:
                    old_center_offset = (old_view_start + old_view_duration / 2.0) - self._manual_x0
                    center = float(x0) + min(max(old_center_offset, 0.0), seg_duration)
                    max_start = float(x0) + seg_duration - self._manual_view_duration
                    self._manual_view_start = min(max_start, max(float(x0), center - self._manual_view_duration / 2.0))
            else:
                self._manual_view_start = float(x0)
                self._manual_view_duration = max(seg_duration, minimum)
            self.duration.blockSignals(True)
            self.duration.setValue(max(self.duration.minimum(), min(self.duration.maximum(), self._manual_view_duration)))
            self.duration.blockSignals(False)

        self._update_navigation_ui()
        self.refresh()

    def clear_manual_segment(self):
        self._manual_data = None
        self._manual_is_preview = False
        self._update_navigation_ui()
        self.refresh()

    def _max_start(self) -> float:
        return max(0.0, self._total_duration - self._duration)

    def _configure_slider(self):
        max_ms = int(round(self._max_start() * 1000.0))
        self.position.blockSignals(True)
        self.position.setRange(0, max(0, max_ms))
        self.position.setValue(min(max_ms, int(round(self._start * 1000.0))))
        self.position.setPageStep(max(1, int(round(self._duration * 1000.0))))
        self.position.blockSignals(False)

    def _is_standalone_segment(self) -> bool:
        return self._manual_data is not None and not self._manual_is_preview

    def _update_navigation_ui(self):
        if self._is_standalone_segment():
            n_times = self._manual_data.shape[1] if self._manual_data.ndim == 2 else 0
            seg_duration = n_times / self._manual_sfreq if self._manual_sfreq > 0 else 0.0
            start = self._manual_view_start
            end = start + self._manual_view_duration
            self.time_label.setText(
                f"{self._format_relative_time(start)} – {self._format_relative_time(end)}"
            )
            self.total_label.setText(f"Epoch span: {seg_duration:.3f} s")
            self.position_caption.setText("Epoch position")
            self.prev_window.setEnabled(False)
            self.next_window.setEnabled(False)
            self.position.setEnabled(False)
        else:
            self.position_caption.setText("Recording position")
            end = min(self._total_duration, self._start + self._duration)
            self.time_label.setText(f"{self._format_time(self._start)} – {self._format_time(end)}")
            self.total_label.setText(f"Total: {self._format_time(self._total_duration)}")
            self.prev_window.setEnabled(self._start > 0.0005)
            self.next_window.setEnabled(self._start < self._max_start() - 0.0005)
            self.position.setEnabled(self._raw is not None)

    def visible_raw_segment(self):
        if self._raw is None:
            return None
        sfreq = float(self._raw.info["sfreq"])
        a = int(round(self._start * sfreq))
        b = min(self._raw.n_times, a + int(round(self._duration * sfreq)) + 1)
        picks = self._display_indices(list(self._raw.ch_names))
        data = self._raw.get_data(picks=picks, start=a, stop=b)
        names = [self._raw.ch_names[p] for p in picks]
        return data, names, sfreq, self._start

    def _duration_changed(self, value: float):
        value = float(value)
        if self._is_standalone_segment():
            self._set_manual_view_duration(value)
            return

        old_duration = self._duration
        center = self._start + old_duration / 2.0
        self._duration = value
        self._start = min(self._max_start(), max(0.0, center - self._duration / 2.0))
        self._configure_slider()
        self._update_navigation_ui()
        # A live preview will be regenerated by the parent after the duration
        # signal; displaying the unpreviewed Raw briefly is less misleading than
        # stretching stale preview samples.
        if self._manual_is_preview:
            self._manual_data = None
            self._manual_is_preview = False
        self.refresh()

    def _set_manual_view_duration(self, value: float):
        if self._manual_data is None or self._manual_sfreq <= 0:
            return
        seg_duration = self._manual_data.shape[1] / self._manual_sfreq
        min_duration = max(10.0 / self._manual_sfreq, 0.02)
        max_duration = max(seg_duration, min_duration) * self._standalone_max_zoom_out_factor
        new_duration = min(max_duration, max(min_duration, float(value)))
        center = self._manual_view_start + self._manual_view_duration / 2.0
        seg_start = self._manual_x0
        self._manual_view_duration = new_duration
        if new_duration >= seg_duration:
            # Once the complete epoch is visible, further '-' presses keep the
            # epoch start fixed at the left edge and add blank space only to
            # the right. The waveform therefore compacts toward the left, as
            # in the BESS review display.
            self._manual_view_start = seg_start
        else:
            max_start = seg_start + seg_duration - new_duration
            self._manual_view_start = min(max_start, max(seg_start, center - new_duration / 2.0))
        # Keep the visible Time scale control synchronized when zooming via
        # keyboard shortcuts. This is display-only; the supplied epoch array
        # and its original boundaries are untouched.
        self.duration.blockSignals(True)
        self.duration.setValue(new_duration)
        self.duration.blockSignals(False)
        self._update_navigation_ui()
        self.refresh()

    def _slider_changed(self, value: int):
        if self._is_standalone_segment():
            return
        self._start = min(self._max_start(), max(0.0, float(value) / 1000.0))
        self._update_navigation_ui()
        # Filter previews are regenerated asynchronously by the parent. Do not
        # redraw stale preview samples at the new time position.
        if self._manual_is_preview:
            self._manual_data = None
            self._manual_is_preview = False
        self.refresh()

    def _pan_requested(self, delta_seconds: float):
        # Accumulate drag movement and redraw on a short timer. This keeps
        # horizontal dragging fluid for dense 32-channel / 1000-Hz recordings.
        self._pending_pan_delta += float(delta_seconds)
        if not self._pan_timer.isActive():
            self._pan_timer.start()

    def _flush_pending_pan(self):
        delta_seconds = float(self._pending_pan_delta)
        self._pending_pan_delta = 0.0
        if not math.isfinite(delta_seconds) or abs(delta_seconds) <= 0.0:
            return

        if self._is_standalone_segment():
            if self._manual_data is None or self._manual_sfreq <= 0:
                return
            seg_duration = self._manual_data.shape[1] / self._manual_sfreq
            if self._manual_view_duration >= seg_duration:
                # A fully visible/shrunken epoch stays left-anchored.
                self._manual_view_start = self._manual_x0
            else:
                max_start = self._manual_x0 + max(0.0, seg_duration - self._manual_view_duration)
                self._manual_view_start = min(
                    max_start,
                    max(self._manual_x0, self._manual_view_start + delta_seconds),
                )
            self._update_navigation_ui()
            self.refresh()
            return

        target = min(self._max_start(), max(0.0, self._start + delta_seconds))
        self.position.setValue(int(round(target * 1000.0)))

    def step_time(self, direction: int):
        """Move horizontally by one visible page (Left/Right shortcuts)."""
        if self._is_standalone_segment():
            self._pan_requested(float(direction) * self._manual_view_duration)
        else:
            self.step_windows(direction)

    def step_windows(self, direction: int):
        if self._is_standalone_segment():
            return
        target = self._start + float(direction) * self._duration
        target = min(self._max_start(), max(0.0, target))
        self.position.setValue(int(round(target * 1000.0)))

    def zoom_time_in(self):
        """Show less time around the current centre (+ shortcut)."""
        if self._is_standalone_segment():
            self._set_manual_view_duration(self._manual_view_duration / 1.25)
        else:
            self.duration.setValue(max(self.duration.minimum(), self._duration / 1.25))

    def zoom_time_out(self):
        """Show more time around the current centre (- shortcut)."""
        if self._is_standalone_segment():
            self._set_manual_view_duration(self._manual_view_duration * 1.25)
        else:
            self.duration.setValue(min(self.duration.maximum(), self._duration * 1.25))

    def increase_sensitivity(self):
        """Make voltage deflections larger on screen (* shortcut)."""
        self.scale.setValue(max(self.scale.minimum(), self.scale.value() / 1.25))

    def decrease_sensitivity(self):
        """Make voltage deflections smaller on screen (/ shortcut)."""
        self.scale.setValue(min(self.scale.maximum(), self.scale.value() * 1.25))


    def visible_time_range(self) -> tuple[float, float]:
        """Return the currently visible recording/segment time range in seconds."""
        if self._is_standalone_segment():
            return (float(self._manual_view_start), float(self._manual_view_start + self._manual_view_duration))
        return (float(self._start), float(min(self._total_duration, self._start + self._duration)))

    def set_overlay_spans(self, spans):
        """Set display-only shaded time spans as ``(start, end, label)`` tuples."""
        cleaned = []
        for span in spans or []:
            try:
                if isinstance(span, dict):
                    start = float(span.get("start_sec", 0.0)); end = float(span.get("end_sec", start)); label = str(span.get("reason", "excluded"))
                else:
                    start, end = float(span[0]), float(span[1]); label = str(span[2]) if len(span) > 2 else "excluded"
            except Exception:
                continue
            if end > start:
                cleaned.append((start, end, label))
        self._overlay_spans = cleaned
        self.refresh()

    def _clear_overlay_items(self):
        for item in list(getattr(self, "_overlay_items", [])):
            try: self.plot.removeItem(item)
            except Exception: pass
        self._overlay_items = []

    def _remember_overlay_item(self, item):
        if item is not None:
            self._overlay_items.append(item)
        return item

    def _draw_overlay_spans(self, x_left: float, x_right: float, n_ch: int):
        if not self._overlay_spans or self._is_standalone_segment():
            return
        brush = pg.mkBrush(210, 80, 80, 48 if self._dark_mode else 34)
        pen = pg.mkPen(220, 105, 105, 150 if self._dark_mode else 120, width=1)
        text_color = "#ffb2b2" if self._dark_mode else "#8d2e2e"
        for start, end, label in self._overlay_spans:
            if end < x_left or start > x_right:
                continue
            a, b = max(x_left, start), min(x_right, end)
            region = pg.LinearRegionItem(values=[a, b], movable=False, brush=brush, pen=pen)
            region.setZValue(12)
            region.setToolTip(f"ICA fit exclusion: {label}\n{start:.3f}–{end:.3f} s")
            self.plot.addItem(region); self._remember_overlay_item(region)
            if b - a > 0.08 * max(x_right - x_left, 1e-9):
                tag = pg.TextItem(text=f"ICA EXCLUDE · {label}", color=text_color, anchor=(0.5, 0.0))
                tag.setPos((a + b) / 2.0, max(float(n_ch) - 0.45, 0.5))
                tag.setZValue(25)
                self.plot.addItem(tag); self._remember_overlay_item(tag)

    def _draw_voltage_calibration(self, x_left: float, x_right: float, n_ch: int, scale_uv: float):
        """Draw a one-row vertical bar labelled with its µV equivalent."""
        x_span = max(float(x_right - x_left), 1e-9)
        x = x_right - 0.055 * x_span
        cap = 0.008 * x_span
        y0, y1 = -1.55, -0.55  # exactly one channel-row spacing
        pen = pg.mkPen(self._theme_fg(), width=2)
        self._remember_overlay_item(self.plot.plot([x, x], [y0, y1], pen=pen))
        self._remember_overlay_item(self.plot.plot([x - cap, x + cap], [y0, y0], pen=pen))
        self._remember_overlay_item(self.plot.plot([x - cap, x + cap], [y1, y1], pen=pen))
        label = pg.TextItem(text=f"{scale_uv:g} µV", color=self._theme_fg(), anchor=(0.0, 0.5))
        label.setPos(x + 1.8 * cap, (y0 + y1) / 2.0)
        self.plot.addItem(label); self._remember_overlay_item(label)

    def _draw_annotations(self, x_left: float, x_right: float, n_ch: int):
        """Overlay event markers while keeping annotation labels readable.

        Display-only de-cluttering is intentionally separate from the actual
        MNE annotation timeline used for epoching.  All events remain attached
        to ``Raw``; this routine only decides how many labels can be drawn
        without covering one another.

        Rules
        -----
        * Exact duplicate annotations at the same onset are drawn once.
        * Different annotations at effectively the same onset share one line;
          the first name is shown and ``(+N)`` indicates additional names.
        * Labels are greedily assigned to several vertical lanes using a simple
          screen-space collision test.  If no lane is free, the marker line is
          still drawn but its label is hidden until the user zooms in.
        * Every marker line has a tooltip containing the complete event name(s),
          including labels that were hidden for de-cluttering.
        """
        if self._raw is None or self._is_standalone_segment() or not self.show_annotations.isChecked():
            return
        annotations = self._raw.annotations
        if annotations is None or len(annotations) == 0:
            return

        onsets = np.asarray(annotations.onset, dtype=float)
        durations = np.asarray(annotations.duration, dtype=float)
        descriptions = np.asarray(annotations.description, dtype=object)
        ends = onsets + np.maximum(durations, 0.0)
        visible = (onsets <= x_right) & (ends >= x_left)
        idx = np.flatnonzero(visible)
        if idx.size == 0:
            return

        marker_color = (255, 145, 145, 210) if self._dark_mode else (180, 55, 55, 175)
        marker_text = (255, 205, 205) if self._dark_mode else (112, 30, 30)
        marker_fill = (35, 38, 43, 235) if self._dark_mode else (255, 255, 255, 225)
        marker_border = (125, 78, 78, 220) if self._dark_mode else (210, 170, 170, 200)
        marker_pen = pg.mkPen(marker_color, width=1)

        # Group annotations that occupy the same time sample.  This prevents an
        # attached TXT event and an equivalent embedded event from producing two
        # labels on top of each other, while leaving the underlying Raw
        # annotations untouched for later event/epoch logic.
        try:
            sfreq = float(self._raw.info["sfreq"])
        except Exception:
            sfreq = 1000.0
        onset_tolerance = max(0.0005, 0.5 / max(sfreq, 1.0))
        grouped: list[dict] = []
        for j in idx:
            onset = float(onsets[j])
            desc = str(descriptions[j]).replace("\n", " ").strip() or "(unnamed event)"
            if grouped and abs(onset - grouped[-1]["onset"]) <= onset_tolerance:
                if desc not in grouped[-1]["descriptions"]:
                    grouped[-1]["descriptions"].append(desc)
                continue
            grouped.append({"onset": onset, "descriptions": [desc]})

        # Screen-space geometry for collision detection.  InfLineLabel text is
        # rotated vertically, so its long dimension is approximately the text
        # length.  We estimate its rectangle conservatively; false positives
        # simply hide a label and are preferable to overlapping text.
        scene_rect = self._view_box.sceneBoundingRect()
        width_px = max(float(scene_rect.width()), 1.0)
        height_px = max(float(scene_rect.height()), 1.0)
        x_span = max(float(x_right - x_left), 1e-9)
        label_positions = (0.94, 0.78, 0.62, 0.46, 0.30, 0.16)
        occupied: list[tuple[float, float, float, float]] = []
        hidden_labels = 0

        # If the plot is extremely dense, keep the event lines but let zooming
        # reveal labels progressively.  Collision handling below remains active
        # at every density.
        labels_enabled = self.show_annotation_labels.isChecked() and len(grouped) <= 120

        def _overlaps(rect, other, pad_x=4.0, pad_y=5.0):
            x0, y0, x1, y1 = rect
            a0, b0, a1, b1 = other
            return not (
                x1 + pad_x < a0 or a1 + pad_x < x0 or
                y1 + pad_y < b0 or b1 + pad_y < y0
            )

        for event in grouped:
            onset = float(event["onset"])
            descs = event["descriptions"]
            first = descs[0]
            shown = first if len(first) <= 34 else first[:31] + "…"
            if len(descs) > 1:
                shown = f"{shown} (+{len(descs) - 1})"
            tooltip = "\n".join(descs)

            chosen_position = None
            candidate_rect = None
            if labels_enabled:
                x_px = ((onset - x_left) / x_span) * width_px
                # Approximate a rotated label.  Height grows with character
                # count; width stays relatively narrow.
                box_w = 22.0
                box_h = min(265.0, max(48.0, 13.0 + 6.4 * len(shown)))
                for pos in label_positions:
                    y_px = (1.0 - pos) * height_px
                    rect = (
                        x_px - box_w / 2.0,
                        y_px - box_h / 2.0,
                        x_px + box_w / 2.0,
                        y_px + box_h / 2.0,
                    )
                    # Keep the whole estimated label inside the plotting area.
                    if rect[1] < 2.0 or rect[3] > height_px - 2.0:
                        continue
                    if any(_overlaps(rect, used) for used in occupied):
                        continue
                    chosen_position = pos
                    candidate_rect = rect
                    break

            if chosen_position is not None:
                line = pg.InfiniteLine(
                    pos=onset,
                    angle=90,
                    movable=False,
                    pen=marker_pen,
                    label=shown,
                    labelOpts={
                        "position": chosen_position,
                        "rotateAxis": (1, 0),
                        "color": marker_text,
                        "fill": marker_fill,
                        "border": marker_border,
                    },
                )
                if candidate_rect is not None:
                    occupied.append(candidate_rect)
                if getattr(line, "label", None) is not None:
                    line.label.setToolTip(tooltip)
                    line.label.setZValue(30)
            else:
                line = pg.InfiniteLine(pos=onset, angle=90, movable=False, pen=marker_pen)
                if self.show_annotation_labels.isChecked():
                    hidden_labels += 1

            line.setToolTip(tooltip)
            line.setZValue(20)
            self.plot.addItem(line); self._remember_overlay_item(line)

        # A small unobtrusive note explains why some names are absent.  It is
        # shown only when labels are requested and some had to be hidden.
        if self.show_annotation_labels.isChecked() and hidden_labels:
            note = pg.TextItem(
                text=f"{hidden_labels} overlapping event label(s) hidden — zoom in (+) to reveal",
                color=marker_text,
                anchor=(0.0, 1.0),
                fill=marker_fill,
                border=marker_border,
            )
            note.setPos(
                x_left + 0.01 * x_span,
                max(float(n_ch) - 0.25, 0.5),
            )
            note.setZValue(40)
            self.plot.addItem(note); self._remember_overlay_item(note)

    def refresh(self):
        standalone = self._is_standalone_segment()
        if self._manual_data is not None:
            data = self._manual_data
            names = self._manual_names
            sfreq = self._manual_sfreq
            x0 = self._manual_x0
        elif self._raw is not None:
            result = self.visible_raw_segment()
            if result is None:
                self._reset_plot_item_cache()
                return
            data, names, sfreq, x0 = result
        else:
            self._reset_plot_item_cache()
            return

        if self._manual_data is not None:
            indices = self._display_indices(list(names))
            data = data[indices] if len(indices) else data[:0]
            names = [names[i] for i in indices]
        # Raw segments were already picked in visible_raw_segment().
        n_ch, n_times = data.shape if data.ndim == 2 else (0, 0)
        if n_ch == 0 or n_times == 0:
            self._reset_plot_item_cache()
            return

        t = x0 + np.arange(n_times, dtype=float) / sfreq
        scale_uv = max(self.scale.value(), 1e-9)
        offsets = np.arange(n_ch - 1, -1, -1, dtype=float)
        trace_names=tuple(names)
        if len(self._trace_items)!=n_ch or self._last_trace_names!=trace_names:
            self._reset_plot_item_cache()
            for _ in range(n_ch):
                item=self.plot.plot([],[],pen=pg.mkPen(self._theme_fg(),width=1))
                item.setDownsampling(auto=True,method="peak"); item.setClipToView(True)
                self._trace_items.append(item)
            self._last_trace_names=trace_names
        else:
            self._clear_overlay_items()
        for i,item in enumerate(self._trace_items):
            polarity = -1.0 if self._polarity_inverted else 1.0
            y = polarity * data[i] * 1e6 / scale_uv + offsets[i]
            if standalone and self._rejected_visual:
                trace_color = "#8d949d" if self._dark_mode else "#777777"
            else:
                trace_color = self._trace_color_override or ("#9ad1ff" if self._dark_mode else "#185f9d")
            item.setPen(pg.mkPen(trace_color,width=1)); item.setData(t,y)

        ticks = [(float(offsets[i]), names[i]) for i in range(n_ch)]
        self.plot.getAxis("left").setTicks([ticks])
        # Reserve the area below the bottom channel for a true voltage scale bar.
        self.plot.setYRange(-2.0, max(float(n_ch), 1.0), padding=0.01)

        if standalone:
            x_left = self._manual_view_start
            x_right = x_left + self._manual_view_duration
        elif self._manual_is_preview:
            x_left = self._start
            x_right = min(self._total_duration, self._start + self._duration)
        else:
            x_left = float(t[0])
            x_right = float(t[-1])

        if x_right <= x_left:
            x_right = x_left + 1.0 / max(sfreq, 1.0)
        self.plot.setXRange(x_left, x_right, padding=0.0)
        self._draw_annotations(x_left, x_right, n_ch)
        self._draw_overlay_spans(x_left, x_right, n_ch)
        if standalone and self._rejected_visual:
            shade = pg.LinearRegionItem(
                values=[x_left, x_right],
                orientation="vertical",
                movable=False,
                brush=pg.mkBrush(110, 110, 110, 55 if self._dark_mode else 42),
                pen=pg.mkPen(None),
            )
            shade.setZValue(60)
            self.plot.addItem(shade); self._remember_overlay_item(shade)
            tag = pg.TextItem(
                text=self._rejected_visual_label,
                color="#d5d8dc" if self._dark_mode else "#555555",
                anchor=(0.5, 0.0),
                fill=(45, 48, 53, 215) if self._dark_mode else (235, 235, 235, 225),
                border=(135, 135, 135, 180),
            )
            tag.setPos((x_left + x_right) / 2.0, max(float(n_ch) - 0.45, 0.5))
            tag.setZValue(70)
            self.plot.addItem(tag); self._remember_overlay_item(tag)
        self._draw_voltage_calibration(x_left, x_right, n_ch, scale_uv)
        self._update_navigation_ui()
