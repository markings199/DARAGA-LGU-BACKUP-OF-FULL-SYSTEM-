# Start the Civil Registry app on the Daraga office server (WIN-HIL0JNDFJIIN).
# Keep this window open so daily automatic backup can run.

$ErrorActionPreference = "Continue"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir

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
    pg_ctl -D $pgData start 2>$null
}

New-Item -ItemType Directory -Force -Path "C:\CivilRegistryBackups" | Out-Null
New-Item -ItemType Directory -Force -Path "C:\CivilRegistryData" | Out-Null

python app.py
