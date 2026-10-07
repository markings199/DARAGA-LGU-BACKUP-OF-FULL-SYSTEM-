# Install Daraga Civil Registry on this computer (Python venv, packages, database).
$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $AppDir

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Python {
    $candidates = @(
        @{ Command = "py"; Args = @("-3.10") },
        @{ Command = "py"; Args = @("-3") },
        @{ Command = "python"; Args = @() }
    )
    foreach ($item in $candidates) {
        try {
            $ver = & $item.Command @($item.Args + @("-c", "import sys; print(sys.version.split()[0])")) 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                return @{ Command = $item.Command; Args = $item.Args; Version = ([string]$ver).Trim() }
            }
        } catch { }
    }
    return $null
}

function Add-PostgresToPath {
    $pgBinCandidates = @(
        "$env:USERPROFILE\scoop\apps\postgresql\current\bin",
        "C:\Program Files\PostgreSQL\16\bin",
        "C:\Program Files\PostgreSQL\15\bin",
        "C:\Program Files\PostgreSQL\14\bin"
    )
    foreach ($bin in $pgBinCandidates) {
        if (Test-Path $bin) {
            $env:Path = "$bin;$env:Path"
            return $bin
        }
    }
    return $null
}

Write-Host "Daraga Civil Registry - Install" -ForegroundColor Green
Write-Host "Folder: $AppDir"

Write-Step "Checking Python"
$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.9+ was not found. Install Python from python.org and enable Add python.exe to PATH." -ForegroundColor Red
    exit 1
}
Write-Host "Using Python $($py.Version)"

Write-Step "Checking PostgreSQL"
$pgBin = Add-PostgresToPath
$pgData = "$env:USERPROFILE\scoop\apps\postgresql\current\data"
if (Test-Path $pgData) {
    try { & pg_ctl -D $pgData start 2>$null | Out-Null } catch { }
}
try {
    & pg_isready -h 127.0.0.1 -p 5432 | Out-Null
} catch { }
if ($LASTEXITCODE -ne 0 -and -not $pgBin) {
    Write-Host "PostgreSQL was not found. Install PostgreSQL and start the service, then run Install again." -ForegroundColor Red
    exit 1
}

Write-Step "Creating virtual environment"
$venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $pyArgs = @()
    if ($py.Args) { $pyArgs += $py.Args }
    $pyArgs += @("-m", "venv", ".venv")
    & $py.Command @pyArgs
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Step "Installing Python packages (OCR may take several minutes)"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit 1 }
& $venvPython -m pip install -r (Join-Path $AppDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Package install failed. Check the error above." -ForegroundColor Red
    exit 1
}

Write-Step "Office folders"
New-Item -ItemType Directory -Force -Path "C:\CivilRegistryData" | Out-Null
New-Item -ItemType Directory -Force -Path "C:\CivilRegistryBackups" | Out-Null
foreach ($sub in @("uploads", "uploads\birth", "uploads\marriage", "uploads\death", "uploads\temp", "backups")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $AppDir $sub) | Out-Null
}

Write-Step "Configuration"
$envFile = Join-Path $AppDir ".env"
$example = Join-Path $AppDir ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $example)) {
    Copy-Item $example $envFile
    Write-Host "Created .env from .env.example. Edit it if your PostgreSQL password is not postgres."
}

Write-Step "Creating database and default users"
& $venvPython (Join-Path $AppDir "scripts\setup_postgres.py")
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Step "Desktop shortcut"
$desktop = [Environment]::GetFolderPath("Desktop")
$startBat = Join-Path $AppDir "Start.bat"
if ((Test-Path $desktop) -and (Test-Path $startBat)) {
    $wsh = New-Object -ComObject WScript.Shell
    $lnkPath = Join-Path $desktop "Daraga Civil Registry.lnk"
    $lnk = $wsh.CreateShortcut($lnkPath)
    $lnk.TargetPath = $startBat
    $lnk.WorkingDirectory = $AppDir
    $lnk.Description = "Municipality of Daraga Civil Registry"
    $lnk.Save()
    Write-Host "Shortcut: $lnkPath"
}

Write-Host ""
Write-Host "Install finished." -ForegroundColor Green
Write-Host "Start the system with Start.bat (or the desktop shortcut)."
Write-Host "Then open http://127.0.0.1:5001"
Write-Host "Default logins: admin / admin123 and staff / staff123"
Write-Host "Change these passwords in Administration after first login."
exit 0
