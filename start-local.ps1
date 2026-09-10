$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$LabRoot = Join-Path $RepoRoot 'mh-redesign-lab'
$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($NodeCommand) {
    $Node = $NodeCommand.Source
} else {
    $Node = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
}
if (-not (Test-Path -LiteralPath $Node)) {
    throw 'Node.js was not found. Install Node.js and run this script again.'
}
$env:PATH = (Split-Path $Node) + ';' + $env:PATH
& (Join-Path $LabRoot 'scripts\start-tenant-api.ps1')
$LogsDir = Join-Path $LabRoot 'logs'
$Existing = Get-NetTCPConnection -LocalPort 5177 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    $Page = Invoke-WebRequest 'http://127.0.0.1:5177/' -UseBasicParsing -TimeoutSec 5
    if ($Page.Content -notmatch 'MARKETSHELPER' -or $Page.Content -notmatch '/src/main.tsx') {
        throw 'Port 5177 belongs to another application. Free the port before starting MARKETSHELPER.'
    }
}
if (-not $Existing) {
    $Process = Start-Process -FilePath $Node -ArgumentList 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5177', '--strictPort' -WorkingDirectory $LabRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogsDir 'frontend.log') -RedirectStandardError (Join-Path $LogsDir 'frontend.err.log')
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        Start-Sleep -Seconds 1
        $Process.Refresh()
        if ($Process.HasExited) { throw 'Frontend exited. See mh-redesign-lab/logs/frontend.err.log.' }
        try {
            $Response = Invoke-WebRequest 'http://127.0.0.1:5177/' -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { break }
        } catch { }
    }
    if (-not $Response -or $Response.StatusCode -ne 200) { throw 'Frontend did not become ready.' }
}
Write-Output 'Open http://127.0.0.1:5177/ in your browser.'
