[Code]
// Call the same versioned preparation entry point as the portable application.
var
  SpeechPreparationPage: TOutputProgressWizardPage;
  SpeechPreparationFailed: Boolean;

procedure SpeechSetupLog(const S: String; const Error, FirstLine: Boolean);
begin
  Log('STT: ' + S);
  if SpeechPreparationPage <> nil then
    SpeechPreparationPage.SetText(S, '');
end;

<event('CurStepChanged')>
procedure PrepareInstalledSpeech(const CurStep: TSetupStep);
var
  Params, ConfigFile: String;
  ExitCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  if WizardIsTaskSelected('preparestt') then
    Params := '--prepare-stt'
  else
    Params := '--defer-stt';
  ConfigFile := ExpandConstant('{param:STTCONFIG|}');
  if ConfigFile <> '' then begin
    if Pos('"', ConfigFile) > 0 then RaiseException('Invalid STT configuration path');
    Params := Params + ' --config "' + ConfigFile + '"';
  end;
  SpeechPreparationPage := CreateOutputProgressPage('Preparing speech recognition',
    'Checking graphics hardware and compiling the local speech backend');
  SpeechPreparationPage.SetText('Detecting NVIDIA, Intel and AMD graphics adapters…', '');
  SpeechPreparationPage.Show;
  try
    if not ExecAndLogOutput(ExpandConstant('{app}\InterviewAssistant.exe'), Params,
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode, @SpeechSetupLog) then
      SpeechPreparationFailed := True
    else
      SpeechPreparationFailed := ExitCode <> 0;
  finally
    SpeechPreparationPage.Hide;
  end;
  if SpeechPreparationFailed then begin
    Log('Speech setup failed. The application can retry from Settings > Audio.');
    if not WizardSilent then
      MsgBox('Speech preparation failed. Open Settings > Audio and choose Detect GPU and prepare to retry.',
        mbError, MB_OK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if SpeechPreparationFailed then Result := 10 else Result := 0;
end;
