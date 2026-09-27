$ErrorActionPreference = 'Continue'
$exe = 'C:/Users/Lenovo/openscience/node_modules/@synsci/openscience-windows-x64/bin/openscience.exe'

# 完整帮助输出到 UTF-8 文件（避免控制台编码乱码）
$job = Start-Job -ScriptBlock {
    param($exe)
    & $exe help 2>&1 | Out-String
} -ArgumentList $exe
if (Wait-Job $job -Timeout 15) {
    $out = Receive-Job $job
    [System.IO.File]::WriteAllText('C:/Users/Lenovo/AppData/Local/Temp/os_help.txt', $out, [System.Text.Encoding]::UTF8)
    Write-Host ("help 输出 {0} bytes" -f $out.Length)
} else {
    Write-Host "  help 超时"
    Stop-Job $job
}
Remove-Job $job -Force

# 提取命令名（openscience xxx 形式）
$c = Get-Content 'C:/Users/Lenovo/AppData/Local/Temp/os_help.txt' -Raw -Encoding UTF8
Write-Host "=== 顶层命令（openscience <cmd>）==="
[regex]::Matches($c, 'openscience\s+(\w+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique | ForEach-Object { Write-Host ("  {0}" -f $_) }
Write-Host ""
Write-Host "=== help 纯文本行（含中文命令描述）==="
$lines = $c -split [char]10
$lines | Where-Object { $_.Trim() -ne '' } | Select-Object -First 60 | ForEach-Object { Write-Host ("  " + $_.Substring(0, [Math]::Min(150, $_.Length))) }
