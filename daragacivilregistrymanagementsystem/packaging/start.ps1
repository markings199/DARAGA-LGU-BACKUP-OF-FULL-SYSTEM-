# Start Daraga Civil Registry. Keep this window open while the office uses the system.
$ErrorActionPreference = "Continue"
$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $AppDir

$venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "The system is not installed yet." -ForegroundColor Yellow
    Write-Host "Double-click Install.bat first."
    Read-Host "Press Enter to close"
    exit 1
}

$pgBinCandidates = @(
    "$env:USERPROFILE\scoop\apps\postgresql\current\bin",
    "C:\Program Files\PostgreSQL\16\bin",
    "C:\Program Files\PostgreSQL\15\bin",
    "C:\Program Files\PostgreSQL\14\bin"
)
foreach ($bin in $pgBinCandidates) {
    if (Test-Path $bin) {
        $env:Path = "$bin;$env:Path"
        break
    }
}
$pgData = "$env:USERPROFILE\scoop\apps\postgresql\current\data"
if (Test-Path $pgData) {
    pg_ctl -D $pgData start 2>$null | Out-Null
}
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        & pg_isready -h 127.0.0.1 -p 5432 | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Host "PostgreSQL did not start. Open PowerShell and run:" -ForegroundColor Red
    Write-Host '  $env:Path = "$env:USERPROFILE\scoop\apps\postgresql\current\bin;$env:Path"'
    Write-Host '  pg_ctl -D "$env:USERPROFILE\scoop\apps\postgresql\current\data" start'
    Read-Host "Press Enter to close"
    exit 1
}

New-Item -ItemType Directory -Force -Path "C:\CivilRegistryData" | Out-Null
New-Item -ItemType Directory -Force -Path "C:\CivilRegistryBackups" | Out-Null

Write-Host "Starting Daraga Civil Registry..." -ForegroundColor Green
Write-Host "Keep this window open. Open http://127.0.0.1:5001 in your browser."
Write-Host "Press Ctrl+C to stop."
Write-Host ""
& $venvPython (Join-Path $AppDir "app.py")
