# Code signing policy

## Purpose and provider

Official direct-download Windows installers for ERP Workbench will be signed
and timestamped through SignPath before they are presented as recommended
downloads.

**Free code signing provided by SignPath.io, certificate by SignPath
Foundation.**

The project is currently applying for this service. Until approval and build
integration are complete, any unsigned test artifact must be clearly labelled
as unsigned and must not be presented as the recommended public download.
Microsoft Store packages follow Microsoft's certification and Store-signing
process instead.

## Signed-artifact scope and provenance

Signing is limited to ERP Workbench release artifacts built from this public
repository and its committed build scripts. The signing workflow must identify
the source commit or release tag used for the build. Arbitrary local binaries,
uncommitted builds, third-party projects, and unrelated executables are outside
the signing scope.

Release verification includes the Authenticode signature and timestamp,
version metadata, a SHA-256 checksum, automated smoke tests, and the applicable
release audit records. Third-party open-source libraries bundled with the
application retain their own identities and licences.

## Team roles

This is currently an independently maintained project.

- Committer and reviewer: [H. C. Challa](https://github.com/haneechaitanya)
- Signing approver: [H. C. Challa](https://github.com/haneechaitanya)

Contributions from other people must be reviewed before merging. A signing
request must be approved by the signing approver after the release commit,
build result, and audit status have been checked.

## Privacy

ERP Workbench processes user-selected neurophysiology files locally. It has no
account system, advertising, telemetry, analytics, or project-operated cloud
service, and it does not upload EEG/ERP recordings or analysis results.

The GitHub distribution can perform a metadata-only update check through
GitHub Releases. Automatic checks are enabled by default and can be disabled in
the application settings; installer download or installation requires user
confirmation. The Microsoft Store build excludes the GitHub updater and
receives updates through Microsoft Store. The SignPath signing workflow
operates on release build artifacts and does not receive end-user scientific
data.

See the full [privacy policy](PRIVACY.md).
