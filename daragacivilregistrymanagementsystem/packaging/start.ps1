# Start Daraga Civil Registry. Keep this window open while the office uses the system.
$ErrorActionPreference = "Continue"
Write-Host ""
Write-Host "Starting Daraga Civil Registry..." -ForegroundColor Green
$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $AppDir

$venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "The system is not installed yet." -ForegroundColor Yellow
    Write-Host "Double-click Install.bat first."
    Read-Host "Press Enter to close"
    exit 1
}

& $venvPython -c "import flask" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Flask is missing. Installing required packages now..." -ForegroundColor Yellow
    Write-Host "This can take several minutes the first time. Keep this window open."
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r (Join-Path $AppDir "requirements.txt")
    & $venvPython -c "import flask" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Packages did not install. Double-click Install.bat, wait until it finishes, then Start.bat again." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Packages installed."
}

$pgBin = Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\bin"
$pgData = Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\data"
$pgLog = Join-Path $env:TEMP "daraga-postgres.log"
$pgIsReady = Join-Path $pgBin "pg_isready.exe"
$pgCtl = Join-Path $pgBin "pg_ctl.exe"
if (Test-Path $pgBin) { $env:Path = "$pgBin;$env:Path" }

function Test-PostgresReady {
    if (-not (Test-Path $pgIsReady)) { return $false }
    & $pgIsReady -h 127.0.0.1 -p 5432 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Write-Host "Checking PostgreSQL..."
if (Test-PostgresReady) {
    Write-Host "PostgreSQL is ready."
} elseif (Test-Path $pgCtl) {
    Write-Host "Starting PostgreSQL..."
    & $pgCtl -D $pgData status 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $pidFile = Join-Path $pgData "postmaster.pid"
        if (Test-Path $pidFile) {
            Write-Host "Clearing a leftover PostgreSQL lock..."
            & $pgCtl -D $pgData -m fast -t 5 stop 2>$null | Out-Null
            Start-Sleep -Seconds 2
            if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
        }
        Start-Process -FilePath $pgCtl -ArgumentList @("-D", $pgData, "-l", $pgLog, "start") -WindowStyle Hidden | Out-Null
    }
    $ready = $false
    for ($i = 1; $i -le 20; $i++) {
        if (Test-PostgresReady) { $ready = $true; break }
        Write-Host "Waiting for PostgreSQL ($i/20)..."
        Start-Sleep -Seconds 2
    }
    if ($ready) {
        Write-Host "PostgreSQL is ready."
    } else {
        Write-Host "PostgreSQL is still starting. Loading the app anyway..."
    }
}

$listening = $false
try {
    $listening = [bool](netstat -ano | Select-String ":5001" | Select-String "LISTENING")
} catch { }
if ($listening) {
    Write-Host "The system is already running. Opening http://127.0.0.1:5001"
    Start-Process "http://127.0.0.1:5001"
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "Keep this window open. Open http://127.0.0.1:5001 in your browser."
Write-Host "Loading the application. First start can take about one minute..."
Write-Host "Press Ctrl+C to stop."
Write-Host ""
$env:PYTHONUNBUFFERED = "1"
& $venvPython -u (Join-Path $AppDir "app.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "The system stopped with an error." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit $LASTEXITCODE
}
