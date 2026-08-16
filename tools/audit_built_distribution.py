"""Create a privacy-safe manifest of a built ERP Workbench directory."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from datetime import datetime, timezone
import zipfile


NATIVE_SUFFIXES = {".dll", ".exe", ".pyd", ".so", ".dylib"}
NOTICE_NAMES = {"license", "licence", "copying", "notice", "copyright"}
VERSION_FIELDS = ("FileVersion", "ProductVersion", "CompanyName", "ProductName")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(relative: Path) -> str:
    lower = relative.as_posix().lower()
    name = relative.name.lower()
    if name.startswith("qt6") or "pyside6" in lower or "shiboken6" in lower:
        return "qt-pyside"
    if relative.suffix.lower() in NATIVE_SUFFIXES:
        return "native-binary"
    if any(name.startswith(token) for token in NOTICE_NAMES):
        return "license-notice"
    return "other"


def windows_version_info(path: Path) -> dict[str, str]:
    """Read public PE version-resource strings without executing the file."""
    result = {f"pe_{field.lower()}": "" for field in VERSION_FIELDS}
    if os.name != "nt":
        return result

    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return result
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return result

        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        translations = []
        if version.VerQueryValueW(
            buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)
        ) and length.value >= 4:
            pairs = (ctypes.c_ushort * (length.value // 2)).from_address(pointer.value)
            translations = list(zip(pairs[0::2], pairs[1::2]))
        if not translations:
            translations = [(0x0409, 0x04B0), (0x0409, 0x04E4)]

        for field in VERSION_FIELDS:
            for language, codepage in translations:
                query = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{field}"
                if version.VerQueryValueW(
                    buffer, query, ctypes.byref(pointer), ctypes.byref(length)
                ) and pointer.value and length.value:
                    value = ctypes.wstring_at(pointer.value, length.value).rstrip("\x00")
                    if value:
                        result[f"pe_{field.lower()}"] = value
                        break
    except (AttributeError, OSError, ValueError):
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("release_audit"))
    args = parser.parse_args()

    root = args.dist_dir.resolve()
    if not root.is_dir():
        parser.error(f"built directory not found: {root}")

    created = datetime.now(timezone.utc)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    audit_name = f"ERP_Workbench_built_artifact_audit_{stamp}"
    staging = args.output_dir.resolve() / audit_name
    staging.mkdir(parents=True, exist_ok=False)

    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        classification = classify(relative)
        record = {
            "path": relative.as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "classification": classification,
        }
        if classification in {"native-binary", "qt-pyside"}:
            record.update(windows_version_info(path))
        else:
            record.update({f"pe_{field.lower()}": "" for field in VERSION_FIELDS})
        records.append(record)

    summary = {
        "created_utc": created.isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "audit_format_version": 2,
        "dist_directory_name": root.name,
        "file_count": len(records),
        "total_size_bytes": sum(row["size_bytes"] for row in records),
        "classification_counts": {
            label: sum(row["classification"] == label for row in records)
            for label in sorted({row["classification"] for row in records})
        },
        "privacy": "Contains filenames, sizes and hashes only; no file contents or environment variables.",
    }

    (staging / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (staging / "file_manifest.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    with (staging / "file_manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "path",
                "size_bytes",
                "sha256",
                "classification",
                "pe_fileversion",
                "pe_productversion",
                "pe_companyname",
                "pe_productname",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir.resolve() / f"{audit_name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.iterdir()):
            bundle.write(path, f"{audit_name}/{path.name}")

    print(f"Created: {archive}")
    print("Upload only this audit ZIP for the final third-party/native-library check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
