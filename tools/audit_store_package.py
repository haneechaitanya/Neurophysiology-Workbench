"""Create a privacy-limited structural audit of the Store MSIX package."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


EXPECTED_IDENTITY = "H.C.Challa.NeurophysiologyWorkbench"
EXPECTED_PUBLISHER = "CN=980625EF-3A8E-46A5-9AEC-3E3F8DACB2C8"
EXPECTED_DISPLAY_NAME = "Neurophysiology Workbench"
FOUNDATION = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
UAP = "http://schemas.microsoft.com/appx/manifest/uap/windows10"
RESCAP = "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"


def _sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return _sha256_stream(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--archive-listing", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    package = args.package.resolve()
    if not package.is_file():
        raise SystemExit(f"MSIX not found: {package}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = package.parents[1] / "release_audit"
    output_dir = output_root / f"store_package_audit_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    entries: list[dict] = []
    with ZipFile(package) as archive:
        manifest_bytes = archive.read("AppxManifest.xml")
        for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
            if info.is_dir():
                continue
            with archive.open(info) as handle:
                digest = _sha256_stream(handle)
            entries.append(
                {
                    "path": PurePosixPath(info.filename).as_posix(),
                    "uncompressed_bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                    "sha256": digest,
                }
            )

    manifest_path = output_dir / "AppxManifest.xml"
    manifest_path.write_bytes(manifest_bytes)
    root = ET.fromstring(manifest_bytes)
    identity = root.find(f"{{{FOUNDATION}}}Identity")
    if identity is None:
        raise SystemExit("MSIX manifest has no Identity element.")
    applications = root.find(f"{{{FOUNDATION}}}Applications")
    application = applications.find(f"{{{FOUNDATION}}}Application") if applications is not None else None
    properties = root.find(f"{{{FOUNDATION}}}Properties")
    package_display_name = (
        properties.findtext(f"{{{FOUNDATION}}}DisplayName", default="")
        if properties is not None
        else ""
    )
    visual_elements = (
        application.find(f"{{{UAP}}}VisualElements")
        if application is not None
        else None
    )
    capabilities_parent = root.find(f"{{{FOUNDATION}}}Capabilities")
    capabilities = []
    if capabilities_parent is not None:
        capabilities = [
            {"tag": child.tag, "name": child.attrib.get("Name", "")}
            for child in list(capabilities_parent)
        ]

    listing_text = ""
    if args.archive_listing and args.archive_listing.is_file():
        listing_text = args.archive_listing.read_text(encoding="utf-8", errors="replace")
        (output_dir / "pyinstaller_archive_listing.txt").write_text(
            listing_text, encoding="utf-8"
        )

    paths_lower = [entry["path"].casefold() for entry in entries]
    updater_markers = [line for line in listing_text.splitlines() if "erpworkbench.updater" in line.casefold()]
    manifest_version = identity.attrib.get("Version", "")
    version_parts = manifest_version.split(".")
    checks = {
        "identity_name_matches": identity.attrib.get("Name") == EXPECTED_IDENTITY,
        "publisher_matches": identity.attrib.get("Publisher") == EXPECTED_PUBLISHER,
        "package_display_name_matches_reserved_name": package_display_name
        == EXPECTED_DISPLAY_NAME,
        "visual_display_name_matches_reserved_name": bool(
            visual_elements is not None
            and visual_elements.attrib.get("DisplayName") == EXPECTED_DISPLAY_NAME
        ),
        "architecture_is_x64": identity.attrib.get("ProcessorArchitecture") == "x64",
        "desktop_full_trust_entrypoint": bool(
            application is not None
            and application.attrib.get("EntryPoint") == "Windows.FullTrustApplication"
        ),
        "main_executable_present": "erpworkbench/erpworkbench.exe" in paths_lower,
        "only_run_full_trust_capability": capabilities == [
            {"tag": f"{{{RESCAP}}}Capability", "name": "runFullTrust"}
        ],
        "updater_module_absent_from_pyinstaller_archive": not updater_markers,
        "no_updater_named_package_file": not any("updater" in path for path in paths_lower),
        "signature_present": "appxsignature.p7x" in paths_lower,
        "revision_number_is_zero": len(version_parts) == 4 and version_parts[3] == "0",
    }
    if args.expected_version:
        checks["manifest_version_matches_expected"] = manifest_version == args.expected_version

    with (output_dir / "package_file_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("path", "uncompressed_bytes", "compressed_bytes", "sha256"),
        )
        writer.writeheader()
        writer.writerows(entries)
    (output_dir / "package_file_manifest.json").write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = {
        "created_utc": timestamp,
        "package_filename": package.name,
        "package_sha256": _sha256(package),
        "package_size_bytes": package.stat().st_size,
        "file_count": len(entries),
        "identity": dict(identity.attrib),
        "application": dict(application.attrib) if application is not None else {},
        "capabilities": capabilities,
        "checks": checks,
        "updater_archive_markers": updater_markers,
        "privacy": "Contains package structure, hashes, public manifest identity, and module names only.",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive_path = output_root / f"ERP_Workbench_store_package_audit_{timestamp}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir.parent).as_posix())

    required_unsigned_checks = [value for key, value in checks.items() if key != "signature_present"]
    print(f"Created: {archive_path}")
    print(f"Package SHA-256: {summary['package_sha256']}")
    print(f"Package files: {len(entries)}")
    print(f"Structural checks passed: {all(required_unsigned_checks)}")
    print(f"Package signed: {checks['signature_present']}")
    return 0 if all(required_unsigned_checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
