# Windows built-artifact audit

This report records the dependency-footprint result for the ERP Workbench 1.0
release candidate. It summarizes filename, size, SHA-256, classification, and
Windows version-resource data produced by `tools/audit_built_distribution.py`.
It does not contain the compiled application itself.

## Accepted audit

| Item | Result |
| --- | ---: |
| Audit created (UTC) | 2026-08-16 11:41:47 |
| Platform | Windows 11, AMD64 |
| Python | CPython 3.13.13 |
| Audit format | 2 |
| Files | 5,354 |
| Total size | 464,409,406 bytes |
| Native binaries | 331 |
| Qt/PySide-classified files | 149 |
| License/notice files | 118 |

The main executable reports `ERP Workbench` version `1.0.0rc4` and company
`H. C. Challa` in its Windows version resources.

## Exclusion result

The accepted manifest contains none of the release-unneeded targets below:

- Qt PDF (`Qt6Pdf*`, `qpdf.dll`)
- Qt QML or Quick (`Qt6Qml*`, `Qt6Quick*`)
- Qt Virtual Keyboard (`Qt6VirtualKeyboard*`, its plugin)
- Tk/Tcl (`_tkinter.pyd`, Tk/Tcl DLLs and data directories)

The previous audits are retained only as diagnostic history:

| Audit | Files | Bytes | Outcome |
| --- | ---: | ---: | --- |
| First | 6,635 | 501,232,782 | Found Qt PDF/QML/Quick/Virtual Keyboard and Tk/Tcl |
| Second | 5,352 | 482,728,532 | Tk/Tcl removed; Qt filter did not match |
| Accepted third | 5,354 | 464,409,406 | All targeted components absent |

The accepted build is 36,823,376 bytes (about 35.1 MiB) and 1,281 files smaller
than the first audited build.

## Remaining Qt/PySide footprint

The remaining Qt libraries are `Qt6Core`, `Qt6Gui`, `Qt6Network`, `Qt6OpenGL`,
`Qt6OpenGLWidgets`, `Qt6Svg`, `Qt6Test`, and `Qt6Widgets`, all version 6.11.1.
The distribution also contains the corresponding PySide bindings, Shiboken,
normal Windows platform and style plugins, image-format and SVG plugins,
network-information and TLS plugins, translations, and `opengl32sw.dll`.

These files remain subject to the exact-source and notice gates in
[`QT_PYSIDE_COMPLIANCE.md`](QT_PYSIDE_COMPLIANCE.md). For 1.0, publishing the
recorded complete Qt 6.11.1 source archive is preferable to relying on a narrow
submodule mapping.

## Decision

The PyInstaller exclusion configuration is accepted and should be frozen for
the next release-preparation stage. A fourth rebuild is not required for this
audit. Before distribution, run the complete application smoke checks on this
accepted build, complete exact-source/notice publication, generate the SBOM,
perform security scans, and build the Store-specific package with its updater
disabled.
