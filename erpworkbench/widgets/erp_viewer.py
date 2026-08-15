from __future__ import annotations

import math
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class NoWheelViewBox(pg.ViewBox):
    """Allow normal drag-pan, but ignore accidental mouse-wheel scaling."""

    def wheelEvent(self, ev, axis=None):  # noqa: N802
        ev.ignore()


class ERPViewer(QWidget):
    """Interactive ERP viewer supporting butterfly and stacked channel views.

    Display operations never alter the underlying MNE Evoked data.  Once the
    whole ERP is visible, further ``-`` presses enlarge the display time range
    to as much as 4x the ERP span while keeping the true epoch start fixed at
    the left.  Thus the ERP can occupy only 25% of the plot width.

    Butterfly view overlays the selected electrodes and uses a fixed legend
    strip *outside* the plotting canvas.  Stacked view arranges the same
    selected electrodes vertically, like the continuous/epoch-review tabs.
    """

    pointClicked = Signal(float, str)  # latency in ms, clicked channel (stacked)
    markerContextRequested = Signal(object)  # marker definition dict
    previousRequested = Signal()
    nextRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark_mode = False
        self._opengl_enabled = False
        self._polarity_inverted = False
        self._display_mode = "butterfly"
        self._stack_scale_uv_per_row = 5.0
        self._stack_scale_user_set = False
        self._stacked_color_override: str | None = None
        self._shortcut_map = {
            "previous": "Left", "next": "Right", "time_in": "+", "time_out": "-",
            "sensitivity_up": "*", "sensitivity_down": "/",
        }

        self._view_box = NoWheelViewBox()
        self.plot = pg.PlotWidget(viewBox=self._view_box)
        self.plot.setBackground("#ffffff")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Latency", units="ms")
        self.plot.setLabel("left", "Amplitude", units="µV")
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.plot.scene().sigMouseClicked.connect(self._mouse_clicked)
        self.plot.setAntialiasing(False)
        self._view_box.sigRangeChanged.connect(self._range_changed)

        self._curves: dict[str, pg.PlotDataItem] = {}
        self._series_uv: dict[str, np.ndarray] = {}
        self._zero_lines = []
        self._window_region = None
        self._window_label = None
        self._marker_items = []
        self._marker_defs: list[dict] = []
        self._times_ms = np.array([], dtype=float)
        self._full_x: tuple[float, float] | None = None
        self._title = ""
        self._cal_items = []
        self._updating_calibration = False
        self._placeholder = None

        # Fixed non-obstructing channel legend.  It deliberately lives outside
        # the PlotWidget, so it cannot be dragged over or cover ERP peaks.
        self.legend_scroll = QScrollArea()
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.legend_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.legend_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.legend_scroll.setFixedHeight(36)
        self.legend_content = QWidget()
        self.legend_layout = QHBoxLayout(self.legend_content)
        self.legend_layout.setContentsMargins(4, 1, 4, 1)
        self.legend_layout.setSpacing(12)
        self.legend_layout.addStretch(1)
        self.legend_scroll.setWidget(self.legend_content)
        self.legend_scroll.hide()

        self.polarity_btn = QPushButton("Positive ↑")
        self.polarity_btn.setCheckable(True)
        self.polarity_btn.setToolTip(
            "Reverse display polarity only. Positive-down is common in some ERP laboratories; "
            "stored amplitudes and exported measurements remain unchanged."
        )
        self.polarity_btn.toggled.connect(self._polarity_toggled)
        self.help_label = QLabel(
            "ERP view: ←/→ condition   |   drag to pan   |   +/− change time scale   |   "
            "*/ change vertical sensitivity. Beyond full-span, − shrinks the waveform to 25% width."
        )
        self.help_label.setProperty("muted", True)
        self.help_label.setWordWrap(True)
        lower = QHBoxLayout()
        lower.addWidget(self.polarity_btn)
        lower.addWidget(self.help_label, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.legend_scroll)
        layout.addWidget(self.plot, 1)
        layout.addLayout(lower)

        self._install_shortcuts()
        self.clear_view("Average accepted epochs or open an averaged-subject file to begin.")

    # ------------------------------------------------------------------
    # public display controls
    def set_display_mode(self, mode: str):
        mode = str(mode or "butterfly").strip().lower()
        if mode not in {"butterfly", "stacked"}:
            mode = "butterfly"
        if mode == self._display_mode:
            return
        old_x = tuple(self._view_box.viewRange()[0]) if self._times_ms.size else None
        # Do not carry a stacked row-index Y-range into butterfly µV space (or
        # vice versa). That was the cause of the distorted butterfly view after
        # switching modes. Re-render the new mode, then preserve only time range.
        self._display_mode = mode
        if self._times_ms.size and self._series_uv:
            window = None
            window_label = ""
            if self._window_region is not None:
                try:
                    window = tuple(self._window_region.getRegion())
                except Exception:
                    pass
            if self._window_label is not None:
                try:
                    window_label = self._window_label.toPlainText()
                except Exception:
                    pass
            defs = list(self._marker_defs)
            self._render_series(preserve_range=True, old_x=old_x, old_y=None)
            self._marker_defs = []
            if window:
                self.set_window(float(window[0]), float(window[1]), window_label)
            for d in defs:
                self.add_marker(**d)

    def display_mode(self) -> str:
        return self._display_mode

    def has_data(self) -> bool:
        """Return True when an ERP waveform is currently loaded in the viewer."""
        return bool(self._times_ms.size and self._series_uv)

    def get_display_state(self) -> dict:
        """Return display-only state suitable for per-condition restoration."""
        state = {
            "x_range": tuple(map(float, self._view_box.viewRange()[0])),
            "polarity_inverted": bool(self._polarity_inverted),
            "stack_scale_uv_per_row": float(self._stack_scale_uv_per_row),
            "stack_scale_user_set": bool(self._stack_scale_user_set),
        }
        if self._display_mode == "butterfly":
            state["butterfly_y_range"] = tuple(map(float, self._view_box.viewRange()[1]))
        return state

    def apply_display_state(self, state: dict | None):
        """Restore time scale and amplitude sensitivity without changing data."""
        if not state or not (self._times_ms.size and self._series_uv):
            return

        self._polarity_inverted = bool(state.get("polarity_inverted", self._polarity_inverted))
        old = self.polarity_btn.blockSignals(True)
        self.polarity_btn.setChecked(self._polarity_inverted)
        self.polarity_btn.setText("Positive ↓" if self._polarity_inverted else "Positive ↑")
        self.polarity_btn.blockSignals(old)

        if "stack_scale_uv_per_row" in state:
            try:
                self._stack_scale_uv_per_row = max(0.05, min(10000.0, float(state["stack_scale_uv_per_row"])))
                self._stack_scale_user_set = bool(state.get("stack_scale_user_set", True))
            except Exception:
                pass

        # Re-render once using restored polarity / stacked sensitivity. Windows and
        # markers are re-applied by the main window immediately after this call.
        self._render_series(preserve_range=False)

        if self._full_x is not None and state.get("x_range"):
            try:
                x0, x1 = map(float, state["x_range"])
                full_lo, full_hi = self._full_x
                max_hi = full_lo + 4.0 * max(full_hi - full_lo, 1e-9)
                x0 = max(full_lo, min(x0, max_hi))
                x1 = max(x0 + 1e-9, min(x1, max_hi))
                self.plot.setXRange(x0, x1, padding=0.0)
            except Exception:
                pass

        if self._display_mode == "butterfly" and state.get("butterfly_y_range"):
            try:
                y0, y1 = map(float, state["butterfly_y_range"])
                if y1 > y0:
                    self.plot.setYRange(y0, y1, padding=0.0)
            except Exception:
                pass
        self._update_calibration()

    def set_opengl_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self.plot.useOpenGL(enabled)
        self.plot.setAntialiasing(False)
        self._opengl_enabled = enabled
        self.plot.viewport().update()

    def set_dark_mode(self, dark: bool):
        self._dark_mode = bool(dark)
        bg = "#17191d" if self._dark_mode else "#ffffff"
        fg = self._theme_fg()
        self.plot.setBackground(bg)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        if self._times_ms.size and self._series_uv:
            self._rebuild_preserving_annotations(preserve_range=True)
        else:
            self._update_placeholder_color()
            self._update_calibration()

    def _theme_fg(self):
        return "#e8ebef" if self._dark_mode else "#20242a"

    def _curve_color(self, i: int, total: int):
        return pg.intColor(i, hues=max(total, 6), values=1, maxValue=255)

    def _theme_curve_pen(self, i: int, total: int):
        return pg.mkPen(self._curve_color(i, total), width=2)

    def _stacked_curve_pen(self):
        # Channel identity is already encoded by vertical position and left-axis
        # labels in stacked mode, so rainbow colors add clutter rather than useful
        # information. Keep a single high-contrast EEG-style trace color.
        color = self._stacked_color_override or ("#7fb6d9" if self._dark_mode else "#2e6f9e")
        return pg.mkPen(color, width=1.6)

    def _install_shortcuts(self):
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        callbacks = {
            "time_in": lambda: self._zoom_axis("x", 1 / 1.25),
            "time_out": lambda: self._zoom_axis("x", 1.25),
            "sensitivity_up": lambda: self._change_vertical_sensitivity(1 / 1.25),
            "sensitivity_down": lambda: self._change_vertical_sensitivity(1.25),
            "previous": self.previousRequested.emit,
            "next": self.nextRequested.emit,
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
        if hasattr(self, "help_label"):
            m = self._shortcut_map
            self.help_label.setText(
                f"ERP view: {m.get('previous','')}/{m.get('next','')} condition   |   drag to pan   |   "
                f"{m.get('time_in','')}/{m.get('time_out','')} change time scale   |   "
                f"{m.get('sensitivity_up','')}/{m.get('sensitivity_down','')} change vertical sensitivity. "
                "Beyond full-span, time zoom-out shrinks the waveform to 25% width."
            )

    def set_shortcut_map(self, mapping: dict):
        self._shortcut_map.update({k: str(v) for k, v in dict(mapping or {}).items() if k in self._shortcut_map})
        self._install_shortcuts()

    def zoom_time_in(self):
        self._zoom_axis("x", 1 / 1.25)

    def zoom_time_out(self):
        self._zoom_axis("x", 1.25)

    def increase_sensitivity(self):
        self._change_vertical_sensitivity(1 / 1.25)

    def decrease_sensitivity(self):
        self._change_vertical_sensitivity(1.25)

    def set_trace_color(self, color: str | None):
        """Set stacked-mode trace color only; butterfly colors remain unchanged."""
        self._stacked_color_override = str(color) if color else None
        if self._times_ms.size and self._series_uv and self._display_mode == "stacked":
            self._rebuild_preserving_annotations(preserve_range=True)

    def _display_data(self, data_uv: np.ndarray) -> np.ndarray:
        data_uv = np.asarray(data_uv, dtype=float)
        return -data_uv if self._polarity_inverted else data_uv

    def _polarity_toggled(self, checked: bool):
        self._polarity_inverted = bool(checked)
        self.polarity_btn.setText("Positive ↓" if checked else "Positive ↑")
        if self._times_ms.size and self._series_uv:
            self._rebuild_preserving_annotations(preserve_range=True)

    # ------------------------------------------------------------------
    # legend and rendering
    def _clear_fixed_legend(self):
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.legend_layout.addStretch(1)

    def _update_fixed_legend(self):
        self._clear_fixed_legend()
        names = list(self._series_uv)
        if self._display_mode != "butterfly" or len(names) <= 1:
            self.legend_scroll.hide()
            return
        # Remove the trailing stretch, add entries, then restore stretch.
        stretch = self.legend_layout.takeAt(self.legend_layout.count() - 1)
        del stretch
        for i, name in enumerate(names):
            color = self._curve_color(i, len(names)).name()
            label = QLabel(f"<span style='color:{color}; font-size:15px'>■</span>&nbsp;{name}")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setToolTip(name)
            self.legend_layout.addWidget(label)
        self.legend_layout.addStretch(1)
        self.legend_scroll.show()

    def _auto_stack_scale(self):
        vals = []
        for data in self._series_uv.values():
            arr = np.asarray(data, dtype=float)
            finite = np.abs(arr[np.isfinite(arr)])
            if finite.size:
                vals.append(float(np.nanpercentile(finite, 99)))
        target = max(vals, default=2.0) * 1.8
        target = max(target, 1.0)
        return self._nice_voltage_bar(target)

    def _stack_baseline(self, channel: str) -> float:
        names = list(self._series_uv)
        try:
            i = names.index(channel)
        except ValueError:
            i = 0
        return float(len(names) - 1 - i)

    def _display_y_for_marker(self, amplitude_uv: float, channel: str = "") -> float:
        display_amp = -float(amplitude_uv) if self._polarity_inverted else float(amplitude_uv)
        if self._display_mode == "stacked" and self._series_uv:
            chosen = channel if channel in self._series_uv else next(iter(self._series_uv))
            return self._stack_baseline(chosen) + display_amp / max(self._stack_scale_uv_per_row, 1e-9)
        return display_amp

    def _configure_left_axis(self):
        axis = self.plot.getAxis("left")
        if self._display_mode == "stacked" and self._series_uv:
            names = list(self._series_uv)
            ticks = [(self._stack_baseline(name), name) for name in names]
            axis.setTicks([ticks])
            axis.setLabel("Channels")
            axis.setStyle(tickTextWidth=80)
        else:
            axis.setTicks(None)
            axis.setLabel("Amplitude", units="µV")

    def _render_series(self, *, preserve_range: bool = False, old_x=None, old_y=None):
        self.plot.clear()
        self._placeholder = None
        self._cal_items = []
        self._marker_items = []
        self._window_region = None
        self._window_label = None
        self._curves = {}
        self._zero_lines = []

        fg = self._theme_fg()
        zero_pen = pg.mkPen(fg, width=1, style=Qt.PenStyle.DashLine)
        self._zero_lines.append(self.plot.addLine(x=0, pen=zero_pen))

        names = list(self._series_uv)
        if self._display_mode == "stacked" and names:
            if not self._stack_scale_user_set:
                self._stack_scale_uv_per_row = self._auto_stack_scale()
            for i, name in enumerate(names):
                baseline = self._stack_baseline(name)
                values = baseline + self._display_data(self._series_uv[name]) / max(self._stack_scale_uv_per_row, 1e-9)
                curve = self.plot.plot(self._times_ms, values, pen=self._stacked_curve_pen())
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
                self._curves[name] = curve
            self.plot.showGrid(x=True, y=False, alpha=0.18)
            self.plot.setYRange(-0.9, max(len(names) - 0.1, 1.1), padding=0.0)
        else:
            self._zero_lines.append(self.plot.addLine(y=0, pen=zero_pen))
            for i, name in enumerate(names):
                values = self._display_data(self._series_uv[name])
                curve = self.plot.plot(self._times_ms, values, pen=self._theme_curve_pen(i, len(names)))
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
                self._curves[name] = curve
            self.plot.showGrid(x=True, y=True, alpha=0.2)
            self._fit_y_to_data()

        self._configure_left_axis()
        self._update_fixed_legend()
        self.plot.setTitle(self._title)

        if self._times_ms.size:
            xmin, xmax = float(np.nanmin(self._times_ms)), float(np.nanmax(self._times_ms))
            self._full_x = (xmin, xmax)
            full_span = max(xmax - xmin, 1e-9)
            self.plot.setLimits(xMin=xmin, xMax=xmin + 4.0 * full_span)
            self.plot.setXRange(xmin, xmax, padding=0.0)
        else:
            self._full_x = None

        if preserve_range and old_x is not None and self._full_x is not None:
            full_lo, full_hi = self._full_x
            max_hi = full_lo + 4.0 * (full_hi - full_lo)
            x0 = max(full_lo, min(float(old_x[0]), max_hi))
            x1 = max(x0 + 1e-9, min(float(old_x[1]), max_hi))
            self.plot.setXRange(x0, x1, padding=0.0)
            if self._display_mode == "butterfly" and old_y is not None:
                self.plot.setYRange(float(old_y[0]), float(old_y[1]), padding=0.0)
        self._update_calibration()

    def _rebuild_preserving_annotations(self, preserve_range: bool = True):
        if not (self._times_ms.size and self._series_uv):
            return
        old_x = tuple(self._view_box.viewRange()[0]) if preserve_range else None
        old_y = tuple(self._view_box.viewRange()[1]) if preserve_range else None
        window = None
        window_label = ""
        if self._window_region is not None:
            try:
                window = tuple(self._window_region.getRegion())
            except Exception:
                window = None
        if self._window_label is not None:
            try:
                window_label = self._window_label.toPlainText()
            except Exception:
                window_label = ""
        defs = list(self._marker_defs)
        self._render_series(preserve_range=preserve_range, old_x=old_x, old_y=old_y)
        self._marker_defs = []
        if window:
            self.set_window(float(window[0]), float(window[1]), window_label)
        for d in defs:
            self.add_marker(**d)

    # ------------------------------------------------------------------
    # data API
    def clear_view(self, message: str = ""):
        self.plot.clear()
        self._clear_fixed_legend()
        self.legend_scroll.hide()
        self._curves = {}
        self._series_uv = {}
        self._times_ms = np.array([], dtype=float)
        self._full_x = None
        self._title = ""
        self._zero_lines = []
        self._window_region = None
        self._window_label = None
        self._marker_items = []
        self._marker_defs = []
        self._cal_items = []
        self._configure_left_axis()
        self.plot.setTitle("ERP average")
        self.plot.setXRange(-200, 800, padding=0.0)
        self.plot.setYRange(-5, 5, padding=0.0)
        if message:
            self._placeholder = pg.TextItem(text=message, color=self._theme_fg(), anchor=(0.5, 0.5))
            self._placeholder.setPos(300, 0)
            self.plot.addItem(self._placeholder)
        else:
            self._placeholder = None

    def _update_placeholder_color(self):
        if self._placeholder is not None:
            try:
                self._placeholder.setColor(self._theme_fg())
            except Exception:
                pass

    def set_evoked(self, times_ms: np.ndarray, data_uv: np.ndarray, title: str = ""):
        self.set_evoked_multi(times_ms, {"ERP": np.asarray(data_uv, dtype=float)}, title)

    def set_evoked_multi(
        self,
        times_ms: np.ndarray,
        series_uv: dict[str, np.ndarray],
        title: str = "",
        *,
        preserve_range: bool = False,
    ):
        old_x = tuple(self._view_box.viewRange()[0]) if preserve_range else None
        old_y = tuple(self._view_box.viewRange()[1]) if preserve_range else None
        self._title = str(title)
        self._series_uv = {str(k): np.asarray(v, dtype=float) for k, v in series_uv.items()}
        self._times_ms = np.asarray(times_ms, dtype=float)
        self._marker_defs = []
        self._stack_scale_user_set = False
        self._render_series(preserve_range=preserve_range, old_x=old_x, old_y=old_y)

    def _fit_y_to_data(self):
        if not self._series_uv or self._display_mode == "stacked":
            return
        vals = []
        for data in self._series_uv.values():
            disp = self._display_data(data)
            if disp.size and np.isfinite(disp).any():
                vals.append(disp[np.isfinite(disp)])
        if not vals:
            return
        joined = np.concatenate(vals)
        ymin, ymax = float(np.nanmin(joined)), float(np.nanmax(joined))
        pad = max((ymax - ymin) * 0.15, 1.0)
        self.plot.setYRange(ymin - pad, ymax + pad, padding=0.0)

    def set_window(self, start_ms: float, end_ms: float, label: str = ""):
        if self._window_region is not None:
            try:
                self.plot.removeItem(self._window_region)
            except Exception:
                pass
        if self._window_label is not None:
            try:
                self.plot.removeItem(self._window_label)
            except Exception:
                pass
        self._window_region = pg.LinearRegionItem(values=[start_ms, end_ms], movable=False, brush=(120, 120, 120, 35))
        self._window_region.setZValue(-10)
        self.plot.addItem(self._window_region)
        self._window_label = None
        if label:
            yr = self._view_box.viewRange()[1]
            y = float(yr[1]) - 0.04 * (float(yr[1]) - float(yr[0]))
            self._window_label = pg.TextItem(text=label, color=self._theme_fg(), anchor=(0.5, 0.0))
            self._window_label.setPos((start_ms + end_ms) / 2.0, y)
            self.plot.addItem(self._window_label)

    def clear_markers(self, *, clear_definitions: bool = True):
        for item in self._marker_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self._marker_items = []
        if clear_definitions:
            self._marker_defs = []

    def add_marker(
        self,
        latency_ms: float,
        amplitude_uv: float | None = None,
        label: str = "",
        channel: str = "",
        measurement_key=None,
    ):
        definition = {
            "latency_ms": float(latency_ms),
            "amplitude_uv": None if amplitude_uv is None else float(amplitude_uv),
            "label": str(label),
            "channel": str(channel),
            "measurement_key": measurement_key,
        }
        self._marker_defs.append(definition)
        if amplitude_uv is None:
            item = pg.InfiniteLine(pos=float(latency_ms), angle=90, movable=False, pen=pg.mkPen("#d9b44a", width=2))
            self.plot.addItem(item)
            self._marker_items.append(item)
            return

        display_y = self._display_y_for_marker(float(amplitude_uv), channel)
        point = pg.ScatterPlotItem([float(latency_ms)], [display_y], size=10, symbol="o")
        self.plot.addItem(point)
        self._marker_items.append(point)
        if label:
            text = label if not channel else f"{label} · {channel}"
            txt = pg.TextItem(text=text, color=self._theme_fg(), anchor=(0.5, 1.15))
            txt.setPos(float(latency_ms), display_y)
            self.plot.addItem(txt)
            self._marker_items.append(txt)

    def set_marker(self, latency_ms: float, amplitude_uv: float | None = None, label: str = ""):
        self.clear_markers()
        self.add_marker(latency_ms, amplitude_uv, label)

    # ------------------------------------------------------------------
    # scaling
    def _change_vertical_sensitivity(self, factor: float):
        if self._display_mode == "stacked" and self._series_uv:
            self._stack_scale_uv_per_row = max(0.05, min(10000.0, self._stack_scale_uv_per_row * float(factor)))
            self._stack_scale_user_set = True
            self._rebuild_preserving_annotations(preserve_range=True)
        else:
            self._zoom_axis("y", factor)

    def _zoom_axis(self, axis: str, factor: float):
        ranges = self._view_box.viewRange()
        idx = 0 if axis == "x" else 1
        lo, hi = map(float, ranges[idx])
        center = (lo + hi) / 2.0
        current_span = max(hi - lo, 1e-9)
        span = max(current_span * factor, 1e-9)

        if axis == "x" and self._full_x is not None:
            full_lo, full_hi = self._full_x
            full_span = max(full_hi - full_lo, 1e-9)
            max_span = 4.0 * full_span
            span = min(span, max_span)
            if factor >= 1.0:
                if span >= full_span or current_span >= full_span * 0.999:
                    new_lo = full_lo
                    new_hi = full_lo + span
                else:
                    new_lo = center - span / 2.0
                    new_hi = center + span / 2.0
                    if new_lo < full_lo:
                        new_hi += full_lo - new_lo
                        new_lo = full_lo
                    if new_hi > full_hi:
                        new_lo -= new_hi - full_hi
                        new_hi = full_hi
            else:
                if current_span > full_span * 1.001:
                    span = max(span, full_span)
                    new_lo = full_lo
                    new_hi = full_lo + span
                else:
                    span = min(span, full_span)
                    new_lo = center - span / 2.0
                    new_hi = center + span / 2.0
                    if new_lo < full_lo:
                        new_hi += full_lo - new_lo
                        new_lo = full_lo
                    if new_hi > full_hi:
                        new_lo -= new_hi - full_hi
                        new_hi = full_hi
            self.plot.setXRange(new_lo, new_hi, padding=0.0)
        elif axis == "y" and self._display_mode == "butterfly":
            self.plot.setYRange(center - span / 2.0, center + span / 2.0, padding=0.0)

    @staticmethod
    def _nice_voltage_bar(target: float) -> float:
        target = max(float(target), 1e-9)
        exponent = math.floor(math.log10(target))
        base = 10.0 ** exponent
        candidates = np.array([1.0, 2.0, 5.0, 10.0]) * base
        return float(candidates[np.argmin(np.abs(candidates - target))])

    def _range_changed(self, *_):
        self._update_calibration()
        if self._window_label is not None and self._window_region is not None:
            try:
                start_ms, end_ms = self._window_region.getRegion()
                yr = self._view_box.viewRange()[1]
                y = float(yr[1]) - 0.04 * (float(yr[1]) - float(yr[0]))
                self._window_label.setPos((start_ms + end_ms) / 2.0, y)
            except Exception:
                pass

    def _update_calibration(self):
        if self._updating_calibration or not self._series_uv:
            return
        self._updating_calibration = True
        try:
            for item in self._cal_items:
                try:
                    self.plot.removeItem(item)
                except Exception:
                    pass
            self._cal_items = []
            xr, yr = self._view_box.viewRange()
            x0, x1 = map(float, xr)
            y0, y1 = map(float, yr)
            xspan, yspan = x1 - x0, y1 - y0
            if xspan <= 0 or yspan <= 0:
                return

            if self._display_mode == "stacked":
                bar_display = 1.0
                bar_uv = self._stack_scale_uv_per_row
            else:
                bar_uv = self._nice_voltage_bar(yspan * 0.18)
                bar_display = bar_uv

            bx = x1 - 0.06 * xspan
            by0 = y0 + 0.08 * yspan
            by1 = by0 + bar_display
            if by1 > y1 - 0.05 * yspan:
                by1 = y1 - 0.05 * yspan
                if self._display_mode == "stacked":
                    frac = max(by1 - by0, 1e-9) / max(bar_display, 1e-9)
                    bar_uv *= frac
                else:
                    bar_uv = max(by1 - by0, 1e-9)
            cap = 0.009 * xspan
            pen = pg.mkPen(self._theme_fg(), width=2)
            line = pg.PlotDataItem([bx, bx], [by0, by1], pen=pen)
            cap0 = pg.PlotDataItem([bx - cap, bx + cap], [by0, by0], pen=pen)
            cap1 = pg.PlotDataItem([bx - cap, bx + cap], [by1, by1], pen=pen)
            text = pg.TextItem(text=f"{bar_uv:g} µV", color=self._theme_fg(), anchor=(0.0, 0.5))
            text.setPos(bx + 1.7 * cap, (by0 + by1) / 2.0)
            for item in [line, cap0, cap1, text]:
                self.plot.addItem(item)
                self._cal_items.append(item)
        finally:
            self._updating_calibration = False

    def _channel_at_click(self, latency_ms: float, display_y: float) -> str:
        """Return the displayed channel whose waveform is closest to the click."""
        if self._display_mode != "stacked" or not self._series_uv or not self._times_ms.size:
            return ""
        x = float(latency_ms)
        best_name = ""
        best_distance = float("inf")
        for name, data in self._series_uv.items():
            amp = float(np.interp(x, self._times_ms, np.asarray(data, dtype=float)))
            curve_y = self._display_y_for_marker(amp, name)
            distance = abs(float(display_y) - curve_y)
            if distance < best_distance:
                best_distance = distance
                best_name = name
        return best_name

    def _nearest_marker_definition(self, scene_pos, max_pixels: float = 16.0):
        """Find a point marker close enough to a right-click in screen pixels."""
        best = None
        best_dist = float(max_pixels)
        for definition in self._marker_defs:
            if definition.get("amplitude_uv") is None or not definition.get("measurement_key"):
                continue
            x = float(definition["latency_ms"])
            y = self._display_y_for_marker(float(definition["amplitude_uv"]), str(definition.get("channel", "")))
            try:
                marker_scene = self._view_box.mapViewToScene(pg.Point(x, y))
                dx = float(marker_scene.x() - scene_pos.x())
                dy = float(marker_scene.y() - scene_pos.y())
                dist = math.hypot(dx, dy)
            except Exception:
                continue
            if dist <= best_dist:
                best_dist = dist
                best = definition
        return best

    def _mouse_clicked(self, event):
        pos = event.scenePos()
        if not self.plot.sceneBoundingRect().contains(pos):
            return

        if event.button() == Qt.MouseButton.RightButton:
            marker = self._nearest_marker_definition(pos)
            if marker is not None:
                event.accept()
                self.markerContextRequested.emit(dict(marker))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.plot.plotItem.vb.mapSceneToView(pos)
        channel = self._channel_at_click(float(point.x()), float(point.y()))
        self.pointClicked.emit(float(point.x()), channel)
