@echo off
setlocal
cd /d "%~dp0"
echo.
echo [1/4] Checking the Caddyfile - nothing changes if this fails
docker run --rm -v "%cd%\Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
if errorlevel 1 (echo. & echo Caddyfile check failed - nothing was changed. Send a screenshot. & pause & exit /b 1)

echo.
echo [2/4] Starting the gate and pointing the tunnel at it - n8n itself keeps running
docker compose --profile public up -d
if errorlevel 1 (echo. & echo docker compose failed. Send a screenshot. & pause & exit /b 1)

echo.
echo [3/4] Checks through the public tunnel address - the address itself is not printed
set "NGROK_DOMAIN="
for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="NGROK_DOMAIN" set "NGROK_DOMAIN=%%b"
if not defined NGROK_DOMAIN (echo NGROK_DOMAIN not found in deploy\.env. Send a screenshot. & pause & exit /b 1)
set "NGROK_DOMAIN=%NGROK_DOMAIN:"=%"

echo Waiting for the tunnel to reconnect (up to 60 seconds)...
set TRIES=0
:wait
set /a TRIES+=1
set "CODE="
for /f %%c in ('curl -s -o nul -m 10 -w "%%{http_code}" "https://%NGROK_DOMAIN%/healthz"') do set "CODE=%%c"
if not "%CODE%"=="200" if %TRIES% lss 12 (timeout /t 5 /nobreak >nul & goto wait)

set FAILED=0
call :check GET /healthz 200
call :check GET / 404
call :check GET /signin 404
call :check GET /rest/settings 404
call :check GET /webhook-test/inquiry 404
call :check GET /webhook/inquiry 404
rem An unsigned POST must reach n8n and be refused there (401), not by the gate (404).
rem It shows up as one "auth rejected" row in Events - count it as test traffic.
call :check POST /webhook/inquiry 401

echo.
echo [4/4] n8n version running now - used later to pin the image
docker exec ether-n8n n8n --version

echo.
if "%FAILED%"=="0" (
  echo All checks passed. Next: one TEST inquiry on lampasworks.com, then send a screenshot of this window.
) else (
  echo Some checks failed. Inquiries are held in the queue meanwhile. Send a screenshot of this window.
)
pause
exit /b 0

:check
set "CODE="
for /f %%c in ('curl -s -o nul -m 20 -X %1 -H "Content-Type: application/json" -w "%%{http_code}" "https://%NGROK_DOMAIN%%2"') do set "CODE=%%c"
if "%CODE%"=="%3" (echo   OK    %1 %2 = %CODE%) else (echo   FAIL  %1 %2 = %CODE%, expected %3 & set FAILED=1)
exit /b 0
