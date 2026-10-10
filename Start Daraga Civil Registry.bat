@echo off
setlocal EnableExtensions
title Daraga Civil Registry (PostgreSQL)

set "ROOT=%~dp0"
set "APP=%ROOT%daragacivilregistrymanagementsystem"
if not exist "%APP%\app.py" (
    echo Could not find app.py in:
    echo   %APP%
    pause
    exit /b 1
)
cd /d "%APP%"

set "PG_BIN=%USERPROFILE%\scoop\apps\postgresql\current\bin"
set "PG_DATA=%USERPROFILE%\scoop\apps\postgresql\current\data"
set "PATH=%PG_BIN%;%PATH%"

if exist "%PG_BIN%\pg_isready.exe" "%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
if errorlevel 1 (
    echo Starting PostgreSQL...
    if exist "%PG_DATA%\postmaster.pid" (
        "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA%" -m fast -t 5 stop >nul 2>&1
        timeout /t 2 /nobreak >nul
        if exist "%PG_DATA%\postmaster.pid" del /f /q "%PG_DATA%\postmaster.pid" >nul 2>&1
    )
    start "" /b "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA%" -l "%TEMP%\daraga-postgres.log" start >nul 2>&1
    set /a _w=0
    :wait_root_pg
    "%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
    if not errorlevel 1 goto pg_root_ready
    set /a _w+=1
    if %_w% GEQ 20 goto pg_root_ready
    timeout /t 2 /nobreak >nul
    goto wait_root_pg
) else (
    echo PostgreSQL is already running.
)
:pg_root_ready

netstat -ano | findstr ":5001" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo The system is already running.
    echo Opening http://127.0.0.1:5001
    start "" "http://127.0.0.1:5001"
    pause
    exit /b 0
)

set "PY="
if exist "%APP%\.venv\Scripts\python.exe" set "PY=%APP%\.venv\Scripts\python.exe"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python was not found. Install Python or create .venv in the app folder.
    pause
    exit /b 1
)

echo.
echo Starting Daraga Civil Registry (PostgreSQL copy)...
echo   This PC:  http://127.0.0.1:5001
echo   Stop:     close this window or press Ctrl+C
echo.
"%PY%" app.py
echo.
echo The system stopped.
pause
