@echo off
title Stop Clinic Booking local servers
echo Stopping API (8077) and Web (8080)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8077 ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul
echo Done. (You can also just close the two server windows.)
pause
