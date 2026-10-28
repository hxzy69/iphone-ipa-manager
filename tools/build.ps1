param([switch]$SkipNative, [string]$Python)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install requirements-dev.txt first.' }

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Push-Location $projectRoot
try {
    Invoke-Checked $python @('tools/make_icon.py')
    Invoke-Checked $python @('tools/fetch_netmuxd.py')
    if (-not $SkipNative) {
        $previousRustFlags = $env:RUSTFLAGS
        $previousEncodedFlags = $env:CARGO_ENCODED_RUSTFLAGS
        $previousCFlags = $env:CFLAGS
        $previousCxxFlags = $env:CXXFLAGS
        $previousShellFlags = $env:CC_SHELL_ESCAPED_FLAGS
        try {
            # Panic locations must not expose the developer's Windows profile.
            $env:RUSTFLAGS = $null
            $env:CARGO_ENCODED_RUSTFLAGS = '--remap-path-prefix=' + $env:USERPROFILE + '=/build/user'
            $env:CC_SHELL_ESCAPED_FLAGS = '1'
            $env:CFLAGS = "/experimental:deterministic '/pathmap:" + $env:USERPROFILE + "=/build/user'"
            $env:CXXFLAGS = $env:CFLAGS
            Invoke-Checked 'cargo' @('build', '--locked', '--release', '--manifest-path', 'native/Cargo.toml')
            Invoke-Checked 'cargo' @('test', '--locked', '--release', '--manifest-path', 'native/Cargo.toml')
            Invoke-Checked 'cargo' @('test', '--locked', '--release', '--manifest-path', 'vendor/block-buffer/Cargo.toml')
        } finally {
            $env:RUSTFLAGS = $previousRustFlags
            $env:CARGO_ENCODED_RUSTFLAGS = $previousEncodedFlags
            $env:CFLAGS = $previousCFlags
            $env:CXXFLAGS = $previousCxxFlags
            $env:CC_SHELL_ESCAPED_FLAGS = $previousShellFlags
        }
        $nativeOutput = Join-Path $projectRoot 'native\target\release\iloader-wifi.exe'
        if ($env:CARGO_TARGET_DIR) { $nativeOutput = Join-Path $env:CARGO_TARGET_DIR 'release\iloader-wifi.exe' }
        $nativeDestination = Join-Path $projectRoot 'tools\optimized-iloader\iloader-wifi.exe'
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $nativeDestination) | Out-Null
        Copy-Item -LiteralPath $nativeOutput -Destination $nativeDestination
        $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        Invoke-Checked $compiler @('/nologo', '/target:exe', '/optimize+', '/reference:System.ServiceProcess.dll',
            '/out:tools\wifi-service\BridgeService.exe', 'tools\wifi-service\BridgeService.cs')
    }
    Invoke-Checked $python @('tools/verify_companions.py')
    Invoke-Checked $python @('-m', 'ruff', 'check', '.')
    Invoke-Checked $python @('-m', 'ruff', 'format', '--check', '.')
    Invoke-Checked $python @('tools/test.py')
    Invoke-Checked $python @('tools/security_audit.py')
    Invoke-Checked $python @('tools/audit_release.py')
    Invoke-Checked $python @('-m', 'PyInstaller', '--noconfirm', '--distpath', 'dist', '--workpath', 'build', 'iPhoneIPAManager.spec')
    Copy-Item -LiteralPath 'docs/START_HERE.html' -Destination 'dist/START_HERE.html'
    Copy-Item -LiteralPath 'LICENSE' -Destination 'dist/LICENSE.txt'
    Copy-Item -LiteralPath 'NOTICE.md' -Destination 'dist/NOTICE.md'
    Invoke-Checked $python @('tools/collect_licenses.py')
    Copy-Item -LiteralPath 'artifacts/dependency-audit.json' -Destination 'dist/dependency-audit.json'
    Copy-Item -LiteralPath 'artifacts/sbom.cdx.json' -Destination 'dist/sbom.cdx.json'
    $report = Join-Path $projectRoot 'dist\self-test.json'
    $check = Start-Process -FilePath 'dist/iPhoneIPAManager.exe' -ArgumentList @('--self-test', '--report-file', ('"' + $report + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw 'Packaged runtime self-test failed.' }
    Invoke-Checked $python @('tools/package_release.py')
    Invoke-Checked 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'tools/build_installer.ps1', '-Python', $python)
    Write-Host 'Build complete. Review dist/self-test.json and docs/VALIDATION.md before publishing.'
} finally { Pop-Location }
