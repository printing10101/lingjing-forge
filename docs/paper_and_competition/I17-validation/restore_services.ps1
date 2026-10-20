$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$privateDirectory = 'D:/nexent-deploy'
$systemPython = '<用户主目录>/AppData/Local/Programs/Python/Python314/python.exe'
$desktopPython = Join-Path $repoRoot 'engineering/python/desktop_runtime/runtime/python.exe'
$report = @()
$dockerDesktop = '<用户主目录>/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe'
if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    $report += @{service='docker_desktop'; action='started'}
} else {
    $report += @{service='docker_desktop'; action='already_running'}
}
$backendListener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $backendListener) {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:AGENT_TOKEN_STORE = Join-Path $privateDirectory 'agent_tokens.json'
    $backendTaskProcess = Start-Process -FilePath $desktopPython -ArgumentList 'start_server.py' -WorkingDirectory (Join-Path $repoRoot 'engineering/python') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privateDirectory 'backend-i17.log') -RedirectStandardError (Join-Path $privateDirectory 'backend-i17.err') -PassThru
    $report += @{service='desktop_backend'; action='started'; process_id=$backendTaskProcess.Id}
} else {
    $report += @{service='desktop_backend'; action='existing_listener_preserved'}
}
$mcpListener = Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue
if ($mcpListener) {
    $mcpTaskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($mcpListener.OwningProcess)"
    if ([System.IO.Path]::GetFullPath($mcpTaskProcess.ExecutablePath) -ne [System.IO.Path]::GetFullPath($systemPython) -or $mcpTaskProcess.CommandLine -notmatch '-m mcp_server.server') {
        throw 'Unexpected MCP listener; service was not stopped.'
    }
    Stop-Process -Id $mcpTaskProcess.ProcessId
}
$env:PYTHONPATH = Join-Path $repoRoot 'engineering/python'
$env:LINGJING_AGENT_TOKEN = (Get-Content -LiteralPath (Join-Path $privateDirectory 'agent-token.txt') -Raw).Trim()
$env:LINGJING_MCP_INGRESS_TOKEN = (Get-Content -LiteralPath (Join-Path $privateDirectory 'mcp-token.txt') -Raw).Trim()
$env:LNN_MCP_ALLOW_REMOTE = '1'
$newMcpTaskProcess = Start-Process -FilePath $systemPython -ArgumentList '-m mcp_server.server --transport sse --host 0.0.0.0 --port 8088' -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privateDirectory 'mcp-i17.log') -RedirectStandardError (Join-Path $privateDirectory 'mcp-i17.err') -PassThru
$report += @{service='authenticated_mcp'; action='started'; process_id=$newMcpTaskProcess.Id}
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'service-start.json') -Encoding utf8
$report | ConvertTo-Json -Compress
