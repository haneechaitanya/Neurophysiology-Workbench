# Security policy

## Supported release

ERP Workbench has not yet reached its first public release. Until 1.0 is
published, the private release candidate is the only version being prepared
for security review. After 1.0, the latest supported release and the default
branch will receive security fixes.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue. Use the
repository's **Security** tab and select **Report a vulnerability** so the
report can be discussed privately. The maintainer should enable GitHub private
vulnerability reporting before making the repository public.

Include the affected version, operating system, steps to reproduce, observed
impact, and any safe proof-of-concept material. Do not include participant EEG,
clinical information, credentials, or other personal data.

The project will acknowledge a report when practicable, investigate it, and
coordinate a fix and disclosure. No guaranteed response time is promised for
this independently maintained, no-cost scientific project.

## Scope and data safety

ERP Workbench is an offline scientific analysis application. It is not a
medical device and must not be used as the sole basis for diagnosis or patient
care. Security reports may cover the application, installer, update mechanism,
release workflow, dependency chain, or unsafe handling of analysis files.

Security audit archives intentionally contain no EEG recordings, participant
files, environment variables, credentials, usernames, hostnames, or raw secret
values.
