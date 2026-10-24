# Build Orbit: the installer (dist\Orbit-Setup-<version>.exe) and the portable exe (dist\Orbit-Portable-<version>.exe).
# Needs Python 3.12 + `pip install -r requirements.txt pyinstaller`, and Inno Setup 6.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path orbit.pyw -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Orbit $version"

if (-not (Test-Path assets\orbit.ico)) { python tools\make_icon.py }

# comtypes writes its UI Automation wrapper on first use; generate it now so the exe ships with it
python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')"
if ($LASTEXITCODE) { throw "generating the UI Automation wrapper failed" }

$common = @("--noconfirm", "--clean", "--windowed", "--icon", "assets\orbit.ico",
            "--exclude-module", "tkinter", "--exclude-module", "numpy",  # comtypes would drag numpy in; unused
            "--hidden-import", "comtypes.gen.UIAutomationClient", "orbit.pyw")

# 1) folder build for the installer
python -m PyInstaller @common --name Orbit --distpath dist --workpath build\onedir
if ($LASTEXITCODE) { throw "PyInstaller (onedir) failed" }

# 2) portable build: a single exe
python -m PyInstaller @common --onefile --name "Orbit-Portable-$version" --distpath dist --workpath build\onefile
if ($LASTEXITCODE) { throw "PyInstaller (onefile) failed" }

# 3) installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found" }
& $iscc "/DAppVersion=$version" installer\Orbit.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

Get-ChildItem dist\*.exe | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
