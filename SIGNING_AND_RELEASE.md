# Security, signing, and first public release

Neurophysiology Workbench is the umbrella project. The first public product is
ERP Workbench 1.0. Planned PSD, MEG, source-analysis, and multimodal modules are
outside the 1.0 release unless explicitly implemented and validated later.

## Release guarantees and limits

The Windows application must remain fully usable offline. End users must not
need Python, MNE, pip, internet access, or a development environment. Update
checks are optional, user-initiated or user-confirmed, and must never silently
install software.

Code signing proves publisher identity and file integrity. Antivirus verdicts
and Microsoft Defender SmartScreen reputation are separate systems, so no
direct-download executable can be promised never to show a warning. The release
process reduces that risk but must not describe it as impossible.

## Distribution channels

### 1. Microsoft Store package — primary first-release route

Build a dedicated MSIX package for Microsoft Store submission. Microsoft signs
accepted Store MSIX/AppX packages. The Store build must not use the GitHub
self-updater; Store updates are handled through the Store. All analysis features
must continue working offline after installation.

### 2. GitHub source and direct installer

Publish the audited source, documentation, checksums, SBOM, and release notes on
GitHub. Direct Windows installers must be signed and timestamped before they are
presented as the recommended GitHub download. SignPath Foundation is the
preferred no-cost signing route after the project satisfies its public,
open-source, maintained, and released-project eligibility requirements.

An unsigned installer may be used privately for controlled testing, but it is
not the preferred public download.

## Mandatory gates before 1.0

1. Freeze an audited release-candidate commit and tag candidate.
2. Confirm AGPL licensing and authorship metadata.
3. Lock exact dependency versions from the verified Windows build environment.
4. Audit every bundled dependency and include required license texts/notices.
5. Generate a software bill of materials (SBOM).
6. Scan source, dependencies, and built artifacts for secrets, vulnerabilities,
   and malware/false-positive signals.
7. Build from a clean Windows environment without UPX, obfuscation, or an
   embedded development environment.
8. Run automated smoke tests and the complete manual scientific workflow test.
9. Test clean install, offline launch/use, uninstall, and user-confirmed update
   behavior on clean Windows 10 and Windows 11 environments.
10. Create and validate the Store-specific MSIX with the GitHub updater disabled.
11. Submit the MSIX to Microsoft Store certification.
12. Make the repository public with the audited source, documentation, security
    policy, citation metadata, and source release.
13. Publish the Store listing as the primary trusted installation route.
14. Apply to SignPath Foundation and integrate signing into the reproducible
    GitHub build workflow.
15. Publish the signed and timestamped direct installer with SHA-256 checksums
    after signature verification and clean-install retesting.

## Collecting the private dependency inventory

Before the dependency lock and third-party notices are finalized, run
`collect_release_audit_windows.bat` from the project folder. It uses the local
`.venv` and creates a ZIP under `release_audit`. The collector excludes the
virtual environment itself, usernames, hostnames, environment variables,
credentials, participant data, EEG files, and analysis exports. The generated
ZIP is a private audit input and is not committed to the public repository.

## Current build characteristics

The PyInstaller configuration uses a one-directory build and disables UPX. The
Inno Setup installer is per-user by default. These choices are retained because
they reduce unnecessary privilege and packing behavior. They do not replace
signing, dependency auditing, or clean-machine testing.
