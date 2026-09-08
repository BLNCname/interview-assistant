[Code]
// Hardware advice for the actual STT engine: faster-whisper / CTranslate2.
// The same pinned CUDA 12 runtime is used for Ampere and Blackwell GPUs.
// This page does not mutate an existing application configuration.
var
  HardwarePage: TWizardPage;
  HardwareText: TNewMemo;

function VersionNumber(const Value: String): Integer;
var
  Parts: TArrayOfString;
begin
  Parts := StringSplit(Trim(Value), ['.'], stExcludeEmpty);
  Result := -1;
  if GetArrayLength(Parts) = 2 then
    if (StrToIntDef(Parts[0], -1) >= 0) and (StrToIntDef(Parts[1], -1) >= 0) then
      Result := StrToIntDef(Parts[0], -1) * 100 + StrToIntDef(Parts[1], -1);
end;

function DescribeGPU(const Line: String): String;
var
  Fields: TStringList;
  TotalMemory, FreeMemory, Capability, Driver: Integer;
begin
  Result := 'GPU information could not be interpreted. Select CPU + Int8 in Settings > Audio.';
  Fields := TStringList.Create;
  try
    Fields.StrictDelimiter := True;
    Fields.Delimiter := ',';
    Fields.DelimitedText := Line;
    if Fields.Count <> 6 then
      Exit;
    TotalMemory := StrToIntDef(Trim(Fields[2]), -1);
    FreeMemory := StrToIntDef(Trim(Fields[3]), -1);
    Capability := VersionNumber(Fields[4]);
    Driver := VersionNumber(Fields[5]);
    Result := Format('GPU %s (nvidia-smi): %s'#13#10'VRAM: %s MiB total, %s MiB free; compute capability %s; driver %s.', [
      Trim(Fields[0]), Trim(Fields[1]), Trim(Fields[2]), Trim(Fields[3]), Trim(Fields[4]), Trim(Fields[5])]);
    if Trim(Fields[0]) <> '0' then begin
      Result := Result + #13#10'Additional GPU: verify the effective CUDA device after installation; CUDA and nvidia-smi numbering may differ.';
      Exit;
    end;
    if (Capability < 700) or (Driver < 57657) or (TotalMemory < 5500) then begin
      Result := Result + #13#10'Recommended initial mode: CPU + Int8 (Settings > Audio).';
      if Driver < 57657 then
        Result := Result + #13#10'For the bundled CUDA profile, a current NVIDIA driver is recommended (576.57 or later).';
      if Capability < 700 then
        Result := Result + #13#10'GPU architecture is unavailable or outside the recommended Int8/Float16 profile.';
      if TotalMemory < 5500 then
        Result := Result + #13#10'Available GPU memory capacity is below the 6 GB trial profile.';
    end else begin
      Result := Result + #13#10'Recommended initial mode: CUDA + Int8 / Float16 (Settings > Audio).';
      if TotalMemory < 7500 then
        Result := Result + #13#10'6 GB is a trial profile; 8 GB or more is recommended.';
      if FreeMemory < 3072 then
        Result := Result + #13#10'Less than 3 GiB is free now. Close GPU workloads or use CPU + Int8.';
    end;
  finally
    Fields.Free;
  end;
end;

function ProbeHardware: String;
var
  Output: TExecOutput;
  ExitCode, I: Integer;
  Probe: String;
begin
  Result := 'No usable NVIDIA driver was detected. The application can use CPU + Int8 in Settings > Audio.';
  try
    ExtractTemporaryFile('installer_gpu_probe.ps1');
    Probe := ExpandConstant('{tmp}\installer_gpu_probe.ps1');
    if not ExecAndCaptureOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Probe + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode, Output) then
      Exit;
    if (ExitCode <> 0) or Output.Error or (GetArrayLength(Output.StdOut) = 0) then
      Exit;
    Result := '';
    for I := 0 to GetArrayLength(Output.StdOut) - 1 do begin
      if I >= 4 then begin
        Result := Result + #13#10'Additional GPUs omitted.';
        Break;
      end;
      if Trim(Output.StdOut[I]) <> '' then begin
        if Result <> '' then Result := Result + #13#10#13#10;
        Result := Result + DescribeGPU(Output.StdOut[I]);
      end;
    end;
  except
    Log('GPU probe unavailable: ' + GetExceptionMessage);
  end;
end;

<event('InitializeWizard')>
procedure InitializeHardwarePage;
var
  Advice: String;
begin
  Advice := ProbeHardware;
  Log('STT hardware: ' + Advice);
  HardwarePage := CreateCustomPage(wpWelcome, 'Speech recognition hardware',
    'Detected GPU and recommended settings');
  HardwareText := TNewMemo.Create(HardwarePage);
  HardwareText.Parent := HardwarePage.Surface;
  HardwareText.SetBounds(0, 0, HardwarePage.SurfaceWidth, HardwarePage.SurfaceHeight);
  HardwareText.ReadOnly := True;
  HardwareText.ScrollBars := ssVertical;
  HardwareText.WordWrap := True;
  HardwareText.Text := Advice + #13#10#13#10 +
    'STT uses faster-whisper / CTranslate2, not PyTorch. Setup downloads the same tested runtime for RTX 3060 Ti and RTX 5070 Ti; no local compilation is required.' + #13#10#13#10 +
    'Set the recommended mode in Settings > Audio after installation. Existing settings are preserved. Actual speed and available VRAM depend on other applications.';
end;
