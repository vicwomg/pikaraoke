@echo off
setlocal enabledelayedexpansion

:: PiKaraoke Pre-Uninstall Script
:: This script runs before the MSI uninstaller removes files.
:: Environment variables:
::   OPTION_REMOVE_DATA      - 1 if user selected to remove data
::   OPTION_REMOVE_SHORTCUTS - 1 if user selected to remove shortcuts

set "DESKTOP=%USERPROFILE%\Desktop"

:: Remove desktop shortcuts if option selected
if "%OPTION_REMOVE_SHORTCUTS%" EQU "1" (
    if exist "!DESKTOP!\PiKaraoke.lnk" del "!DESKTOP!\PiKaraoke.lnk" 2>nul
    if exist "!DESKTOP!\PiKaraoke (Headless).lnk" del "!DESKTOP!\PiKaraoke (Headless).lnk" 2>nul

    :: Also check public desktop for per-machine installs
    if exist "%PUBLIC%\Desktop\PiKaraoke.lnk" del "%PUBLIC%\Desktop\PiKaraoke.lnk" 2>nul
    if exist "%PUBLIC%\Desktop\PiKaraoke (Headless).lnk" del "%PUBLIC%\Desktop\PiKaraoke (Headless).lnk" 2>nul
)

:: Remove user data if option selected
if "%OPTION_REMOVE_DATA%" EQU "1" (
    :: Main data directory: %APPDATA%\pikaraoke
    if exist "%APPDATA%\pikaraoke" rmdir /s /q "%APPDATA%\pikaraoke" 2>nul

    :: Temp directory: %LOCALAPPDATA%\Temp\pikaraoke
    if exist "%LOCALAPPDATA%\Temp\pikaraoke" rmdir /s /q "%LOCALAPPDATA%\Temp\pikaraoke" 2>nul
)

endlocal
exit /b 0
