; PiKaraoke Windows Installer Script
; Inno Setup 6.x required
; Location: /build_scripts/windows/installer.iss

; --- VERSIONING LOGIC ---
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-DEV"
#endif

#define MyAppName "PiKaraoke"
#define MyAppPublisher "Vic Wong"
#define MyAppURL "https://github.com/vicwomg/pikaraoke"
#define MyAppExeName "pikaraoke.exe"
#define MyAppDescription "KTV-style karaoke song search and queueing system"

[Setup]
AppId={{8F7A2B3C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequiredOverridesAllowed=dialog
; Output to the main project 'dist' folder (Up 2 levels)
OutputDir=..\..\dist\installer
OutputBaseFilename=PiKaraoke-Setup-{#MyAppVersion}
; Logo path is relative to the PROJECT ROOT, so go up 2 levels
SetupIconFile=..\..\pikaraoke\logo.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Types]
Name: "full"; Description: "Full installation (includes FFmpeg)"
Name: "minimal"; Description: "Minimal installation (FFmpeg required separately)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "main"; Description: "PiKaraoke Application"; Types: full minimal custom; Flags: fixed
Name: "ffmpeg"; Description: "FFmpeg (required for audio/video processing)"; Types: full; ExtraDiskSpaceRequired: 73400320

[Files]
; Look in the PROJECT ROOT's dist folder (Up 2 levels)
Source: "..\..\dist\pikaraoke\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main

; Look in the PROJECT ROOT's build folder (Up 2 levels)
Source: "..\..\build\ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion; Components: ffmpeg; Check: FileExists(ExpandConstant('{#SourcePath}\..\..\build\ffmpeg\ffmpeg.exe'))

[Dirs]
Name: "{userappdata}\pikaraoke"; Flags: uninsneveruninstall
Name: "{code:GetSongsDir}"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""
Name: "{group}\{#MyAppName} (Headless Mode)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--headless --download-path ""{code:GetSongsDir}"""
Name: "{group}\Open Songs Folder"; Filename: "{code:GetSongsDir}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent shellexec; Parameters: "--download-path ""{code:GetSongsDir}"""

[UninstallDelete]
Type: files; Name: "{userappdata}\pikaraoke\*.ini"

[Code]
var
  InfoPage: TOutputMsgMemoWizardPage;
  SongsDirPage: TInputDirWizardPage;

function GetSongsDir(Param: String): String;
begin
  if SongsDirPage = nil then
    Result := ExpandConstant('{userdocs}\pikaraoke-songs')
  else
    Result := SongsDirPage.Values[0];
end;

procedure InitializeWizard;
begin
  InfoPage := CreateOutputMsgMemoPage(wpWelcome,
    'Installation Information',
    'Please read the following important information before continuing.',
    'System Requirements & Configuration:',
    'HARDWARE WARNING (RAM):' + #13#10 +
    'This application uses an in-memory indexing system.' + #13#10 +
    'All songs are currently stored in RAM to ensure fast search results.' + #13#10 +
    'Please ensure your device has sufficient free memory for your library size.' + #13#10 + #13#10 +
    'Prerequisites:' + #13#10 +
    '1. FFmpeg - For audio/video processing (included in Full installation)' + #13#10 +
    '2. Internet connection - For downloading karaoke videos from YouTube'
  );

  SongsDirPage := CreateInputDirPage(wpSelectDir,
    'Select Songs Directory',
    'Where would you like to store your karaoke songs?',
    'Select the folder where PiKaraoke will store your song library, then click Next.',
    False, '');

  SongsDirPage.Add('');
  SongsDirPage.Values[0] := ExpandConstant('{userdocs}\pikaraoke-songs');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not FileExists(ExpandConstant('{app}\ffmpeg\ffmpeg.exe')) then
    begin
      if not WizardSilent then
      begin
        if MsgBox('FFmpeg was not included in this installation. ' + #13#10 +
                  'PiKaraoke requires FFmpeg to function properly.' + #13#10 + #13#10 +
                  'Would you like to download FFmpeg now?',
                   mbConfirmation, MB_YESNO) = IDYES then
        begin
          ShellExec('open', 'https://www.gyan.dev/ffmpeg/builds/', '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
        end;
      end;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox('Are you sure you want to uninstall PiKaraoke?' + #13#10 + #13#10 +
                   'Your song library and settings will be preserved.',
                   mbConfirmation, MB_YESNO) = IDYES;
end;

procedure DeinitializeSetup();
begin
  if IsUninstaller then Exit;
  if (GetExceptionMessage = '') and (not WizardSilent) then
  begin
    MsgBox('PiKaraoke has been successfully installed!' + #13#10 + #13#10 +
           'QUICK START GUIDELINES:' + #13#10 +
           '1. COPY SONGS: Put your CDG/MP3/MP4 files into:' + #13#10 +
           '   ' + GetSongsDir('') + #13#10 + #13#10 +
           '2. LAUNCH: Open PiKaraoke from the Start Menu.' + #13#10 +
           '3. PLAY: Go to http://localhost:5555 to search/queue songs.',
           mbInformation, MB_OK);
  end;
end;

function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;