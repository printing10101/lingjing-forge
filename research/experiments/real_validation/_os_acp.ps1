$ErrorActionPreference = 'Continue'
$exe = 'C:/Users/Lenovo/openscience/node_modules/@synsci/openscience-windows-x64/bin/openscience.exe'
function TryCmd($a, $label) {
    Write-Host ("=== {0} ===" -f $label)
    $out = & $exe @a 2>&1 | Out-String
    if ($out) { Write-Host ($out.Substring(0, [Math]::Min(2000, $out.Length))) } else { Write-Host "  (无输出)" }
    Write-Host ""
}
TryCmd @('acp', '--help') 'acp --help'
TryCmd @('session', '--help') 'session --help'
TryCmd @('export', '--help') 'export --help'
TryCmd @('local', '--help') 'local --help'
