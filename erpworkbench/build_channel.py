"""Distribution-channel flags set before application imports begin."""

from __future__ import annotations

import os


DISTRIBUTION_CHANNEL = str(
    os.environ.get("ERP_WORKBENCH_DISTRIBUTION", "github") or "github"
).strip().lower()
IS_STORE_BUILD = DISTRIBUTION_CHANNEL == "store"
