@echo off
rem Sketch2CAD - one-time install: finds (or installs) Python, creates .venv, installs packages,
rem adds a desktop shortcut. Safe to run again to update packages.
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python was not found - installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
    echo.
    echo Could not install Python automatically.
    echo Install Python 3.11+ from https://www.python.org/downloads/ ^(check "Add to PATH"^) and run install.bat again.
    pause
    exit /b 1
)

echo Creating the virtual environment...
if "%PY:~1,1%"==":" ("%PY%" -m venv .venv) else (%PY% -m venv .venv)
if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Package installation failed - check the internet connection and run install.bat again.
    pause
    exit /b 1
)

rem Streamlit asks for an e-mail on its first run; an empty credentials file skips the question.
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    mkdir "%USERPROFILE%\.streamlit" 2>nul
    > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
    >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Sketch2CAD.lnk'); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"

echo.
echo Sketch2CAD is installed. Start it from the "Sketch2CAD" shortcut on the desktop (or run.bat).
echo For DWG files also install ODA File Converter: https://www.opendesign.com/guestfiles/oda_file_converter
pause
