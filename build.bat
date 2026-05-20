@echo off
:: ─────────────────────────────────────────────────────────────
:: build.bat — clean build script for DemoVoiceAssistant
:: Run from project root with venv active:
::   venv\Scripts\activate
::   build.bat
:: ─────────────────────────────────────────────────────────────

echo [1/4] Stopping any running instance...
tasklist /fi "imagename eq DemoVoiceAssistant.exe" 2>NUL | find /i "DemoVoiceAssistant.exe" >NUL
if not errorlevel 1 (
    taskkill /f /im DemoVoiceAssistant.exe >NUL 2>&1
    echo       Killed running DemoVoiceAssistant.exe
    timeout /t 2 /nobreak >NUL
) else (
    echo       Not running, skipping.
)

echo [2/4] Stopping any lingering Python processes (optional safety)...
tasklist /fi "imagename eq python.exe" 2>NUL | find /i "python.exe" >NUL
if not errorlevel 1 (
    echo       WARNING: python.exe is running — skipping to avoid killing unrelated processes.
    echo       If the build fails with PermissionError, close other Python processes manually.
) else (
    echo       No python.exe running.
)

echo [3/4] Building...
pyinstaller DemoVoiceAssistant.spec --clean --noconfirm --log-level WARN > builder.log 2>&1

if errorlevel 1 (
    echo.
    echo [FAILED] Build failed. Check builder.log for details.
    echo          Quick error summary:
    findstr /I " ERROR " builder.log
    exit /b 1
)

echo [4/4] Verifying output...
if exist "dist\DemoVoiceAssistant\DemoVoiceAssistant.exe" (
    echo.
    echo [SUCCESS] Build complete.
    echo           dist\DemoVoiceAssistant\DemoVoiceAssistant.exe
    for %%A in ("dist\DemoVoiceAssistant\DemoVoiceAssistant.exe") do echo           Size: %%~zA bytes
) else (
    echo.
    echo [FAILED] EXE not found in dist\ — build may have silently failed.
    echo          Check builder.log for details.
    exit /b 1
)