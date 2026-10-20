@echo off
rem One-click launcher for Windows. Uses waitress (production WSGI) when available.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto nopython
python server.py --serve waitress --threads 32
if errorlevel 1 pause
goto :eof

:nopython
echo [ERROR] Python not found in PATH.
echo         Install Python 3.10+ from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during setup.
pause
