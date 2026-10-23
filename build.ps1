# Build Orbit: installer (dist\Orbit-Setup-<versi>.exe) dan exe portable (dist\Orbit-Portable-<versi>.exe).
# Butuh: Python 3.12 + `pip install -r requirements.txt pyinstaller`, dan Inno Setup 6.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path orbit.pyw -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Orbit $version"

if (-not (Test-Path assets\orbit.ico)) { python tools\make_icon.py }

$common = @("--noconfirm", "--clean", "--windowed", "--icon", "assets\orbit.ico",
            "--exclude-module", "tkinter", "orbit.pyw")

# 1) versi folder untuk installer
python -m PyInstaller @common --name Orbit --distpath dist --workpath build\onedir
if ($LASTEXITCODE) { throw "PyInstaller (onedir) gagal" }

# 2) versi portable: satu file exe
python -m PyInstaller @common --onefile --name "Orbit-Portable-$version" --distpath dist --workpath build\onefile
if ($LASTEXITCODE) { throw "PyInstaller (onefile) gagal" }

# 3) installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 tidak ditemukan" }
& $iscc "/DAppVersion=$version" installer\Orbit.iss
if ($LASTEXITCODE) { throw "Inno Setup gagal" }

Get-ChildItem dist\*.exe | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
