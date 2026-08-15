ERP Workbench pre-1.0 release hardening

- Manual Check for updates now attaches to an already-running quiet startup check and shows its result.
- Update status returns to Ready after a no-release/offline result.
- Exact ERP Workbench version is stored in .erpavg manifests and Excel metadata.
- Grand-average exports report both the current application version and each source package version.
- Added setup_development_windows.bat for reproducible local .venv setup.
- Release build no longer upgrades dependencies automatically; it freezes the already-tested .venv.
- Release builder runs verify_v1_windows.bat before PyInstaller and detects Inno Setup 6 or 7.
- Version remains 1.0.0rc4 until the final tested installer is frozen.
