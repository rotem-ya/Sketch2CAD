@echo off
rem Sketch2CAD - stop the background server and remove the automatic start and the shortcuts.
rem The program folder, the data folder and the projects are not touched: delete the folder by hand to remove
rem the program itself.
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\python.exe .venv\Scripts\python.exe -m sketch2cad.launcher --stop
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Sketch2CAD server.lnk'), ([Environment]::GetFolderPath('Desktop')+'\Sketch2CAD.lnk')"
echo.
echo Sketch2CAD no longer starts with Windows and the shortcuts were removed. Nothing else was deleted:
echo the program folder, the data folder (settings, catalog, index) and the projects are all still there.
pause
