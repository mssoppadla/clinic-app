@echo off
setlocal
title Clinic Booking - local launcher
set "ROOT=%~dp0"

echo ============================================================
echo  Clinic Booking SaaS - starting on your computer (localhost)
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not installed or not on PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

cd /d "%ROOT%apps\api"

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)
echo Installing dependencies (first run only, may take a minute)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

REM config: local SQLite, stub integrations, CORS for the web page (inherited by child windows)
set "APP_DATABASE_URL=sqlite+pysqlite:///./local.db"
set "APP_CORS_ORIGINS=http://localhost:8080"
set "APP_ENV=local"

echo Seeding the canary clinic...
".venv\Scripts\python.exe" -m app.seed

REM start API window (uses the venv python directly; env is inherited from this window)
start "Clinic API (port 8077)" "%ROOT%apps\api\.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8077

REM start static web server window
cd /d "%ROOT%web"
start "Clinic Web (port 8080)" python -m http.server 8080

timeout /t 4 >nul
start "" "http://localhost:8080/?api=http://localhost:8077"

echo.
echo ============================================================
echo  Opened: http://localhost:8080/?api=http://localhost:8077
echo  API docs: http://localhost:8077/docs
echo  To STOP: close the two server windows, or run stop_local.bat
echo ============================================================
echo You can close THIS window.
pause
