@echo off
setlocal
cd /d "%~dp0"
title PPWR Compliance Desk

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 app.py
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python app.py
    goto :eof
)

echo.
echo ============================================================
echo  Python was not found on this computer.
echo.
echo  This app needs Python 3 to run. Install it once from:
echo      https://www.python.org/downloads/
echo  During install, tick "Add python.exe to PATH", then
echo  double-click this file again.
echo ============================================================
echo.
pause
