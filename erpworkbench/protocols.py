from __future__ import annotations

import json
from pathlib import Path

from .models import ProtocolDefinition


def save_protocol(protocol: ProtocolDefinition, path: str | Path) -> None:
    Path(path).write_text(json.dumps(protocol.to_dict(), indent=2), encoding="utf-8")


def load_protocol(path: str | Path) -> ProtocolDefinition:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProtocolDefinition.from_dict(data)
