from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable

GITHUB_OWNER = "haneechaitanya"
GITHUB_REPO = "Neurophysiology-Workbench"
GITHUB_API_VERSION = "2026-03-10"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
EXPECTED_INSTALLER_RE = re.compile(r"^ERP_Workbench_(\d+\.\d+\.\d+)_Setup\.exe$", re.IGNORECASE)
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024  # defensive 2 GiB ceiling


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str
    size: int = 0
    digest: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str
    body: str
    html_url: str
    published_at: str
    asset: ReleaseAsset | None


class UpdateError(RuntimeError):
    pass


class NoPublicRelease(UpdateError):
    """Raised when GitHub has no anonymously visible stable release."""


def _version_key(value: str) -> tuple[int, int, int, int, int]:
    """Small SemVer-like comparator supporting final/rc/beta/alpha builds.

    Examples: 1.0.0 > 1.0.0rc4 > 1.0.0b2 > 1.0.0a1.
    Unknown suffixes are treated as prerelease builds rather than newer finals.
    """
    text = str(value or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:(a|alpha|b|beta|rc)[.\-_]?(\d+)?)?$", text)
    if not match:
        nums = [int(x) for x in re.findall(r"\d+", text)[:3]]
        while len(nums) < 3:
            nums.append(0)
        return nums[0], nums[1], nums[2], 0, 0
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    stage = match.group(4)
    stage_num = int(match.group(5) or 0)
    if stage is None:
        rank = 3
    elif stage in {"rc"}:
        rank = 2
    elif stage in {"b", "beta"}:
        rank = 1
    else:
        rank = 0
    return major, minor, patch, rank, stage_num


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _select_windows_installer(assets: Iterable[dict]) -> ReleaseAsset | None:
    candidates = []
    for raw in assets or []:
        name = str(raw.get("name", "") or "")
        url = str(raw.get("browser_download_url", "") or "")
        if not name.lower().endswith(".exe") or not url:
            continue
        score = 0
        low = name.lower()
        if "setup" in low:
            score += 4
        if "erp" in low and "workbench" in low:
            score += 3
        if "windows" in low or "win" in low:
            score += 1
        candidates.append((score, name, ReleaseAsset(
            name=name,
            browser_download_url=url,
            size=int(raw.get("size", 0) or 0),
            digest=str(raw.get("digest", "") or ""),
        )))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return candidates[0][2]


def _stable_three_part_version(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return ""
    return ".".join(match.groups())


def _is_expected_release_page(url: str, tag_name: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return False
    if parsed.scheme.lower() != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        return False
    expected = f"/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/{tag_name}"
    return parsed.path.rstrip("/") == expected.rstrip("/")


def _is_expected_asset_url(url: str, tag_name: str, asset_name: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return False
    if parsed.scheme.lower() != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        return False
    expected = f"/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/{tag_name}/{asset_name}"
    return urllib.parse.unquote(parsed.path).rstrip("/") == expected.rstrip("/")


def _is_allowed_download_endpoint(url: str) -> bool:
    """Allow GitHub itself and GitHub-controlled release-content hosts only."""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return host in {"github.com", "www.github.com"} or host.endswith(".githubusercontent.com")


def _valid_sha256_digest(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    return bool(re.fullmatch(r"[0-9a-f]{64}", text))


def release_from_payload(payload: dict) -> ReleaseInfo:
    if bool(payload.get("draft", False)) or bool(payload.get("prerelease", False)):
        raise UpdateError("The update endpoint returned a draft or prerelease instead of a stable release.")
    return ReleaseInfo(
        tag_name=str(payload.get("tag_name", "") or ""),
        name=str(payload.get("name", "") or payload.get("tag_name", "") or "Release"),
        body=str(payload.get("body", "") or ""),
        html_url=str(payload.get("html_url", "") or ""),
        published_at=str(payload.get("published_at", "") or ""),
        asset=_select_windows_installer(payload.get("assets", [])),
    )


def validate_release_for_download(release: ReleaseInfo) -> ReleaseAsset:
    """Validate stable release provenance before any installer is downloaded."""
    tag = str(release.tag_name or "").strip()
    version = _stable_three_part_version(tag)
    if not version:
        raise UpdateError("The release tag is not a stable three-part version (expected vX.Y.Z).")
    if not _is_expected_release_page(release.html_url, tag):
        raise UpdateError("The release page does not belong to the configured ERP Workbench GitHub repository.")

    asset = release.asset
    if asset is None:
        raise UpdateError("This release does not contain a Windows installer asset.")
    match = EXPECTED_INSTALLER_RE.fullmatch(str(asset.name or ""))
    if not match:
        raise UpdateError("The release installer name does not match ERP_Workbench_X.Y.Z_Setup.exe.")
    if match.group(1) != version:
        raise UpdateError("The installer version does not match the GitHub release tag.")
    if not _is_expected_asset_url(asset.browser_download_url, tag, asset.name):
        raise UpdateError("The installer download URL does not belong to the configured GitHub release.")
    if int(asset.size or 0) < 0 or int(asset.size or 0) > MAX_INSTALLER_BYTES:
        raise UpdateError("The installer size reported by GitHub is outside the permitted range.")
    if not _valid_sha256_digest(asset.digest):
        raise UpdateError(
            "GitHub did not provide a valid SHA-256 digest for this installer; automatic installation is blocked."
        )
    return asset


def fetch_latest_release(*, progress: Callable[[str], None] | None = None, timeout: float = 8.0) -> ReleaseInfo:
    if progress:
        progress("Checking GitHub for a stable ERP Workbench release…")
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "ERP-Workbench-Updater",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoPublicRelease(
                "No public stable GitHub release is visible yet. This is expected while the repository is private or before the first release is published."
            ) from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code} while checking for updates.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Could not reach GitHub: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an unreadable release response.") from exc
    release = release_from_payload(payload)
    if not release.tag_name:
        raise UpdateError("GitHub's latest release did not contain a version tag.")
    return release


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_asset_digest(path: Path, expected_digest: str) -> bool:
    expected = str(expected_digest or "").strip().lower()
    if not expected:
        return False
    if expected.startswith("sha256:"):
        expected = expected.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    return _sha256_file(Path(path)).lower() == expected


def download_release_asset(
    release: ReleaseInfo,
    *,
    progress: Callable[[str], None] | None = None,
    timeout: float = 20.0,
) -> Path:
    asset = validate_release_for_download(release)

    tag_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", release.tag_name or "latest")
    destination = Path(tempfile.gettempdir()) / "ERPWorkbenchUpdate" / tag_dir / asset.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    request = urllib.request.Request(
        asset.browser_download_url,
        headers={"User-Agent": "ERP-Workbench-Updater"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            final_url = str(getattr(response, "geturl", lambda: asset.browser_download_url)() or "")
            if not _is_allowed_download_endpoint(final_url):
                raise UpdateError("GitHub redirected the installer download to an unapproved host; download was blocked.")
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            if total < 0 or total > MAX_INSTALLER_BYTES:
                raise UpdateError("The installer download size is outside the permitted range.")
            received = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES:
                    raise UpdateError("The installer exceeded the maximum permitted download size.")
                output.write(chunk)
                if progress:
                    if total > 0:
                        progress(f"Downloading update… {min(100, round(received * 100 / total))}%")
                    else:
                        progress(f"Downloading update… {received / (1024 * 1024):.1f} MB")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, UpdateError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update download failed: {exc}") from exc

    if asset.size and temporary.stat().st_size != int(asset.size):
        temporary.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer size does not match the size reported by GitHub.")

    if not verify_asset_digest(temporary, asset.digest):
        temporary.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer failed its SHA-256 integrity check and was deleted.")

    os.replace(temporary, destination)
    if progress:
        progress("Update installer downloaded and verified.")
    return destination
