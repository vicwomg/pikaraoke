# PiKaraoke Headless Mode Verification Script (Windows)
# This script starts PiKaraoke in headless mode, waits for initialization,
# and verifies that key web endpoints are serving content.

$ErrorActionPreference = "Stop"

# Ensure pikaraoke is in the PATH for this session
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "Installing PiKaraoke for CI..."
./build_scripts/install/install.ps1 -Confirm:$false -Local:$true

# Reload path again just in case installer updated it
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "Starting PiKaraoke in headless mode..."
$proc = Start-Process pikaraoke -ArgumentList "--headless" -PassThru -RedirectStandardOutput output.log -RedirectStandardError error.log

try {
    Write-Host "Waiting for PiKaraoke to initialize (max 30s)..."
    $initialized = $false
    for ($i=0; $i -lt 30; $i++) {
        if (Test-Path output.log, error.log) {
            if (Get-Content output.log, error.log -ErrorAction SilentlyContinue | Select-String "Connect the player host to:") {
                Write-Host "Found expected initialization output."
                $initialized = $true
                break
            }
        }
        Start-Sleep -Seconds 1
    }

    if (-not $initialized) {
        Write-Error "Timed out waiting for PiKaraoke to initialize."
        if (Test-Path output.log) { Write-Host "--- STDOUT ---"; Get-Content output.log }
        if (Test-Path error.log) { Write-Host "--- STDERR ---"; Get-Content error.log }
        exit 1
    }

    Write-Host "Verifying web endpoints..."
    $endpoints = @("/", "/splash", "/queue", "/search", "/browse", "/info")
    $failed = $false

    foreach ($path in $endpoints) {
        Write-Host "Checking http://localhost:5555$path ..."
        try {
            # Use curl.exe to avoid PowerShell's Invoke-WebRequest alias issues
            $result = curl.exe -s http://localhost:5555$path | Select-String "DOCTYPE"
            if (-not $result) {
                Write-Host "Error: Failed to verify $path (DOCTYPE not found)"
                $failed = $true
            }
        } catch {
            Write-Host "Error: Failed to connect to $path"
            $failed = $true
        }
    }

    if ($failed) {
        Write-Error "One or more endpoint verifications failed."
        exit 1
    }

    Write-Host "Headless mode verification successful!"
    exit 0

} finally {
    Write-Host "Cleaning up..."
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
