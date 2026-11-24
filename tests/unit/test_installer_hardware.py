"""Exercise the real Inno post-install entry point on an isolated desktop."""
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from tests.unit.test_online_installer import ROOT, compiler  # noqa: F401


@pytest.mark.parametrize("mode,flag,expected", [("default", "--prepare-stt", 0),
                                                ("skip", "--defer-stt", 0),
                                                ("fail", "--prepare-stt", 10)])
def test_installer_runs_preparation_automatically(tmp_path: Path, compiler, mode, flag, expected):  # noqa: F811
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csc.is_file():
        pytest.skip("Windows .NET Framework compiler is unavailable for the native fixture")
    source = tmp_path / "Fixture.cs"
    source.write_text('using System.IO; class Fixture {static int Main(string[] a) {'
                      'File.WriteAllLines(a[2]+".called", a); return File.Exists(a[2]+".fail") ? 19 : 0;}}')
    probe = tmp_path / "probe.exe"
    subprocess.run([str(csc), "/nologo", "/out:" + str(probe), str(source)], check=True)
    iss = tmp_path / "fixture.iss"
    iss.write_text(f'''[Setup]
AppId=HardwareFixture-{uuid4().hex}
AppName=Hardware fixture
AppVersion=1
DefaultDirName={tmp_path / "installed"}
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
OutputDir={tmp_path}
OutputBaseFilename=setup
Compression=none
[Tasks]
Name: "preparestt"; Description: "Auto"
[Files]
Source: "{probe}"; DestDir: "{{app}}"; DestName: "InterviewAssistant.exe"
#include "{ROOT / "packaging/online_hardware.iss"}"
''', encoding="utf-8-sig")
    subprocess.run([str(compiler), "/Q", str(iss)], capture_output=True, check=True, timeout=90)
    config = tmp_path / "isolated.yaml"
    if mode == "fail":
        Path(str(config) + ".fail").touch()
    args = [str(tmp_path / "setup.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            "/STTCONFIG=" + str(config)]
    if mode == "skip":
        args.append("/TASKS=!preparestt")
    proc = subprocess.run(args, timeout=45)
    assert proc.returncode == expected
    assert Path(str(config) + ".called").read_text(encoding="utf-8-sig").splitlines() == [flag, "--config", str(config)]
