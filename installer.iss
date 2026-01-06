; PiKaraoke Windows Installer Script
; Inno Setup 6.x required
; https://jrsoftware.org/isinfo.php

#define MyAppName "PiKaraoke"
#define MyAppVersion "1.15.3"
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
OutputDir=dist\installer
OutputBaseFilename=PiKaraoke-Setup-{#MyAppVersion}
SetupIconFile=pikaraoke\logo.ico
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
; Main application files
Source: "dist\pikaraoke\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main

; FFmpeg binary (if available in build/ffmpeg/ directory)
Source: "build\ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion; Components: ffmpeg; Check: FileExists(ExpandConstant('{#SourcePath}\build\ffmpeg\ffmpeg.exe'))

[Dirs]
; Create config folder in AppData (Standard for settings)
Name: "{userappdata}\pikaraoke"; Flags: uninsneveruninstall

; Create the songs directory based on USER SELECTION (See Code section)
Name: "{code:GetSongsDir}"; Flags: uninsneveruninstall

[Icons]
; Start Menu shortcuts - Using {code:GetSongsDir} to point to the user's chosen folder
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""
Name: "{group}\{#MyAppName} (Headless Mode)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--headless --download-path ""{code:GetSongsDir}"""
Name: "{group}\Open Songs Folder"; Filename: "{code:GetSongsDir}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""; Tasks: desktopicon

; Quick Launch shortcut
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--download-path ""{code:GetSongsDir}"""; Tasks: quicklaunchicon

[Run]
; Launch options after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent shellexec; Parameters: "--download-path ""{code:GetSongsDir}"""

[UninstallDelete]
; FIXED: Added "Type: files"
Type: files; Name: "{userappdata}\pikaraoke\*.ini"

[Code]
var
  InfoPage: TOutputMsgMemoWizardPage;
  SongsDirPage: TInputDirWizardPage;

// --- 1. Helper Function to get the path needed by [Dirs], [Icons], and [Run] ---
function GetSongsDir(Param: String): String;
begin
  Result := SongsDirPage.Values[0];
end;

// --- 2. Initialize Wizard Pages ---
procedure InitializeWizard;
begin
  // A. Create Information Page (RAM Warning + General Info)
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

  // B. Create "Select Songs Directory" Page
  // We use InfoPage.ID to force it to appear right after the RAM warning
  SongsDirPage := CreateInputDirPage(InfoPage.ID,
    'Select Songs Directory',
    'Where would you like to store your karaoke songs?',
    'Select the folder where PiKaraoke will store your song library, then click Next.',
    False, '');
  
  // Add the input field
  SongsDirPage.Add('');

  // Set default value to Documents\pikaraoke-songs
  SongsDirPage.Values[0] := ExpandConstant('{userdocs}\pikaraoke-songs');
end;

// --- 3. Check for FFmpeg post-install ---
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Check if FFmpeg was installed
    if not FileExists(ExpandConstant('{app}\ffmpeg\ffmpeg.exe')) then
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

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox('Are you sure you want to uninstall PiKaraoke?' + #13#10 + #13#10 +
                   'Your song library and settings will be preserved.',
                   mbConfirmation, MB_YESNO) = IDYES;
end;

// --- 4. Success Message with Dynamic Path Instructions ---
procedure DeinitializeSetup();
begin
  if IsUninstaller then Exit;
  // Only show on successful installation
  if GetExceptionMessage = '' then
  begin
    MsgBox('PiKaraoke has been successfully installed!' + #13#10 + #13#10 +
           'QUICK START GUIDELINES:' + #13#10 +
           '1. COPY SONGS: Put your CDG/MP3/MP4 files into:' + #13#10 +
           '   ' + SongsDirPage.Values[0] + #13#10 + #13#10 +
           '2. LAUNCH: Open PiKaraoke from the Start Menu.' + #13#10 +
           '3. PLAY: Go to http://localhost:5555 to search/queue songs.',
           mbInformation, MB_OK);
  end;
end;

// Function to check if we need to add to PATH (Preserved from your original)
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