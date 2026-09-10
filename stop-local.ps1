param([switch]$Force)
$ErrorActionPreference = 'Stop'
$Api = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Api) {
    $Health = Invoke-RestMethod 'http://127.0.0.1:8001/api/v1/health' -TimeoutSec 5
    if ($Health.service -ne 'marketshelper-tenant') { throw 'Unexpected service on port 8001' }
    $State = Invoke-RestMethod 'http://127.0.0.1:8001/api/v1/sync/status' -TimeoutSec 5
    if ($State.status -eq 'running' -and -not $Force) { throw 'Synchronization is running. Wait for completion or use -Force to interrupt it.' }
    taskkill.exe /PID $Api.OwningProcess /F
}
$Frontend = Get-NetTCPConnection -LocalPort 5177 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Frontend) {
    $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($Frontend.OwningProcess)"
    if ($ProcessInfo.CommandLine -notlike '*node_modules/vite/bin/vite.js*') { throw 'Unexpected service on port 5177' }
    taskkill.exe /PID $Frontend.OwningProcess /F
}
Write-Output 'Local services stopped.'
