# Privacy policy

Effective date: 16 August 2026

## Summary

Neurophysiology Workbench and its ERP Workbench application are designed for
local, offline scientific analysis. The project does not operate a user-account
service and does not collect, sell, or upload personal information, EEG/ERP
recordings, annotations, protocols, or analysis results.

## Local data processing

The application reads scientific data files that the user selects and writes
results only to locations selected or controlled by the user. Application
settings, review records, and temporary processing data remain on the user's
computer unless the user independently chooses to copy or share them.

Users are responsible for obtaining appropriate permission for the data they
analyse and for protecting identifiable, clinical, or research-participant
information. Sensitive data must not be included in public GitHub issues or
security reports.

## Network access and updates

The application has no advertising, telemetry, usage analytics, crash-report
upload, or project-operated cloud storage.

The GitHub distribution includes an update function that contacts the
configured Neurophysiology Workbench repository through GitHub's HTTPS
services. Metadata-only automatic update checks are enabled by default and can
be disabled in the application settings. The user can also request a check
manually. Downloading or installing an available release requires user
confirmation. Standard connection information, such as an IP address and
request metadata, may therefore be processed by GitHub under GitHub's own
privacy terms. Scientific recordings and analysis results are not transmitted
by this update function.

The Microsoft Store build excludes the GitHub updater. Installation and updates
for that build are handled by Microsoft Store under Microsoft's applicable
privacy terms.

## Third-party software and code signing

The application uses open-source scientific and user-interface libraries that
run locally as part of the application. Their licences and notices are listed
in `THIRD_PARTY_NOTICES.md` and the `licenses` directory.

The release signing service processes build artifacts for code-signing
purposes. It does not receive end-user recordings or analysis results. See the
[code signing policy](CODE_SIGNING_POLICY.md).

## Contact

General questions may be raised through the project's GitHub repository.
Suspected vulnerabilities should be reported privately according to
`SECURITY.md`. Do not include personal, participant, or clinical data in a
public report.
