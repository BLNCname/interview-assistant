[CmdletBinding()]
param(
    [string]$ConfigPath,
    [switch]$FullTests,
    [switch]$SelfTestOnly
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment is missing. Run uv sync --extra dev once in $RepositoryRoot."
}
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $RepositoryRoot "config.yaml"
} elseif (-not [IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $ConfigPath))
}

Push-Location -LiteralPath $RepositoryRoot
try {
    if ($FullTests) {
        & $PythonPath -m pytest --basetemp=.cache/pytest-startup -q
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if ($SelfTestOnly) {
        & $PythonPath main.py --self-test --config $ConfigPath
    } else {
        # main.py performs the same cheap smoke checks before GUI readiness.
        & $PythonPath main.py --config $ConfigPath
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
