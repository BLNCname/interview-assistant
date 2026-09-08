# Called by Inno before any payload download. No network or configuration writes.
$ErrorActionPreference = 'Stop'
$windowsRoot = [Environment]::GetFolderPath('Windows')
$programFiles64 = [Environment]::GetEnvironmentVariable('ProgramW6432')
$candidates = @(
    (Join-Path $windowsRoot 'System32\nvidia-smi.exe'),
    (Join-Path $windowsRoot 'Sysnative\nvidia-smi.exe')
)
if ($programFiles64) {
    $candidates += Join-Path $programFiles64 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'
}
$probeExecutable = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $probeExecutable) { exit 2 }
$process = New-Object System.Diagnostics.Process
$process.StartInfo.FileName = $probeExecutable
$process.StartInfo.Arguments = '--query-gpu=index,name,memory.total,memory.free,compute_cap,driver_version --format=csv,noheader,nounits'
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.CreateNoWindow = $true
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
try {
    if (-not $process.Start()) { exit 3 }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(10000)) {
        $process.Kill()
        $process.WaitForExit(2000) | Out-Null
        exit 4
    }
    if ($process.ExitCode -ne 0) { exit 5 }
    $text = $stdout.GetAwaiter().GetResult()
    if ($text.Length -gt 16384) { exit 6 }
    Write-Output $text.Trim()
} finally {
    $process.Dispose()
}
