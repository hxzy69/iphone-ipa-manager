param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$version = (& $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not read the application version. Run from the project folder.' }
$payload = Join-Path $project "artifacts\iPhoneIPAManager-$version-windows-x64.zip"
if (-not (Test-Path -LiteralPath $payload)) { throw 'Build and package the Windows release first.' }
$hashFile = Join-Path $project 'build\installer-payload.sha256'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $hashFile) | Out-Null
[IO.File]::WriteAllText($hashFile, (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash.ToLowerInvariant())
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is unavailable.' }
$installer = Join-Path $project "artifacts\iPhoneIPAManager-$version-setup-x64.exe"
& $compiler /nologo /target:winexe /platform:x64 /langversion:5 /optimize+ /debug- "/out:$installer" "/win32manifest:$PSScriptRoot\setup.manifest" "/win32icon:$project\assets\app.ico" /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/resource:$payload,payload.zip" "/resource:$hashFile,payload.sha256" "$PSScriptRoot\SetupBootstrap.cs"
if ($LASTEXITCODE -ne 0) { throw 'Setup launcher compilation failed.' }
$line = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + (Split-Path -Leaf $installer) + "`n"
[IO.File]::WriteAllText((Join-Path $project 'artifacts\SETUP-SHA256SUMS.txt'), $line)
$report = Join-Path $project 'artifacts\setup-self-test.json'
$check = Start-Process -FilePath $installer -ArgumentList @('--verify-payload', ('"' + $report + '"')) -WindowStyle Hidden -Wait -PassThru
if ($check.ExitCode -ne 0) { throw 'Setup extraction and runtime self-test failed.' }
& $Python -c "import sys; from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable; value=read_manifest_from_executable(sys.argv[1]); assert b'asInvoker' in value and b'requireAdministrator' not in value" $installer
if ($LASTEXITCODE -ne 0) { throw 'Setup requests unexpected Windows privileges.' }
Write-Output "Created per-user setup: $installer"
