[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SttModelPath,

    [string]$OutputPath = "",

    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._+-]*$")]
    [string]$Version = "0.1.2",

    [string]$RepositoryPath = (Join-Path $PSScriptRoot ".."),

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{40,64}$")]
    [string]$SourceCommit
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

function ConvertTo-ExtendedLengthPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    if ($fullPath.StartsWith("\\", [StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $fullPath.Substring(2)
    }
    return "\\?\" + $fullPath
}

function Get-ConfiguredTemporaryRoot {
    $configuredPath = $env:INTERVIEW_ASSISTANT_ARCHIVE_TEMP
    if ([string]::IsNullOrWhiteSpace($configuredPath)) {
        $configuredPath = $env:TEMP
    }
    if ([string]::IsNullOrWhiteSpace($configuredPath)) {
        $configuredPath = $env:TMP
    }
    if ([string]::IsNullOrWhiteSpace($configuredPath)) {
        throw "INTERVIEW_ASSISTANT_ARCHIVE_TEMP, TEMP, or TMP is required for staging."
    }
    return [IO.Path]::GetFullPath($configuredPath)
}

function Remove-VerifiedDirectoryTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $extendedPath = ConvertTo-ExtendedLengthPath -Path $Path
    if ([IO.Directory]::Exists($extendedPath)) {
        $pendingDirectories = New-Object "System.Collections.Generic.Stack[string]"
        $pendingDirectories.Push($extendedPath)
        while ($pendingDirectories.Count -gt 0) {
            $currentDirectory = $pendingDirectories.Pop()
            foreach ($entryPath in [IO.Directory]::EnumerateFileSystemEntries(
                $currentDirectory
            )) {
                $attributes = [IO.File]::GetAttributes($entryPath)
                $isDirectory = (
                    $attributes -band [IO.FileAttributes]::Directory
                ) -ne 0
                $isReparsePoint = (
                    $attributes -band [IO.FileAttributes]::ReparsePoint
                ) -ne 0
                if ($isDirectory -and -not $isReparsePoint) {
                    $pendingDirectories.Push($entryPath)
                }
                elseif (
                    -not $isDirectory -and
                    -not $isReparsePoint -and
                    ($attributes -band [IO.FileAttributes]::ReadOnly) -ne 0
                ) {
                    $writableAttributes = $attributes -bxor (
                        [IO.FileAttributes]::ReadOnly
                    )
                    [IO.File]::SetAttributes(
                        $entryPath,
                        [IO.FileAttributes]$writableAttributes
                    )
                }
            }
        }
        [IO.Directory]::Delete($extendedPath, $true)
    }
}

function Remove-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $extendedPath = ConvertTo-ExtendedLengthPath -Path $Path
    if ([IO.File]::Exists($extendedPath)) {
        $attributes = [IO.File]::GetAttributes($extendedPath)
        if (($attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
            $writableAttributes = $attributes -bxor [IO.FileAttributes]::ReadOnly
            [IO.File]::SetAttributes(
                $extendedPath,
                [IO.FileAttributes]$writableAttributes
            )
        }
        [IO.File]::Delete($extendedPath)
    }
}

function Invoke-ReleasePython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,

        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $validatorOutput = @(& $PythonPath $ScriptPath @Arguments 2>&1)
        $validatorExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($validatorExitCode -ne 0) {
        throw "A release privacy validator failed."
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
$releasePython = Join-Path (Join-Path $PSScriptRoot "..") ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    $pythonCommand = Get-Command python.exe -CommandType Application `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $pythonCommand) {
        throw "Python is required to validate release privacy."
    }
    $releasePython = $pythonCommand.Source
}
$historyScanner = Join-Path $PSScriptRoot "scan_release_git_history.py"
$configValidator = Join-Path $PSScriptRoot "validate_source_release_config.py"
foreach ($validatorPath in @($historyScanner, $configValidator)) {
    if (-not (Test-Path -LiteralPath $validatorPath -PathType Leaf)) {
        throw "A required release privacy validator is missing."
    }
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
    "${SourceCommit}^{commit}"
)
$sourceHead = ([string]($headOutput | Select-Object -Last 1)).Trim()
if (
    $sourceHead -notmatch "^[0-9a-fA-F]{40,64}$" -or
    $sourceHead -ne $SourceCommit.ToLowerInvariant()
) {
    throw "Unable to resolve the pinned source commit."
}
$branchOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
    "-C",
    $resolvedRepository,
    "symbolic-ref",
    "--quiet",
    "--short",
    "HEAD"
)
$sourceBranch = ([string]($branchOutput | Select-Object -Last 1)).Trim()
if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
    throw "The source HEAD must be attached to a local branch."
}
[void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
    "-C",
    $resolvedRepository,
    "merge-base",
    "--is-ancestor",
    $sourceHead,
    "refs/heads/$sourceBranch"
))

$manifestObject = "${sourceHead}:packaging/stt_model_manifest.json"
$manifestOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
    "-C",
    $resolvedRepository,
    "show",
    $manifestObject
)
$manifestJson = ($manifestOutput -join "`n")
if ([string]::IsNullOrWhiteSpace($manifestJson)) {
    throw "The captured HEAD STT model manifest is missing."
}
try {
    $manifest = $manifestJson | ConvertFrom-Json
}
catch {
    throw "The captured HEAD STT model manifest is invalid."
}
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
$temporaryRoot = Get-ConfiguredTemporaryRoot
$stagingRoot = [IO.Path]::GetFullPath(
    [IO.Path]::Combine($temporaryRoot, $stagingName)
)
Assert-PathWithinRoot -Candidate $stagingRoot -Root $temporaryRoot
if ((Split-Path -Leaf $stagingRoot) -ne $stagingName) {
    throw "Refusing to create an unexpected staging directory."
}
$archiveTopLevel = "InterviewAssistant-source-$Version"
$anticipatedGitDirectory = [IO.Path]::Combine(
    $stagingRoot,
    $archiveTopLevel,
    ".git"
)
if ($anticipatedGitDirectory.Length -ge 260) {
    throw "The archive staging path is too long for Git on Windows; choose a shorter INTERVIEW_ASSISTANT_ARCHIVE_TEMP."
}
$resolvedStagingRoot = $stagingRoot
$stagingCreationAttempted = $false
$producedArchive = $null
$primaryError = $null
$cleanupError = $null
try {
    $stagingCreationAttempted = $true
    $extendedStagingRoot = ConvertTo-ExtendedLengthPath -Path $resolvedStagingRoot
    [void][IO.Directory]::CreateDirectory($extendedStagingRoot)

    $checkoutPath = Join-Path $resolvedStagingRoot $archiveTopLevel
    Assert-PathWithinRoot -Candidate $checkoutPath -Root $resolvedStagingRoot
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "clone",
        "--no-local",
        "--single-branch",
        "--no-tags",
        "--branch",
        $sourceBranch,
        "--no-checkout",
        "--",
        $resolvedRepository,
        $checkoutPath
    ))
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "checkout",
        "-B",
        $sourceBranch,
        $sourceHead
    ))
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "remote",
        "remove",
        "origin"
    ))
    $remoteHeadRefPath = Join-Path $checkoutPath ".git\refs\remotes\origin\HEAD"
    $extendedRemoteHeadRefPath = ConvertTo-ExtendedLengthPath -Path $remoteHeadRefPath
    if ([IO.File]::Exists($extendedRemoteHeadRefPath)) {
        [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
            "-C",
            $checkoutPath,
            "symbolic-ref",
            "--delete",
            "refs/remotes/origin/HEAD"
        ))
    }
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
    $clonedBranchOutput = Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD"
    )
    $clonedBranch = ([string]($clonedBranchOutput | Select-Object -Last 1)).Trim()
    if ($clonedBranch -ne $sourceBranch) {
        throw "The standalone clone is not on the captured source branch."
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
    $remainingRemoteRefs = @(
        Invoke-GitCommand -GitPath $git.Source -Arguments @(
            "-C",
            $checkoutPath,
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/"
        )
    )
    if ($remainingRemoteRefs.Count -ne 0) {
        throw "The standalone clone unexpectedly retains a remote-tracking ref."
    }
    $remoteRefsDirectory = Join-Path $checkoutPath ".git\refs\remotes"
    Assert-PathWithinRoot -Candidate $remoteRefsDirectory -Root $resolvedStagingRoot
    Remove-VerifiedDirectoryTree -Path $remoteRefsDirectory
    $alternatesPath = Join-Path $checkoutPath ".git\objects\info\alternates"
    if (Test-Path -LiteralPath $alternatesPath) {
        throw "The source clone depends on an external Git object store."
    }

    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "reflog",
        "expire",
        "--expire=now",
        "--expire-unreachable=now",
        "--all"
    ))
    [void](Invoke-GitCommand -GitPath $git.Source -Arguments @(
        "-C",
        $checkoutPath,
        "gc",
        "--prune=now"
    ))
    $gitLogsPath = Join-Path $checkoutPath ".git\logs"
    Assert-PathWithinRoot -Candidate $gitLogsPath -Root $resolvedStagingRoot
    Remove-VerifiedDirectoryTree -Path $gitLogsPath
    foreach ($transientGitFile in @("FETCH_HEAD", "ORIG_HEAD")) {
        $transientGitPath = Join-Path $checkoutPath ".git\$transientGitFile"
        Assert-PathWithinRoot -Candidate $transientGitPath -Root $resolvedStagingRoot
        Remove-VerifiedFile -Path $transientGitPath
    }
    $fsckOutput = @(
        Invoke-GitCommand -GitPath $git.Source -Arguments @(
            "-C",
            $checkoutPath,
            "fsck",
            "--no-reflogs",
            "--unreachable",
            "--no-progress"
        )
    )
    $unexpectedFsckOutput = @($fsckOutput | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($unexpectedFsckOutput.Count -ne 0) {
        throw "The standalone clone retains unreachable Git objects."
    }
    Invoke-ReleasePython `
        -PythonPath $releasePython `
        -ScriptPath $historyScanner `
        -Arguments @(
            "--repository",
            $checkoutPath,
            "--expected-commit",
            $sourceHead
        )

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
        $actualHash = (
            Get-FileHash -LiteralPath $sourceModelFile -Algorithm SHA256
        ).Hash
        if ($actualHash -ne ([string]$entry.sha256).ToUpperInvariant()) {
            throw "A manifest-listed STT model file has an unexpected SHA-256."
        }
        $validatedModelFiles += [PSCustomObject]@{
            Name = $relativeFile
            Source = $sourceModelFile
        }
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
    $sanitizedConfigTemplate = Join-Path (
        Join-Path $checkoutPath "packaging"
    ) "source_release_config.yaml"
    if (-not (Test-Path -LiteralPath $sanitizedConfigTemplate -PathType Leaf)) {
        throw "The pinned source commit is missing the sanitized config template."
    }
    Copy-Item -LiteralPath $sanitizedConfigTemplate -Destination $runtimeConfig
    Invoke-ReleasePython `
        -PythonPath $releasePython `
        -ScriptPath $configValidator `
        -Arguments @("--config", $runtimeConfig)

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
    if ($normalizedEntries -notcontains "${expectedPrefix}config.yaml") {
        throw "The source ZIP archive is missing the sanitized config."
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
catch {
    $primaryError = $_
}
finally {
    try {
        if ($stagingCreationAttempted) {
            $cleanupCandidate = [IO.Path]::GetFullPath($resolvedStagingRoot)
            $cleanupTemporaryRoot = Get-ConfiguredTemporaryRoot
            Assert-PathWithinRoot `
                -Candidate $cleanupCandidate `
                -Root $cleanupTemporaryRoot
            if ((Split-Path -Leaf $cleanupCandidate) -ne $stagingName) {
                throw "Refusing to clean an unexpected staging directory."
            }
            Remove-VerifiedDirectoryTree -Path $cleanupCandidate
            $extendedCleanupCandidate = ConvertTo-ExtendedLengthPath `
                -Path $cleanupCandidate
            if ([IO.Directory]::Exists($extendedCleanupCandidate)) {
                throw "The script-owned staging directory could not be removed."
            }
        }
    }
    catch {
        $cleanupError = $_
    }
}

if ($null -ne $primaryError) {
    if ($null -ne $cleanupError) {
        Write-Warning "Staging cleanup encountered a secondary error."
    }
    throw $primaryError
}
if ($null -ne $cleanupError) {
    throw $cleanupError
}
if ($null -eq $producedArchive) {
    throw "The source ZIP archive was not produced."
}
Write-Output $producedArchive
