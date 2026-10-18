@echo off
rem Sketch2CAD - start with a console window that shows every message (for troubleshooting).
rem The normal start is the desktop shortcut or run.bat: the app opens in a window of its own.
rem Keep this window open while working: closing it stops the program.
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call install.bat
if not exist .venv\Scripts\python.exe (
    echo The program is not installed - run install.bat first.
    pause
    exit /b 1
)
echo Starting Sketch2CAD - the browser opens at http://localhost:8517
echo Keep this window open while working. Closing it stops the program.
echo.
rem python -m streamlit (not streamlit.exe) keeps working even if the folder was moved after install
.venv\Scripts\python.exe -m streamlit run app\app.py --server.port 8517
echo.
echo Sketch2CAD stopped. If there is an error above, send it to support.
echo "Port 8517 is already in use" means the program is already running: open http://localhost:8517
pause
