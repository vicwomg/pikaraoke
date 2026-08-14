Param(
    # Defaults to the version in pikaraoke/version.py
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

Write-Host "--- PiKaraoke Installer Build ---" -ForegroundColor Cyan

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$issPath = Join-Path $PSScriptRoot "pikaraoke.iss"

# 1. Locate the Inno Setup compiler
$iscc = ""
$isccPaths = @(
    (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    # winget installs Inno Setup per-user by default
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
foreach ($p in $isccPaths) { if ($p -and (Test-Path $p)) { $iscc = $p; break } }

if (!$iscc) {
    Write-Error @"
Inno Setup compiler (ISCC.exe) not found. Install it with:

    winget install -e --id JRSoftware.InnoSetup
"@
    exit 1
}
Write-Host "Using compiler: $iscc"

# 2. Resolve the version
if (!$Version) {
    $versionFile = Join-Path $repoRoot "pikaraoke\version.py"
    $match = Select-String -Path $versionFile -Pattern '__version__\s*=\s*"([^"]+)"'
    if (!$match) { throw "Could not read __version__ from $versionFile" }
    $Version = $match.Matches[0].Groups[1].Value
}
Write-Host "Building version: $Version" -ForegroundColor Yellow

# 3. Compile
& $iscc "/DAppVersion=$Version" $issPath
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed with exit code $LASTEXITCODE" }

$output = Join-Path $repoRoot "dist\PiKaraoke-$Version-setup.exe"

Write-Host "`n--------------------------------------------------------" -ForegroundColor Green
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Installer: $output"
Write-Host "--------------------------------------------------------"
