@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File deploy-monitor.ps1
pause
