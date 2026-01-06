@echo off
REM PiKaraoke Windows Installer Build Script (Batch Version)
REM This is a simple wrapper to call the PowerShell build script

echo =========================================
echo   PiKaraoke Windows Installer Builder
echo =========================================
echo.

REM Check if PowerShell is available
where powershell >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: PowerShell not found
    echo Please install PowerShell or run the build_windows_installer.ps1 script manually
    pause
    exit /b 1
)

echo Running PowerShell build script...
echo.

REM Run the PowerShell script with execution policy bypass
powershell -ExecutionPolicy Bypass -File "%~dp0build_windows_installer.ps1" %*

echo.
pause
