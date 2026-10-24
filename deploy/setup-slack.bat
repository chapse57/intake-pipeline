@echo off
cd /d "%~dp0"
echo.
echo Paste the Slack webhook URL (right-click or Ctrl+V), then Enter
set /p SURL=Slack URL: 
powershell -NoProfile -Command "$u=$env:SURL.Trim(); if ($u -notmatch '^https://hooks\.slack\.com/') { Write-Host 'That does not look like a Slack webhook URL (https://hooks.slack.com/...). Nothing saved.'; exit 1 }; $c = Get-Content inquiry.env | Where-Object { $_ -notmatch '^SLACK_WEBHOOK_URL=' }; $c + ('SLACK_WEBHOOK_URL=' + $u) | Set-Content inquiry.env -Encoding ascii; Write-Host 'Saved.'"
if errorlevel 1 (pause & exit /b 1)
echo Restarting n8n so it picks up the URL...
docker compose --profile public up -d --force-recreate n8n
echo.
echo Done. This window closes when you press a key.
pause
