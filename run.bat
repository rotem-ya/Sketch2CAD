@echo off
rem Sketch2CAD - start the program. The desktop shortcut does the same without this window.
rem The app opens in a window of its own (Edge or Chrome in app mode); closing that window stops the program.
rem Messages go to <data folder>\logs\app.log. To watch them live, start with run-debug.bat instead.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe call install.bat
if not exist .venv\Scripts\pythonw.exe (
    echo The program is not installed - run install.bat first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m sketch2cad.launcher
