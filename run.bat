@echo off
rem Sketch2CAD - open the program. The desktop shortcut does the same without this window.
rem The server runs in the background all the time (it starts with Windows); this makes sure it runs and
rem opens the browser at http://localhost:8517. Messages: <data folder>\logs\app.log.
rem Other scripts: stop.bat stops the background server, run-debug.bat runs it in a console window.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe call install.bat
if not exist .venv\Scripts\pythonw.exe (
    echo The program is not installed - run install.bat first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m sketch2cad.launcher
