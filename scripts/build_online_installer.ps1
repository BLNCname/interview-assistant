[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$IsccPath,
    [Parameter(Mandatory = $true)][string]$DistPath,
    [Parameter(Mandatory = $true)][string]$InventoryPath,
    [Parameter(Mandatory = $true)][string]$CollectTocPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '0.1.3',
    [string]$CachePath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$compiler = (Resolve-Path -LiteralPath $IsccPath).Path
$dist = (Resolve-Path -LiteralPath $DistPath).Path
$inventory = (Resolve-Path -LiteralPath $InventoryPath).Path
$collect = (Resolve-Path -LiteralPath $CollectTocPath).Path
$output = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw 'Online installer output must be empty. Use a fresh OutputPath.'
    }
}
New-Item -ItemType Directory -Force -Path $output | Out-Null
if ([string]::IsNullOrWhiteSpace($CachePath)) {
    $CachePath = Join-Path $root 'build\online-installer-cache'
}
$cache = [IO.Path]::GetFullPath($CachePath)
$generated = Join-Path $output 'payload'
$manifest = Join-Path $root 'packaging\stt_model_manifest.json'
$prepare = Join-Path $root 'scripts\prepare_online_payload.py'
$validator = Join-Path $root 'scripts\validate_release_dist.py'

& $python $validator --dist $dist --manifest $manifest --inventory $inventory
if ($LASTEXITCODE -ne 0) { throw 'Portable inventory validation failed.' }
$preparationArgs = @(
    $prepare, '--dist', $dist, '--inventory', $inventory, '--collect', $collect,
    '--lock', (Join-Path $root 'uv.lock'), '--site-packages', (Join-Path $root '.venv\Lib\site-packages'),
    '--model-manifest', $manifest, '--output', $generated
)
& $python @preparationArgs
if ($LASTEXITCODE -ne 0) { throw 'Online payload mapping failed.' }
& $python (Join-Path $root 'scripts\fetch_online_payload.py') `
    --manifest (Join-Path $generated 'online_payload.json') --cache $cache --dist $dist
if ($LASTEXITCODE -ne 0) { throw 'Pinned upstream download verification failed.' }
& $python @preparationArgs --verify-cache $cache
if ($LASTEXITCODE -ne 0) { throw 'Upstream archive member validation failed.' }
$payload = Get-Content -Raw -LiteralPath (Join-Path $generated 'online_payload.json') | ConvertFrom-Json
if (-not $payload.wheel_archives_verified) { throw 'Wheel archives were not verified.' }

$compilerInputs = @(
    'packaging\interview_assistant_online.iss', 'packaging\online_downloads.iss',
    'packaging\online_hardware.iss', 'packaging\installer_gpu_probe.ps1',
    'packaging\online_legacy_cleanup.iss',
    'scripts\prepare_online_payload.py', 'scripts\fetch_online_payload.py',
    'scripts\build_online_installer.ps1', 'uv.lock', 'packaging\stt_model_manifest.json'
)
$sourceHashes = @{}
foreach ($relativePath in $compilerInputs) {
    $sourceHashes[$relativePath] = (Get-FileHash -LiteralPath (Join-Path $root $relativePath) -Algorithm SHA256).Hash
}
$sourceHashes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'installer-source-hashes.json') -Encoding utf8

$arguments = @(
    "/DAppVersion=$Version", ('/DDistPath="' + $dist + '"'),
    ('/DOutputPath="' + $output + '"'),
    ('/DPayloadFilesPath="' + (Join-Path $generated 'payload_files.iss') + '"'),
    ('/DPayloadDataPath="' + (Join-Path $generated 'payload_data.iss') + '"'),
    ('"' + (Join-Path $root 'packaging\interview_assistant_online.iss') + '"')
)
$compile = Start-Process -FilePath $compiler -ArgumentList $arguments -WindowStyle Hidden `
    -Wait -PassThru -RedirectStandardOutput (Join-Path $output 'iscc.log') `
    -RedirectStandardError (Join-Path $output 'iscc-errors.log')
if ($compile.ExitCode -ne 0) { throw 'Inno compilation failed; see iscc-errors.log.' }
foreach ($relativePath in $compilerInputs) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $relativePath) -Algorithm SHA256).Hash -ne $sourceHashes[$relativePath]) {
        throw "Installer source changed during compilation: $relativePath. Build again in a fresh output directory."
    }
}
$setup = Join-Path $output 'Setup.exe'
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'Setup.exe was not produced.' }
if ((Get-Item -LiteralPath $setup).Length -ge 2147483648) { throw 'Setup exceeds GitHub asset limit.' }
if (@(Get-ChildItem -LiteralPath $output -Filter '*.bin').Count -gt 0) {
    throw 'Online Setup unexpectedly produced external disk slices.'
}
$digest = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
"$digest  Setup.exe" | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
Write-Output $setup
