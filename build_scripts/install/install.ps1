Param(
    [switch]$Confirm = $true,
    [switch]$Local = $false
)

$ErrorActionPreference = "Stop"

Write-Host "--- PiKaraoke Windows Installer ---" -ForegroundColor Cyan

# 1. Check for Winget
if (!(Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Error "Winget not found. Please ensure you are on a modern version of Windows 10 or 11."
    exit 1
}

# Determine packages to install
$installList = @("pikaraoke (via uv)")
$skipDeno = $false
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Node.js detected. Skipping Deno installation."
    $skipDeno = $true
}

if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $installList += "ffmpeg" }
if (!$skipDeno -and !(Get-Command deno -ErrorAction SilentlyContinue)) { $installList += "deno" }

# Helper function defined earlier is needed here, moving it up
function Is-PythonCompatible {
    if (!(Get-Command python -ErrorAction SilentlyContinue)) { return $false }
    try {
        $pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        $parts = $pythonVersion.Split('.')
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        return ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10))
    } catch {
        return $false
    }
}

if (!(Is-PythonCompatible)) { $installList += "python" }

Write-Host "The following packages will be installed/updated: $($installList -join ', ')"
if ($Confirm) {
    $confirmation = Read-Host "Do you want to proceed? (y/n)"
    if ($confirmation -notmatch "^[Yy]$") {
        Write-Host "Installation cancelled."
        exit 1
    }
}


# 2. Install Dependencies via Winget
Write-Host "Installing dependencies (ffmpeg, deno, python)..." -ForegroundColor Yellow

# Install FFmpeg
if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing ffmpeg..."
    winget install --id=Gyan.FFmpeg -e --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Failed to install ffmpeg via winget" }
} else {
    Write-Host "ffmpeg is already installed."
}

# Install Deno
if (!$skipDeno) {
    if (!(Get-Command deno -ErrorAction SilentlyContinue)) {
        Write-Host "Installing deno..."
        winget install --id=DenoLand.Deno -e --silent --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw "Failed to install deno via winget" }
    } else {
        Write-Host "deno is already installed."
    }
}

# Install Python (Required for pikaraoke)
if (Is-PythonCompatible) {
    Write-Host "Compatible Python version detected. Skipping Python installation."
} else {
    Write-Host "Python 3.10+ not found. Installing via Winget..." -ForegroundColor Yellow
    winget install --id=Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Python via winget" }
    Write-Host "Python installed. You may need to restart your terminal if the next steps fail." -ForegroundColor Magenta
}

# 3. Install/Configure uv
if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    # Attempt to install uv via irm
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install uv" }

    # Reload Path for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
# 4. Install/Upgrade dependencies via uv
Write-Host "Checking for existing uv installations..." -ForegroundColor Yellow
$uvPackages = ""
$uvPackages = uv tool list | Out-String

# pikaraoke
if ($uvPackages -match "pikaraoke") {
    Write-Host "Upgrading pikaraoke via uv..." -ForegroundColor Yellow
    if ($Local) {
        uv tool install --force .
    } else {
        uv tool upgrade pikaraoke
    }
} else {
    Write-Host "Installing pikaraoke via uv..." -ForegroundColor Yellow
    if ($Local) {
        uv tool install .
    } else {
        uv tool install pikaraoke
    }
}
if ($LASTEXITCODE -ne 0) { throw "Failed to install/upgrade pikaraoke via uv tool" }

# 6. Create Desktop Shortcut
Write-Host "Creating Desktop Shortcuts..." -ForegroundColor Yellow
try {
    $desktopPath = [System.Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($desktopPath)) { throw "Could not resolve Desktop path" }
    # Robust path resolution for pikaraoke.exe
    $pikaraokeExe = ""
    $exePaths = @(
        (Get-Command pikaraoke -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        (Join-Path $env:LOCALAPPDATA "uv\bin\pikaraoke.exe"),
        (Join-Path $HOME ".local\bin\pikaraoke.exe") # uv also uses this on some setups
    )
    foreach ($p in $exePaths) { if ($p -and (Test-Path $p)) { $pikaraokeExe = $p; break } }

    if ($pikaraokeExe) {
        $WScriptShell = New-Object -ComObject WScript.Shell

        # Download Icon from GitHub once if needed
        $iconPath = Join-Path ([System.IO.Path]::GetDirectoryName($pikaraokeExe)) "logo.ico"
        $iconFound = $false
        try {
            $iconUrl = "https://raw.githubusercontent.com/vicwomg/pikaraoke/refs/heads/master/pikaraoke/static/icons/logo.ico"
            if (!(Test-Path $iconPath)) {
                Invoke-WebRequest -Uri $iconUrl -OutFile $iconPath -ErrorAction Stop
            }
            if (Test-Path $iconPath) { $iconFound = $true }
        } catch {
            Write-Host "Could not download icon from GitHub." -ForegroundColor Cyan
        }

        # Create multiple shortcuts
        $shortcutConfigs = @(
            @{ Name = "PiKaraoke"; Args = "" },
            @{ Name = "PiKaraoke (headless)"; Args = "--headless" }
        )

        foreach ($config in $shortcutConfigs) {
            $sName = $config.Name
            $shortcutPath = Join-Path $desktopPath "$sName.lnk"
            $shortcut = $WScriptShell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $pikaraokeExe
            $shortcut.Arguments = $config.Args
            $shortcut.WorkingDirectory = [System.IO.Path]::GetDirectoryName($pikaraokeExe)
            if ($iconFound) {
                $shortcut.IconLocation = "$iconPath,0"
            }
            $shortcut.Save()
            Write-Host "Created shortcut: $sName" -ForegroundColor Green
        }
    } else {
        Write-Host "Could not find pikaraoke.exe to create shortcuts." -ForegroundColor Red
    }
} catch {
    Write-Host "Failed to create desktop shortcuts: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n--------------------------------------------------------" -ForegroundColor Green
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "Please restart your terminal (PowerShell) to ensure all PATH changes are loaded."
Write-Host "Then, simply run: `pikaraoke` or launch PiKaraoke from the desktop shortcuts."
Write-Host "--------------------------------------------------------"
