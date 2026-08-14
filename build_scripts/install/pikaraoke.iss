; Bootstrapper around install.ps1 / uninstall.ps1. Owns only the shortcuts and the
; Add/Remove Programs entry. Build with build_installer.ps1.

#define AppName "PiKaraoke"
#define AppPublisher "vicwomg"
#define AppURL "https://github.com/vicwomg/pikaraoke"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{0F840587-0065-4392-9265-066F0297E386}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
VersionInfoVersion={#AppVersion}

; Elevating would install uv's tools into the administrator's profile.
PrivilegesRequired=lowest

; Holds only the uninstaller and its helper; uv owns the package itself.
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\logo.ico

; winget's floor.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible

OutputDir=..\..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\..\pikaraoke\static\icons\logo.ico
Compression=lzma2/max
WizardStyle=modern

; Captures the PowerShell output; the only record of a failed dependency install.
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "headlessicon"; Description: "Create a headless (server only) shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; dontcopy: install.ps1 runs before the file copy step, from {tmp}.
Source: "install.ps1"; Flags: dontcopy
Source: "uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\pikaraoke\static\icons\logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{code:GetExePath}"; IconFilename: "{app}\logo.ico"; Check: HaveExe
Name: "{group}\{#AppName} (headless)"; Filename: "{code:GetExePath}"; Parameters: "--headless"; IconFilename: "{app}\logo.ico"; Check: HaveExe
Name: "{autodesktop}\{#AppName}"; Filename: "{code:GetExePath}"; IconFilename: "{app}\logo.ico"; Tasks: desktopicon; Check: HaveExe
Name: "{autodesktop}\{#AppName} (headless)"; Filename: "{code:GetExePath}"; Parameters: "--headless"; IconFilename: "{app}\logo.ico"; Tasks: headlessicon; Check: HaveExe

[Run]
; Via Explorer: a child of Setup would inherit Setup's PATH, captured before winget ran.
Filename: "{win}\explorer.exe"; Parameters: """{code:GetExePath}"""; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent; Check: HaveExe

[Code]

var
  PikaraokeExe: String;

function HaveExe: Boolean;
begin
  Result := PikaraokeExe <> '';
end;

function GetExePath(Param: String): String;
begin
  Result := PikaraokeExe;
end;

{ Mirrors the script's console output onto the wizard. }
procedure OnInstallOutput(const S: String; const Error, FirstLine: Boolean);
begin
  if Trim(S) <> '' then
    WizardForm.StatusLabel.Caption := Copy(Trim(S), 1, 100);
end;

{ Runs during ssInstall so [Icons] can point at an executable that now exists. }
procedure RunInstallScript;
var
  ScriptPath, ExeOutFile, Params: String;
  RawPath: AnsiString;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('install.ps1');
  ScriptPath := ExpandConstant('{tmp}\install.ps1');
  ExeOutFile := ExpandConstant('{tmp}\pikaraoke_exe.txt');

  { -File passes arguments as literal strings, so switches must be bare tokens. }
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '"' +
            ' -NoConfirm -NoShortcuts -ExePathOutFile "' + ExeOutFile + '"';

  WizardForm.StatusLabel.Caption :=
    'Installing ffmpeg, deno and PiKaraoke. This can take several minutes...';

  { winget writes progress with carriage returns, so capture stalls on big downloads. }
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if not ExecAndLogOutput('powershell.exe', Params, '', SW_HIDE, ewWaitUntilTerminated,
                            ResultCode, @OnInstallOutput) then
      RaiseException('Could not start PowerShell: ' + SysErrorMessage(DLLGetLastError));
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;

  if ResultCode <> 0 then
    RaiseException('The PiKaraoke install script failed with exit code ' +
                   IntToStr(ResultCode) + '.' + #13#10 + #13#10 +
                   'The full output is in:' + #13#10 + ExpandConstant('{log}'));

  if LoadStringFromFile(ExeOutFile, RawPath) then
    PikaraokeExe := Trim(String(RawPath));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    RunInstallScript;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ScriptPath: String;
  ResultCode: Integer;
begin
  { usUninstall runs before the files are deleted, so uninstall.ps1 still exists. }
  if CurUninstallStep <> usUninstall then
    Exit;

  ScriptPath := ExpandConstant('{app}\uninstall.ps1');
  if not FileExists(ScriptPath) then
    Exit;

  if not Exec('powershell.exe',
              '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '" -NoConfirm',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;

  if ResultCode <> 0 then
    MsgBox('The pikaraoke package could not be removed automatically.' + #13#10 +
           'You can remove it by hand with:  uv tool uninstall pikaraoke',
           mbError, MB_OK);
end;
