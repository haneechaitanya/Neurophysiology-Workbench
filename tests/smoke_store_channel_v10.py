import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["ERP_WORKBENCH_DISTRIBUTION"] = "store"

from PySide6.QtWidgets import QApplication

from erpworkbench.build_channel import IS_STORE_BUILD
from erpworkbench import main_window


assert IS_STORE_BUILD
assert main_window.updater is None

app = QApplication.instance() or QApplication([])
window = main_window.ERPWorkbench()
# Retain the PySide QAction wrappers while traversing the owned QMenu. Creating
# the menu wrapper inside a generator can let its temporary owner wrapper be
# collected before the following line on some PySide6 Windows builds.
menu_actions = list(window.menuBar().actions())
menu_labels = [action.text() for action in menu_actions]
help_action = next(action for action in menu_actions if action.text() == "&Help")
help_menu = help_action.menu()
assert help_menu is not None
help_actions = list(help_menu.actions())
help_labels = [action.text() for action in help_actions]

assert "Check for updates…" not in help_labels
assert window._auto_update_checks is False
assert window._update_network_manager is None
assert window.auto_update_checkbox is None
window.close()

print("STORE_CHANNEL_V10_SMOKE_TEST_OK", menu_labels)
