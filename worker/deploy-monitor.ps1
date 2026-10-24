# Adds the uptime monitor to the ether-inquiry Worker:
#   1. a KV namespace for the state (created once, id written into wrangler.toml)
#   2. the Slack webhook as a Worker secret (read from ../deploy/inquiry.env)
#   3. a deploy, which also registers the 5-minute Cron Trigger
# 'Continue' on purpose: Windows PowerShell turns a native tool's stderr into
# terminating errors under 'Stop', and wrangler writes warnings to stderr.
$ErrorActionPreference = 'Continue'
function Fail($m) { Write-Host "`nFAILED: $m" -ForegroundColor Red; exit 1 }
Set-Location $PSScriptRoot
$W = @('--yes', 'wrangler@latest')

Write-Host "`n[1/3] KV namespace for the monitor state"
$toml = Get-Content wrangler.toml -Raw
if ($toml -notmatch 'binding\s*=\s*"STATUS"') {
  $out = (& npx @W kv namespace create ether-status 2>&1 | Out-String)
  $id = $null
  # match the namespace id specifically: the account id is also 32 hex chars
  if ($out -match '"?id"?\s*[:=]\s*"([0-9a-f]{32})"') { $id = $Matches[1] }
  else {
    # already exists from an earlier run: look it up
    $list = (& npx @W kv namespace list 2>$null | Out-String) | ConvertFrom-Json
    $hit = $list | Where-Object { $_.title -match 'ether-status' } | Select-Object -First 1
    if ($hit) { $id = $hit.id }
  }
  if (-not $id) { Write-Host $out; Fail 'Could not create or find the KV namespace.' }
  Add-Content wrangler.toml "`n[[kv_namespaces]]`nbinding = `"STATUS`"`nid = `"$id`"`n"
  Write-Host "  created, id $id"
} else { Write-Host '  already configured' }

Write-Host "`n[2/3] Slack webhook secret"
$line = Get-Content ..\deploy\inquiry.env | Where-Object { $_ -match '^SLACK_WEBHOOK_URL=' } | Select-Object -First 1
if (-not $line) { Fail 'SLACK_WEBHOOK_URL is not in deploy\inquiry.env (run deploy\setup-slack.bat first).' }
$line.Substring(18).Trim() | & npx @W secret put SLACK_WEBHOOK_URL

Write-Host "`n[3/3] Deploy (registers the 5-minute check)"
& npx @W deploy
if ($LASTEXITCODE -ne 0) { Fail 'Deploy failed - send a screenshot of this window.' }
Write-Host "`nDone. Status page: https://ether-inquiry.ether-dev134.workers.dev/api/status"
