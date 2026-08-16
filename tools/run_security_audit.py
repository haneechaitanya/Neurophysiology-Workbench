"""Run privacy-limited release security checks and create a review ZIP."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from generate_sbom import generate as generate_sbom


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "security_audit"
SECURITY_PYTHON = PROJECT_ROOT / ".security-venv" / "Scripts" / "python.exe"
DIST_ROOT = PROJECT_ROOT / "dist" / "ERPWorkbench"
SOURCE_SCAN_PATHS = (
    "app.py",
    "erpworkbench",
    "tools",
    "tests",
    "ERPWorkbench.spec",
)
BANDIT_SCAN_PATHS = tuple(path for path in SOURCE_SCAN_PATHS if path != "tests")


def _sanitize(text: str) -> str:
    replacements = (
        (str(PROJECT_ROOT), "<PROJECT_ROOT>"),
        (str(PROJECT_ROOT).replace("\\", "/"), "<PROJECT_ROOT>"),
        (str(SECURITY_PYTHON.parent.parent), "<SECURITY_ENV>"),
    )
    result = text
    for original, replacement in replacements:
        result = result.replace(original, replacement)
        result = result.replace(original.casefold(), replacement)
    return result


def _run(command: list[str], timeout: int = 1800) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": _sanitize(completed.stdout),
            "stderr": _sanitize(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": _sanitize(exc.stdout or ""),
            "stderr": _sanitize((exc.stderr or "") + "\nTimed out."),
        }
    except OSError as exc:
        return {"exit_code": 127, "stdout": "", "stderr": str(exc)}


def _write_result(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _redact_json_values(value):
    if isinstance(value, dict):
        return {
            key: _redact_json_values(item)
            for key, item in value.items()
            if key.casefold() not in {"code", "secret", "raw_secret"}
        }
    if isinstance(value, list):
        return [_redact_json_values(item) for item in value]
    if isinstance(value, str):
        return _sanitize(value)
    return value


def _sanitize_json_file(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    path.write_text(
        json.dumps(_redact_json_values(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_versions() -> dict:
    modules = ("pip_audit", "bandit", "detect_secrets")
    return {
        module: _run([str(SECURITY_PYTHON), "-m", module, "--version"], timeout=60)
        for module in modules
    }


def _defender_scan() -> dict:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    candidates = [program_files / "Windows Defender" / "MpCmdRun.exe"]
    platform_dir = program_data / "Microsoft" / "Windows Defender" / "Platform"
    if platform_dir.is_dir():
        candidates = sorted(platform_dir.glob("*\\MpCmdRun.exe"), reverse=True) + candidates
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        return {"exit_code": 127, "stdout": "", "stderr": "Microsoft Defender command-line scanner not found."}
    return _run(
        [str(executable), "-Scan", "-ScanType", "3", "-File", str(DIST_ROOT), "-DisableRemediation"],
        timeout=3600,
    )


def _zip_directory(source: Path, destination: Path) -> None:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source.parent).as_posix())


def main() -> int:
    if not SECURITY_PYTHON.is_file():
        print("ERROR: Run setup_security_tools_windows.bat first.")
        return 1
    if not (DIST_ROOT / "ERPWorkbench.exe").is_file():
        print("ERROR: Accepted dist/ERPWorkbench build not found.")
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = OUTPUT_ROOT / f"security_audit_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    bom = generate_sbom(output_dir / "erp-workbench.cdx.json")
    results: dict[str, dict] = {}
    results["pip_check"] = _run([sys.executable, "-m", "pip", "check"], timeout=120)

    site_packages = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
    pip_audit_output = output_dir / "pip-audit.json"
    results["pip_audit"] = _run(
        [
            str(SECURITY_PYTHON), "-m", "pip_audit",
            "--path", str(site_packages), "--format", "json",
            "--output", str(pip_audit_output),
        ]
    )

    bandit_output = output_dir / "bandit.json"
    results["bandit"] = _run(
        [
            str(SECURITY_PYTHON), "-m", "bandit", "-r",
            *[
                str(PROJECT_ROOT / path)
                for path in BANDIT_SCAN_PATHS
                if (PROJECT_ROOT / path).exists()
            ],
            "-f", "json", "-o", str(bandit_output),
        ]
    )

    secrets_output = output_dir / "detect-secrets.json"
    secret_scan = _run(
        [
            str(SECURITY_PYTHON), "-m", "detect_secrets", "scan", "--all-files",
            *[
                str(PROJECT_ROOT / path)
                for path in SOURCE_SCAN_PATHS
                if (PROJECT_ROOT / path).exists()
            ],
        ]
    )
    secrets_output.write_text(secret_scan.pop("stdout"), encoding="utf-8")
    results["detect_secrets"] = secret_scan
    results["defender"] = _defender_scan()

    for filename in ("bandit.json", "pip-audit.json", "detect-secrets.json"):
        path = output_dir / filename
        if path.is_file():
            _sanitize_json_file(path)

    report = {
        "created_utc": timestamp,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "sbom_format": "CycloneDX 1.6 JSON",
        "sbom_components": len(bom["components"]),
        "checks": results,
        "tool_versions": _tool_versions(),
        "interpretation": (
            "Non-zero tool exit codes may indicate findings and require review; "
            "they do not by themselves prove a vulnerability."
        ),
        "privacy": (
            "No EEG/participant files, environment variables, credentials, usernames, "
            "hostnames, or raw secret values are intentionally collected."
        ),
    }
    _write_result(output_dir / "summary.json", report)

    hashes = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            hashes.append({"file": path.name, "sha256": _sha256(path)})
    (output_dir / "report_hashes.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive_path = OUTPUT_ROOT / f"ERP_Workbench_security_audit_{timestamp}.zip"
    _zip_directory(output_dir, archive_path)
    print(f"Created: {archive_path}")
    print(f"SBOM runtime components: {len(bom['components'])}")
    for name, result in results.items():
        print(f"{name}: exit code {result['exit_code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
