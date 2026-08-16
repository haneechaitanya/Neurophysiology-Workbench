# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

_store_build = os.environ.get("ERP_WORKBENCH_STORE_BUILD", "").strip() == "1"
_collection_name = "ERPWorkbenchStore" if _store_build else "ERPWorkbench"
_runtime_hooks = ["hooks/runtime_store.py"] if _store_build else []

_datas, _binaries, _hiddenimports = [], [], []


def _keep_release_data(entry):
    """Discard upstream test/example archives that are not runtime inputs."""
    source = str(entry[0]).replace("\\", "/").lower()
    destination = str(entry[1]).replace("\\", "/").lower()
    combined = f"{source}/{destination}"

    if source.endswith((".tar.gz", ".csv.gz", ".json.gz", ".arff.gz", ".ima.gz")):
        return False
    if source.endswith(".fif.gz") and "/mne/data/" in source:
        return False
    if any(
        marker in combined
        for marker in (
            "/sklearn/datasets/",
            "/sklearn/tests/",
            "/pyqtgraph/examples/",
            "/matplotlib/mpl-data/sample_data/",
            "/mne/data/helmets/",
        )
    ):
        return False
    return True

# Collect scientific packages that ERP Workbench actually uses.
# MNE-ICALabel is bundled with ONNX Runtime; PyTorch is intentionally not
# required by this release.
for pkg in ["mne", "mne_icalabel", "sklearn", "scipy", "onnxruntime"]:
    try:
        d, b, h = collect_all(pkg)
        _datas += d
        _binaries += b
        _hiddenimports += h
    except Exception:
        pass

# PyQtGraph is used for 2-D GraphicsView plots only.  collect_all() also finds
# its optional 3-D OpenGL package, so filter that optional branch out.
try:
    d, b, h = collect_all("pyqtgraph")
    _datas += d
    _binaries += b
    _hiddenimports += [
        name for name in h
        if name != "pyqtgraph.opengl"
        and not name.startswith("pyqtgraph.opengl.")
        and name != "OpenGL"
        and not name.startswith("OpenGL.")
    ]
except Exception:
    pass

# Remove optional Torch/PyOpenGL modules that may have been discovered through
# package metadata.  ICLabel will use the bundled ONNX Runtime backend.
_hiddenimports = [
    name for name in _hiddenimports
    if name not in {"torch", "torchvision", "torchaudio", "OpenGL"}
    and not name.startswith(("torch.", "torchvision.", "torchaudio.", "OpenGL."))
    and name != "pyqtgraph.examples"
    and not name.startswith("pyqtgraph.examples.")
    and name != "sklearn.datasets"
    and not name.startswith("sklearn.datasets.")
    and name != "sklearn.tests"
    and not name.startswith("sklearn.tests.")
]

_datas = [entry for entry in _datas if _keep_release_data(entry)]

# Current ERP Workbench stores user protocols outside the installation and no
# longer has a repository-root "protocols" resource folder. Legal and credit
# material is deliberately installed with every binary distribution.
_datas += [
    ("assets", "assets"),
    ("LICENSE", "."),
    ("COPYRIGHT", "."),
    ("AUTHORS.md", "."),
    ("AI_ASSISTANCE.md", "."),
    ("CITATION.cff", "."),
    ("THIRD_PARTY_NOTICES.md", "."),
    ("QT_PYSIDE_COMPLIANCE.md", "."),
    ("BUILT_ARTIFACT_AUDIT.md", "."),
    ("SECURITY.md", "."),
    ("SECURITY_AUDIT_REVIEW.md", "."),
    ("licenses", "licenses"),
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=["hooks"],
    hooksconfig={
        # ERP Workbench embeds Matplotlib in PySide6. Do not collect Tk or the
        # other optional GUI backends.
        "matplotlib": {"backends": ["QtAgg"]},
    },
    runtime_hooks=_runtime_hooks,
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "OpenGL",
        "pyqtgraph.opengl",
        "tkinter",
        "_tkinter",
        "matplotlib.backends.backend_tkagg",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtVirtualKeyboard",
        *(["erpworkbench.updater"] if _store_build else []),
    ],
    noarchive=False,
    optimize=0,
)

# Package hooks can add data after the initial collect_all() results are
# prepared. Apply the same release-data policy to Analysis' final TOC so those
# hooks cannot reintroduce test datasets and example archives.
a.datas = [
    entry for entry in a.datas
    if _keep_release_data((entry[1], entry[0]))
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ERPWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/ERPWorkbench.ico",
    version="version_info.txt",
    manifest="erpworkbench.exe.manifest",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=_collection_name,
)
