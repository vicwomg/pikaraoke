@echo off
setlocal enabledelayedexpansion

echo.
echo PiKaraoke Desktop Shortcut Setup
echo ================================
echo.

:: Get the installation directory
set "INSTALL_DIR=%CD%"

:: Check if icon exists
set "ICON_PATH=%INSTALL_DIR%\app\pikaraoke\static\icons\logo.ico"
if not exist "!ICON_PATH!" (
    echo Warning: Icon not found at !ICON_PATH!
    echo Shortcuts will use default icon.
    set "ICON_PATH="
)

:: Ask about regular shortcut
set /p createShortcut="Create desktop shortcut for PiKaraoke? (Y/N): "
if /I "!createShortcut!"=="Y" (
    echo Creating desktop shortcut...

    if defined ICON_PATH (
        powershell -NoLogo -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Desktop = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'PiKaraoke.lnk'); $Shortcut = $WshShell.CreateShortcut($Desktop); $Shortcut.TargetPath = 'cmd.exe'; $Shortcut.Arguments = '/c pikaraoke'; $Shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile'); $Shortcut.IconLocation = '!ICON_PATH!'; $Shortcut.Save()"
    ) else (
        powershell -NoLogo -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Desktop = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'PiKaraoke.lnk'); $Shortcut = $WshShell.CreateShortcut($Desktop); $Shortcut.TargetPath = 'cmd.exe'; $Shortcut.Arguments = '/c pikaraoke'; $Shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile'); $Shortcut.Save()"
    )

    if !errorlevel! equ 0 (
        echo Desktop shortcut created successfully.
    ) else (
        echo Warning: Failed to create desktop shortcut.
    )
    echo.
)

:: Ask about headless shortcut
set /p createHeadless="Create desktop shortcut for PiKaraoke (Headless)? (Y/N): "
if /I "!createHeadless!"=="Y" (
    echo Creating headless desktop shortcut...

    if defined ICON_PATH (
        powershell -NoLogo -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Desktop = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'PiKaraoke (Headless).lnk'); $Shortcut = $WshShell.CreateShortcut($Desktop); $Shortcut.TargetPath = 'cmd.exe'; $Shortcut.Arguments = '/c pikaraoke --headless'; $Shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile'); $Shortcut.IconLocation = '!ICON_PATH!'; $Shortcut.Save()"
    ) else (
        powershell -NoLogo -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Desktop = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'PiKaraoke (Headless).lnk'); $Shortcut = $WshShell.CreateShortcut($Desktop); $Shortcut.TargetPath = 'cmd.exe'; $Shortcut.Arguments = '/c pikaraoke --headless'; $Shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile'); $Shortcut.Save()"
    )

    if !errorlevel! equ 0 (
        echo Headless desktop shortcut created successfully.
    ) else (
        echo Warning: Failed to create headless desktop shortcut.
    )
    echo.
)

echo Desktop shortcut setup complete.
echo.

endlocal