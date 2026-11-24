# setup.ps1 — one-time setup for Grympha Trading Desk on a fresh Windows PC.
# Installs Python 3.11 (64-bit, with pip + tkinter) if missing, then the Python
# libraries the desk needs. Safe to re-run: it skips Python if 3.11 is already there.
#
# Run from the Trading Desk folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

Write-Host "=== Grympha Trading Desk setup ===" -ForegroundColor Cyan

function Test-Py311 {
    try {
        $out = & py -3.11 -c "import sys,struct,tkinter; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# --- 1. Python 3.11 (64-bit, pip, tkinter) ---
if (Test-Py311) {
    Write-Host "[1/2] Python 3.11 (64-bit, tkinter) already installed - skipping." -ForegroundColor Green
} else {
    Write-Host "[1/2] Installing Python 3.11 (64-bit)..." -ForegroundColor Yellow
    $url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $exe = Join-Path $env:TEMP "python311-setup.exe"
    Write-Host "      Downloading installer..."
    Invoke-WebRequest -Uri $url -OutFile $exe
    Write-Host "      Running installer (this may take a minute)..."
    $p = Start-Process -FilePath $exe -ArgumentList '/passive','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_tcltk=1','Include_launcher=1' -Wait -PassThru
    Remove-Item $exe -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { throw "Python installer exited with code $($p.ExitCode)." }
    if (-not (Test-Py311)) {
        throw "Python 3.11 did not verify after install. Close PowerShell, reopen it, and run setup.ps1 again."
    }
    Write-Host "      Python 3.11 installed." -ForegroundColor Green
}

# --- 2. Python libraries ---
Write-Host "[2/2] Installing Python libraries from requirements.txt..." -ForegroundColor Yellow
if (-not (Test-Path ".\requirements.txt")) {
    throw "requirements.txt not found. Run this script from inside the Trading Desk folder."
}
& py -3.11 -m pip install --upgrade pip
& py -3.11 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit code $LASTEXITCODE)." }

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "You can now launch the desk by double-clicking START_APP.cmd" -ForegroundColor Green
Write-Host "(First, copy your config.json and .env into this folder.)" -ForegroundColor Green
