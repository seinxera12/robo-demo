# ============================================================================
# Lightweight Voice Demo — Setup Script (Windows PowerShell)
# Usage:
# powershell -ExecutionPolicy Bypass -File .\setup.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "[1/5] Checking for Python 3.11..."

$pythonCmd = $null

# Try py launcher first
try {
    py -3.11 --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonCmd = "py -3.11"
    }
} catch {}

# Fallback to python
if (-not $pythonCmd) {
    try {
        $version = python --version 2>&1
        if ($version -match "3\.11") {
            $pythonCmd = "python"
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host ""
    Write-Host "[ERROR] Python 3.11 not found."
    Write-Host "Install Python 3.11 and try again:"
    Write-Host "https://www.python.org/downloads/"
    exit 1
}

Write-Host "[OK] Python 3.11 detected."

# ============================================================================
# Check uv
# ============================================================================

Write-Host ""
Write-Host "[2/5] Checking for uv..."

$uvExists = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uvExists) {
    Write-Host "[INFO] uv not found. Installing..."

    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"

    $env:Path += ";$HOME\.local\bin"
    $env:Path += ";$HOME\.cargo\bin"

    $uvExists = Get-Command uv -ErrorAction SilentlyContinue

    if (-not $uvExists) {
        Write-Host "[ERROR] uv installation failed."
        exit 1
    }
}

Write-Host "[OK] uv detected."

# ============================================================================
# Create venv
# ============================================================================

Write-Host ""
Write-Host "[3/5] Creating virtual environment..."

uv venv --python 3.11 venv

Write-Host "[OK] Virtual environment created."

# ============================================================================
# Install dependencies
# ============================================================================

Write-Host ""
Write-Host "[4/5] Installing dependencies..."
Write-Host "This may take several minutes."

uv pip install --python .\venv\Scripts\python.exe -r requirements.txt

Write-Host "[OK] Dependencies installed."

# ============================================================================
# Download Kokoro model
# ============================================================================

Write-Host ""
Write-Host "[5/5] Downloading Kokoro model..."

$tempPy = @"
from kokoro import KPipeline

print("Downloading Kokoro English model...")
KPipeline(lang_code='a')
print("Kokoro model ready.")
"@

$tempPy | Out-File temp_kokoro_download.py -Encoding utf8

.\venv\Scripts\python.exe temp_kokoro_download.py

Remove-Item temp_kokoro_download.py

Write-Host "[OK] Kokoro model downloaded."

# ============================================================================
# ENV check
# ============================================================================

Write-Host ""

if (-not (Test-Path ".env")) {
    Write-Host "[WARNING] No .env file found."
    Write-Host ""
    Write-Host "Run:"
    Write-Host "Copy-Item .env.example .env"
}
else {
    Write-Host "[OK] .env file found."
}

# ============================================================================
# Done
# ============================================================================

Write-Host ""
Write-Host "=============================================================="
Write-Host "SETUP COMPLETE"
Write-Host "=============================================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "1. Configure environment:"
Write-Host "   Copy-Item .env.example .env"
Write-Host ""
Write-Host "2. Start the demo:"
Write-Host "   .\start.ps1"
Write-Host ""