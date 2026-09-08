#ifndef AppVersion
  #define AppVersion "0.1.3"
#endif
#ifndef DistPath
  #define DistPath "..\dist\InterviewAssistant"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist\online-installer"
#endif
#ifndef PayloadFilesPath
  #error PayloadFilesPath is required
#endif
#ifndef PayloadDataPath
  #error PayloadDataPath is required
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
OutputBaseFilename=Setup
Compression=none
DiskSpanning=no
ArchiveExtraction=full
SetupIconFile={#SourcePath}\..\assets\branding\interview-assistant.ico
WizardStyle=modern
SetupLogging=yes
SetupMutex=InterviewAssistantOnlineSetup
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\InterviewAssistant.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

#include "online_legacy_cleanup.iss"

[Files]
Source: "{#SourcePath}\installer_gpu_probe.ps1"; Flags: dontcopy
#include PayloadFilesPath

[Icons]
Name: "{group}\Interview Assistant"; Filename: "{app}\InterviewAssistant.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Interview Assistant"; Filename: "{app}\InterviewAssistant.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
#include "online_downloads.iss"
#include "online_hardware.iss"
