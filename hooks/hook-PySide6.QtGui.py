"""Collect only Qt GUI plugins used by ERP Workbench.

PyInstaller's standard QtGui hook intentionally collects every available image
and input-context plugin. The PySide wheel therefore pulls in optional Qt PDF,
QML, Quick, and Virtual Keyboard libraries that this widgets-only application
does not use. Filtering the two triggering plugins here keeps those unrelated
modules out of the release without altering Qt's required platform, image,
icon, OpenGL, or TLS support.
"""

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

_EXCLUDED_PLUGIN_FILENAMES = {
    "qpdf.dll",
    "qtvirtualkeyboardplugin.dll",
}


def _is_required_binary(entry) -> bool:
    filenames = {
        str(part).replace("\\", "/").lower().rsplit("/", 1)[-1]
        for part in entry
    }
    return not bool(filenames & _EXCLUDED_PLUGIN_FILENAMES)


binaries = [entry for entry in binaries if _is_required_binary(entry)]
