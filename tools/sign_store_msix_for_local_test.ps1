[CmdletBinding()]
param(
    [string]$PackagePath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot "store\AppxManifest.xml"
$expectedPublisher = "CN=980625EF-3A8E-46A5-9AEC-3E3F8DACB2C8"
$friendlyName = "ERP Workbench local MSIX test certificate"

if (-not $PackagePath) {
    $PackagePath = Join-Path $repoRoot "store_package\ERP_Workbench_1.0.0.0_x64.msix"
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $repoRoot "store_package\ERP_Workbench_1.0.0.0_x64-local-test.msix"
}

$PackagePath = [System.IO.Path]::GetFullPath($PackagePath)
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
    throw "Unsigned staging package not found: $PackagePath"
}
if ($PackagePath -eq $OutputPath) {
    throw "OutputPath must differ from PackagePath so the unsigned Store candidate remains unchanged."
}

[xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw
$publisher = $manifest.Package.Identity.Publisher
if ($publisher -cne $expectedPublisher) {
    throw "Manifest Publisher mismatch. Expected '$expectedPublisher'; found '$publisher'."
}

$now = Get-Date
$certificate = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object {
        $_.Subject -ceq $expectedPublisher -and
        $_.FriendlyName -eq $friendlyName -and
        $_.HasPrivateKey -and
        $_.NotBefore -le $now -and
        $_.NotAfter -gt $now.AddDays(30)
    } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

if (-not $certificate) {
    Write-Host "Creating a non-exportable self-signed certificate for local testing only..."
    $certificate = New-SelfSignedCertificate `
        -Type Custom `
        -Subject $expectedPublisher `
        -FriendlyName $friendlyName `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyAlgorithm RSA `
        -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -KeyUsage DigitalSignature `
        -KeyExportPolicy NonExportable `
        -NotAfter $now.AddYears(1) `
        -TextExtension @(
            "2.5.29.37={text}1.3.6.1.5.5.7.3.3",
            "2.5.29.19={text}"
        )
}

if ($certificate.Subject -cne $expectedPublisher) {
    throw "Certificate subject does not exactly match the Store Publisher."
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$publicCertificatePath = Join-Path $outputDirectory "ERP_Workbench_local_test.cer"
Export-Certificate -Cert $certificate -FilePath $publicCertificatePath -Force | Out-Null

Copy-Item -LiteralPath $PackagePath -Destination $OutputPath -Force

$locator = Join-Path $PSScriptRoot "find_windows_sdk_tool.ps1"
$signTool = & $locator signtool.exe | Select-Object -First 1
if (-not $signTool -or -not (Test-Path -LiteralPath $signTool -PathType Leaf)) {
    throw "SignTool.exe was not found in the installed Windows SDK."
}

Write-Host "Signing the separate local-test copy with SHA-256..."
& $signTool sign /fd SHA256 /sha1 $certificate.Thumbprint $OutputPath
if ($LASTEXITCODE -ne 0) {
    throw "SignTool failed with exit code $LASTEXITCODE."
}

$signature = Get-AuthenticodeSignature -LiteralPath $OutputPath
if (-not $signature.SignerCertificate) {
    throw "The signed package does not expose a signer certificate."
}
if ($signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
    throw "The signed package certificate thumbprint does not match the selected certificate."
}

Write-Host ""
Write-Host "LOCAL-TEST MSIX SIGNED."
Write-Host "Signed copy: $OutputPath"
Write-Host "Public certificate: $publicCertificatePath"
Write-Host "Certificate thumbprint: $($certificate.Thumbprint)"
Write-Host "The original unsigned Store candidate was not changed."
Write-Host "Next: run trust_store_local_test_certificate.ps1 as Administrator."
