@echo off
rem Redeploy the Worker code only. The secrets and the KV namespace set up by
rem deploy-relay.bat / deploy-monitor.bat stay as they are.
cd /d "%~dp0"
echo.
echo [1/2] Cloudflare sign-in check
call npx --yes wrangler@latest whoami 2>nul | findstr /C:"You are logged in" >nul
if errorlevel 1 (
  echo A browser tab will open. Click "Allow" there, then come back to this window.
  call npx --yes wrangler@latest login
)
echo.
echo [2/2] Deploying
call npx --yes wrangler@latest deploy
if errorlevel 1 (echo Deploy failed - send a screenshot of this window. & pause & exit /b 1)
echo.
echo Done. Close this window.
pause
