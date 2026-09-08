"""Exercise installer inventory selection without starting Inno Setup."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.unit.test_release_packaging import ROOT, _powershell, _write_dist_fixture


def _installer_fixture(tmp_path):
    repository = tmp_path / "repository with spaces"
    repository.mkdir()
    dist, manifest, inventory, _ = _write_dist_fixture(repository)
    (repository / "scripts").mkdir()
    (repository / "packaging").mkdir()
    for name in ("build_installer.ps1", "validate_release_dist.py"):
        shutil.copyfile(ROOT / "scripts" / name, repository / "scripts" / name)
    shutil.copyfile(manifest, repository / "packaging" / "stt_model_manifest.json")
    # Deliberately stale historical inventory. Only the explicitly supplied
    # inventory authenticates this fixture's complete file contents.
    (repository / "packaging" / "dist_inventory.json").write_text(
        json.dumps({"schema_version": 1, "application": "InterviewAssistant", "files": []}),
        encoding="utf-8",
    )
    (repository / "packaging" / "interview_assistant.iss").write_text("; fixture", encoding="utf-8")
    compiler = repository / "unused compiler.exe"
    compiler.write_bytes(b"not executable; intercepted before launch")
    wrapper = tmp_path / "invoke-builder.ps1"
    wrapper.write_text(
        r"""param([string]$BuildScript, [string]$Compiler, [string]$Distribution,
    [string]$Inventory, [string]$Compression = "", [switch]$DiskSpanning,
    [string]$FakeParts = "", [long]$ReportedInstallerSize = -1)
$ErrorActionPreference = "Stop"
function Get-Item {
    param([string]$LiteralPath)
    $item = Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath
    if ($ReportedInstallerSize -ge 0 -and $item.Extension -eq ".exe") {
        return [PSCustomObject]@{
            Name = $item.Name; FullName = $item.FullName; Length = $ReportedInstallerSize
        }
    }
    return $item
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    [Console]::WriteLine("ISCC_ARGS=" + ($ArgumentList | ConvertTo-Json -Compress))
    if ($FakeParts) {
        $outputDirectory = Get-Variable resolvedOutput -Scope 1 -ValueOnly
        foreach ($part in (Get-Content -Raw -LiteralPath $FakeParts | ConvertFrom-Json)) {
            [IO.File]::WriteAllText((Join-Path $outputDirectory $part), "fixture:" + $part)
        }
        return [PSCustomObject]@{ ExitCode = 0 }
    }
    throw "COMPILER_BOUNDARY_REACHED"
}
$parameters = @{ IsccPath = $Compiler; DistPath = $Distribution }
if ($Inventory -ne "__default__") { $parameters.InventoryPath = $Inventory }
if ($Compression) { $parameters.Compression = $Compression }
if ($DiskSpanning) { $parameters.DiskSpanning = $true }
& $BuildScript @parameters
""",
        encoding="utf-8",
    )
    return repository, dist, inventory, compiler, wrapper


def _invoke_builder(
    fixture, *, inventory, compression=None, disk_spanning=False, fake_parts=None,
    reported_size=None,
):
    repository, dist, _, compiler, wrapper = fixture
    env = {key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [
            _powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(wrapper), "-BuildScript", str(repository / "scripts" / "build_installer.ps1"),
            "-Compiler", str(compiler), "-Distribution", str(dist), "-Inventory", str(inventory),
            *(["-Compression", compression] if compression is not None else []),
            *(["-DiskSpanning"] if disk_spanning else []),
            *(["-FakeParts", str(fake_parts)] if fake_parts is not None else []),
            *(["-ReportedInstallerSize", str(reported_size)] if reported_size is not None else []),
        ],
        cwd=wrapper.parent, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("relative", [False, True])
def test_explicit_matching_inventory_passes_real_validator_before_compiler(tmp_path, relative):
    fixture = _installer_fixture(tmp_path)
    inventory = fixture[2].relative_to(fixture[0]) if relative else fixture[2]
    result = _invoke_builder(fixture, inventory=inventory)
    output = result.stdout + result.stderr
    assert "COMPILER_BOUNDARY_REACHED" in output, output
    assert "failed release validation" not in output


def test_omitted_inventory_still_uses_historical_default(tmp_path):
    fixture = _installer_fixture(tmp_path)
    result = _invoke_builder(fixture, inventory="__default__")
    output = result.stdout + result.stderr
    assert "failed release validation" in output, output
    assert "COMPILER_BOUNDARY_REACHED" not in output


def test_supplied_inventory_cannot_bypass_modified_distribution(tmp_path):
    fixture = _installer_fixture(tmp_path)
    (fixture[1] / "InterviewAssistant.exe").write_bytes(b"tampered binary")
    result = _invoke_builder(fixture, inventory=fixture[2])
    output = result.stdout + result.stderr
    assert "failed release validation" in output, output
    assert "COMPILER_BOUNDARY_REACHED" not in output


def test_missing_supplied_inventory_is_rejected_before_compiler(tmp_path):
    fixture = _installer_fixture(tmp_path)
    result = _invoke_builder(fixture, inventory="missing-inventory.json")
    output = result.stdout + result.stderr
    assert "distribution inventory is missing" in output, output
    assert "COMPILER_BOUNDARY_REACHED" not in output


@pytest.mark.parametrize("compression,expected", [(None, "ultra64"), ("normal", "normal")])
def test_installer_compression_default_and_override_reach_compiler(tmp_path, compression, expected):
    fixture = _installer_fixture(tmp_path)
    result = _invoke_builder(fixture, inventory=fixture[2], compression=compression)
    output = result.stdout + result.stderr
    assert "COMPILER_BOUNDARY_REACHED" in output, output
    line = next(line for line in result.stdout.splitlines() if line.startswith("ISCC_ARGS="))
    arguments = json.loads(line.removeprefix("ISCC_ARGS="))
    assert f"/DAppCompression=lzma2/{expected}" in arguments


def test_installer_rejects_unsupported_compression_before_compiler(tmp_path):
    fixture = _installer_fixture(tmp_path)
    result = _invoke_builder(fixture, inventory=fixture[2], compression="unsupported")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "COMPILER_BOUNDARY_REACHED" not in output
    assert "ParameterArgumentValidationError" in output


@pytest.mark.parametrize("enabled", [False, True])
def test_installer_disk_spanning_is_opt_in(tmp_path, enabled):
    fixture = _installer_fixture(tmp_path)
    result = _invoke_builder(fixture, inventory=fixture[2], disk_spanning=enabled)
    output = result.stdout + result.stderr
    assert "COMPILER_BOUNDARY_REACHED" in output, output
    line = next(line for line in result.stdout.splitlines() if line.startswith("ISCC_ARGS="))
    arguments = json.loads(line.removeprefix("ISCC_ARGS="))
    assert f"/DAppDiskSpanning={'yes' if enabled else 'no'}" in arguments


@pytest.mark.parametrize("spanning", [False, True])
def test_installer_checksums_cover_executable_and_every_disk_slice(tmp_path, spanning):
    fixture = _installer_fixture(tmp_path)
    stem = "InterviewAssistant-Setup-0.1.2-win64"
    parts = [stem + ".exe"]
    if spanning:
        parts += [stem + "-1.bin", stem + "-2.bin", stem + "-3.bin"]
    fake_parts = tmp_path / "compiler-outputs.json"
    fake_parts.write_text(json.dumps(parts), encoding="utf-8")
    result = _invoke_builder(
        fixture, inventory=fixture[2], disk_spanning=spanning, fake_parts=fake_parts
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = fixture[0] / "dist/installer"
    checksums = (output / (stem + "-SHA256SUMS.txt")).read_text(encoding="ascii")
    expected = {
        f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}"
        for name in parts
    }
    assert set(checksums.splitlines()) == expected
    assert result.stdout.splitlines()[-1] == str(output / (stem + ".exe"))


def test_spanning_installer_requires_first_slice(tmp_path):
    fixture = _installer_fixture(tmp_path)
    fake_parts = tmp_path / "compiler-outputs.json"
    fake_parts.write_text(json.dumps(["InterviewAssistant-Setup-0.1.2-win64.exe"]))
    result = _invoke_builder(
        fixture, inventory=fixture[2], disk_spanning=True, fake_parts=fake_parts
    )
    assert result.returncode != 0
    assert "did not produce the first installer disk slice" in result.stdout + result.stderr


@pytest.mark.parametrize("suffix", [".exe", "-3.bin", "-SHA256SUMS.txt"])
def test_installer_refuses_stale_artifacts_before_compiler(tmp_path, suffix):
    fixture = _installer_fixture(tmp_path)
    output = fixture[0] / "dist/installer"
    output.mkdir(parents=True)
    sentinel = output / ("InterviewAssistant-Setup-0.1.2-win64" + suffix)
    sentinel.write_text("preserve", encoding="utf-8")
    result = _invoke_builder(fixture, inventory=fixture[2], disk_spanning=True)
    assert result.returncode != 0
    assert "installer output already exists" in result.stdout + result.stderr
    assert "COMPILER_BOUNDARY_REACHED" not in result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_inno_disk_slices_stay_under_github_asset_limit():
    source = (ROOT / "packaging/interview_assistant.iss").read_text(encoding="utf-8")
    assert '#define AppDiskSpanning "no"' in source
    assert "DiskSpanning={#AppDiskSpanning}" in source
    assert "DiskSliceSize=1073741824" in source
    assert "SlicesPerDisk=1" in source


@pytest.mark.parametrize("size,allowed", [(2147483647, True), (2147483648, False)])
def test_spanned_installer_enforces_strict_github_size_boundary(tmp_path, size, allowed):
    fixture = _installer_fixture(tmp_path)
    stem = "InterviewAssistant-Setup-0.1.2-win64"
    fake_parts = tmp_path / "compiler-outputs.json"
    fake_parts.write_text(json.dumps([stem + ".exe", stem + "-1.bin"]))
    # Inject compiler-output metadata; never allocate a 2 GiB test artifact.
    result = _invoke_builder(
        fixture, inventory=fixture[2], disk_spanning=True, fake_parts=fake_parts,
        reported_size=size,
    )
    manifest = fixture[0] / "dist/installer" / (stem + "-SHA256SUMS.txt")
    assert (result.returncode == 0) == allowed, result.stdout + result.stderr
    assert manifest.is_file() == allowed
    if not allowed:
        assert "strictly less than 2 GiB" in result.stdout + result.stderr
