# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

_datas, _binaries, _hiddenimports = [], [], []

# Collect scientific packages that ERP Workbench actually uses.
# MNE-ICALabel is bundled with ONNX Runtime; PyTorch is intentionally not
# required by this release.
for pkg in ["mne", "mne_icalabel", "sklearn", "scipy", "matplotlib", "onnxruntime"]:
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
]

# Current ERP Workbench stores user protocols outside the installation and no
# longer has a repository-root "protocols" resource folder.
_datas += [("assets", "assets")]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "OpenGL",
        "pyqtgraph.opengl",
    ],
    noarchive=False,
    optimize=0,
)

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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ERPWorkbench",
)
