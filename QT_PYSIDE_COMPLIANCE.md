# Qt/PySide LGPL compliance checklist

ERP Workbench uses PySide6/Qt under `LGPL-3.0-only`. The application itself is
free software under `AGPL-3.0-only`, and its full corresponding source is made
available. This file turns the remaining Qt obligations into release gates.

## Exact audited version

- PySide6, PySide6_Essentials, PySide6_Addons, and shiboken6: **6.11.1**
- Python: **CPython 3.13.13, Windows x86-64**
- PySide source archive:
  `pyside-setup-everywhere-src-6.11.1.tar.xz`
- PySide source SHA-256:
  `6ffd9835bb0dd2c56f061d62f1616bb1707cfc0202b80e3165d6be087f3965e2`
- Qt complete source archive:
  `qt-everywhere-src-6.11.1.tar.xz`
- Qt complete source SHA-256 published by Qt:
  `252acef8c5ae68074d91cadba2ee4a83465051bbb970dd26e8f0daa0f3904e03`

Official sources:

- <https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/>
- <https://download.qt.io/official_releases/qt/6.11/6.11.1/single/>
- <https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/>

The complete Qt archive is very large. After the final Windows build, map every
bundled Qt DLL/plugin to its Qt source submodule and attach the exact required
6.11.1 submodule archives to the public source release. If that mapping cannot
be demonstrated completely, provide the complete Qt source archive instead.
Do not rely only on an upstream web link for the final release.

## Required release behavior

- Keep PyInstaller in **one-directory** mode.
- Keep Qt/PySide DLLs as separate files; do not statically link, merge, encrypt,
  obfuscate, or pack them into an irreplaceable executable.
- Do not use PyInstaller one-file mode for the public Windows build.
- Ship `THIRD_PARTY_NOTICES.md`, `licenses/LGPL-3.0.txt`,
  `licenses/GPL-3.0.txt`, and the exact upstream notices with the application.
- Preserve every upstream copyright and license notice.
- State prominently in the About dialog that Qt for Python is used under
  LGPL-3.0-only and that recipients may replace/rebuild it.
- Do not impose terms that prohibit modification, reverse engineering needed
  for debugging modifications, or installation of a legitimately modified
  build.

## Final Windows artifact gate

Before either the Store MSIX or direct installer is distributed:

1. Generate a recursive filename and SHA-256 manifest of `dist/ERPWorkbench`.
2. Enumerate all `Qt6*.dll`, PySide/Shiboken binaries, Qt plugins, ICU/OpenSSL or
   other native libraries, fonts, and codecs actually present.
3. Match each shipped item to its license and exact corresponding source.
4. Add any notices missed by the Python package metadata audit.
5. Verify that a clean one-directory build can be rebuilt from the public
   application source with a compatible modified PySide6/Qt build.
6. Document how a Windows user can build and install that modified application.
7. For MSIX, verify that users can install their own rebuilt/signed package or
   unpackaged build without technical or contractual restrictions that defeat
   LGPL rights. Do not submit if this cannot be demonstrated.
8. Publish the required exact source archives with the release and record their
   SHA-256 checksums.

Three built-file audits were performed on 2026-08-16. The first confirmed that
the application uses separate Qt DLLs, but also found unused Qt PDF, QML/Quick,
Virtual Keyboard, and Tk/Tcl components introduced by automatic collection.
The second confirmed removal of Tk/Tcl, but the first Qt filter was too strict
for PyInstaller's binary tuple layout. The third audit, generated at
`2026-08-16T11:41:47Z`, confirmed that the corrected filename-level filter
removed all of the targeted Qt and Tk/Tcl files. It contained 5,354 files and
was 464,409,406 bytes, a reduction of 1,281 files and 36,823,376 bytes from the
first audit. See [`BUILT_ARTIFACT_AUDIT.md`](BUILT_ARTIFACT_AUDIT.md).

The remaining Qt/PySide payload consists of Qt Core, Gui, Network, OpenGL,
OpenGLWidgets, Svg, Test, and Widgets; the matching PySide modules; platform,
image-format, SVG, network-information, style, and TLS plugins; translations;
Shiboken; and the software OpenGL fallback `opengl32sw.dll`. Before public
distribution, preserve the exact PySide 6.11.1 source archive and either:

- attach the complete Qt 6.11.1 source archive recorded above; or
- finish a file-by-file source-module and third-party-notice mapping for the
  smaller set actually shipped.

Using the complete Qt source archive is the safer 1.0 release option because it
avoids an incomplete submodule mapping. The audit inventory is complete, but
publication of the exact source archive(s), final notice verification for
`opengl32sw.dll` and bundled image codecs, and end-user rebuild instructions
remain release gates. No further rebuild is required solely for the successful
component-exclusion check.

The same checklist applies to Store and GitHub distributions. Microsoft signing
does not replace open-source license compliance.
