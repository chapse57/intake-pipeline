@echo off
cd /d "%~dp0"
echo.
echo [1/3] Cloudflare sign-in check (first run downloads wrangler, about a minute)
call npx --yes wrangler@latest whoami 2>nul | findstr /C:"You are logged in" >nul
if errorlevel 1 (
  echo A browser tab will open. Click "Allow" there, then come back to this window.
  call npx --yes wrangler@latest login
)
echo.
echo [2/3] Deploying the inquiry page + relay
call npx --yes wrangler@latest deploy
if errorlevel 1 (echo Deploy failed - send a screenshot of this window. & pause & exit /b 1)
echo.
echo [3/3] Setting the two secrets
powershell -NoProfile -Command "$l = Get-Content ..\deploy\inquiry.env | Where-Object { $_ -match '^INQUIRY_SECRET=' } | Select-Object -First 1; [Console]::Out.Write($l.Substring(15).Trim())" | npx --yes wrangler@latest secret put INQUIRY_SECRET
powershell -NoProfile -Command "$l = Get-Content ..\deploy\.env | Where-Object { $_ -match '^NGROK_DOMAIN=' } | Select-Object -First 1; [Console]::Out.Write('https://' + $l.Substring(13).Trim() + '/webhook/inquiry')" | npx --yes wrangler@latest secret put N8N_WEBHOOK_URL
echo.
echo Done. The page address is the https://ether-inquiry....workers.dev line above.
pause
