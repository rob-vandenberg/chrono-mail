# --- Version ------------------------------------------------------------
# build_windows.ps1 2.4.322
# --- Version history ----------------------------------------------------
# v2.4.322: The AES code of pycryptodomex (Cryptodome), which pyzipper uses for a protected backup (D-244)
# v1.5.257: The Public Suffix List file of publicsuffixlist (the main domain of a link, D-215)
# v0.5.131: The program's own package information, so that it knows its version (About said "version unknown")
# v0.5.129: First run on Windows: Inno Setup in Program Files (x86) is found; time-zone files once; only the parts of win32ctypes the password store uses
# v0.5.127: Initial: the Windows program folder and setup.exe (written on Linux; first run on the Windows machine)
#
# Builds dist\chrono-mail_<version>_setup.exe: Nuitka makes the program folder (Python, Qt and the program; nothing needs
# to be installed by the user), Inno Setup makes the installer with a Start menu entry and an uninstaller. Run from any
# folder in PowerShell: powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Needs Python 3.14 (the py launcher) and Inno Setup 6. See packaging\PACKAGING.md.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Version = (Select-String -Path "$Root\pyproject.toml" -Pattern '^version = "(.*)"').Matches[0].Groups[1].Value
$Build = "$Root\build\windows"
$Venv = "$Build\venv"

if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
New-Item -ItemType Directory -Force -Path $Build, "$Root\dist" | Out-Null

py -3.14 -m venv $Venv
& "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
# Nuitka 4.3 corrupts compiled inner functions with PySide6 and Python 3.14 (startup crash, Nuitka issue 4081); 4.2.2 is
# the last good version. Move on to a newer one only after checking that the issue is fixed.
& "$Venv\Scripts\python.exe" -m pip install --quiet $Root nuitka==4.2.2 ordered-set zstandard
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& "$Venv\Scripts\python.exe" -m nuitka `
    --mode=standalone `
    --assume-yes-for-downloads `
    --enable-plugin=pyside6 `
    --include-qt-plugins=sensible `
    --include-package=chrono_mail `
    --include-package-data=chrono_mail `
    --include-package=keyring.backends `
    --include-package=win32ctypes.core `
    --include-package=win32ctypes.pywin32 `
    --include-distribution-metadata=keyring `
    --include-distribution-metadata=chrono-mail `
    --include-package=tzdata `
    --include-package-data=tzdata:zones `
    --include-package-data=dateutil `
    --include-package-data=publicsuffixlist:public_suffix_list.dat `
    --include-package=Cryptodome `
    --windows-console-mode=disable `
    --windows-icon-from-ico="$Root\src\chrono_mail\ui\resources\chrono-mail.ico" `
    --product-name="Chrono Mail" `
    --company-name="Chrono Mail" `
    --file-description="Chrono Mail e-mail client" `
    --product-version=$Version `
    --file-version=$Version `
    --output-dir=$Build `
    --output-filename=chrono-mail.exe `
    "$Root\packaging\chrono_mail_main.py"
if ($LASTEXITCODE -ne 0) { throw "Nuitka failed" }
# Nuitka names the folder after the script; the installer expects chrono-mail.dist.
Rename-Item "$Build\chrono_mail_main.dist" "chrono-mail.dist"

$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) was not found" }
& $Iscc "/DVersion=$Version" "/DSource=$Build\chrono-mail.dist" "/O$Root\dist" "$Root\packaging\chrono-mail.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Built: $Root\dist\chrono-mail_${Version}_setup.exe"
