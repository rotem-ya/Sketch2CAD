@echo off
rem Sketch2CAD - update to the latest version and restart.
rem From the app: update.bat <app folder> <server PID> [<supervisor PID>].  By hand: double-click.
rem Launched by the app (a server pid is present) every child is pythonw, so no console window appears.
rem Messages from that run: <data folder>\logs\update.log
chcp 65001 >nul
rem Run from a temp copy: the update overwrites this file while it is running.
if not defined S2C_UPDATE_COPY (
    set "S2C_UPDATE_COPY=1"
    copy /y "%~f0" "%TEMP%\s2c_update.bat" >nul
    if "%~1"=="" ("%TEMP%\s2c_update.bat" "%~dp0") else ("%TEMP%\s2c_update.bat" "%~1" "%~2" "%~3")
)
set "APP=%~1"
if "%APP%"=="" set "APP=%~dp0"
cd /d "%APP%"
echo Updating Sketch2CAD in %CD%
set "PYW=.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW="
rem The supervisor (launcher --serve) goes first, or it would start the server again mid-update.
if not "%~3"=="" (
    if defined PYW (
        "%PYW%" -c "import sys; from sketch2cad.launcher import terminate_pid; terminate_pid(int(sys.argv[1]))" %~3 || taskkill /PID %~3 /F >nul 2>&1
    ) else (
        taskkill /PID %~3 /F >nul 2>&1
    )
)
if not "%~2"=="" (
    if defined PYW (
        "%PYW%" -c "import time,sys; time.sleep(2); from sketch2cad.launcher import terminate_pid; terminate_pid(int(sys.argv[1]))" %~2 || taskkill /PID %~2 /F >nul 2>&1
    ) else (
        timeout /t 2 /nobreak >nul
        taskkill /PID %~2 /F >nul 2>&1
    )
)
if "%~2"=="" if exist .venv\Scripts\python.exe .venv\Scripts\python.exe -m sketch2cad.launcher --stop
if not exist .venv\Scripts\activate.bat (
    echo Not installed yet - run install.bat first.
    if "%~2"=="" pause
    exit /b 1
)
call .venv\Scripts\activate.bat
rem pythonw when the app started this (no console to inherit). python.exe when a person double-clicked.
if not "%~2"=="" if defined PYW goto :apply_hidden
python -m sketch2cad.updater apply
goto :apply_done
:apply_hidden
"%PYW%" -m sketch2cad.updater apply
:apply_done
if errorlevel 1 (
    echo.
    echo Update failed. The previous version is still installed.
    if "%~2"=="" (
        echo See the message above.
        pause
    ) else (
        echo See the data folder logs\update.log
    )
    exit /b 1
)
echo Starting Sketch2CAD ...
start "" "%CD%\.venv\Scripts\pythonw.exe" -m sketch2cad.launcher
if "%~2"=="" timeout /t 3 /nobreak >nul
