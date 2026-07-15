@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 下载 P16-P25 720P
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download-p16-p25-720p.ps1"
echo.
pause
