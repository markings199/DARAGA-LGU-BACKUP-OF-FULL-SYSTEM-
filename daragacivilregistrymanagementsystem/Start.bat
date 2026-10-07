@echo off
title Daraga Civil Registry
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\start.ps1"
if errorlevel 1 pause
