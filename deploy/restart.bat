@echo off
cd /d "%~dp0"
docker compose --profile public up -d --force-recreate
echo.
echo Restarted with the new settings.
pause
