# Neurophysiology Workbench

Neurophysiology Workbench is an open scientific-software project intended to
grow into a transparent, modular workstation for neurophysiological analysis.

The first public release is **ERP Workbench 1.0**, a Windows-first desktop
EEG/ERP analysis application built on MNE-Python, PySide6/Qt, and PyQtGraph.
Planned future modules include spectral/PSD analysis, MEG, source analysis, and
multimodal integration such as EEG/MEG with fMRI. Those planned modules are not
presented as completed features of version 1.0.

The current workflow covers:

EDF/FIF → continuous EEG preprocessing → ICA (BETA, optional) → epoching →
epoch review → subject ERP measurement → saved subject averages →
grand averaging → Excel export.

## Project status

ERP Workbench 1.0 is currently in final pre-release testing.

ICA remains explicitly BETA while its methodology and user workflow undergo
additional validation.

## Author and development disclosure

Neurophysiology Workbench and its ERP Workbench application were conceived and
scientifically directed by **H. C. Challa**
([ORCID 0009-0009-3546-0027](https://orcid.org/0009-0009-3546-0027)). Significant
parts of its implementation, tests, and documentation were developed with
generative-AI assistance under the author's direction and iterative testing.
See [AUTHORS.md](AUTHORS.md) and [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for the
full attribution and development disclosure.

## License

Neurophysiology Workbench, including ERP Workbench, is free software licensed
under the **GNU Affero General Public License, version 3 only**
(`AGPL-3.0-only`). This protects every recipient's freedom to use, study,
modify, and redistribute the software and requires corresponding source
availability for redistributed versions and qualifying modified network
deployments. See [LICENSE](LICENSE) for the complete terms.

Third-party components retain their own copyright and license terms. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the bundled
[`licenses`](licenses) directory. Qt/PySide redistribution requirements and the
remaining exact-source gates are recorded in
[QT_PYSIDE_COMPLIANCE.md](QT_PYSIDE_COMPLIANCE.md); the accepted Windows
dependency footprint is summarized in
[BUILT_ARTIFACT_AUDIT.md](BUILT_ARTIFACT_AUDIT.md).

The dedicated Microsoft Store build, reserved package identity, minimal
capabilities, and updater exclusion are documented in
[STORE_PACKAGING.md](STORE_PACKAGING.md).
