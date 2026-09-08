[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallerPath,

    [string]$InstallPath = "",

    [string]$ReportPath = "",

    [string]$InventoryPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$buildRoot = [IO.Path]::GetFullPath((Join-Path $root "build"))
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
if ([string]::IsNullOrWhiteSpace($InstallPath)) {
    $runName = "installer-smoke-" + [Guid]::NewGuid().ToString("N")
    $InstallPath = Join-Path (Join-Path $buildRoot $runName) "installer-smoke"
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
    throw "The installer smoke path must end in installer-smoke under the workspace build directory."
}
$runRoot = Split-Path -Parent $smoke
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $runRoot "installer-smoke.json"
}
elseif (-not [IO.Path]::IsPathRooted($ReportPath)) {
    $ReportPath = Join-Path $root $ReportPath
}
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw "The installer smoke input does not exist."
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$manifest = Join-Path $root "packaging\stt_model_manifest.json"
if ([string]::IsNullOrWhiteSpace($InventoryPath)) {
    $InventoryPath = Join-Path $root "packaging\dist_inventory.json"
}
elseif (-not [IO.Path]::IsPathRooted($InventoryPath)) {
    $InventoryPath = Join-Path $root $InventoryPath
}
if (-not (Test-Path -LiteralPath $InventoryPath -PathType Leaf)) {
    throw "The installer smoke distribution inventory is missing."
}
$inventory = (Resolve-Path -LiteralPath $InventoryPath).Path
$validator = Join-Path $root "scripts\validate_release_dist.py"
$python = Join-Path $root ".venv\Scripts\python.exe"
foreach ($requiredFile in @($manifest, $inventory, $validator, $python)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "An installer smoke dependency is missing."
    }
}

$installLog = Join-Path $runRoot "installer-smoke-install.log"
$uninstallLog = Join-Path $runRoot "installer-smoke-uninstall.log"
$diagnosticsOutput = Join-Path $runRoot "installed-diagnostics.json"
$missingConfig = Join-Path $runRoot "installed-smoke-missing.yaml"
foreach ($newPath in @(
    $smoke, $installLog, $uninstallLog, $diagnosticsOutput, $missingConfig, $ReportPath
)) {
    if (Test-Path -LiteralPath $newPath) {
        throw "An installer smoke output already exists; choose a new run directory."
    }
}

# A directory junction must not redirect the installer outside the workspace.
$ancestor = $runRoot
while ($ancestor.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
    if (Test-Path -LiteralPath $ancestor) {
        $item = Get-Item -LiteralPath $ancestor -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The installer smoke path must not contain directory links."
        }
    }
    if ($ancestor -eq $buildRoot) { break }
    $ancestor = Split-Path -Parent $ancestor
}

# /DIR does not isolate Inno Setup's AppId-based uninstall registration.
$uninstallKey = "{9CE7901A-56E8-49CB-A8ED-8D5CF4F97C7D}_is1"
foreach ($registryRoot in @(
    "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "Registry::HKEY_CURRENT_USER\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    "Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "Registry::HKEY_LOCAL_MACHINE\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)) {
    if (Test-Path -LiteralPath "$registryRoot\$uninstallKey" -ErrorAction Stop) {
        throw "Interview Assistant is already registered; run installer smoke in a clean Windows environment."
    }
}
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportPath) | Out-Null

$install = Start-Process `
    -FilePath $installer `
    -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NORESTARTAPPLICATIONS",
        "/NOCLOSEAPPLICATIONS",
        "/NOICONS",
        '/TASKS=""',
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
    InventoryPath = $inventory
    InstallPath = $smoke
}
$result | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
$result
