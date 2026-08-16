"""Generate a deterministic CycloneDX JSON SBOM from the audited environment."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
from urllib.parse import quote
import uuid

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = PROJECT_ROOT / "requirements-runtime-lock-windows-py313.txt"
DIRECT_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
APP_VERSION = "1.0.0rc4"
APP_REF = f"pkg:generic/erp-workbench@{APP_VERSION}"


def _requirement_names(path: Path) -> list[str]:
    names: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(canonicalize_name(Requirement(line).name))
    return sorted(set(names))


def _license_expression(message) -> str:
    expression = str(message.get("License-Expression") or "").strip()
    if expression:
        return expression
    value = str(message.get("License") or "").strip()
    if value and "\n" not in value and len(value) <= 100:
        return value
    return "NOASSERTION"


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{quote(canonicalize_name(name))}@{quote(version)}"


def generate(output_path: Path) -> dict:
    locked_names = _requirement_names(RUNTIME_LOCK)
    direct_names = set(_requirement_names(DIRECT_REQUIREMENTS))
    records: dict[str, dict] = {}

    for name in locked_names:
        distribution = metadata.distribution(name)
        message = distribution.metadata
        canonical_name = canonicalize_name(str(message.get("Name") or name))
        version = str(distribution.version)
        ref = _purl(canonical_name, version)
        records[canonical_name] = {
            "distribution": distribution,
            "ref": ref,
            "component": {
                "type": "library",
                "bom-ref": ref,
                "name": str(message.get("Name") or canonical_name),
                "version": version,
                "purl": ref,
                "licenses": [{"expression": _license_expression(message)}],
                "scope": "required",
                "properties": [
                    {
                        "name": "erp-workbench:direct-dependency",
                        "value": "true" if canonical_name in direct_names else "false",
                    }
                ],
            },
        }

    dependencies: list[dict] = []
    for name, record in sorted(records.items()):
        targets: set[str] = set()
        for value in record["distribution"].requires or []:
            try:
                requirement = Requirement(value)
                if requirement.marker and not requirement.marker.evaluate():
                    continue
                target = records.get(canonicalize_name(requirement.name))
                if target:
                    targets.add(target["ref"])
            except Exception:
                continue
        dependencies.append({"ref": record["ref"], "dependsOn": sorted(targets)})

    # Every locked runtime component is shipped as part of the standalone
    # application, including dependencies selected through extras or platform
    # markers that may not appear in a marker evaluation on another machine.
    app_dependencies = sorted(record["ref"] for record in records.values())
    dependencies.insert(0, {"ref": APP_REF, "dependsOn": app_dependencies})

    identity_material = "\n".join(
        f"{name}=={record['component']['version']}"
        for name, record in sorted(records.items())
    )
    serial_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{APP_REF}\n{identity_material}")
    lock_sha256 = hashlib.sha256(RUNTIME_LOCK.read_bytes()).hexdigest()

    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial_uuid}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": APP_REF,
                "name": "ERP Workbench",
                "version": APP_VERSION,
                "licenses": [{"expression": "AGPL-3.0-only"}],
                "externalReferences": [
                    {
                        "type": "vcs",
                        "url": "https://github.com/haneechaitanya/Neurophysiology-Workbench",
                    }
                ],
                "properties": [
                    {
                        "name": "erp-workbench:runtime-lock-sha256",
                        "value": lock_sha256,
                    },
                    {
                        "name": "erp-workbench:target",
                        "value": "Windows x86-64; CPython 3.13.13",
                    },
                ],
            }
        },
        "components": [
            record["component"] for _, record in sorted(records.items())
        ],
        "dependencies": dependencies,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bom


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bom = generate(args.output.resolve())
    print(f"Created CycloneDX 1.6 SBOM: {args.output}")
    print(f"Runtime components: {len(bom['components'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
