# Microsoft Store packaging

ERP Workbench uses a dedicated Store build that is separate from the GitHub
one-directory build and Inno Setup installer.

## Reserved Partner Center identity

| Field | Value |
| --- | --- |
| Package/Identity/Name | `H.C.Challa.NeurophysiologyWorkbench` |
| Package/Identity/Publisher | `CN=980625EF-3A8E-46A5-9AEC-3E3F8DACB2C8` |
| PublisherDisplayName | `H. C. Challa` |
| Store ID | `9NG4P3MHBDG6` |
| Reserved display name | `Neurophysiology Workbench` |
| Architecture | x64 |
| Package version for RC4 | `1.0.0.0` |

MSIX versions are four numeric components. Microsoft Store requires the fourth
component (revision) to be zero. `1.0.0rc4` is therefore represented as package
version `1.0.0.0`. Every later Store submission must use a strictly higher
version while retaining a zero revision, for example `1.0.1.0`; never decrease
this value.

## Store-specific behavior

- `hooks/runtime_store.py` marks the frozen application as the Store channel
  before application modules import.
- The Help-menu update action, automatic check, update preferences, GitHub API
  request, installer download, and installer launch are inaccessible in the
  Store channel.
- `erpworkbench.updater` is excluded from PyInstaller analysis and its absence
  is checked recursively inside the frozen executable archive.
- Store updates are handled only by Microsoft Store.
- Scientific processing remains fully usable offline.
- The manifest declares only `runFullTrust`, required for this packaged classic
  desktop application. It declares no background, broad-library, webcam,
  microphone, location, or internet capability.

## Building the first staging package

1. Install the Windows 10 or Windows 11 SDK if `MakeAppx.exe` is unavailable.
2. Run `build_store_msix_windows.bat` from the repository root.
3. The script runs all smoke tests, creates a separate Store PyInstaller build,
   proves the updater module is absent, generates exact-size visual assets,
   creates an unsigned x64 MSIX, and writes a privacy-safe audit ZIP.
4. Upload the `release_audit/ERP_Workbench_store_package_audit_*.zip` for
   review. Do not upload the approximately hundreds-of-megabytes MSIX here.

If a failure occurs only after the Store frozen folder and package layout were
completed, run `build_store_msix_windows.bat --resume-package` after correcting
the packaging-tool problem. The resume mode preserves the completed build and
continues from Windows SDK discovery.

The staging MSIX is deliberately unsigned. Do not submit or distribute it yet.
After its structure is reviewed, create a Store-matching test certificate,
sign the candidate for local installation testing, run Windows App
Certification Kit, and then prepare the Partner Center submission package.

## Local installation test signing

The pre-submission audit of `ERP_Workbench_1.0.0.4_x64.msix` passed the structural
gate: the reserved Store identity, x64 architecture, full-trust entry point,
five visual assets, package metadata, updater exclusion, and minimized Qt
payload were confirmed. Its recorded pre-signing SHA-256 is
`664bd95babf3fa7efdc7dec320f85440619c59fbd33892d4da961cabd0cd0111`.
The absent signature is expected for this staging package.

Microsoft signs an accepted Store MSIX. The following certificate is only for
private local installation testing; it is not a public-release certificate:

1. From a normal PowerShell window in the repository root, run:
   `powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_store_msix_for_local_test.ps1`
2. The script creates or reuses a one-year, non-exportable test key under the
   current user's Personal certificate store. Its subject is checked against
   the exact manifest Publisher. It exports only the public `.cer` and signs a
   separate `-local-test.msix` copy, leaving the unsigned Store candidate
   unchanged.
3. Open PowerShell with **Run as administrator**, then run:
   `powershell -NoProfile -ExecutionPolicy Bypass -File tools\trust_store_local_test_certificate.ps1 -Confirm`
4. Read the trust target and thumbprint shown by PowerShell, then approve only
   if they are the expected ERP Workbench local-test certificate. The script
   imports the public certificate into Local Computer > Trusted People and
   verifies the package signature.
5. Double-click the `-local-test.msix` and complete the private install test.

Never commit or share a PFX/private key. Never submit the `-local-test.msix` to
Partner Center. The unsigned candidate remains the Store-upload candidate;
Microsoft supplies Store signing after certification.

The first local WACK run completed with an overall `WARNING`. Its DPI finding
is addressed by the embedded Per-Monitor-V2 executable manifest, and unused
upstream dataset/example archives are excluded from the next build. Optional
process-launch and blocked-string findings are reviewed in
[`STORE_CERTIFICATION_NOTES.md`](STORE_CERTIFICATION_NOTES.md). A clean rebuild
and complete WACK rerun are required before submission.

The final Store package must also pass clean install, offline scientific
workflow, uninstall, final dependency/security scans, and Qt/PySide source and
license gates. Microsoft Store signing does not replace those obligations.
