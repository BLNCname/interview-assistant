"""Execute the installer's GPU profile rules using the real Pascal compiler."""

from pathlib import Path
import subprocess

from tests.unit.test_online_installer import ROOT, compiler  # noqa: F401


def test_native_gpu_profiles(tmp_path: Path, compiler) -> None:  # noqa: F811
    output = tmp_path / "profiles.txt"
    script = tmp_path / "hardware.iss"
    def quote(value):
        return str(value).replace("'", "''")
    script.write_text(fr"""
[Setup]
AppName=Interview Assistant hardware fixture
AppVersion=1
DefaultDirName={{tmp}}\HardwareFixture
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputDir={tmp_path}
OutputBaseFilename=HardwareFixture
Compression=none
[Files]
Source: "{ROOT / 'packaging/installer_gpu_probe.ps1'}"; Flags: dontcopy
#include "{ROOT / 'packaging/online_hardware.iss'}"
[Code]
function InitializeSetup: Boolean;
var Results: String;
begin
  Results := DescribeGPU('0, NVIDIA GeForce RTX 3060 Ti, 8192, 5000, 8.6, 576.57') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('0, NVIDIA GeForce RTX 5070 Ti, 16303, 14000, 12.0, 610.74') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('0, NVIDIA GeForce RTX 3060 Ti, 8192, 5000, 8.6, 560.94') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('0, NVIDIA GeForce RTX 4050 Laptop GPU, 6144, 2000, 8.9, 610.74') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('0, NVIDIA GeForce GTX 1650, 4096, 3000, 7.5, 610.74') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('unknown') + #13#10'---'#13#10;
  Results := Results + DescribeGPU('1, NVIDIA GeForce RTX 5070 Ti, 16303, 14000, 12.0, 610.74');
  if not SaveStringToFile('{quote(output)}', Results, False) then RaiseException('Cannot save fixture');
  Result := False;
end;
""", encoding="utf-8")
    compiled = subprocess.run([str(compiler), "/Q", str(script)], capture_output=True,
                              text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(tmp_path / "HardwareFixture.exe"), "/VERYSILENT",
                             "/SUPPRESSMSGBOXES", "/SP-"], capture_output=True, timeout=30)
    assert result.returncode != 0  # Fixture stops before installation by design.
    profiles = output.read_text(encoding="utf-8").split("---")
    assert len(profiles) == 7
    assert "mode: CUDA + Int8 / Float16" in profiles[0]
    assert "mode: CUDA + Int8 / Float16" in profiles[1]
    assert "mode: CPU + Int8" in profiles[2] and "576.57 or later" in profiles[2]
    assert "6 GB is a trial profile" in profiles[3] and "Less than 3 GiB" in profiles[3]
    assert "mode: CPU + Int8" in profiles[4]
    assert "could not be interpreted" in profiles[5]
    assert "Additional GPU" in profiles[6]
