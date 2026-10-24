@echo off
cd /d "%~dp0"
docker compose up -d n8n
echo.
echo n8n is starting. Open http://localhost:5678 in a minute.
pause
