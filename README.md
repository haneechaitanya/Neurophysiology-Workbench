# ERP Workbench 1.0 RC3 — almost-final test build

ERP Workbench is a Windows-first desktop ERP analysis application built around **MNE-Python**, **PySide6/Qt** and **PyQtGraph**. The current workflow is:

`EDF/FIF → continuous EEG preprocessing → ICA (BETA, optional) → epoching → epoch review → subject ERP measurement → saved .erpavg → grand average → Excel export`

## Current release-candidate focus

This RC keeps the scientific MNE processing path from the earlier builds while finishing the user-facing workflow before 1.0.0 is frozen. ICA remains explicitly **BETA**. The planned GitHub updater is intentionally **not included yet**.

Highlights in this build:

- Welcome Tour removed for now. A future tutorial can be redesigned as a genuinely interactive/animated guide instead of an opening dialog.
- Settings and Methodology/Readings moved to the application menu rather than occupying workflow tabs.
- Help now explains the basis, purpose, implementation and cautions for each processing stage and includes scientific reading plus relevant MNE documentation links.
- User protocol library in `Documents\ERP Workbench\Protocols` (with a local-app-data fallback on locked-down profiles); no built-in task protocols are exposed.
- Protocols can store default display channels, event grouping/exclusions, epoch/rejection settings and ERP component definitions.
- ICA-fit exclusion reasons are editable directly in the table while start/end times remain read-only.
- ICA component table prioritizes Blink correlation before ICLabel; ICLabel remains a trained-classifier suggestion that must be checked against component morphology and topography.
- ICA source inspection uses one all-components time-domain browser with fixed row scaling while scrolling.
- Component removal is explicit. Post-ICA reconstruction runs in chunks using MNE ICA.apply, reports real percentage progress plus an estimated remaining time, and preserves both pre-ICA and post-ICA processed EEG.
- Horizontal EEG/ICA viewing reuses existing PyQtGraph curve objects and coalesces drag redraws instead of clearing/recreating the entire scene for every mouse movement.
- Remappable waveform shortcuts are routed to the active viewer so the same mapping applies across the workflow.
- Diagnostic terminal is optional; the frozen Windows release configuration uses `console=False`, so it starts hidden.
- `ERP Tools` provides direct access to EEG-specific interpolation/re-reference and the later ERP workflow stages; filtering remains in the continuous-EEG sidebar.
- Multiple independent ERP Workbench windows can be opened for different recordings.

## Scientific invariants

Display polarity, zoom, trace color and sensitivity are visual only. Preprocessing is rebuilt deterministically from the imported Raw rather than cumulatively re-filtering a previously filtered signal. Structural processing remains interpolation → re-reference → ICA. ICA-fit exclusions affect only the temporary fit copy. Difference waves remain explicitly local/difference results and are not silently inserted as real experimental conditions. Grand averaging remains subject-level with equal subject weight.

## Running from source

Use the existing Windows virtual environment/dependencies and launch with the included batch file or:

```bat
python app.py
```

For a final distributable release, first run `verify_v1_windows.bat`, then use the Windows build/installer scripts. The frozen end-user application is intended to bundle Python and its required runtime dependencies so the destination computer does not need a separate Python installation.

## Test status

Backend smoke tests cover import/core preprocessing, annotations, event grouping, protocol exact-stimulus exclusions, epoch cutting/review, peak-to-peak rejection, subject averaging, grand averaging/export, ICA fit exclusions, blink-correlation aid and chunked post-ICA reconstruction. Qt GUI interactions still require visual testing on Windows because the automated execution environment used for this build does not include PySide6.
