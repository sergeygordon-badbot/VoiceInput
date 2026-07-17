#define MyAppName "Речка"
#define MyAppExeName "Rechka.exe"
#ifndef MyAppVersion
#define MyAppVersion "0.6.0"
#endif
#ifndef MyAppSourceDir
#define MyAppSourceDir "..\dist\VoiceInput-" + MyAppVersion + "\Rechka"
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
DisableReadyPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist\installer
OutputBaseFilename=VoiceInput-Setup-{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern light zircon includetitlebar
WizardSizePercent=110,110
WizardImageFile=..\assets\installer-wizard.png
WizardSmallImageFile=..\assets\installer-small.png
WizardImageBackColor=$00EBF1F3
WizardSmallImageBackColor=$00EBF1F3
SetupLogging=yes
CloseApplications=force
CloseApplicationsFilter=VoiceInput.exe,Rechka.exe
RestartApplications=no
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
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait

[Code]
function IsUpdateInstall: Boolean;
begin
  Result := CompareText(ExpandConstant('{param:UPDATE|0}'), '1') = 0;
end;

function GetLaunchParameters(Param: String): String;
begin
  if IsUpdateInstall then
    Result := '--minimized'
  else
    Result := '';
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel1.Caption := 'Речка';
  WizardForm.WelcomeLabel2.Caption :=
    'Локальный голосовой ввод для Windows 11.' + #13#10 + #13#10 +
    'Говорите свободно — получите готовый текст в любом приложении.';
  WizardForm.FinishedHeadingLabel.Caption := 'Речка готова';
  WizardForm.FinishedLabel.Caption :=
    'Программа установлена и будет запущена после закрытия мастера.';
end;
