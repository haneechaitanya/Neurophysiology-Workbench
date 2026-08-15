from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QSpinBox,
    QToolButton,
)


class _ReliableArrowMixin:
    """Spin-box buttons that remain clickable across Windows/Qt styles.

    Native QAbstractSpinBox arrow hit-testing has proved inconsistent after
    application-wide styling on some Windows systems.  Instead of trying to
    reinterpret the native hit area, these classes hide the native buttons and
    overlay two real QToolButtons.  The value logic, text editing, keyboard
    entry and wheel behaviour remain those of the underlying QSpinBox/
    QDoubleSpinBox.
    """

    _step_button_width = 22

    def _setup_reliable_step_buttons(self):
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        self._step_up_button = QToolButton(self)
        self._step_up_button.setArrowType(Qt.ArrowType.UpArrow)
        self._step_up_button.setAutoRepeat(True)
        self._step_up_button.setAutoRepeatDelay(350)
        self._step_up_button.setAutoRepeatInterval(80)
        self._step_up_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._step_up_button.setToolTip("Increase")
        self._step_up_button.clicked.connect(self.stepUp)

        self._step_down_button = QToolButton(self)
        self._step_down_button.setArrowType(Qt.ArrowType.DownArrow)
        self._step_down_button.setAutoRepeat(True)
        self._step_down_button.setAutoRepeatDelay(350)
        self._step_down_button.setAutoRepeatInterval(80)
        self._step_down_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._step_down_button.setToolTip("Decrease")
        self._step_down_button.clicked.connect(self.stepDown)

        # Keep suffix/text clear of the overlaid buttons.
        line_edit = self.lineEdit()
        if line_edit is not None:
            margins = line_edit.textMargins()
            line_edit.setTextMargins(
                margins.left(), margins.top(),
                max(margins.right(), self._step_button_width + 3),
                margins.bottom(),
            )
        self._layout_reliable_step_buttons()

    def _layout_reliable_step_buttons(self):
        if not hasattr(self, "_step_up_button"):
            return
        w = min(self._step_button_width, max(16, self.width() // 3))
        h = max(1, self.height())
        top_h = h // 2
        x = max(0, self.width() - w)
        self._step_up_button.setGeometry(x, 0, w, top_h)
        self._step_down_button.setGeometry(x, top_h, w, h - top_h)
        self._step_up_button.raise_()
        self._step_down_button.raise_()

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._layout_reliable_step_buttons()

    def showEvent(self, event):  # noqa: N802 - Qt API
        super().showEvent(event)
        self._layout_reliable_step_buttons()


class ReliableDoubleSpinBox(_ReliableArrowMixin, QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_reliable_step_buttons()


class ReliableSpinBox(_ReliableArrowMixin, QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_reliable_step_buttons()
