@echo off
cd /d "%~dp0"
echo.
echo Step 1: paste the ngrok Authtoken (right-click or Ctrl+V), then Enter
set /p TOKEN=Authtoken: 
echo.
echo Step 2: paste the ngrok domain, e.g. abc-def.ngrok-free.app, then Enter
set /p DOMAIN=Domain: 
powershell -NoProfile -Command "$t=$env:TOKEN.Trim() -replace '^ngrok config add-authtoken\s+',''; $d=$env:DOMAIN.Trim() -replace '^https?://','' -replace '/.*$',''; (Get-Content .env) -replace '^NGROK_AUTHTOKEN=.*',('NGROK_AUTHTOKEN='+$t) -replace '^NGROK_DOMAIN=.*',('NGROK_DOMAIN='+$d) | Set-Content .env -Encoding ascii"
echo.
echo Saved. Starting n8n + public tunnel...
docker compose --profile public up -d
echo.
echo Done. You can close this window.
pause
