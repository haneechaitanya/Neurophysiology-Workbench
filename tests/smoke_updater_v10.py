from pathlib import Path
import hashlib
import tempfile

from erpworkbench import updater

assert updater.is_newer_version("v1.0.0", "1.0.0rc4")
assert updater.is_newer_version("1.0.1", "1.0.0")
assert not updater.is_newer_version("v1.0.0", "1.0.0")
assert not updater.is_newer_version("v1.0.0rc3", "1.0.0rc4")

valid_digest = "sha256:" + "0" * 64
payload = {
    "tag_name": "v1.0.1",
    "name": "ERP Workbench 1.0.1",
    "body": "Bug fixes",
    "html_url": "https://github.com/haneechaitanya/Neurophysiology-Workbench/releases/tag/v1.0.1",
    "published_at": "2026-08-16T00:00:00Z",
    "draft": False,
    "prerelease": False,
    "assets": [
        {"name": "source.zip", "browser_download_url": "https://example.invalid/source.zip", "size": 10},
        {
            "name": "ERP_Workbench_1.0.1_Setup.exe",
            "browser_download_url": "https://github.com/haneechaitanya/Neurophysiology-Workbench/releases/download/v1.0.1/ERP_Workbench_1.0.1_Setup.exe",
            "size": 100,
            "digest": valid_digest,
        },
    ],
}
release = updater.release_from_payload(payload)
assert release.asset is not None
assert release.asset.name == "ERP_Workbench_1.0.1_Setup.exe"
assert updater.validate_release_for_download(release) == release.asset

# Stable-release only.
for bad_flag in ("draft", "prerelease"):
    bad = dict(payload)
    bad[bad_flag] = True
    try:
        updater.release_from_payload(bad)
    except updater.UpdateError:
        pass
    else:
        raise AssertionError(f"{bad_flag} release was not rejected")

# Installer filename/version/source are pinned to the configured GitHub repo/tag.
def expect_rejected(mutator):
    bad_payload = dict(payload)
    bad_payload["assets"] = [dict(payload["assets"][1])]
    mutator(bad_payload)
    bad_release = updater.release_from_payload(bad_payload)
    try:
        updater.validate_release_for_download(bad_release)
    except updater.UpdateError:
        return
    raise AssertionError("Unsafe release metadata was accepted")

expect_rejected(lambda p: p["assets"][0].update(name="OtherProduct_1.0.1_Setup.exe"))
expect_rejected(lambda p: p["assets"][0].update(name="ERP_Workbench_1.0.2_Setup.exe"))
expect_rejected(lambda p: p["assets"][0].update(browser_download_url="https://example.com/ERP_Workbench_1.0.1_Setup.exe"))
expect_rejected(lambda p: p["assets"][0].update(digest=""))

assert updater._is_allowed_download_endpoint("https://github.com/a/b")
assert updater._is_allowed_download_endpoint("https://release-assets.githubusercontent.com/path")
assert updater._is_allowed_download_endpoint("https://objects.githubusercontent.com/path")
assert not updater._is_allowed_download_endpoint("http://github.com/a/b")
assert not updater._is_allowed_download_endpoint("https://github.example.com/a/b")

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "test.bin"
    p.write_bytes(b"erp-workbench-updater")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert updater.verify_asset_digest(p, "sha256:" + digest)
    assert not updater.verify_asset_digest(p, "sha256:" + "0" * 64)
    assert not updater.verify_asset_digest(p, "")

print("UPDATER_V10_SMOKE_TEST_OK")
