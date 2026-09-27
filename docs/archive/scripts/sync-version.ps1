# sync-version.ps1
# Version synchronization script for Windows
# Updates all version references from the root VERSION file

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$versionFile = Join-Path $projectRoot "VERSION"

# Read version from VERSION file
if (-not (Test-Path $versionFile)) {
    Write-Error "VERSION file not found at $versionFile"
    exit 1
}

$version = (Get-Content $versionFile).Trim()
if (-not ($version -match '^\d+\.\d+\.\d+$')) {
    Write-Error "Invalid version format in VERSION file: $version (expected SemVer: x.y.z)"
    exit 1
}

Write-Host "Syncing version: $version"

# 1. Update Cargo.toml
$cargoToml = Join-Path (Join-Path $projectRoot "src-tauri") "Cargo.toml"
if (Test-Path $cargoToml) {
    $cargoContent = Get-Content $cargoToml -Raw
    $newContent = $cargoContent -replace '(?m)^version\s*=\s*"[^"]*"', "version = `"$version`""
    if ($newContent -ne $cargoContent) {
        Set-Content -Path $cargoToml -Value $newContent -NoNewline
        Write-Host "  [OK] Cargo.toml updated"
    } else {
        Write-Host "  [OK] Cargo.toml already at $version"
    }
} else {
    Write-Warning "Cargo.toml not found at $cargoToml"
}

# 2. Update package.json
$packageJson = Join-Path $projectRoot "package.json"
if (Test-Path $packageJson) {
    $rawJson = Get-Content $packageJson -Raw -Encoding UTF8
    try {
        $pkg = $rawJson | ConvertFrom-Json
    } catch {
        Write-Warning "Failed to parse package.json (encoding issue), using regex fallback"
        $pkgVersion = [regex]::Match($rawJson, '"version"\s*:\s*"([^"]+)"').Groups[1].Value
        if ($pkgVersion -ne $version) {
            $newJson = $rawJson -replace '"version"\s*:\s*"[^"]+"', "`"version`": `"$version`""
            [System.IO.File]::WriteAllText($packageJson, $newJson, [System.Text.Encoding]::UTF8)
            Write-Host "  [OK] package.json updated (regex fallback)"
        } else {
            Write-Host "  [OK] package.json already at $version"
        }
        $pkg = $null
    }
    
    if ($pkg) {
        if ($pkg.version -ne $version) {
            $pkg.version = $version
            $newJson = $pkg | ConvertTo-Json -Depth 10
            [System.IO.File]::WriteAllText($packageJson, $newJson, [System.Text.Encoding]::UTF8)
            Write-Host "  [OK] package.json updated"
        } else {
            Write-Host "  [OK] package.json already at $version"
        }
    }
} else {
    Write-Warning "package.json not found at $packageJson"
}

# 3. Update python/app/version.py (VERSION file is the source, no need to update)
$pyVersion = Join-Path (Join-Path (Join-Path $projectRoot "python") "app") "version.py"
if (Test-Path $pyVersion) {
    Write-Host "  [OK] python/app/version.py reads from VERSION file (no update needed)"
} else {
    Write-Warning "python/app/version.py not found at $pyVersion"
}

# 4. Verify all versions are consistent
Write-Host ""
Write-Host "=== Version Verification ==="

$cargoVersion = Select-String -Path $cargoToml -Pattern '^version\s*=\s*"([^"]+)"' | ForEach-Object { $_.Matches.Groups[1].Value }
$rawPkg = Get-Content $packageJson -Raw -Encoding UTF8
$pkgVersion = [regex]::Match($rawPkg, '"version"\s*:\s*"([^"]+)"').Groups[1].Value
$rootVersion = Get-Content $versionFile | ForEach-Object { $_.Trim() }

$allMatch = ($cargoVersion -eq $version) -and ($pkgVersion -eq $version) -and ($rootVersion -eq $version)

if ($allMatch) {
    Write-Host "All versions match: $version" -ForegroundColor Green
} else {
    Write-Host "Version mismatch detected!" -ForegroundColor Red
    Write-Host "  VERSION file:     $rootVersion"
    Write-Host "  Cargo.toml:       $cargoVersion"
    Write-Host "  package.json:     $pkgVersion"
    exit 1
}
