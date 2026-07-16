#define MyAppName "Речка"
#define MyAppExeName "Rechka.exe"
#ifndef MyAppVersion
#define MyAppVersion "0.3.3"
#endif
#define MyAppPublisher "EBSF"

[Setup]
AppId={{D4ACD420-4548-4D21-9FA9-3AA5BA7896D5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Локальный голосовой ввод на базе Whisper
AppReadmeFile={app}\README.md
SetupIconFile=..\assets\voiceinput.ico
DefaultDirName={localappdata}\Programs\VoiceInput
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist\installer
OutputBaseFilename=VoiceInput-Setup-{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
CloseApplicationsFilter=VoiceInput.exe,Rechka.exe
RestartApplications=no
AppMutex=Local\VoiceInputDesktopApp
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Установщик локального голосового ввода
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\VoiceInput-{#MyAppVersion}\Rechka\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[InstallDelete]
Type: files; Name: "{autoprograms}\Голосовой ввод.lnk"
Type: files; Name: "{autodesktop}\Голосовой ввод.lnk"
Type: files; Name: "{autoprograms}\Речка.lnk"
Type: files; Name: "{autodesktop}\Речка.lnk"
Type: files; Name: "{app}\VoiceInput.exe"
Type: filesandordirs; Name: "{app}\models\faster-whisper-small"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "VoiceInput"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait
