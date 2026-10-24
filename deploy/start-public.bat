@echo off
cd /d "%~dp0"
docker compose --profile public up -d
echo.
echo n8n + public tunnel are starting.
pause
