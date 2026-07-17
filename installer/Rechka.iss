#define MyAppName "Речка"
#define MyAppExeName "Rechka.exe"
#ifndef MyAppVersion
#error MyAppVersion must be provided by build-installer.ps1
#endif
#ifndef MyAppSourceDir
#define MyAppSourceDir "..\dist\Rechka-" + MyAppVersion + "\Rechka"
#endif
#define MyAppPublisher "EBSF"

[Setup]
AppId={{D4ACD420-4548-4D21-9FA9-3AA5BA7896D5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Автоматический голосовой ввод с сетевым и локальным распознаванием
AppReadmeFile={app}\README.md
SetupIconFile=..\assets\voiceinput.ico
DefaultDirName={localappdata}\Programs\Rechka
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist\installer
OutputBaseFilename=Rechka-Setup-{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern light windows11 hidebevels
WizardSizePercent=115,115
WizardImageFile=
WizardSmallImageFile=..\assets\installer-small.png
WizardSmallImageBackColor=$00FFFFFF
ShowLanguageDialog=no
SetupLogging=yes
CloseApplications=force
CloseApplicationsFilter=Rechka.exe
RestartApplications=no
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Установщик Речки
VersionInfoOriginalFileName=Rechka-Setup-{#MyAppVersion}.exe
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
Type: filesandordirs; Name: "{app}\models\faster-whisper-tiny"
Type: filesandordirs; Name: "{app}\models\faster-whisper-small"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "VoiceInput"; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Rechka"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Description: "Запустить Речку"; WorkingDir: "{app}"; Flags: nowait

[Code]
var
  WelcomeVersion: TNewStaticText;
  WelcomePoint1: TNewStaticText;
  WelcomePoint2: TNewStaticText;
  WelcomePoint3: TNewStaticText;
  WelcomeNote: TNewStaticText;
  WelcomeAccent: TPanel;

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

function CreateText(
  Parent: TWinControl;
  ALeft, ATop, AWidth, AHeight: Integer;
  const ACaption: String;
  AFontSize: Integer;
  ABold: Boolean
): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := Parent;
  Result.Left := ALeft;
  Result.Top := ATop;
  Result.Width := AWidth;
  Result.Height := AHeight;
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.Caption := ACaption;
  Result.Font.Size := AFontSize;
  if ABold then
    Result.Font.Style := [fsBold];
end;

procedure StyleNavigation;
var
  ButtonTop: Integer;
  ButtonHeight: Integer;
begin
  ButtonTop := WizardForm.NextButton.Top;
  ButtonHeight := WizardForm.NextButton.Height;

  WizardForm.NextButton.Width := ScaleX(132);
  WizardForm.NextButton.Height := ButtonHeight;
  WizardForm.NextButton.Left :=
    WizardForm.ClientWidth - WizardForm.NextButton.Width - ScaleX(24);
  WizardForm.NextButton.Top := ButtonTop;
  WizardForm.NextButton.Font.Style := [fsBold];

  WizardForm.CancelButton.Width := ScaleX(92);
  WizardForm.CancelButton.Height := ButtonHeight;
  WizardForm.CancelButton.Left :=
    WizardForm.NextButton.Left - WizardForm.CancelButton.Width - ScaleX(12);
  WizardForm.CancelButton.Top := ButtonTop;

  WizardForm.BackButton.Width := ScaleX(92);
  WizardForm.BackButton.Height := ButtonHeight;
  WizardForm.BackButton.Left :=
    WizardForm.CancelButton.Left - WizardForm.BackButton.Width - ScaleX(12);
  WizardForm.BackButton.Top := ButtonTop;
end;

procedure InitializeWizard;
var
  WelcomeParent: TWinControl;
  FinishedParent: TWinControl;
  PreparingParent: TWinControl;
  ContentLeft: Integer;
  ContentWidth: Integer;
begin
  WizardForm.Caption := 'Установка Речки';
  WizardForm.Bevel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.WizardBitmapImage.Visible := False;
  WizardForm.WizardBitmapImage2.Visible := False;

  ContentLeft := ScaleX(44);
  WelcomeParent := WizardForm.WelcomeLabel1.Parent;
  ContentWidth := WelcomeParent.ClientWidth - (ContentLeft * 2);

  WizardForm.WelcomeLabel1.Caption := 'Речка';
  WizardForm.WelcomeLabel1.Left := ContentLeft;
  WizardForm.WelcomeLabel1.Top := ScaleY(34);
  WizardForm.WelcomeLabel1.Width := ScaleX(300);
  WizardForm.WelcomeLabel1.Height := ScaleY(38);
  WizardForm.WelcomeLabel1.Font.Size := 22;
  WizardForm.WelcomeLabel1.Font.Style := [fsBold];

  WelcomeVersion := CreateText(
    WelcomeParent,
    WelcomeParent.ClientWidth - ContentLeft - ScaleX(120),
    ScaleY(45),
    ScaleX(120),
    ScaleY(22),
    'Версия {#MyAppVersion}',
    9,
    False
  );
  WelcomeVersion.Alignment := taRightJustify;

  WelcomeAccent := TPanel.Create(WizardForm);
  WelcomeAccent.Parent := WelcomeParent;
  WelcomeAccent.Left := ContentLeft;
  WelcomeAccent.Top := ScaleY(105);
  WelcomeAccent.Width := ScaleX(4);
  WelcomeAccent.Height := ScaleY(58);
  WelcomeAccent.BevelOuter := bvNone;
  WelcomeAccent.ParentBackground := False;
  WelcomeAccent.Color := $0036FFC7;

  WizardForm.WelcomeLabel2.Caption :=
    'Говорите — Речка превратит голос в готовый текст.' + #13#10 +
    'Сама подберёт быстрый сетевой или локальный режим.';
  WizardForm.WelcomeLabel2.Left := ContentLeft + ScaleX(18);
  WizardForm.WelcomeLabel2.Top := ScaleY(103);
  WizardForm.WelcomeLabel2.Width := ContentWidth - ScaleX(18);
  WizardForm.WelcomeLabel2.Height := ScaleY(64);
  WizardForm.WelcomeLabel2.Font.Size := 11;
  WizardForm.WelcomeLabel2.Font.Style := [];

  WelcomePoint1 := CreateText(
    WelcomeParent,
    ContentLeft,
    ScaleY(190),
    ContentWidth,
    ScaleY(26),
    '•  Диктовка в любом приложении',
    10,
    False
  );
  WelcomePoint2 := CreateText(
    WelcomeParent,
    ContentLeft,
    ScaleY(226),
    ContentWidth,
    ScaleY(26),
    '•  Аккуратный текст со знаками препинания',
    10,
    False
  );
  WelcomePoint3 := CreateText(
    WelcomeParent,
    ContentLeft,
    ScaleY(262),
    ContentWidth,
    ScaleY(26),
    '•  Быстрый запуск: Ctrl + Пробел',
    10,
    False
  );
  WelcomeNote := CreateText(
    WelcomeParent,
    ContentLeft,
    WelcomeParent.ClientHeight - ScaleY(42),
    ContentWidth,
    ScaleY(24),
    'Установка займёт несколько минут.',
    9,
    False
  );

  WizardForm.PageNameLabel.Left := ScaleX(28);
  WizardForm.PageNameLabel.Top := ScaleY(9);
  WizardForm.PageNameLabel.Width :=
    WizardForm.MainPanel.ClientWidth - ScaleX(110);
  WizardForm.PageNameLabel.Height := ScaleY(27);
  WizardForm.PageNameLabel.Font.Size := 13;
  WizardForm.PageNameLabel.Font.Style := [fsBold];
  WizardForm.PageDescriptionLabel.Left := WizardForm.PageNameLabel.Left;
  WizardForm.PageDescriptionLabel.Top := ScaleY(37);
  WizardForm.PageDescriptionLabel.Width :=
    WizardForm.PageNameLabel.Width;
  WizardForm.PageDescriptionLabel.Height := ScaleY(20);
  WizardForm.WizardSmallBitmapImage.Left :=
    WizardForm.MainPanel.ClientWidth - ScaleX(58);
  WizardForm.WizardSmallBitmapImage.Top := ScaleY(8);
  WizardForm.WizardSmallBitmapImage.Width := ScaleX(42);
  WizardForm.WizardSmallBitmapImage.Height := ScaleY(42);
  WizardForm.WizardSmallBitmapImage.Stretch := True;

  PreparingParent := WizardForm.PreparingLabel.Parent;
  WizardForm.PreparingErrorBitmapImage.Visible := False;
  WizardForm.PreparingLabel.Left := ContentLeft;
  WizardForm.PreparingLabel.Top := ScaleY(40);
  WizardForm.PreparingLabel.Width :=
    PreparingParent.ClientWidth - (ContentLeft * 2);
  WizardForm.PreparingLabel.Height := ScaleY(62);
  WizardForm.PreparingLabel.Font.Size := 11;
  WizardForm.PreparingMemo.Visible := False;
  WizardForm.PreparingYesRadio.Left := ContentLeft;
  WizardForm.PreparingYesRadio.Top := ScaleY(132);
  WizardForm.PreparingYesRadio.Width := ContentWidth;
  WizardForm.PreparingYesRadio.Height := ScaleY(28);
  WizardForm.PreparingYesRadio.Caption :=
    'Закрыть Речку и продолжить';
  WizardForm.PreparingYesRadio.Font.Style := [fsBold];
  WizardForm.PreparingNoRadio.Left := ContentLeft;
  WizardForm.PreparingNoRadio.Top := ScaleY(172);
  WizardForm.PreparingNoRadio.Width := ContentWidth;
  WizardForm.PreparingNoRadio.Height := ScaleY(28);
  WizardForm.PreparingNoRadio.Caption :=
    'Не закрывать — я сделаю это сам';

  WizardForm.StatusLabel.Left := ScaleX(32);
  WizardForm.StatusLabel.Top := ScaleY(64);
  WizardForm.StatusLabel.Width :=
    WizardForm.InstallingPage.ClientWidth - ScaleX(64);
  WizardForm.ProgressGauge.Left := WizardForm.StatusLabel.Left;
  WizardForm.ProgressGauge.Top := ScaleY(102);
  WizardForm.ProgressGauge.Width := WizardForm.StatusLabel.Width;
  WizardForm.ProgressGauge.Height := ScaleY(10);
  WizardForm.FilenameLabel.Visible := False;

  FinishedParent := WizardForm.FinishedHeadingLabel.Parent;
  WizardForm.FinishedHeadingLabel.Caption := 'Речка готова';
  WizardForm.FinishedHeadingLabel.Left := ContentLeft;
  WizardForm.FinishedHeadingLabel.Top := ScaleY(36);
  WizardForm.FinishedHeadingLabel.Width :=
    FinishedParent.ClientWidth - WizardForm.FinishedHeadingLabel.Left -
    ContentLeft;
  WizardForm.FinishedHeadingLabel.Height := ScaleY(40);
  WizardForm.FinishedHeadingLabel.Font.Size := 20;
  WizardForm.FinishedHeadingLabel.Font.Style := [fsBold];
  WizardForm.FinishedLabel.Caption :=
    'Всё установлено. Запустите Речку и нажмите Ctrl + Пробел,' + #13#10 +
    'чтобы начать диктовку.';
  WizardForm.FinishedLabel.Left := ContentLeft;
  WizardForm.FinishedLabel.Top := ScaleY(122);
  WizardForm.FinishedLabel.Width :=
    FinishedParent.ClientWidth - (ContentLeft * 2);
  WizardForm.FinishedLabel.Height := ScaleY(58);
  WizardForm.FinishedLabel.Font.Size := 11;
  WizardForm.RunList.Left := ContentLeft;
  WizardForm.RunList.Top := ScaleY(206);
  WizardForm.RunList.Width :=
    FinishedParent.ClientWidth - (ContentLeft * 2);
  WizardForm.RunList.Height := ScaleY(46);
  WizardForm.RunList.BorderStyle := bsNone;

  StyleNavigation;
  WizardForm.NextButton.Caption := 'Установить';
  WizardForm.CancelButton.Caption := 'Отмена';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PageID = wpSelectTasks;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpWelcome then
  begin
    WizardForm.NextButton.Caption := 'Установить';
    WizardForm.CancelButton.Caption := 'Отмена';
  end
  else if CurPageID = wpPreparing then
  begin
    WizardForm.PageNameLabel.Caption := 'Речка уже запущена';
    WizardForm.PageDescriptionLabel.Caption :=
      'Освободим файлы перед обновлением.';
    WizardForm.PreparingLabel.Caption :=
      'Чтобы обновить программу, установщик на несколько секунд закроет ' +
      'Речку. Настройки и история сохранятся.';
    WizardForm.PreparingYesRadio.Checked := True;
    WizardForm.NextButton.Caption := 'Продолжить';
  end
  else if CurPageID = wpInstalling then
  begin
    WizardForm.PageNameLabel.Caption := 'Устанавливаем Речку';
    WizardForm.PageDescriptionLabel.Caption :=
      'Можно продолжать пользоваться компьютером.';
  end
  else if CurPageID = wpFinished then
  begin
    WizardForm.NextButton.Caption := 'Готово';
  end;
end;

procedure CancelButtonClick(
  CurPageID: Integer;
  var Cancel, Confirm: Boolean
);
begin
  if CurPageID = wpWelcome then
    Confirm := False;
end;
