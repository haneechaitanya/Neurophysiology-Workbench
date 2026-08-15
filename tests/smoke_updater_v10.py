from pathlib import Path
import tempfile

from erpworkbench import updater

assert updater.is_newer_version("v1.0.0", "1.0.0rc4")
assert updater.is_newer_version("1.0.1", "1.0.0")
assert not updater.is_newer_version("v1.0.0", "1.0.0")
assert not updater.is_newer_version("v1.0.0rc3", "1.0.0rc4")

payload = {
    "tag_name": "v1.0.1",
    "name": "ERP Workbench 1.0.1",
    "body": "Bug fixes",
    "html_url": "https://github.com/haneechaitanya/Neurophysiology-Workbench/releases/tag/v1.0.1",
    "published_at": "2026-08-16T00:00:00Z",
    "assets": [
        {"name": "source.zip", "browser_download_url": "https://example.invalid/source.zip", "size": 10},
        {"name": "ERP_Workbench_1.0.1_Setup.exe", "browser_download_url": "https://example.invalid/setup.exe", "size": 100, "digest": "sha256:" + "0" * 64},
    ],
}
release = updater.release_from_payload(payload)
assert release.asset is not None
assert release.asset.name == "ERP_Workbench_1.0.1_Setup.exe"

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "test.bin"
    p.write_bytes(b"erp-workbench-updater")
    import hashlib
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert updater.verify_asset_digest(p, "sha256:" + digest)
    assert not updater.verify_asset_digest(p, "sha256:" + "0" * 64)

print("UPDATER_V10_SMOKE_TEST_OK")
