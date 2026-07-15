@echo off
cd /d "%~dp0"
title P16-P25 720P Download and Course Builder
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download-p16-p25-720p.ps1"
echo.
pause
