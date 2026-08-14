Param(
    [switch]$NoConfirm = $false
)

$ErrorActionPreference = "Stop"

Write-Host "--- PiKaraoke Windows Uninstaller ---" -ForegroundColor Cyan

Write-Host "This removes the pikaraoke package and its shortcuts."
Write-Host "Your songs, settings and the shared tools (ffmpeg, deno, uv) are kept."

if (!$NoConfirm) {
    $confirmation = Read-Host "Do you want to proceed? (y/n)"
    if ($confirmation -notmatch "^[Yy]$") {
        Write-Host "Uninstall cancelled."
        exit 1
    }
}

# 1. Remove the pikaraoke tool installation and the icon install.ps1 put beside it
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvPackages = uv tool list | Out-String
    if ($uvPackages -match "pikaraoke") {
        Write-Host "Removing pikaraoke via uv..." -ForegroundColor Yellow
        uv tool uninstall pikaraoke
        if ($LASTEXITCODE -ne 0) { throw "Failed to uninstall pikaraoke via uv tool" }
    } else {
        Write-Host "pikaraoke is not installed via uv. Nothing to remove."
    }

    $uvBinDir = ""
    try { $uvBinDir = (uv tool dir --bin 2>$null | Out-String).Trim() } catch { }
    if ($uvBinDir) { Remove-Item (Join-Path $uvBinDir "logo.ico") -Force -ErrorAction SilentlyContinue }
} else {
    Write-Host "uv not found. Skipping package removal." -ForegroundColor Yellow
}

# 2. Remove desktop shortcuts created by install.ps1 (a no-op under the installer)
Write-Host "Removing desktop shortcuts..." -ForegroundColor Yellow
try {
    $desktopPath = [System.Environment]::GetFolderPath("Desktop")
    foreach ($sName in @("PiKaraoke", "PiKaraoke (headless)")) {
        $shortcutPath = Join-Path $desktopPath "$sName.lnk"
        if (Test-Path $shortcutPath) {
            Remove-Item $shortcutPath -Force
            Write-Host "Removed shortcut: $sName" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "Failed to remove desktop shortcuts: $($_.Exception.Message)" -ForegroundColor Red
}

$songsPath = Join-Path $HOME "pikaraoke-songs"
$dataPath = Join-Path $env:APPDATA "pikaraoke"

Write-Host "`n--------------------------------------------------------" -ForegroundColor Green
Write-Host "Uninstall complete!" -ForegroundColor Green
Write-Host "`nThese were left untouched. Delete them yourself if you want them gone:" -ForegroundColor Cyan
Write-Host "  Songs:    $songsPath"
Write-Host "  Settings: $dataPath"
Write-Host "  Tools:    ffmpeg, deno and uv (winget uninstall <id>)"
Write-Host "--------------------------------------------------------"
