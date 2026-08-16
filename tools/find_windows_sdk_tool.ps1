param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("makeappx.exe", "signtool.exe", "appcert.exe")]
    [string]$ToolName
)

$roots = @(
    [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::ProgramFilesX86),
    [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::ProgramFiles)
) | Where-Object { $_ } | Select-Object -Unique

$candidates = foreach ($root in $roots) {
    $pattern = Join-Path $root "Windows Kits\10\bin\*\x64\$ToolName"
    Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue
}

$selected = $candidates |
    Sort-Object @{ Expression = {
        try { [version]$_.Directory.Parent.Name } catch { [version]"0.0" }
    } } -Descending |
    Select-Object -First 1

if (-not $selected) {
    $command = Get-Command $ToolName -ErrorAction SilentlyContinue
    if ($command) {
        $selected = Get-Item $command.Source
    }
}

if ($selected) {
    $selected.FullName
    exit 0
}

exit 1
