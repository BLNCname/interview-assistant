[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SttModelPath,

    [string]$OutputPath = "",

    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._+-]*$")]
    [string]$Version = "0.1.0",

    [string]$RepositoryPath = (Join-Path $PSScriptRoot "..")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $effectiveArguments = @("-c", "core.longpaths=true") + $Arguments
        $commandOutput = @(& $GitPath @effectiveArguments 2>&1)
        $gitExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($gitExitCode -ne 0) {
        throw "A required Git operation failed."
    }
    return $commandOutput
}

function Assert-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $separator = [IO.Path]::DirectorySeparatorChar
    $normalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd($separator)
    $normalizedCandidate = [IO.Path]::GetFullPath($Candidate)
    $rootPrefix = $normalizedRoot + $separator
    if (-not $normalizedCandidate.StartsWith(
        $rootPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to modify a path outside the script-owned staging root."
    }
}

$git = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue | `
    Select-Object -First 1
if ($null -eq $git) {
    throw "Git is required to create a standalone editable source archive."
}
$tar = Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue | `
    Select-Object -First 1
if ($null -eq $tar) {
    throw "Windows tar.exe is required to create the ZIP archive."
}

if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
    throw "The source repository does not exist."
}
$resolvedRepositoryInput = (Resolve-Path -LiteralPath $RepositoryPath).Path
$topLevelOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
    "-C",
    $resolvedRepositoryInput,
    "rev-parse",
    "--show-toplevel"
)
$repositoryTopLevel = [string]($topLevelOutput | Select-Object -Last 1)
if (-not (Test-Path -LiteralPath $repositoryTopLevel -PathType Container)) {
    throw "Unable to resolve the source repository root."
}
$resolvedRepository = (Resolve-Path -LiteralPath $repositoryTopLevel).Path
$headOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
    "-C",
    $resolvedRepository,
    "rev-parse",
    "--verify",
    "HEAD"
)
$sourceHead = ([string]($headOutput | Select-Object -Last 1)).Trim()
if ($sourceHead -notmatch "^[0-9a-fA-F]{40,64}$") {
    throw "Unable to resolve the current Git HEAD."
}

$manifestPath = Join-Path $resolvedRepository "packaging\stt_model_manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "The tracked STT model manifest is missing."
}
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$requiredManifestProperties = @(
    "schema_version",
    "name",
    "repository",
    "revision",
    "license",
    "bundle_subdirectory",
    "files"
)
foreach ($propertyName in $requiredManifestProperties) {
    if ($null -eq $manifest.PSObject.Properties[$propertyName]) {
        throw "The tracked STT model manifest schema is invalid."
    }
}
if (
    $manifest.schema_version -ne 1 -or
    $manifest.name -ne "large-v3-turbo" -or
    $manifest.repository -ne "dropbox-dash/faster-whisper-large-v3-turbo" -or
    $manifest.revision -ne "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" -or
    $manifest.license -ne "MIT" -or
    $manifest.bundle_subdirectory -ne "models/stt/large-v3-turbo" -or
    @($manifest.files).Count -eq 0
) {
    throw "The tracked STT model manifest metadata is invalid."
}

if (-not (Test-Path -LiteralPath $SttModelPath -PathType Container)) {
    throw "The supplied STT model directory does not exist."
}
$resolvedModelPath = (Resolve-Path -LiteralPath $SttModelPath).Path
$validatedModelFiles = @()
$seenModelFiles = @{}
foreach ($entry in @($manifest.files)) {
    foreach ($propertyName in @("path", "size", "sha256", "runtime_required")) {
        if ($null -eq $entry.PSObject.Properties[$propertyName]) {
            throw "The tracked STT model manifest file schema is invalid."
        }
    }
    $relativeFile = [string]$entry.path
    $normalizedKey = $relativeFile.ToLowerInvariant()
    if (
        [string]::IsNullOrWhiteSpace($relativeFile) -or
        $relativeFile -ne [IO.Path]::GetFileName($relativeFile) -or
        $seenModelFiles.ContainsKey($normalizedKey) -or
        ($entry.size -isnot [int] -and $entry.size -isnot [long]) -or
        [long]$entry.size -lt 0 -or
        [string]$entry.sha256 -notmatch "^[0-9a-fA-F]{64}$" -or
        $entry.runtime_required -isnot [bool]
    ) {
        throw "The tracked STT model manifest file entry is invalid."
    }
    $seenModelFiles[$normalizedKey] = $true
    $sourceModelFile = Join-Path $resolvedModelPath $relativeFile
    if (-not (Test-Path -LiteralPath $sourceModelFile -PathType Leaf)) {
        throw "A manifest-listed STT model file is missing."
    }
    $modelFileInfo = Get-Item -LiteralPath $sourceModelFile
    if ($modelFileInfo.Length -ne [long]$entry.size) {
        throw "A manifest-listed STT model file has an unexpected size."
    }
    $actualHash = (Get-FileHash -LiteralPath $sourceModelFile -Algorithm SHA256).Hash
    if ($actualHash -ne ([string]$entry.sha256).ToUpperInvariant()) {
        throw "A manifest-listed STT model file has an unexpected SHA-256."
    }
    $validatedModelFiles += [PSCustomObject]@{
        Name = $relativeFile
        Source = $sourceModelFile
    }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path (
        Join-Path $resolvedRepository "dist"
    ) "InterviewAssistant-source-$Version.zip"
}
elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $resolvedRepository $OutputPath
}
$absoluteOutputPath = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($absoluteOutputPath) -ne ".zip") {
    throw "-OutputPath must name a .zip archive."
}
$outputDirectory = [IO.Path]::GetDirectoryName($absoluteOutputPath)
if ([string]::IsNullOrWhiteSpace($outputDirectory)) {
    throw "Unable to resolve the archive output directory."
}
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$resolvedOutputDirectory = (Resolve-Path -LiteralPath $outputDirectory).Path
$resolvedOutputPath = Join-Path $resolvedOutputDirectory (
    [IO.Path]::GetFileName($absoluteOutputPath)
)
if (Test-Path -LiteralPath $resolvedOutputPath) {
    throw "The output archive already exists; choose a new -OutputPath."
}

$stagingName = "InterviewAssistant-source-staging-$([Guid]::NewGuid().ToString('N'))"
$stagingRoot = Join-Path ([IO.Path]::GetTempPath()) $stagingName
$resolvedStagingRoot = $null
$producedArchive = $null
try {
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null
    $resolvedStagingRoot = (Resolve-Path -LiteralPath $stagingRoot).Path
    $temporaryRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
    Assert-PathWithinRoot -Candidate $resolvedStagingRoot -Root $temporaryRoot

    $archiveTopLevel = "InterviewAssistant-source-$Version"
    $checkoutPath = Join-Path $resolvedStagingRoot $archiveTopLevel
    Assert-PathWithinRoot -Candidate $checkoutPath -Root $resolvedStagingRoot
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "clone",
        "--no-hardlinks",
        "--no-checkout",
        "--",
        $resolvedRepository,
        $checkoutPath
    ))
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "checkout",
        "--detach",
        $sourceHead
    ))
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "remote",
        "remove",
        "origin"
    ))
    $clonedHeadOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "rev-parse",
        "--verify",
        "HEAD"
    )
    $clonedHead = ([string]($clonedHeadOutput | Select-Object -Last 1)).Trim()
    if ($clonedHead -ne $sourceHead) {
        throw "The standalone clone does not match the source HEAD."
    }
    $remainingRemotes = @(
        Invoke-GitCommand -GitPath $git.Source -Arguments @(
            "-C",
            $checkoutPath,
            "remote"
        )
    )
    if ($remainingRemotes.Count -ne 0) {
        throw "The standalone clone unexpectedly retains a Git remote."
    }
    $alternatesPath = Join-Path $checkoutPath ".git\objects\info\alternates"
    if (Test-Path -LiteralPath $alternatesPath) {
        throw "The source clone depends on an external Git object store."
    }

    $runtimeConfig = Join-Path $checkoutPath "config.yaml"
    if (Test-Path -LiteralPath $runtimeConfig -PathType Leaf) {
        Assert-PathWithinRoot -Candidate $runtimeConfig -Root $resolvedStagingRoot
        [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
            "-C",
            $checkoutPath,
            "update-index",
            "--skip-worktree",
            "--",
            "config.yaml"
        ))
        Remove-Item -LiteralPath $runtimeConfig -Force
    }

    $bundleRelativePath = ([string]$manifest.bundle_subdirectory).Replace(
        "/",
        [IO.Path]::DirectorySeparatorChar
    )
    $modelDestination = Join-Path $checkoutPath $bundleRelativePath
    Assert-PathWithinRoot -Candidate $modelDestination -Root $resolvedStagingRoot
    if (Test-Path -LiteralPath $modelDestination) {
        throw "The source HEAD unexpectedly contains the model overlay directory."
    }
    New-Item -ItemType Directory -Path $modelDestination | Out-Null
    foreach ($modelFile in $validatedModelFiles) {
        $destinationFile = Join-Path $modelDestination $modelFile.Name
        Assert-PathWithinRoot -Candidate $destinationFile -Root $resolvedStagingRoot
        Copy-Item -LiteralPath $modelFile.Source -Destination $destinationFile
    }

    $stagedArchive = Join-Path $resolvedStagingRoot "source.zip"
    $tarOutput = @(& $tar.Source @(
        "-a",
        "-c",
        "-f",
        $stagedArchive,
        "-C",
        $resolvedStagingRoot,
        $archiveTopLevel
    ) 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed to create the source ZIP archive."
    }
    if (-not (Test-Path -LiteralPath $stagedArchive -PathType Leaf)) {
        throw "tar.exe did not create the source ZIP archive."
    }
    if ((Get-Item -LiteralPath $stagedArchive).Length -le 0) {
        throw "tar.exe created an empty source ZIP archive."
    }

    $archiveEntries = @(& $tar.Source @("-t", "-f", $stagedArchive) 2>&1)
    if ($LASTEXITCODE -ne 0 -or $archiveEntries.Count -eq 0) {
        throw "The source ZIP archive could not be verified."
    }
    $expectedPrefix = "$archiveTopLevel/"
    $normalizedEntries = @($archiveEntries | ForEach-Object {
        ([string]$_).Replace("\", "/")
    })
    foreach ($entryName in $normalizedEntries) {
        if (
            $entryName -ne $archiveTopLevel -and
            -not $entryName.StartsWith(
                $expectedPrefix,
                [StringComparison]::Ordinal
            )
        ) {
            throw "The source ZIP archive has more than one top-level directory."
        }
    }
    if ($normalizedEntries -notcontains "$expectedPrefix.git/HEAD") {
        throw "The source ZIP archive is missing standalone Git history."
    }
    foreach ($modelFile in $validatedModelFiles) {
        $expectedModelEntry = (
            "$expectedPrefix$($manifest.bundle_subdirectory)/$($modelFile.Name)"
        )
        if ($normalizedEntries -notcontains $expectedModelEntry) {
            throw "The source ZIP archive is missing a manifest-listed model file."
        }
    }
    if (($normalizedEntries -join "`n").ToLowerInvariant().Contains(
        ".cache/huggingface"
    )) {
        throw "The source ZIP archive unexpectedly contains model cache metadata."
    }

    Move-Item -LiteralPath $stagedArchive -Destination $resolvedOutputPath
    if (-not (Test-Path -LiteralPath $resolvedOutputPath -PathType Leaf)) {
        throw "The source ZIP archive was not moved to the requested output path."
    }
    $producedArchive = (Resolve-Path -LiteralPath $resolvedOutputPath).Path
}
finally {
    if (
        $null -ne $resolvedStagingRoot -and
        (Test-Path -LiteralPath $resolvedStagingRoot -PathType Container)
    ) {
        $cleanupCandidate = (Resolve-Path -LiteralPath $resolvedStagingRoot).Path
        $cleanupTemporaryRoot = (
            Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())
        ).Path
        Assert-PathWithinRoot -Candidate $cleanupCandidate -Root $cleanupTemporaryRoot
        if ((Split-Path -Leaf $cleanupCandidate) -ne $stagingName) {
            throw "Refusing to clean an unexpected staging directory."
        }
        Remove-Item -LiteralPath $cleanupCandidate -Recurse -Force
    }
}

if ($null -eq $producedArchive) {
    throw "The source ZIP archive was not produced."
}
Write-Output $producedArchive
