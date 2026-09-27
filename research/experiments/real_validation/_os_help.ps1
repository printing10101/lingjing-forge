$ErrorActionPreference = 'Continue'
$exe = 'C:/Users/Lenovo/openscience/node_modules/@synsci/openscience-windows-x64/bin/openscience.exe'
$out = & $exe help 2>&1 | Out-String
[System.IO.File]::WriteAllText('C:/Users/Lenovo/AppData/Local/Temp/os_help.txt', $out, [System.Text.Encoding]::UTF8)
Write-Host ("saved {0} bytes" -f $out.Length)
$c = [System.IO.File]::ReadAllText('C:/Users/Lenovo/AppData/Local/Temp/os_help.txt', [System.Text.Encoding]::UTF8)
$cmds = [regex]::Matches($c, 'openscience (w+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
Write-Host "=== top-level commands ==="
foreach ($x in $cmds) { Write-Host ("  " + $x) }
Write-Host ""
Write-Host "=== help text ==="
$lines = $c -split [char]10
foreach ($ln in $lines) {
    $t = $ln.Trim()
    if ($t.Length -gt 0 -and $t -notmatch '^[\u2500-\u257F]') {
        Write-Host ("  " + $t.Substring(0, [Math]::Min(140, $t.Length)))
    }
}
