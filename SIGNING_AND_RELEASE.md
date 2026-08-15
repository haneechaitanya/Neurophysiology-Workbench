# Windows signing and release

The build scripts create a self-contained Windows application and, when Inno Setup 6 is installed on the build PC, `release/ERP_Workbench_1.0_Setup.exe`. End users do not need Python, MNE, pip, or a development environment.

## Before distributing publicly

1. Run `verify_v1_windows.bat`.
2. Run `build_v1_installer_windows.bat`.
3. Test the installer on a separate Windows 10/11 x64 machine or Windows Sandbox.
4. If you have a trusted Authenticode code-signing certificate, sign the application executable and final installer with Microsoft SignTool and a trusted timestamp service.
5. Re-test the signed installer, uninstall, EDF/FIF loading, annotation attachment, preprocessing, ICA BETA, epoching, subject-average save/open, grand-average loading, and Excel export.

A code-signing certificate is deliberately **not** bundled or fabricated. An unsigned/new executable may still show Windows SmartScreen reputation warnings. Signing proves publisher/integrity; reputation is controlled by Windows/Microsoft.
