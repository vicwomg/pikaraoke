# PiKaraoke Windows Installer Build Script
# This script automates building the Windows installer using PyInstaller and Inno Setup
# Location: /build_scripts/windows/build_windows_installer.ps1

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

# Get script directory (Should be inside /build_scripts/windows/)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Define Project Root (Up 2 levels: windows -> build_scripts -> root)
$projectRoot = $scriptDir | Split-Path -Parent | Split-Path -Parent
Write-Info "Project Root: $projectRoot"

# Clean build directories (Relative to Project Root)
if ($Clean) {
    Write-Info "Cleaning build directories..."
    $buildDir = Join-Path $projectRoot "build"
    $distDir = Join-Path $projectRoot "dist"

    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
    if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
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
    exit 1
}
Write-Host ""

# Install dependencies via pip at PROJECT ROOT
Write-Info "Installing/Updating PiKaraoke dependencies..."
Push-Location $projectRoot
try {
    pip install -e .
    Write-Success "[OK] Dependencies installed"
} finally {
    Pop-Location
}

# GET VERSION
Write-Info "Detecting Package Version..."
try {
    $pkgVersion = (pip show pikaraoke | Select-String "Version:").ToString().Split(":")[1].Trim()
    Write-Success "[OK] Detected Version: $pkgVersion"
} catch {
    $pkgVersion = "1.0.0"
    Write-Warning "[!] Could not detect version. Defaulting to 1.0.0"
}
Write-Host ""


# --- FFmpeg Auto-Download Logic (Stored in Project Root/build/ffmpeg) ---
if (-not $SkipFFmpeg) {
    Write-Info "Checking for FFmpeg..."
    $buildDir = Join-Path $projectRoot "build"
    $ffmpegDir = Join-Path $buildDir "ffmpeg"
    $ffmpegExe = Join-Path $ffmpegDir "ffmpeg.exe"

    if (-not (Test-Path $ffmpegDir)) {
        New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
    }

    if (-not (Test-Path $ffmpegExe)) {
        Write-Warning "FFmpeg not found. Downloading latest release..."
        $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        $zipPath = Join-Path $ffmpegDir "ffmpeg.zip"

        try {
            Write-Info "Downloading from $url..."
            Invoke-WebRequest -Uri $url -OutFile $zipPath

            Write-Info "Extracting..."
            Expand-Archive -Path $zipPath -DestinationPath $ffmpegDir -Force

            $extractedBin = Get-ChildItem -Path $ffmpegDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1

            if ($extractedBin) {
                Move-Item -Path $extractedBin.FullName -Destination $ffmpegExe -Force
                Write-Success "[OK] FFmpeg updated"
                Remove-Item $zipPath -Force
                Get-ChildItem -Path $ffmpegDir -Directory | Remove-Item -Recurse -Force
            } else {
                throw "Could not find ffmpeg.exe in zip."
            }
        } catch {
            Write-Error "[X] Failed to download FFmpeg: $_"
            exit 1
        }
    } else {
        Write-Success "[OK] Found: FFmpeg"
    }
    Write-Host ""
}

# Build with PyInstaller
if (-not $SkipPyInstaller) {
    Write-Info "Building executable with PyInstaller..."
    Write-Info "Using spec file in current directory..."

    # We define explicit dist/work paths so they go to the project root
    $rootDist = Join-Path $projectRoot "dist"
    $rootBuild = Join-Path $projectRoot "build"

    try {
        pyinstaller pikaraoke.spec --clean --noconfirm --distpath $rootDist --workpath $rootBuild
        Write-Success "[OK] PyInstaller build complete"

        # Verify
        $exePath = Join-Path $rootDist "pikaraoke\pikaraoke.exe"
        if (Test-Path $exePath) {
            $exeSize = (Get-Item $exePath).Length / 1MB
            Write-Info "  Executable Size: $([math]::Round($exeSize, 2)) MB"
        } else {
            Write-Error "[X] pikaraoke.exe not found at $exePath"
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
    Write-Info "Using Version: $pkgVersion"

    try {
        & $isccPath "/DMyAppVersion=$pkgVersion" "installer.iss"
        Write-Success "[OK] Installer build complete"

        # Check dist/installer in the Project Root
        $installerDir = Join-Path $projectRoot "dist\installer"
        $installerPath = Get-ChildItem "$installerDir\PiKaraoke-Setup-*.exe" | Select-Object -First 1

        if ($installerPath) {
            Write-Success "========================================="
            Write-Success "  Build Complete!"
            Write-Success "========================================="
            Write-Info "Location: $($installerPath.FullName)"
            Write-Success "========================================="
        } else {
            Write-Error "[X] Installer not found in $installerDir"
            exit 1
        }
    } catch {
        Write-Error "[X] Inno Setup build failed: $_"
        exit 1
    }
}

Write-Host ""
Write-Success "All done!"
$response = Read-Host "Open installer folder? (y/n)"
if ($response -eq "y") {
    Start-Process explorer.exe (Join-Path $projectRoot "dist\installer")
}