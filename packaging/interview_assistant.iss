#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef DistPath
  #define DistPath "..\dist\InterviewAssistant"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist\installer"
#endif

[Setup]
AppId={{9CE7901A-56E8-49CB-A8ED-8D5CF4F97C7D}
AppName=Interview Assistant
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\InterviewAssistant
DefaultGroupName=Interview Assistant
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputPath}
OutputBaseFilename=InterviewAssistant-Setup-{#AppVersion}-win64
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\InterviewAssistant.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#DistPath}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Interview Assistant"; Filename: "{app}\InterviewAssistant.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Interview Assistant"; Filename: "{app}\InterviewAssistant.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\InterviewAssistant.exe"
