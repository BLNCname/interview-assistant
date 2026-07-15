param(
    [switch]$SkipTests,
    [switch]$VerifyCuda,
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$spec = Join-Path $root "packaging\interview_assistant.spec"
$distRoot = Join-Path $root "dist"
$workRoot = Join-Path $root "build\pyinstaller"
$executable = Join-Path $distRoot "InterviewAssistant\InterviewAssistant.exe"
$diagnosticsOutput = Join-Path $workRoot "packaged-diagnostics.json"
$smokeConfig = [IO.Path]::GetFullPath(
    (Join-Path $workRoot "packaged-diagnostics-missing-config.yaml")
)

if ($env:OS -ne "Windows_NT") {
    throw "The Interview Assistant package can only be built on Windows."
}
$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | `
    Select-Object -First 1
if ($null -eq $uv) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/."
}

Push-Location $root
try {
    & $uv.Source lock --check
    if ($LASTEXITCODE -ne 0) {
        throw "uv.lock is missing or out of date; regenerate and review it before building."
    }

    & $uv.Source sync --extra dev --frozen
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen uv sync failed; regenerate and review uv.lock before building."
    }

    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "uv did not create the project .venv Python runtime."
    }

    $commitEpoch = (& git -C $root show -s --format=%ct HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $commitEpoch -notmatch "^\d+$") {
        throw "Unable to derive SOURCE_DATE_EPOCH from the current Git commit."
    }
    $env:SOURCE_DATE_EPOCH = $commitEpoch
    $env:PYTHONHASHSEED = "0"
    $env:PYTHONUTF8 = "1"

    if (-not $SkipTests) {
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed; package was not built."
        }
    }

    # Equivalent reproducible invocation: python -m PyInstaller ...
    & $python -m PyInstaller `
        --clean `
        --noconfirm `
        --distpath $distRoot `
        --workpath $workRoot `
        $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed."
    }
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "PyInstaller did not produce InterviewAssistant.exe."
    }

    New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
    $resolvedWorkRoot = [IO.Path]::GetFullPath($workRoot)
    if ([IO.Path]::GetDirectoryName($smokeConfig) -ne $resolvedWorkRoot) {
        throw "Refusing to prepare a diagnostics config outside the build work directory."
    }
    Remove-Item -LiteralPath $smokeConfig -Force -ErrorAction SilentlyContinue
    $quotedDiagnosticsOutput = '"' + $diagnosticsOutput + '"'
    $quotedSmokeConfig = '"' + $smokeConfig + '"'
    $process = Start-Process `
        -FilePath $executable `
        -ArgumentList @(
            "--diagnostics",
            "--no-gui",
            "--config",
            $quotedSmokeConfig,
            "--diagnostics-output",
            $quotedDiagnosticsOutput
        ) `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Packaged diagnostics failed with exit code $($process.ExitCode)."
    }
    $diagnostics = Get-Content -Raw -LiteralPath $diagnosticsOutput | ConvertFrom-Json
    if (
        $diagnostics.status -ne "ok" -or
        -not $diagnostics.frozen -or
        $diagnostics.config -ne "missing"
    ) {
        throw "Packaged diagnostics did not confirm a healthy frozen runtime."
    }

    if ($VerifyCuda) {
        if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
            throw "-VerifyCuda requires -ConfigPath with the production STT model."
        }
        $resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
        & $python scripts\verify_cuda.py --config $resolvedConfig
        if ($LASTEXITCODE -ne 0) {
            throw "CUDA verification failed."
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Built and verified: $executable"
