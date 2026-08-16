# Third-party software notices

ERP Workbench 1.0 is distributed under `AGPL-3.0-only`. Third-party software
listed here remains under its own copyright and license terms. Nothing in the
project's AGPL license replaces or narrows those terms.

This inventory was generated from the private Windows release environment on
2026-08-16 (Windows 11, 64-bit CPython 3.13.13). `pip check` reported no broken
requirements. License files collected from the installed distributions and
exact source releases are in [`licenses/third_party`](licenses/third_party).

## Runtime components

These packages were reachable from the release application's dependency roots.
The list has been compared with the accepted PyInstaller
`dist/ERPWorkbench` manifest summarized in
[`BUILT_ARTIFACT_AUDIT.md`](BUILT_ARTIFACT_AUDIT.md). A package listed here may
still be absent from the binary, while native libraries and data introduced by
hooks remain governed by their own notices and the final source-publication
gates.

| Component | Version | License reported by distribution metadata |
| --- | ---: | --- |
| CPython | 3.13.13 | Python Software Foundation License Version 2 and incorporated notices |
| certifi | 2026.7.22 | MPL-2.0 |
| charset-normalizer | 3.5.1 | MIT |
| colorama | 0.4.6 | BSD |
| contourpy | 1.3.3 | BSD-3-Clause |
| cycler | 0.12.1 | BSD-3-Clause |
| decorator | 5.3.1 | BSD-2-Clause |
| et_xmlfile | 2.0.0 | MIT; includes Python license notice |
| flatbuffers | 25.12.19 | Apache-2.0 |
| fonttools | 4.63.0 | MIT plus bundled-component notices |
| idna | 3.18 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| joblib | 1.5.3 | BSD-3-Clause |
| kiwisolver | 1.5.0 | BSD-3-Clause |
| lazy-loader | 0.5 | BSD-3-Clause |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| matplotlib | 3.11.1 | Matplotlib license plus bundled font notices |
| mne | 1.12.1 | BSD-3-Clause |
| mne-icalabel | 0.9.0 | BSD-3-Clause |
| narwhals | 2.24.0 | MIT |
| numpy | 2.5.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; see bundled notices |
| onnxruntime | 1.28.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.5 | BSD-3-Clause |
| pillow | 12.3.0 | MIT-CMU |
| platformdirs | 4.11.3 | MIT |
| pooch | 1.9.0 | BSD-3-Clause |
| protobuf | 7.35.1 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| pyparsing | 3.3.2 | MIT |
| pyqtgraph | 0.14.0 | MIT |
| PySide6 | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| PySide6_Addons | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| PySide6_Essentials | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| python-dateutil | 2.9.0.post0 | BSD-3-Clause OR Apache-2.0 |
| requests | 2.34.2 | Apache-2.0 |
| scikit-learn | 1.9.0 | BSD-3-Clause |
| scipy | 1.18.0 | BSD and bundled-component notices |
| shiboken6 | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| six | 1.17.0 | MIT |
| threadpoolctl | 3.6.0 | BSD-3-Clause |
| tqdm | 4.70.0 | MPL-2.0 AND MIT; see exact distribution license |
| tzdata | 2026.3 | Apache-2.0 |
| urllib3 | 2.7.0 | MIT |

### Native components supplied with CPython 3.13.13

The Windows artifact audit found native libraries supplied by the official
CPython build. CPython's version-pinned build configuration identifies the
corresponding source releases below. Their exact notices are included under
`licenses/third_party/CPython-externals`.

| Component | Version/revision | License |
| --- | ---: | --- |
| bzip2/libbzip2 | 1.0.8 | bzip2 license |
| Expat | 2.7.5 | MIT |
| HACL* hash implementations | `bb3d0dc8d9d15a5cd51094d5b69e70aa09005ff0` | MIT |
| libffi | 3.4.4 | MIT |
| libmpdec | 4.0.0 | BSD-2-Clause-style |
| OpenSSL | 3.0.19 | Apache-2.0 |
| SQLite | 3.50.4 | Public domain |
| XZ Utils/liblzma | 5.2.5 | Public domain for liblzma; see exact COPYING file |
| zlib | 1.3.1 | Zlib |

The optimized release build excludes Tk/Tcl because ERP Workbench uses QtAgg,
not Tk. Microsoft Universal CRT and Visual C++ runtime files are also present;
their redistribution remains subject to Microsoft's applicable runtime terms.

## Build-only components

These were installed in the build environment but were not reachable from the
runtime dependency roots. They are not automatically treated as shipped
components. PyInstaller is nevertheless acknowledged because its bootloader is
part of the generated executable.

| Component | Version | Role | License reported by distribution metadata |
| --- | ---: | --- | --- |
| altgraph | 0.17.5 | PyInstaller dependency | MIT |
| pefile | 2024.8.26 | PyInstaller dependency | MIT |
| pip | 26.2.1 | environment tooling | MIT |
| pyinstaller | 6.22.1 | packager; bootloader shipped | GPL-2.0-or-later with the PyInstaller bootloader exception |
| pyinstaller-hooks-contrib | 2026.6 | build hooks | Apache-2.0/GPL classifiers; see included license |
| pywin32-ctypes | 0.2.3 | PyInstaller dependency | BSD-3-Clause |
| setuptools | 84.0.0 | build tooling | MIT and bundled notices |

## Qt for Python notice

ERP Workbench uses Qt for Python (PySide6) 6.11.1 under the GNU Lesser General
Public License, version 3 only. Qt and PySide are not authored by H. C. Challa.
They are dynamically loaded as separate libraries in the one-directory Windows
build. Recipients may study, modify, replace, and rebuild those libraries under
their applicable licenses. The application source and build material are
provided to permit recombination with a compatible modified Qt/PySide build.

Copies of GPL-3.0, LGPL-3.0, and MPL-2.0 are in [`licenses`](licenses). Exact source and
installation-information requirements are tracked in
[`QT_PYSIDE_COMPLIANCE.md`](QT_PYSIDE_COMPLIANCE.md). The Store package must not
be submitted until that checklist is complete. The built-file inventory and
component-exclusion check are complete; exact-source publication, final native
notice verification, rebuild instructions, SBOM, and security checks remain
open.

## Source and notices

Project source: <https://github.com/haneechaitanya/Neurophysiology-Workbench>

The exact license copies are retained in the per-component directories. Where
an installed wheel omitted a license file, the license was recovered from the
exact source distribution or the canonical license named by that distribution's
metadata. The public release must ship
this document and the complete `licenses` directory both with the installed
application and with the source archive.

This document records the project's good-faith dependency audit; it is not a
substitute for the license texts and is not legal advice.
