"""PyInstaller runtime hook identifying the Microsoft Store distribution."""

import os


# Set unconditionally so an inherited environment value cannot re-enable the
# GitHub update UI in the Store package.
os.environ["ERP_WORKBENCH_DISTRIBUTION"] = "store"
