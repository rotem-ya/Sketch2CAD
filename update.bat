@echo off
rem Sketch2CAD - update to the latest version and restart.
rem From the app: update.bat <app folder> <app PID>.  By hand: double-click (close the app first).
chcp 65001 >nul
rem Run from a temp copy: the update overwrites this file while it is running.
if not defined S2C_UPDATE_COPY (
    set "S2C_UPDATE_COPY=1"
    copy /y "%~f0" "%TEMP%\s2c_update.bat" >nul
    if "%~1"=="" ("%TEMP%\s2c_update.bat" "%~dp0") else ("%TEMP%\s2c_update.bat" "%~1" "%~2")
)
set "APP=%~1"
if "%APP%"=="" set "APP=%~dp0"
cd /d "%APP%"
echo Updating Sketch2CAD in %CD%
if not "%~2"=="" (
    timeout /t 2 /nobreak >nul
    taskkill /PID %~2 /F >nul 2>&1
)
if not exist .venv\Scripts\activate.bat (
    echo Not installed yet - run install.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m sketch2cad.updater apply
if errorlevel 1 (
    echo.
    echo Update failed - see the message above. The previous version is still installed.
    pause
    exit /b 1
)
echo Starting Sketch2CAD ...
start "Sketch2CAD" "%CD%\run.bat"
timeout /t 3 /nobreak >nul
