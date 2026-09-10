$ErrorActionPreference = "Stop"

$LabRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogsDir = Join-Path $LabRoot "logs"
$Port = 8011

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique

if ($Existing) {
  Write-Output "Dashboard API bridge already listening on http://127.0.0.1:$Port/ (PID $Existing)"
  exit 0
}

Start-Process `
  -FilePath "node.exe" `
  -ArgumentList "scripts/dashboard-api-bridge.mjs" `
  -WorkingDirectory $LabRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogsDir "api-bridge.log") `
  -RedirectStandardError (Join-Path $LogsDir "api-bridge.err.log")

Start-Sleep -Seconds 2

$Started = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique

if (-not $Started) {
  throw "Dashboard API bridge did not start. Check $LogsDir\api-bridge.err.log"
}

Write-Output "Dashboard API bridge listening on http://127.0.0.1:$Port/ (PID $Started)"
