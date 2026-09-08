"""Exercise smoke preflight and arguments without running an installer."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.unit.test_release_packaging import APP_ID, ROOT, _powershell


def _smoke_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository with spaces"
    for directory in ("scripts", "packaging", ".venv/Scripts"):
        (repository / directory).mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts" / "smoke_installer.ps1",
        repository / "scripts" / "smoke_installer.ps1",
    )
    for name in (
        "packaging/stt_model_manifest.json",
        "packaging/dist_inventory.json",
        "scripts/validate_release_dist.py",
        ".venv/Scripts/python.exe",
        "fixture installer.exe",
        "new inventory.json",
    ):
        (repository / name).write_bytes(b"fixture; never executed")
    wrapper = tmp_path / "invoke-smoke.ps1"
    wrapper.write_text(
        r"""param([string]$SmokeScript, [string]$Installer,
    [string]$Inventory, [string]$InstallDirectory, [switch]$Registered)
function Test-Path {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$PathType)
    if ($LiteralPath.StartsWith("Registry::")) { return $Registered.IsPresent }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    $selectedInventory = Get-Variable inventory -Scope 1 -ValueOnly
    $selectedReport = Get-Variable ReportPath -Scope 1 -ValueOnly
    $boundary = @{ Inventory = $selectedInventory; Report = $selectedReport;
        Arguments = $ArgumentList; WindowStyle = $WindowStyle; Executable = $FilePath }
    [Console]::WriteLine("SMOKE_BOUNDARY=" + ($boundary | ConvertTo-Json -Compress))
    throw "INSTALLER_BOUNDARY_REACHED"
}
$parameters = @{ InstallerPath = $Installer }
if ($Inventory -ne "__default__") { $parameters.InventoryPath = $Inventory }
if ($InstallDirectory -ne "__default__") { $parameters.InstallPath = $InstallDirectory }
& $SmokeScript @parameters
""",
        encoding="utf-8",
    )
    return repository, wrapper


def _invoke_smoke(
    fixture: tuple[Path, Path],
    *,
    inventory: str = "__default__",
    install: str = "__default__",
    registered: bool = False,
) -> subprocess.CompletedProcess[str]:
    repository, wrapper = fixture
    command = [
        _powershell(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(wrapper),
        "-SmokeScript",
        str(repository / "scripts/smoke_installer.ps1"),
        "-Installer",
        str(repository / "fixture installer.exe"),
        "-Inventory",
        inventory,
        "-InstallDirectory",
        install,
    ]
    if registered:
        command.append("-Registered")
    # Windows PowerShell must load its own built-in management module.
    env = {key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"}
    return subprocess.run(
        command, cwd=wrapper.parent, env=env, capture_output=True, text=True, timeout=30
    )


def _boundary(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    output = result.stdout + result.stderr
    assert "INSTALLER_BOUNDARY_REACHED" in output, output
    line = next(line for line in result.stdout.splitlines() if line.startswith("SMOKE_BOUNDARY="))
    return json.loads(line.removeprefix("SMOKE_BOUNDARY="))


@pytest.mark.parametrize("relative", [False, True])
def test_smoke_uses_explicit_inventory_and_isolated_run_paths(
    tmp_path: Path, relative: bool
) -> None:
    fixture = _smoke_fixture(tmp_path)
    repository = fixture[0]
    inventory = Path("new inventory.json") if relative else repository / "new inventory.json"
    result = _invoke_smoke(
        fixture, inventory=str(inventory), install="build/release-test/installer-smoke"
    )

    boundary = _boundary(result)
    assert Path(boundary["Inventory"]) == repository / "new inventory.json"
    assert Path(boundary["Report"]).parent == repository / "build/release-test"
    assert boundary["WindowStyle"] == "Hidden"
    arguments = boundary["Arguments"]
    for argument in ("/NOICONS", '/TASKS=""', "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS"):
        assert argument in arguments
    log = next(argument for argument in arguments if argument.startswith('/LOG="'))
    assert Path(log[6:-1]).parent == repository / "build/release-test"


def test_smoke_default_selects_unique_run_and_historical_inventory(tmp_path: Path) -> None:
    fixture = _smoke_fixture(tmp_path)
    first = _boundary(_invoke_smoke(fixture))
    second = _boundary(_invoke_smoke(fixture))

    assert Path(first["Inventory"]) == fixture[0] / "packaging/dist_inventory.json"
    first_report, second_report = Path(first["Report"]), Path(second["Report"])
    assert first_report != second_report
    assert first_report.parent.parent == fixture[0] / "build"
    assert second_report.parent.parent == fixture[0] / "build"


@pytest.mark.parametrize(
    "existing_name",
    ["installer-smoke/must-remain.txt", "installer-smoke-install.log", "installer-smoke.json"],
)
def test_smoke_refuses_existing_output_without_deleting_it(
    tmp_path: Path, existing_name: str
) -> None:
    fixture = _smoke_fixture(tmp_path)
    target = fixture[0] / "build/release-test/installer-smoke"
    sentinel = target.parent / existing_name
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("preserve me", encoding="utf-8")

    result = _invoke_smoke(fixture, install=str(target))
    output = result.stdout + result.stderr
    assert "already exists" in output, output
    assert "INSTALLER_BOUNDARY_REACHED" not in output
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_smoke_refuses_registered_application_before_launch(tmp_path: Path) -> None:
    fixture = _smoke_fixture(tmp_path)
    result = _invoke_smoke(fixture, registered=True)
    output = result.stdout + result.stderr

    assert "already registered" in output, output
    assert "INSTALLER_BOUNDARY_REACHED" not in output


@pytest.mark.parametrize("install", ["outside/installer-smoke", "build/run/not-smoke"])
def test_smoke_rejects_unconfined_install_path(tmp_path: Path, install: str) -> None:
    fixture = _smoke_fixture(tmp_path)
    result = _invoke_smoke(fixture, install=install)
    output = result.stdout + result.stderr

    assert "must end in installer-smoke under the workspace build directory" in output, output
    assert "INSTALLER_BOUNDARY_REACHED" not in output


def test_smoke_registration_guard_tracks_installer_app_id() -> None:
    source = (ROOT / "scripts/smoke_installer.ps1").read_text(encoding="utf-8")
    assert f'$uninstallKey = "{{{APP_ID}}}_is1"' in source


def test_smoke_rejects_missing_inventory_before_launch(tmp_path: Path) -> None:
    fixture = _smoke_fixture(tmp_path)
    result = _invoke_smoke(fixture, inventory="missing.json")
    output = result.stdout + result.stderr

    assert "inventory is missing" in output, output
    assert "INSTALLER_BOUNDARY_REACHED" not in output


def test_smoke_launches_original_installer_beside_untouched_disk_slices(tmp_path: Path) -> None:
    fixture = _smoke_fixture(tmp_path)
    parts = [fixture[0] / f"fixture installer-{number}.bin" for number in (1, 2, 3)]
    for part in parts:
        part.write_bytes(b"fixture compressed slice")
    boundary = _boundary(_invoke_smoke(fixture))
    assert Path(boundary["Executable"]) == fixture[0] / "fixture installer.exe"
    for part in parts:
        assert part.read_bytes() == b"fixture compressed slice"
