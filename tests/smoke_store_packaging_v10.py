from pathlib import Path
import xml.etree.ElementTree as ET


root = Path(__file__).resolve().parents[1]
manifest_path = root / "erpworkbench.exe.manifest"
spec_path = root / "ERPWorkbench.spec"

tree = ET.parse(manifest_path)
document = tree.getroot()

ns = {
    "asmv3": "urn:schemas-microsoft-com:asm.v3",
    "dpi2005": "http://schemas.microsoft.com/SMI/2005/WindowsSettings",
    "dpi2016": "http://schemas.microsoft.com/SMI/2016/WindowsSettings",
}

execution = document.find(
    ".//asmv3:requestedExecutionLevel",
    ns,
)
assert execution is not None
assert execution.attrib["level"] == "asInvoker"
assert execution.attrib["uiAccess"] == "false"

dpi_legacy = document.find(".//dpi2005:dpiAware", ns)
dpi_modern = document.find(".//dpi2016:dpiAwareness", ns)
assert dpi_legacy is not None and dpi_legacy.text == "true/pm"
assert dpi_modern is not None and dpi_modern.text == "PerMonitorV2, PerMonitor"

spec = spec_path.read_text(encoding="utf-8")
assert 'manifest="erpworkbench.exe.manifest"' in spec
assert "_keep_release_data" in spec
assert "entry for entry in a.datas" in spec
assert "_keep_release_data((entry[1], entry[0]))" in spec
assert 'name.startswith("sklearn.datasets.")' in spec
assert 'name.startswith("pyqtgraph.examples.")' in spec

print("STORE_PACKAGING_V10_SMOKE_TEST_OK")
