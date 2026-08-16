[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [string]$CertificatePath = "",
    [string]$PackagePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$expectedPublisher = "CN=980625EF-3A8E-46A5-9AEC-3E3F8DACB2C8"

if (-not $CertificatePath) {
    $CertificatePath = Join-Path $repoRoot "store_package\ERP_Workbench_local_test.cer"
}
if (-not $PackagePath) {
    $PackagePath = Join-Path $repoRoot "store_package\ERP_Workbench_1.0.0.0_x64-local-test.msix"
}

$CertificatePath = [System.IO.Path]::GetFullPath($CertificatePath)
$PackagePath = [System.IO.Path]::GetFullPath($PackagePath)

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Open PowerShell with Run as administrator, then run this script again."
}
if (-not (Test-Path -LiteralPath $CertificatePath -PathType Leaf)) {
    throw "Public certificate not found: $CertificatePath"
}
if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
    throw "Signed local-test package not found: $PackagePath"
}

$certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath)
if ($certificate.Subject -cne $expectedPublisher) {
    throw "Certificate subject mismatch. Expected '$expectedPublisher'; found '$($certificate.Subject)'."
}

$signature = Get-AuthenticodeSignature -LiteralPath $PackagePath
if (-not $signature.SignerCertificate) {
    throw "The local-test package is not signed."
}
if ($signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
    throw "The public certificate does not match the certificate that signed the package."
}

$target = "Local Computer\Trusted People"
if ($PSCmdlet.ShouldProcess($target, "Trust ERP Workbench local-test certificate $($certificate.Thumbprint)")) {
    Import-Certificate `
        -FilePath $CertificatePath `
        -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" | Out-Null
}

$locator = Join-Path $PSScriptRoot "find_windows_sdk_tool.ps1"
$signTool = & $locator signtool.exe | Select-Object -First 1
if (-not $signTool -or -not (Test-Path -LiteralPath $signTool -PathType Leaf)) {
    throw "SignTool.exe was not found in the installed Windows SDK."
}

& $signTool verify /pa /v $PackagePath
if ($LASTEXITCODE -ne 0) {
    throw "Signature verification failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "LOCAL-TEST CERTIFICATE TRUSTED AND SIGNATURE VERIFIED."
Write-Host "Double-click the local-test MSIX to install it."
Write-Host "Do not distribute this self-signed test package or submit it to Partner Center."
