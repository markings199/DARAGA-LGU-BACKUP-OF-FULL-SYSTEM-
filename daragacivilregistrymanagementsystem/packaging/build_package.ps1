# Build a clean ZIP for the professor / office PC (no scans, backups, venv, or secrets).
$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Stamp = Get-Date -Format "yyyy-MM-dd"
$DistRoot = Join-Path $AppDir "dist"
$StageName = "DaragaCivilRegistry"
$Stage = Join-Path $DistRoot $StageName
$ZipPath = Join-Path $DistRoot "DaragaCivilRegistry_Package_$Stamp.zip"

Write-Host "Building package from: $AppDir"

if (Test-Path $DistRoot) {
    Remove-Item -Recurse -Force $DistRoot
}
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$excludeDirs = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
@(
    ".git", ".venv", "venv", "env", "__pycache__", "uploads", "backups",
    "dist", "build", ".idea", ".vscode"
) | ForEach-Object { [void]$excludeDirs.Add($_) }

$excludeNames = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
@(".env", "Thumbs.db", "Desktop.ini") | ForEach-Object { [void]$excludeNames.Add($_) }

function Test-ExcludedPath([string]$FullPath) {
    $rel = $FullPath.Substring($AppDir.Length).TrimStart("\", "/")
    foreach ($part in $rel.Split([char[]]@("\", "/"))) {
        if ($excludeDirs.Contains($part)) { return $true }
        if ($part.StartsWith("_restore")) { return $true }
        if ($part.EndsWith(".egg-info")) { return $true }
    }
    $leaf = Split-Path $FullPath -Leaf
    if ($excludeNames.Contains($leaf)) { return $true }
    if ($leaf -match '\.(pyc|pyo|log)$') { return $true }
    if ($leaf -like ".env.*" -and $leaf -ne ".env.example") { return $true }
    return $false
}

$files = Get-ChildItem -Path $AppDir -Recurse -File -Force | Where-Object { -not (Test-ExcludedPath $_.FullName) }
$count = 0
foreach ($file in $files) {
    $rel = $file.FullName.Substring($AppDir.Length).TrimStart("\", "/")
    $dest = Join-Path $Stage $rel
    $destDir = Split-Path $dest -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    }
    Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
    $count++
}

foreach ($sub in @("uploads", "uploads\birth", "uploads\marriage", "uploads\death", "uploads\temp", "backups")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Stage $sub) | Out-Null
}
Set-Content -Path (Join-Path $Stage "uploads\.keep") -Value "Scans are stored here after install." -Encoding UTF8
Set-Content -Path (Join-Path $Stage "backups\.keep") -Value "Backup ZIP files are stored here after install." -Encoding UTF8

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $Stage -DestinationPath $ZipPath -CompressionLevel Optimal

$desktop = [Environment]::GetFolderPath("Desktop")
$desktopZip = Join-Path $desktop "DaragaCivilRegistry_Package_$Stamp.zip"
if (Test-Path $desktop) {
    Copy-Item -LiteralPath $ZipPath -Destination $desktopZip -Force
}

Write-Host ""
Write-Host "Package ready." -ForegroundColor Green
Write-Host "Files copied: $count"
Write-Host "ZIP: $ZipPath"
if (Test-Path $desktopZip) {
    Write-Host "Desktop copy: $desktopZip"
}
Get-Item $ZipPath | Select-Object FullName, @{N = "SizeMB"; E = { [math]::Round($_.Length / 1MB, 1) } }
