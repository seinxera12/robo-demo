@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM Lightweight Voice Demo — Start Script (Windows)
REM Usage: start.bat  (double-click or run from Command Prompt)
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

REM ── Launch server in background ───────────────────────────────────────────────
echo.
echo [*] Starting server on http://localhost:8000 ...
start /B "VoiceDemoServer" venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8000

REM Give the server a moment to initialise before starting the client
timeout /t 2 /nobreak >nul

REM ── Launch audio client in background ────────────────────────────────────────
echo [*] Starting audio client...
start /B "VoiceDemoClient" venv\Scripts\python.exe -m client.main

REM Give the client a moment to connect before opening the browser
timeout /t 1 /nobreak >nul

REM ── Open browser ──────────────────────────────────────────────────────────────
echo [*] Opening browser at http://localhost:8000 ...
start http://localhost:8000

echo.
echo ========================================================================
echo  Lightweight Voice Demo is running.
echo  Browser UI : http://localhost:8000
echo.
echo  To stop the demo, close this window or press Ctrl+C.
echo  Background processes (server and client) will continue running until
echo  you end them via Task Manager or by closing their console windows.
echo ========================================================================
echo.
pause
