$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$LabRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$TenantDir = Join-Path $RepoRoot "tenant-app"
$ApiDir = Join-Path $LabRoot "tenant-api"
$Python = Join-Path $TenantDir ".venv\Scripts\python.exe"
$LogsDir = Join-Path $LabRoot "logs"
$Port = 8001

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique

if ($Existing) {
  $Health = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 5
  if ($Health.service -ne 'marketshelper-tenant') { throw "Port $Port belongs to another service" }
  Write-Output "Tenant API already listening on http://127.0.0.1:$Port/ (PID $Existing)"
  exit 0
}

if (-not (Test-Path $Python)) {
  throw "Tenant venv Python was not found: $Python"
}

$env:MH_LOCAL_JOBS = '1'
Start-Process `
  -FilePath $Python `
  -ArgumentList "-m", "uvicorn", "api_server:app", "--app-dir", "`"$ApiDir`"", "--host", "127.0.0.1", "--port", "$Port" `
  -WorkingDirectory $TenantDir `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogsDir "tenant-api.log") `
  -RedirectStandardError (Join-Path $LogsDir "tenant-api.err.log")

for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
  Start-Sleep -Seconds 1
  $Started = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  if ($Started) {
    break
  }
}

if (-not $Started) {
  throw "Tenant API did not start. Check $LogsDir\tenant-api.err.log"
}

Write-Output "Tenant API listening on http://127.0.0.1:$Port/ (PID $Started)"
