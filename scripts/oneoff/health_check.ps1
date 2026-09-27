Start-Sleep -Seconds 3
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 10
    Write-Output "HTTP Status: $($r.StatusCode)"
    Write-Output $r.Content
} catch {
    Write-Output "Error: $_"
}
