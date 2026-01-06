@echo off
REM PiKaraoke Windows Installer Wrapper
REM Location: /build_scripts/windows/build_windows_installer.bat

echo =========================================
echo   PiKaraoke Windows Installer Builder
echo =========================================
echo.

where powershell >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: PowerShell not found
    pause
    exit /b 1
)

echo Running PowerShell build script...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0build_windows_installer.ps1" %*

echo.
pause