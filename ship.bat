@echo off
REM Double-click to ship the current changes (uses Git Bash). Optional: pass a message.
cd /d "%~dp0"
where bash >nul 2>nul || ( echo Git Bash not found. Install Git for Windows. & pause & exit /b 1 )
bash ship.sh %*
pause
