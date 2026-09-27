$ErrorActionPreference = 'Continue'
$base = 'http://localhost:18787'
$projID = '7b598ef1b710e2238a300d38621bba34deab5bea'
$cwd = 'C:\Users\<user>\Desktop\灵境制造（上线版）'

Write-Host "=== 1) POST /session（灵境制造项目）==="
$body = @{ prompt = '2+2=?'; projectID = $projID; cwd = $cwd; mcpServers = @() } | ConvertTo-Json
try {
    $r = Invoke-WebRequest -Uri "$base/session" -Method POST -Body $body -ContentType 'application/json' -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
    Write-Host ("  HTTP {0} | {1}" -f $r.StatusCode, $r.Content.Substring(0, [Math]::Min(500, $r.Content.Length)))
} catch {
    Write-Host ("  失败: {0}" -f $_.Exception.Message.Substring(0, [Math]::Min(150, $_.Exception.Message.Length)))
    if ($_.Exception.Response) {
        $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        Write-Host ("  响应体: " + $sr.ReadToEnd().Substring(0, [Math]::Min(400, $sr.ReadToEnd().Length)))
    }
}

Write-Host ""
Write-Host "=== 2) POST /agent（探测触发执行）==="
$body2 = '{"command":"start","cwd":"C:/Users/<user>/Desktop/灵境制造（上线版）"}'
try {
    $r = Invoke-WebRequest -Uri "$base/agent" -Method POST -Body $body2 -ContentType 'application/json' -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    Write-Host ("  HTTP {0} | {1}" -f $r.StatusCode, $r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))
} catch {
    $code = 'ERR'; if ($_.Exception.Response) { $code = $_.Exception.Response.StatusCode.value__ }
    Write-Host ("  HTTP {0}" -f $code)
}

Write-Host ""
Write-Host "=== 3) GET /session/list 或类似 ==="
foreach ($p in @('/session/list', '/sessions', '/agent/list')) {
    try {
        $r = Invoke-WebRequest -Uri ("$base" + $p) -TimeoutSec 6 -UseBasicParsing -ErrorAction Stop
        Write-Host ("  GET {0,-14} HTTP {1} [{2}] | {3}" -f $p, $r.StatusCode, $r.Headers['Content-Type'], $r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))
    } catch {
        $code = 'ERR'; if ($_.Exception.Response) { $code = $_.Exception.Response.StatusCode.value__ }
        Write-Host ("  GET {0,-14} {1}" -f $p, $code)
    }
}
