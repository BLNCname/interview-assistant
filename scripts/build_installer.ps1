[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$IsccPath,

    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._+-]*$")]
    [string]$Version = "0.1.1",

    [string]$DistPath = "",

    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$issPath = Join-Path $root "packaging\interview_assistant.iss"
$manifestPath = Join-Path $root "packaging\stt_model_manifest.json"
$distInventoryPath = Join-Path $root "packaging\dist_inventory.json"
$distValidatorPath = Join-Path $root "scripts\validate_release_dist.py"
$releasePython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw "The supplied Inno Setup compiler does not exist."
}
$resolvedIscc = (Resolve-Path -LiteralPath $IsccPath).Path
if (-not (Test-Path -LiteralPath $issPath -PathType Leaf)) {
    throw "The Inno Setup definition is missing."
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "The tracked STT model manifest is missing."
}
if (-not (Test-Path -LiteralPath $distInventoryPath -PathType Leaf)) {
    throw "The tracked distribution inventory is missing."
}
if (-not (Test-Path -LiteralPath $distValidatorPath -PathType Leaf)) {
    throw "The release distribution validator is missing."
}
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    $pythonCommand = Get-Command python.exe -CommandType Application `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $pythonCommand) {
        throw "Python is required to validate the release distribution."
    }
    $releasePython = $pythonCommand.Source
}

if ([string]::IsNullOrWhiteSpace($DistPath)) {
    $DistPath = Join-Path $root "dist\InterviewAssistant"
}
elseif (-not [IO.Path]::IsPathRooted($DistPath)) {
    $DistPath = Join-Path $root $DistPath
}
if (-not (Test-Path -LiteralPath $DistPath -PathType Container)) {
    throw "The PyInstaller onedir distribution does not exist."
}
$resolvedDist = (Resolve-Path -LiteralPath $DistPath).Path
$applicationExecutable = Join-Path $resolvedDist "InterviewAssistant.exe"
if (-not (Test-Path -LiteralPath $applicationExecutable -PathType Leaf)) {
    throw "The PyInstaller distribution is missing InterviewAssistant.exe."
}

$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if (
    $manifest.schema_version -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$manifest.bundle_subdirectory) -or
    @($manifest.files).Count -eq 0
) {
    throw "The tracked STT model manifest is invalid."
}
$bundleRelativePath = ([string]$manifest.bundle_subdirectory).Replace(
    "/",
    [IO.Path]::DirectorySeparatorChar
)
if (
    [IO.Path]::IsPathRooted($bundleRelativePath) -or
    $bundleRelativePath -match "(^|[\\/])\.\.([\\/]|$)"
) {
    throw "The tracked STT bundle path is unsafe."
}
$modelBundle = Join-Path (Join-Path $resolvedDist "_internal") $bundleRelativePath
if (-not (Test-Path -LiteralPath $modelBundle -PathType Container)) {
    throw "The PyInstaller distribution is missing the bundled STT model."
}
$seenModelFiles = @{}
foreach ($entry in @($manifest.files)) {
    $relativeFile = [string]$entry.path
    if (
        [string]::IsNullOrWhiteSpace($relativeFile) -or
        $relativeFile -ne [IO.Path]::GetFileName($relativeFile) -or
        $seenModelFiles.ContainsKey($relativeFile.ToLowerInvariant())
    ) {
        throw "The tracked STT model manifest contains an unsafe or duplicate file."
    }
    $seenModelFiles[$relativeFile.ToLowerInvariant()] = $true
    $bundledFile = Join-Path $modelBundle $relativeFile
    if (-not (Test-Path -LiteralPath $bundledFile -PathType Leaf)) {
        throw "The PyInstaller distribution has an incomplete STT model bundle."
    }
}

$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $validationOutput = @(& $releasePython $distValidatorPath `
        "--dist" $resolvedDist `
        "--manifest" $manifestPath `
        "--inventory" $distInventoryPath 2>&1)
    $validationExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($validationExitCode -ne 0) {
    throw "The PyInstaller distribution failed release validation."
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root "dist\installer"
}
elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $root $OutputPath
}
New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $OutputPath).Path

$versionDefine = "/DAppVersion=$Version"
$distDefine = "/DDistPath=`"$resolvedDist`""
$outputDefine = "/DOutputPath=`"$resolvedOutput`""
$issArgument = "`"$issPath`""
$process = Start-Process `
    -FilePath $resolvedIscc `
    -ArgumentList @($versionDefine, $distDefine, $outputDefine, $issArgument) `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($process.ExitCode -ne 0) {
    throw "Inno Setup failed with exit code $($process.ExitCode)."
}

$installerPath = Join-Path $resolvedOutput "InterviewAssistant-Setup-$Version-win64.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Inno Setup did not produce the expected versioned installer."
}
$resolvedInstaller = (Resolve-Path -LiteralPath $installerPath).Path
Write-Output $resolvedInstaller
