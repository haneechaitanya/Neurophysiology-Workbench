ERP Workbench — GitHub updater patch

Apply to the canonical repository only:
D:\Projects\Electrophysiology\Neurophysiology-Workbench

Replace/add the files in this ZIP, preserving folders.

This adds:
- Help > Check for updates...
- Help > About ERP Workbench...
- Settings > Updates
- optional once-per-24h background stable-release check
- GitHub Releases source: haneechaitanya/Neurophysiology-Workbench
- Windows installer download to the user's temporary folder
- SHA-256 release-asset verification when GitHub supplies a digest
- explicit user confirmation before launching the installer
- no required network connection for analysis

The repository is currently private and has no public stable release, so a manual
check is EXPECTED to report that no public stable release is available yet.
That is the correct test result at this stage.

Do not change __version__ to 1.0.0 yet.
