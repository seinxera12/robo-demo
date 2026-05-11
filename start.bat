@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM Lightweight Voice Demo — Start Script (Windows)
REM Usage: start.bat  (run from Command Prompt or PowerShell)
REM Press Ctrl+C to stop all processes cleanly.
REM ─────────────────────────────────────────────────────────────────────────────

REM ── Check for .env ────────────────────────────────────────────────────────────
if not exist ".env" (
    echo.
    echo [WARNING] No .env file found.
    echo    Please copy .env.example to .env and fill in your API keys:
    echo      copy .env.example .env
    echo    Then open .env in a text editor and set GROQ_API_KEY=^<your key^>
    echo    After editing, re-run: start.bat
    echo.
    pause
    exit /b 1
)

REM ── Check virtual environment ─────────────────────────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Virtual environment not found. Please run setup first.
    echo    See README.md for Windows setup instructions, or run:
    echo      uv venv --python 3.11 venv\
    echo      uv pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ── Use PowerShell to manage processes with proper cleanup on Ctrl+C ──────────
echo.
echo [*] Starting Lightweight Voice Demo...
echo     Press Ctrl+C to stop all processes.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "$serverProc = $null; $clientProc = $null;" ^
    "try {" ^
    "  Write-Host '[*] Starting server on http://localhost:8000 ...';" ^
    "  $serverProc = Start-Process -FilePath 'venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','server.main:app','--host','0.0.0.0','--port','8000' -PassThru -NoNewWindow;" ^
    "  Start-Sleep -Seconds 3;" ^
    "  Write-Host '[*] Starting audio client...';" ^
    "  $clientProc = Start-Process -FilePath 'venv\Scripts\python.exe' -ArgumentList '-m','client.main' -PassThru -NoNewWindow;" ^
    "  Start-Sleep -Seconds 1;" ^
    "  Write-Host '[*] Opening browser at http://localhost:8000 ...';" ^
    "  Start-Process 'http://localhost:8000';" ^
    "  Write-Host '';" ^
    "  Write-Host '========================================================================';" ^
    "  Write-Host ' Lightweight Voice Demo is running.';" ^
    "  Write-Host ' Browser UI : http://localhost:8000';" ^
    "  Write-Host ' Press Ctrl+C to stop all processes.';" ^
    "  Write-Host '========================================================================';" ^
    "  Write-Host '';" ^
    "  if ($serverProc) { $serverProc.WaitForExit() }" ^
    "} finally {" ^
    "  Write-Host '';" ^
    "  Write-Host '[*] Shutting down...';" ^
    "  if ($clientProc -and -not $clientProc.HasExited) { $clientProc.Kill(); Write-Host '    Audio client stopped.' }" ^
    "  if ($serverProc -and -not $serverProc.HasExited) { $serverProc.Kill(); Write-Host '    Server stopped.' }" ^
    "  Write-Host '[*] Done.';" ^
    "}"

