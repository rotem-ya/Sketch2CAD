@echo off
rem Sketch2CAD - one-time install (needs Python 3.11+ from python.org, "Add to PATH" checked)
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Installed. Start with run.bat
pause
