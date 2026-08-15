ERP Workbench — pre-1.0 release hardening patch

Apply this ONLY to the canonical Git repository:
D:\Projects\Electrophysiology\Neurophysiology-Workbench

Copy the files in this ZIP over the repository, preserving the directory structure.

This patch intentionally does NOT:
- change the internal version from 1.0.0rc4
- alter EEG/ERP preprocessing algorithms
- alter ICA fitting/reconstruction
- alter epoching, averaging, or grand averaging calculations
- add branding/icons yet

After applying, run verify_v1_windows.bat before committing.
