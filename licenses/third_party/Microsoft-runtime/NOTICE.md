# Microsoft Windows and Visual C++ runtime components

The Windows distribution contains Microsoft Universal C Runtime/API-set files
and Visual C++ runtime files required by CPython, PySide6/Qt, NumPy/Pandas,
scikit-learn, and other compiled dependencies. These Microsoft components are
not covered by the ERP Workbench AGPL license.

The audited build contained Visual C++ runtime files from several upstream
binary wheels, including versions 14.24, 14.40, 14.44, and 14.51, plus Windows
Universal CRT/API-set files version 10.0.22621.5040. The final artifact manifest
is authoritative because dependency wheels may change before release.

Official terms and redistribution guidance:

- https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/
- https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

Before public distribution, confirm that the selected Store and direct
installer packaging methods satisfy the applicable Microsoft terms. Where
appropriate, prefer an official Microsoft framework/runtime dependency over
shipping unnecessary duplicate runtime files.
