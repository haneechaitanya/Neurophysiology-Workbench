# Microsoft Store certification notes

These notes record local Windows App Certification Kit (WACK) findings for the
Store-specific ERP Workbench package. They are not a substitute for the final
Partner Center certification report.

## First local WACK run: package 1.0.0.4

The local test on 2026-08-16 used WACK 10.0.28000.2526 on Windows 11 and
completed without a partial run. The overall XML result was `WARNING`, not a
clean `PASS`.

### Store version correction

Partner Center does not accept nonzero MSIX revision numbers. The first upload
was therefore correctly rejected because it used `1.0.0.4`. The corrected
candidate uses `1.0.0.0`; this change is packaging metadata only and requires
a fresh build and package validation before submission.

The resume path now refreshes `store_build/layout/AppxManifest.xml` from the
current source manifest before repacking. The package audit also fails if the
manifest revision is nonzero or differs from the build's expected version.

Partner Center also requires both manifest display-name fields to use a name
reserved for this product. They now use `Neurophysiology Workbench`; the audit
checks both the package Properties name and the application VisualElements
name. The executable/module name remains `ERPWorkbench`.

The package passed installation/signing, run-level, application-count,
manifest, registry, enterprise-feature, resource-package, banned-file,
private-signing-key, branding, debug-configuration, capability, metadata, and
processor-architecture checks.

Three findings required review:

1. **DPI awareness — warning, non-optional.** The first executable did not
   contain an explicit DPI-awareness declaration. The release manifest now
   declares Per-Monitor-V2 awareness with a Per-Monitor fallback and `asInvoker`
   execution. This must be confirmed by a rebuilt-package WACK run.
2. **Archive files — optional test.** WACK listed 95 compressed upstream data
   files. Eighty-two were scikit-learn datasets/tests, nine were MNE MEG helmet
   files, and the remainder were example/time-zone/scientific-data archives.
   None was a nested executable. The Store build now removes these unused ERP
   1.0 datasets and examples. The filter is applied again to PyInstaller's final
   Analysis data table because package hooks can otherwise reintroduce their
   own datasets. Scientific user input such as `.fif.gz` remains supported
   because this filter applies only to files bundled inside the app.
3. **Blocked executables — optional test.** WACK found standard process-launch
   API imports in the PyInstaller bootloader, Python runtime, and Qt platform
   libraries. It also reported case-insensitive byte-string matches such as
   `cmd`, `reg`, `cDB`, and `dNx` inside libraries, Python scientific modules,
   style/data files, and neural-network model weights. These strings do not by
   themselves demonstrate process launch. The Store build independently proves
   that `erpworkbench.updater` is absent, and the application exposes no update
   UI in Help or Settings. Generic Qt/Python process APIs may remain because
   this is a packaged full-trust desktop application; remaining messages must
   be retained as certification-review evidence rather than silently ignored.

## Required re-test

Rebuild the unsigned MSIX, repeat the structural audit, create a new signed
local-test copy, uninstall the earlier same-version test package, install the
replacement, and rerun every applicable WACK test. The final report should be
retained with the release audit. Any remaining warning or optional failure must
be reviewed against the exact rebuilt package before Partner Center submission.
