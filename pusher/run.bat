@echo off
REM Run the pusher once. Designed for Windows Task Scheduler:
REM   Action      = Start a program
REM   Program     = C:\path\to\pusher\run.bat
REM   Start in    = C:\path\to\pusher
REM Idempotent — duplicates are deduped by external_id on the bot side.

setlocal ENABLEEXTENSIONS
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv missing. Run setup.bat first.
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python pusher.py
exit /b %ERRORLEVEL%
