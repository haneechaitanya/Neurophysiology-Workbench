"""Create a privacy-limited inventory of the Windows release environment.

This script uses only the Python standard library. It records installed package
names, exact versions, declared dependency/licence metadata, candidate licence
texts, non-identifying platform details, pip consistency results, and hashes of
release-defining source files. It deliberately does not record environment
variables, usernames, home paths, hostnames, network configuration, EEG data,
credentials, or the contents of the virtual environment.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = PROJECT_ROOT / "release_audit"
MAX_LICENSE_FILE_BYTES = 5 * 1024 * 1024
HASH_TARGETS = (
    "requirements.txt",
    "ERPWorkbench.spec",
    "version_info.txt",
    "build_v1_installer_windows.bat",
    "verify_v1_windows.bat",
    "installer/ERPWorkbench_v1.0.iss",
)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _metadata_values(message, key: str) -> list[str]:
    values = message.get_all(key) or []
    return [str(item).strip() for item in values if str(item).strip()]


def _project_urls(message) -> list[str]:
    return _metadata_values(message, "Project-URL")


def _license_classifiers(message) -> list[str]:
    return [
        value
        for value in _metadata_values(message, "Classifier")
        if value.startswith("License ::")
    ]


def _package_records() -> tuple[list[dict], list[tuple[metadata.Distribution, dict]]]:
    combined: list[tuple[metadata.Distribution, dict]] = []
    for distribution in metadata.distributions():
        message = distribution.metadata
        name = str(message.get("Name") or getattr(distribution, "name", "") or "unknown")
        record = {
            "name": name,
            "version": str(distribution.version or ""),
            "license_expression": str(message.get("License-Expression") or "").strip(),
            "license_field": str(message.get("License") or "").strip(),
            "license_classifiers": _license_classifiers(message),
            "project_urls": _project_urls(message),
            "requires": sorted(str(item) for item in (distribution.requires or [])),
        }
        combined.append((distribution, record))
    combined.sort(key=lambda item: item[1]["name"].casefold())
    return [item[1] for item in combined], combined


def _copy_candidate_licenses(
    distributions: list[tuple[metadata.Distribution, dict]], destination: Path
) -> list[dict]:
    copied: list[dict] = []
    for distribution, record in distributions:
        package_dir = destination / _safe_name(record["name"])
        seen_names: set[str] = set()
        for item in distribution.files or []:
            filename = Path(str(item)).name
            lower = filename.casefold()
            if not lower.startswith(("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            try:
                if not source.is_file() or source.stat().st_size > MAX_LICENSE_FILE_BYTES:
                    continue
            except OSError:
                continue
            target_name = _safe_name(filename)
            if target_name.casefold() in seen_names:
                continue
            seen_names.add(target_name.casefold())
            package_dir.mkdir(parents=True, exist_ok=True)
            target = package_dir / target_name
            try:
                shutil.copyfile(source, target)
            except OSError:
                continue
            copied.append(
                {
                    "package": record["name"],
                    "version": record["version"],
                    "file": target.relative_to(destination.parent).as_posix(),
                }
            )
    return copied


def _write_csv(path: Path, records: list[dict]) -> None:
    columns = (
        "name",
        "version",
        "license_expression",
        "license_field",
        "license_classifiers",
        "project_urls",
        "requires",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    **record,
                    "license_classifiers": " | ".join(record["license_classifiers"]),
                    "project_urls": " | ".join(record["project_urls"]),
                    "requires": " | ".join(record["requires"]),
                }
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_hashes() -> list[dict]:
    results = []
    for relative in HASH_TARGETS:
        path = PROJECT_ROOT / relative
        if path.is_file():
            results.append({"file": relative, "sha256": _sha256(path)})
    return results


def _pip_check() -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return {"exit_code": completed.returncode, "output": output}


def _zip_directory(source: Path, destination: Path) -> None:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and path != destination:
                archive.write(path, path.relative_to(source.parent).as_posix())


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = AUDIT_ROOT / f"dependency_audit_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    records, distributions = _package_records()
    licence_files = _copy_candidate_licenses(distributions, output_dir / "license_candidates")
    pip_check = _pip_check()

    environment = {
        "generated_at_utc": timestamp,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_architecture": platform.architecture()[0],
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "operating_system_version": platform.version(),
        "machine_architecture": platform.machine(),
        "package_count": len(records),
        "pip_check_exit_code": pip_check["exit_code"],
    }

    (output_dir / "environment.json").write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "dependency_metadata.json").write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_csv(output_dir / "dependency_metadata.csv", records)
    (output_dir / "installed_packages.txt").write_text(
        "".join(f"{record['name']}=={record['version']}\n" for record in records),
        encoding="utf-8",
    )
    (output_dir / "license_candidates.json").write_text(
        json.dumps(licence_files, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "pip_check.txt").write_text(
        f"exit_code={pip_check['exit_code']}\n{pip_check['output']}\n", encoding="utf-8"
    )
    (output_dir / "source_hashes.json").write_text(
        json.dumps(_source_hashes(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "COLLECTION_README.txt").write_text(
        "Neurophysiology Workbench private dependency audit\n\n"
        "This archive contains package names, exact versions, package-declared licence "
        "metadata, candidate licence texts, non-identifying platform versions, pip "
        "consistency results, and hashes of release-defining project files.\n\n"
        "It intentionally excludes the virtual environment itself, environment variables, "
        "usernames, home paths, hostnames, network configuration, credentials, participant "
        "data, EEG recordings, and analysis exports.\n",
        encoding="utf-8",
    )

    archive_path = AUDIT_ROOT / f"Neurophysiology_Workbench_dependency_audit_{timestamp}.zip"
    _zip_directory(output_dir, archive_path)
    print(f"Created: {archive_path}")
    print(f"Installed packages recorded: {len(records)}")
    print(f"Candidate licence files copied: {len(licence_files)}")
    print(f"pip check exit code: {pip_check['exit_code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
