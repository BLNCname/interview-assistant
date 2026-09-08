// Native Inno Setup 6.7.2 download/extraction support. No executable downloader.
#ifndef OnlineCacheDirectory
  #define OnlineCacheDirectory "{localappdata}\InterviewAssistant\InstallerCache"
#endif

type
  TPayloadArtifact = record
    Id, Kind, Url, SHA256, DisplayName, CacheName: String;
    Size, ExpandedSize: Int64;
  end;
  TPayloadFile = record
    RelativePath, MemberPath, SHA256: String;
    ArtifactIndex: Integer;
    Size: Int64;
  end;

var
  Artifacts: array of TPayloadArtifact;
  PayloadFiles: array of TPayloadFile;
  PayloadLoaded: Boolean;
  PayloadCacheRoot, PayloadStageRoot: String;
  PayloadDownloadPage: TDownloadWizardPage;
  PayloadExtractionPage: TExtractionWizardPage;
  PayloadValidationPage: TOutputProgressWizardPage;
  PayloadCacheHits: array of Boolean;

#include PayloadDataPath

function PayloadGetFileAttributes(const Path: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function PayloadGetLastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';

procedure CheckPayloadPath(const Path: String);
var
  Current, Parent: String;
  Attributes, Error: LongWord;
begin
  Current := Path;
  while Current <> '' do begin
    Attributes := PayloadGetFileAttributes(Current);
    if Attributes = $FFFFFFFF then begin
      Error := PayloadGetLastError;
      if (Error <> 2) and (Error <> 3) then
        RaiseException('Cannot inspect installer payload path: ' + Current);
    end else if (Attributes and $400) <> 0 then
      RaiseException('Installer payload paths must not contain symbolic links or junctions: ' + Current);
    Parent := ExtractFileDir(Current);
    if Parent = Current then
      Exit;
    Current := Parent;
  end;
end;

function PayloadPath(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '/', '\', True);
end;

function SafePayloadPath(const Value: String): Boolean;
var
  Path: String;
begin
  Path := PayloadPath(Value);
  Result := (Path <> '') and (Copy(Path, 1, 1) <> '\') and
    (Pos(':', Path) = 0) and (Pos('*', Path) = 0) and (Pos('?', Path) = 0) and
    (Pos('"', Path) = 0) and (Pos('<', Path) = 0) and (Pos('>', Path) = 0) and
    (Pos('|', Path) = 0) and (Pos('\..\', '\' + Path + '\') = 0) and
    (Pos('\.\', '\' + Path + '\') = 0);
end;

function SafePayloadName(const Value: String): Boolean;
begin
  Result := SafePayloadPath(Value) and (Pos('\', PayloadPath(Value)) = 0);
end;

function ValidPayloadHash(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := Length(Value) = 64;
  if Result then
    for I := 1 to Length(Value) do
      if Pos(Lowercase(Copy(Value, I, 1)), '0123456789abcdef') = 0 then
        Result := False;
end;

procedure EnsurePayloadLoaded;
begin
  if not PayloadLoaded then begin
    LoadOnlinePayload;
    PayloadCacheRoot := ExpandConstant('{#OnlineCacheDirectory}');
    PayloadStageRoot := ExpandConstant('{tmp}\payload');
    PayloadLoaded := True;
  end;
end;

function ArtifactCachePath(const Index: Integer): String;
begin
  Result := AddBackslash(PayloadCacheRoot) + Artifacts[Index].CacheName;
end;

function GetPreparedFile(const Param: String): String;
var
  Index, ArtifactIndex: Integer;
begin
  EnsurePayloadLoaded;
  Index := StrToIntDef(Param, -1);
  if (Index < 0) or (Index >= GetArrayLength(PayloadFiles)) then
    RaiseException('The installer contains an invalid payload file index.');
  ArtifactIndex := PayloadFiles[Index].ArtifactIndex;
  if (ArtifactIndex < 0) or (ArtifactIndex >= GetArrayLength(Artifacts)) then
    RaiseException('The installer contains an invalid artifact index.');
  if Artifacts[ArtifactIndex].Kind = 'file' then
    Result := ArtifactCachePath(ArtifactIndex)
  else
    Result := AddBackslash(PayloadStageRoot) + Artifacts[ArtifactIndex].Id + '\' +
      PayloadPath(PayloadFiles[Index].MemberPath);
end;

procedure ValidatePayloadMetadata;
var
  I, Index: Integer;
begin
  for I := 0 to GetArrayLength(Artifacts) - 1 do begin
    if not SafePayloadName(Artifacts[I].Id) or
      not SafePayloadName(Artifacts[I].CacheName) or
      not ValidPayloadHash(Artifacts[I].SHA256) or
      (Artifacts[I].Size <= 0) or (Artifacts[I].ExpandedSize <= 0) or
      ((Artifacts[I].Kind <> 'file') and (Artifacts[I].Kind <> 'wheel')) then
      RaiseException('The installer contains invalid artifact metadata.');
    if Artifacts[I].Kind = 'wheel' then begin
      if CompareText(Artifacts[I].CacheName, Artifacts[I].SHA256 + '.zip') <> 0 then
        RaiseException('The installer contains an invalid wheel cache name.');
    end else if CompareText(Artifacts[I].CacheName, Artifacts[I].SHA256 + '.bin') <> 0 then
      RaiseException('The installer contains an invalid file cache name.');
  end;
  for I := 0 to GetArrayLength(PayloadFiles) - 1 do begin
    Index := PayloadFiles[I].ArtifactIndex;
    if (Index < 0) or (Index >= GetArrayLength(Artifacts)) or
      not SafePayloadPath(PayloadFiles[I].RelativePath) or
      not ValidPayloadHash(PayloadFiles[I].SHA256) or (PayloadFiles[I].Size < 0) then
      RaiseException('The installer contains invalid payload file metadata.');
    if (Artifacts[Index].Kind = 'wheel') and
      not SafePayloadPath(PayloadFiles[I].MemberPath) then
      RaiseException('The installer contains an unsafe archive member path.');
  end;
end;

function PayloadFileMatches(const Path, Hash: String; const ExpectedSize: Int64): Boolean;
var
  ActualSize: Int64;
begin
  Result := False;
  CheckPayloadPath(Path);
  try
    if FileSize64(Path, ActualSize) and (ActualSize = ExpectedSize) then
      Result := CompareText(GetSHA256OfFile(Path), Hash) = 0;
  except
    Log('Unable to verify cached or prepared payload file.');
  end;
end;

function ExistingPayloadDirectory(const Path: String): String;
var
  Parent: String;
begin
  Result := Path;
  while not DirExists(Result) do begin
    Parent := ExtractFileDir(Result);
    if (Parent = '') or (Parent = Result) then
      RaiseException('Cannot determine available disk space for ' + Path);
    Result := Parent;
  end;
end;

procedure CheckPayloadDiskSpace;
var
  I, J: Integer;
  Locations: array[0..2] of String;
  Needed: array[0..2] of Int64;
  Missing, LargestDownload, Staging, Installed, Required, Free, Total: Int64;
begin
  Missing := 0;
  LargestDownload := 0;
  Staging := 0;
  Installed := 0;
  SetArrayLength(PayloadCacheHits, GetArrayLength(Artifacts));
  for I := 0 to GetArrayLength(Artifacts) - 1 do begin
    PayloadCacheHits[I] := PayloadFileMatches(ArtifactCachePath(I), Artifacts[I].SHA256, Artifacts[I].Size);
    if not PayloadCacheHits[I] then begin
      Missing := Missing + Artifacts[I].Size;
      if Artifacts[I].Size > LargestDownload then
        LargestDownload := Artifacts[I].Size;
    end;
    if Artifacts[I].Kind = 'wheel' then
      Staging := Staging + Artifacts[I].ExpandedSize;
  end;
  for I := 0 to GetArrayLength(PayloadFiles) - 1 do
    Installed := Installed + PayloadFiles[I].Size;
  Locations[0] := PayloadCacheRoot;
  Locations[1] := ExpandConstant('{tmp}');
  Locations[2] := ExpandConstant('{app}');
  Needed[0] := Missing;
  Needed[1] := LargestDownload + Staging;
  Needed[2] := Installed;
  for I := 0 to 2 do begin
    { Reserve 256 MiB for the embedded core, filesystem overhead, and uninstall
      records, in addition to Inno's own target-directory space check. }
    Required := 268435456;
    for J := 0 to 2 do
      if CompareText(ExtractFileDrive(Locations[I]), ExtractFileDrive(Locations[J])) = 0 then
        Required := Required + Needed[J];
    if not GetSpaceOnDisk64(ExistingPayloadDirectory(Locations[I]), Free, Total) then
      RaiseException('Cannot check available disk space for ' + Locations[I]);
    if Free < Required then
      RaiseException('Not enough free disk space for download, staging, and installation. ' +
        'Required on ' + ExtractFileDrive(Locations[I]) + ': ' + IntToStr(Required div 1048576) +
        ' MiB; available: ' + IntToStr(Free div 1048576) + ' MiB.');
  end;
end;

procedure CachePayloadArtifact(const Index: Integer);
var
  CachePath, DownloadedPath, CopyPath, Error: String;
  Attempt: Integer;
begin
  CachePath := ArtifactCachePath(Index);
  if PayloadCacheHits[Index] and
    PayloadFileMatches(CachePath, Artifacts[Index].SHA256, Artifacts[Index].Size) then begin
    Log('Verified payload cache hit: ' + Artifacts[Index].DisplayName);
    Exit;
  end;
  if FileExists(CachePath) and not DeleteFile(CachePath) then
    RaiseException('Cannot replace a corrupt installer cache file: ' + CachePath);
  for Attempt := 1 to 3 do begin
    PayloadDownloadPage.Clear;
    PayloadDownloadPage.Add(Artifacts[Index].Url, Artifacts[Index].CacheName, Artifacts[Index].SHA256);
    PayloadDownloadPage.SetText(Artifacts[Index].DisplayName + ' (attempt ' + IntToStr(Attempt) + '/3)', '');
    PayloadDownloadPage.Show;
    Error := '';
    try
      try
        PayloadDownloadPage.Download;
        DownloadedPath := ExpandConstant('{tmp}\') + Artifacts[Index].CacheName;
        if not PayloadFileMatches(DownloadedPath, Artifacts[Index].SHA256, Artifacts[Index].Size) then
          RaiseException('The downloaded payload size or SHA-256 does not match.');
        CheckPayloadPath(CachePath);
        if not RenameFile(DownloadedPath, CachePath) then begin
          { TEMP may reside on another drive. Publish only a complete, verified
            copy, using a private staging name on the cache's own volume. }
          CopyPath := CachePath + '.tmp';
          CheckPayloadPath(CopyPath);
          if FileExists(CopyPath) and not DeleteFile(CopyPath) then
            RaiseException('Cannot clear an incomplete cache copy.');
          try
            if not FileCopy(DownloadedPath, CopyPath, True) or
              not PayloadFileMatches(CopyPath, Artifacts[Index].SHA256, Artifacts[Index].Size) or
              not RenameFile(CopyPath, CachePath) then
              RaiseException('Cannot retain the verified download in the installer cache.');
          finally
            if FileExists(CopyPath) then
              DeleteFile(CopyPath);
          end;
          DeleteFile(DownloadedPath);
        end;
        Log('Verified payload cached: ' + Artifacts[Index].DisplayName);
        Exit;
      except
        Error := GetExceptionMessage;
        if PayloadDownloadPage.AbortedByUser then
          RaiseException('Download cancelled. The installed application was not changed.');
        Log('Payload preparation attempt failed: ' + Error);
      end;
    finally
      PayloadDownloadPage.Hide;
    end;
  end;
  RaiseException('Unable to download ' + Artifacts[Index].DisplayName +
    ' after 3 attempts. Check the connection and retry Setup. ' + Error);
end;

procedure ExtractPayloadWheels;
var
  I: Integer;
begin
  for I := 0 to GetArrayLength(Artifacts) - 1 do
    if Artifacts[I].Kind = 'wheel' then begin
      PayloadExtractionPage.Clear;
      if not PayloadFileMatches(ArtifactCachePath(I), Artifacts[I].SHA256, Artifacts[I].Size) then
        RaiseException('Cached archive changed before extraction: ' + Artifacts[I].DisplayName);
      CheckPayloadPath(AddBackslash(PayloadStageRoot) + Artifacts[I].Id);
      PayloadExtractionPage.Add(ArtifactCachePath(I), AddBackslash(PayloadStageRoot) + Artifacts[I].Id, True);
      PayloadExtractionPage.Show;
      try
        PayloadExtractionPage.Extract;
      finally
        PayloadExtractionPage.Hide;
      end;
    end;
end;

procedure VerifyPreparedPayload;
var
  I: Integer;
begin
  PayloadValidationPage.Show;
  try
    for I := 0 to GetArrayLength(PayloadFiles) - 1 do begin
      PayloadValidationPage.SetText('Verifying prepared application files', PayloadFiles[I].RelativePath);
      PayloadValidationPage.SetProgress(I, GetArrayLength(PayloadFiles));
      if not PayloadFileMatches(GetPreparedFile(IntToStr(I)), PayloadFiles[I].SHA256, PayloadFiles[I].Size) then
        RaiseException('Prepared payload verification failed: ' + PayloadFiles[I].RelativePath);
    end;
  finally
    PayloadValidationPage.Hide;
  end;
end;

procedure InitializeWizard;
begin
  EnsurePayloadLoaded;
  PayloadDownloadPage := CreateDownloadPage('Downloading dependencies', 'Preparing the application', nil);
  PayloadDownloadPage.ShowBaseNameInsteadOfUrl := True;
  PayloadExtractionPage := CreateExtractionPage('Preparing dependencies', 'Extracting verified archives', nil);
  PayloadExtractionPage.ShowArchiveInsteadOfFile := True;
  PayloadValidationPage := CreateOutputProgressPage('Checking application files', 'Checking sizes and SHA-256');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  Result := '';
  try
    EnsurePayloadLoaded;
    ValidatePayloadMetadata;
    CheckPayloadPath(ExpandConstant('{app}\_internal'));
    CheckPayloadPath(PayloadCacheRoot);
    CheckPayloadPath(PayloadStageRoot);
    if not ForceDirectories(PayloadCacheRoot) then
      RaiseException('Cannot create the installer download cache: ' + PayloadCacheRoot);
    CheckPayloadDiskSpace;
    for I := 0 to GetArrayLength(Artifacts) - 1 do
      CachePayloadArtifact(I);
    ExtractPayloadWheels;
    VerifyPreparedPayload;
    Log('All online payload files are prepared and verified; installation may begin.');
  except
    Result := GetExceptionMessage;
    Log('Online payload preparation failed: ' + Result);
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfoInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfoInfo + NewLine + MemoGroupInfo + NewLine + MemoTasksInfo + NewLine + NewLine +
    'Setup downloads and verifies the required dependencies and STT model before installation.' + NewLine +
    'Completed downloads are retained for retries and future installations in:' + NewLine + PayloadCacheRoot;
end;
