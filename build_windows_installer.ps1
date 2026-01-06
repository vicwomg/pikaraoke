# PiKaraoke Windows Installer Build Script
# This script automates building the Windows installer using PyInstaller and Inno Setup
# Run this on a Windows machine with PowerShell

param(
    [switch]$SkipFFmpeg,
    [switch]$SkipPyInstaller,
    [switch]$SkipInnoSetup,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

# Color output functions
function Write-Success { Write-Host $args -ForegroundColor Green }
function Write-Info { Write-Host $args -ForegroundColor Cyan }
function Write-Warning { Write-Host $args -ForegroundColor Yellow }
function Write-Error { param($msg) Write-Host $msg -ForegroundColor Red }

Write-Info "========================================="
Write-Info "  PiKaraoke Windows Installer Builder"
Write-Info "========================================="
Write-Host ""

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Clean build directories if requested
if ($Clean) {
    Write-Info "Cleaning build directories..."
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
    Write-Success "[OK] Clean complete"
    Write-Host ""
}

# Check for Python
Write-Info "Checking for Python..."
try {
    $pythonVersion = python --version 2>&1
    Write-Success "[OK] Found: $pythonVersion"
} catch {
    Write-Error "[X] Python not found. Please install Python 3.10 or higher."
    exit 1
}

# Verify Python version is 3.10+
$versionMatch = $pythonVersion -match "Python (\d+)\.(\d+)"
if ($versionMatch) {
    $majorVersion = [int]$matches[1]
    $minorVersion = [int]$matches[2]
    if ($majorVersion -lt 3 -or ($majorVersion -eq 3 -and $minorVersion -lt 10)) {
        Write-Error "[X] Python 3.10+ required. Found: $pythonVersion"
        exit 1
    }
}

# Check for PyInstaller
Write-Info "Checking for PyInstaller..."
try {
    $pyinstallerVersion = pyinstaller --version 2>&1
    Write-Success "[OK] Found: PyInstaller $pyinstallerVersion"
} catch {
    Write-Warning "[X] PyInstaller not found. Installing..."
    pip install pyinstaller
    Write-Success "[OK] PyInstaller installed"
}

# Check for Inno Setup
Write-Info "Checking for Inno Setup..."
$innoSetupPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 5\ISCC.exe"
)

$isccPath = $null
foreach ($path in $innoSetupPaths) {
    if (Test-Path $path) {
        $isccPath = $path
        break
    }
}

if ($isccPath) {
    Write-Success "[OK] Found: Inno Setup at $isccPath"
} else {
    Write-Error "[X] Inno Setup not found. Please install from https://jrsoftware.org/isdl.php"
    Write-Warning "After installing, re-run this script."
    exit 1
}

Write-Host ""

# Install dependencies
if (-not $SkipPyInstaller) {
    Write-Info "Installing PiKaraoke dependencies..."
    pip install -e .
    Write-Success "[OK] Dependencies installed"
    Write-Host ""
}

# Download FFmpeg (optional)
if (-not $SkipFFmpeg) {
    Write-Info "Checking for FFmpeg..."
    $ffmpegDir = Join-Path $scriptDir "build\ffmpeg"
    $ffmpegExe = Join-Path $ffmpegDir "ffmpeg.exe"

    if (-not (Test-Path $ffmpegExe)) {
        Write-Warning "FFmpeg not found in build\ffmpeg"
        Write-Info "To include FFmpeg in the installer:"
        Write-Info "1. Download from: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        Write-Info "2. Extract ffmpeg.exe from the 'bin' folder"
        Write-Info "3. Place it in: $ffmpegDir"
        Write-Host ""

        $response = Read-Host "Continue without FFmpeg? (y/n)"
        if ($response -ne "y") {
            Write-Info "Aborting. Please download FFmpeg and re-run."
            exit 1
        }
        Write-Warning "Building installer without FFmpeg (minimal installation)"
    } else {
        Write-Success "[OK] Found: FFmpeg at $ffmpegExe"
        $ffmpegSize = (Get-Item $ffmpegExe).Length / 1MB
        Write-Info "  Size: $([math]::Round($ffmpegSize, 2)) MB"
    }
    Write-Host ""
}

# Build with PyInstaller
if (-not $SkipPyInstaller) {
    Write-Info "Building executable with PyInstaller..."
    Write-Info "This may take 5-10 minutes..."
    Write-Host ""

    try {
        pyinstaller pikaraoke.spec --clean --noconfirm
        Write-Success "[OK] PyInstaller build complete"

        # Verify the build
        $exePath = Join-Path $scriptDir "dist\pikaraoke\pikaraoke.exe"
        if (Test-Path $exePath) {
            $exeSize = (Get-Item $exePath).Length / 1MB
            Write-Info "  Executable: $([math]::Round($exeSize, 2)) MB"

            # Get total dist folder size
            $distSize = (Get-ChildItem "dist\pikaraoke" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
            Write-Info "  Total size: $([math]::Round($distSize, 2)) MB"
        } else {
            Write-Error "[X] Build failed: pikaraoke.exe not found"
            exit 1
        }
    } catch {
        Write-Error "[X] PyInstaller build failed: $_"
        exit 1
    }
    Write-Host ""
}

# Build installer with Inno Setup
if (-not $SkipInnoSetup) {
    Write-Info "Building installer with Inno Setup..."

    try {
        & $isccPath "installer.iss"
        Write-Success "[OK] Installer build complete"

        # Find the installer
        $installerPath = Get-ChildItem "dist\installer\PiKaraoke-Setup-*.exe" | Select-Object -First 1
        if ($installerPath) {
            $installerSize = $installerPath.Length / 1MB
            Write-Success ""
            Write-Success "========================================="
            Write-Success "  Build Complete!"
            Write-Success "========================================="
            Write-Info "Installer: $($installerPath.Name)"
            Write-Info "Size: $([math]::Round($installerSize, 2)) MB"
            Write-Info "Location: $($installerPath.FullName)"
            Write-Success "========================================="
        } else {
            Write-Error "[X] Installer not found in dist\installer"
            exit 1
        }
    } catch {
        Write-Error "[X] Inno Setup build failed: $_"
        exit 1
    }
}

Write-Host ""
Write-Success "All done! You can now test the installer."
Write-Info "To install: Double-click the .exe file in dist\installer"
Write-Host ""

# Open the installer folder
$response = Read-Host "Open installer folder? (y/n)"
if ($response -eq "y") {
    Start-Process explorer.exe (Join-Path $scriptDir "dist/installer")
}
