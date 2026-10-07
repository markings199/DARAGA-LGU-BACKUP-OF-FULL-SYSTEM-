@echo off
title Daraga Civil Registry - Install
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\install.ps1"
if errorlevel 1 (
  echo.
  echo Install did not finish.
  pause
)
