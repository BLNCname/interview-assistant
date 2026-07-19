[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallerPath,

    [string]$InstallPath = "",

    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$buildRoot = [IO.Path]::GetFullPath((Join-Path $root "build"))
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
if ([string]::IsNullOrWhiteSpace($InstallPath)) {
    $InstallPath = Join-Path $buildRoot "installer-smoke"
}
elseif (-not [IO.Path]::IsPathRooted($InstallPath)) {
    $InstallPath = Join-Path $root $InstallPath
}
$smoke = [IO.Path]::GetFullPath($InstallPath)
$buildPrefix = $buildRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + `
    [IO.Path]::DirectorySeparatorChar
if (
    -not $smoke.StartsWith($buildPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $smoke) -ne "installer-smoke"
) {
    throw "The installer smoke path must be the workspace build/installer-smoke directory."
}
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $buildRoot "installer-smoke.json"
}
elseif (-not [IO.Path]::IsPathRooted($ReportPath)) {
    $ReportPath = Join-Path $root $ReportPath
}
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw "The installer smoke input does not exist."
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$manifest = Join-Path $root "packaging\stt_model_manifest.json"
$inventory = Join-Path $root "packaging\dist_inventory.json"
$validator = Join-Path $root "scripts\validate_release_dist.py"
$python = Join-Path $root ".venv\Scripts\python.exe"
foreach ($requiredFile in @($manifest, $inventory, $validator, $python)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "An installer smoke dependency is missing."
    }
}

if (Test-Path -LiteralPath $smoke) {
    Remove-Item -LiteralPath $smoke -Recurse -Force
}
$installLog = Join-Path $buildRoot "installer-smoke-install.log"
$uninstallLog = Join-Path $buildRoot "installer-smoke-uninstall.log"
$diagnosticsOutput = Join-Path $buildRoot "installed-diagnostics.json"
$missingConfig = Join-Path $buildRoot "installed-smoke-missing.yaml"
Remove-Item `
    -LiteralPath $installLog,$uninstallLog,$diagnosticsOutput,$missingConfig `
    -Force `
    -ErrorAction SilentlyContinue

$install = Start-Process `
    -FilePath $installer `
    -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        ('/DIR="' + $smoke + '"'),
        ('/LOG="' + $installLog + '"')
    ) `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($install.ExitCode -ne 0) {
    throw "Installer smoke installation failed."
}

$installedExe = Join-Path $smoke "InterviewAssistant.exe"
$uninstaller = Join-Path $smoke "unins000.exe"
if (
    -not (Test-Path -LiteralPath $installedExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $uninstaller -PathType Leaf)
) {
    throw "Installer smoke did not produce the expected inventory."
}

$validationOutput = @(& $python $validator `
    "--dist" $smoke `
    "--manifest" $manifest `
    "--inventory" $inventory `
    "--installed" 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw "Installed distribution failed inventory and model hash validation."
}

$diagnostics = Start-Process `
    -FilePath $installedExe `
    -ArgumentList @(
        "--diagnostics",
        "--no-gui",
        "--config",
        ('"' + $missingConfig + '"'),
        "--diagnostics-output",
        ('"' + $diagnosticsOutput + '"')
    ) `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($diagnostics.ExitCode -ne 0) {
    throw "Installed diagnostics failed."
}
$diagnosticsReport = Get-Content -Raw -LiteralPath $diagnosticsOutput | ConvertFrom-Json
if ($diagnosticsReport.status -ne "ok" -or -not $diagnosticsReport.frozen) {
    throw "Installed diagnostics did not report a healthy frozen application."
}

$uninstall = Start-Process `
    -FilePath $uninstaller `
    -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        ('/LOG="' + $uninstallLog + '"')
    ) `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($uninstall.ExitCode -ne 0) {
    throw "Installer smoke uninstall failed."
}
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while ((Test-Path -LiteralPath $smoke) -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
}
if (Test-Path -LiteralPath $smoke) {
    throw "Installer smoke uninstall did not remove the installation tree."
}

$result = [PSCustomObject]@{
    Status = "ok"
    InstallExitCode = $install.ExitCode
    DiagnosticsExitCode = $diagnostics.ExitCode
    UninstallExitCode = $uninstall.ExitCode
    Frozen = $diagnosticsReport.frozen
    ModelInventoryAndHashesValidated = $true
    InstallTreeRemoved = $true
}
$result | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
$result
