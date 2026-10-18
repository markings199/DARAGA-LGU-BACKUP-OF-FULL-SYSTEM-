@echo off
setlocal EnableExtensions
title Daraga Civil Registry
echo.
echo Starting Daraga Civil Registry...
echo Keep this window open.
echo.

cd /d "%~dp0"
if not exist "%~dp0app.py" (
    echo Could not find app.py in:
    echo   %~dp0
    echo Open the DaragaCivilRegistry folder, then double-click Start.bat.
    pause
    exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo The system is not installed yet.
    echo Double-click Install.bat first, then Start.bat again.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Flask is missing. Installing required packages now...
    echo This can take several minutes the first time. Keep this window open.
    echo.
    "%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    "%~dp0.venv\Scripts\python.exe" -c "import flask" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Packages did not install. Double-click Install.bat, wait until it finishes, then Start.bat again.
        pause
        exit /b 1
    )
    echo Packages installed.
    echo.
)

set "PG_BIN=%USERPROFILE%\scoop\apps\postgresql\current\bin"
set "PG_DATA=%USERPROFILE%\scoop\apps\postgresql\current\data"
set "PG_LOG=%TEMP%\daraga-postgres.log"
if exist "%PG_BIN%\pg_ctl.exe" set "PATH=%PG_BIN%;%PATH%"

echo Checking PostgreSQL...
"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
if not errorlevel 1 (
    echo PostgreSQL is ready.
    goto pg_ready
)

if exist "%PG_BIN%\pg_ctl.exe" if exist "%PG_DATA%\postgresql.conf" (
    echo Starting PostgreSQL...
    "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA%" status >nul 2>&1
    if errorlevel 1 (
        if exist "%PG_DATA%\postmaster.pid" (
            echo Clearing a leftover PostgreSQL lock...
            "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA%" -m fast -t 5 stop >nul 2>&1
            timeout /t 2 /nobreak >nul
            if exist "%PG_DATA%\postmaster.pid" del /f /q "%PG_DATA%\postmaster.pid" >nul 2>&1
        )
        start "" /b "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA%" -l "%PG_LOG%" start >nul 2>&1
    ) else (
        echo PostgreSQL is already running.
    )
)

set /a _pgwait=0
:wait_pg
"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
if not errorlevel 1 (
    echo PostgreSQL is ready.
    goto pg_ready
)
set /a _pgwait+=1
if %_pgwait% GEQ 20 (
    echo PostgreSQL is still starting. Loading the app anyway...
    goto pg_ready
)
echo Waiting for PostgreSQL (%_pgwait%/20^)...
timeout /t 2 /nobreak >nul
goto wait_pg

:pg_ready
netstat -ano | findstr ":5001" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo The system is already running.
    echo Opening http://127.0.0.1:5001
    start "" "http://127.0.0.1:5001"
    pause
    exit /b 0
)

echo Loading the application. First start can take about one minute...
echo Then open: http://127.0.0.1:5001
echo Press Ctrl+C to stop.
echo.
set "PYTHONUNBUFFERED=1"
"%~dp0.venv\Scripts\python.exe" -u "%~dp0app.py"
echo.
echo The system stopped.
pause
