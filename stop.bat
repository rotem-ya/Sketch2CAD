@echo off
rem Sketch2CAD - stop the background server (it starts again at the next logon, or with run.bat).
cd /d "%~dp0"
.venv\Scripts\python.exe -m sketch2cad.launcher --stop
pause
