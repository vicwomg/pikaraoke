@echo off
setlocal enabledelayedexpansion

:: ---------------------------------------------------------------------------
:: PiKaraoke Post-Install Script (Path-Fixed Version)
:: ---------------------------------------------------------------------------

:: 1. SETUP LOGGING
set "LOGFILE=%TEMP%\pikaraoke_install_log.txt"
echo [%DATE% %TIME%] Starting Post-Install Script > "%LOGFILE%"

:: 2. DETECT INSTALL DIRECTORY SAFELY
set "SCRIPT_DIR=%~dp0"
:: Resolve the PARENT directory (go up one level from _installer)
for %%I in ("%SCRIPT_DIR%..") do set "INSTALL_DIR=%%~fI"

echo [%DATE% %TIME%] Script Dir: "%SCRIPT_DIR%" >> "%LOGFILE%"
echo [%DATE% %TIME%] Install Dir: "%INSTALL_DIR%" >> "%LOGFILE%"

:: 3. DEFINE VARIABLES
set "ICON_PATH=%INSTALL_DIR%\app\pikaraoke\static\icons\logo.ico"
set "LINK_PATH_STD=%USERPROFILE%\Desktop\PiKaraoke.lnk"
set "LINK_PATH_HEADLESS=%USERPROFILE%\Desktop\PiKaraoke (Headless).lnk"

if "%ALLUSERS%"=="1" (
    set "LINK_PATH_STD=%PUBLIC%\Desktop\PiKaraoke.lnk"
    set "LINK_PATH_HEADLESS=%PUBLIC%\Desktop\PiKaraoke (Headless).lnk"
)

:: 4. LOGIC ROUTING
echo [%DATE% %TIME%] Checking options... >> "%LOGFILE%"

if "%OPTION_DESKTOP_SHORTCUT%"=="1" call :CreateStandardShortcut
if "%OPTION_HEADLESS_SHORTCUT%"=="1" call :CreateHeadlessShortcut

echo [%DATE% %TIME%] Script finished successfully. >> "%LOGFILE%"
exit /b 0

:: ---------------------------------------------------------------------------
:: SUBROUTINES
:: ---------------------------------------------------------------------------

:CreateStandardShortcut
echo [%DATE% %TIME%] Creating Standard Shortcut... >> "%LOGFILE%"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell;"^
    "$s = $ws.CreateShortcut($env:LINK_PATH_STD);"^
    "$s.TargetPath = 'cmd.exe';"^
    "$s.Arguments = '/c pikaraoke';"^
    "$s.WorkingDirectory = [Environment]::GetFolderPath('UserProfile');"^
    "$s.IconLocation = $env:ICON_PATH;"^
    "$s.Description = 'Launch PiKaraoke karaoke system';"^
    "$s.Save()" >> "%LOGFILE%" 2>&1
goto :eof

:CreateHeadlessShortcut
echo [%DATE% %TIME%] Creating Headless Shortcut... >> "%LOGFILE%"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell;"^
    "$s = $ws.CreateShortcut($env:LINK_PATH_HEADLESS);"^
    "$s.TargetPath = 'cmd.exe';"^
    "$s.Arguments = '/c pikaraoke --headless';"^
    "$s.WorkingDirectory = [Environment]::GetFolderPath('UserProfile');"^
    "$s.IconLocation = $env:ICON_PATH;"^
    "$s.Description = 'Launch PiKaraoke in headless mode';"^
    "$s.Save()" >> "%LOGFILE%" 2>&1
goto :eof