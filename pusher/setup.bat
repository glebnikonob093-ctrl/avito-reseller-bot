@echo off
REM One-time setup for the Avito pusher on Windows.
REM Run this once after cloning the repo. Re-running is safe (idempotent).

setlocal ENABLEEXTENSIONS
cd /d "%~dp0"

echo === Avito pusher: setup ===

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not on PATH. Install Python 3.11+ from https://www.python.org/downloads/windows/ ^(check "Add Python to PATH"^).
    exit /b 1
)

if not exist .venv (
    echo Creating virtualenv .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Make sure `python` is a real Python 3.11+ install, not the App Store stub.
        exit /b 1
    )
)

echo Activating venv ...
call ".venv\Scripts\activate.bat"

echo Upgrading pip ...
python -m pip install --upgrade pip --disable-pip-version-check

echo Installing requirements ...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. See messages above.
    exit /b 1
)

echo Installing Chromium for Playwright ...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] playwright install failed. See messages above.
    exit /b 1
)

if not exist .env (
    echo Creating .env from .env.example ...
    copy /Y ".env.example" ".env" >nul
    echo [NEXT] Open .env in Notepad and fill in WEBHOOK_URL and DUFF_WEBHOOK_SECRET.
)

if not exist targets.json (
    echo Creating targets.json from targets.example.json ...
    copy /Y "targets.example.json" "targets.json" >nul
    echo [NEXT] Open targets.json in Notepad and put YOUR catalog URLs / category / region.
)

echo.
echo === Setup OK ===
echo Edit .env and targets.json, then run.bat to push once.
exit /b 0
