@echo off
rem Sketch2CAD - one-time install: finds (or installs) Python, creates .venv, installs packages,
rem adds a desktop shortcut. Safe to run again to update packages.
chcp 65001 >nul
cd /d "%~dp0"

rem Supported Python: 3.11 - 3.14. Newer versions have no prebuilt packages yet on Windows for
rem pyarrow and PyYAML, so pip tries to compile them and fails without a C++ compiler.
set "PYCHECK=import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)"
set "PY="
for %%v in (3.13 3.12 3.14 3.11) do if not defined PY (py -%%v -c "%PYCHECK%" >nul 2>&1 && set "PY=py -%%v")
if not defined PY python -c "%PYCHECK%" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo A supported Python version - 3.11 to 3.14 - was not found. Installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
    echo.
    echo Could not install Python automatically.
    echo Install Python 3.12 from https://www.python.org/downloads/release/python-31210/ ^(check "Add to PATH"^)
    echo and run install.bat again. Python 3.15 or newer is not supported yet.
    pause
    exit /b 1
)
echo Using Python: %PY%

echo %CD% | findstr /i /b /c:"%ProgramFiles%" >nul && (
    echo.
    echo WARNING: this folder is under Program Files, where Windows blocks the program from saving its own
    echo files ^(data folder setting, updates^). Better: extract to a folder such as C:\Sketch2CAD.
    echo.
)

rem A .venv made with an unsupported Python version is recreated.
if not exist .venv\Scripts\python.exe goto :venv_create
.venv\Scripts\python.exe -c "%PYCHECK%" >nul 2>&1
if not errorlevel 1 goto :venv_create
echo The existing .venv uses an unsupported Python version - recreating it...
rmdir /s /q .venv
:venv_create
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
    echo If the error mentions "Microsoft Visual C++" or "Failed building wheel", the Python version is too
    echo new: install Python 3.12 from https://www.python.org/downloads/release/python-31210/ and run again.
    pause
    exit /b 1
)

rem Data folder (settings, search index, personal catalog and templates). Projects go where you
rem choose per project. Keeps the program on C: and the data on another drive when wanted.
set "DATA="
if exist data_home.txt goto :data_done
echo.
echo Where should Sketch2CAD keep its data (settings, search index, personal catalog and templates)?
set /p "DATA=Press Enter for %APPDATA%\Sketch2CAD, or type a folder such as E:\Sketch2CAD\Data: "
if not defined DATA goto :data_done
if not exist "%DATA%" mkdir "%DATA%"
if not exist "%DATA%" (
    echo Could not create %DATA% - the default data folder will be used. Change it later in Settings.
    goto :data_done
)
rem Point to the new folder and copy existing data (settings, project list, API key, index) into it.
python -c "from sketch2cad import config; config.set_data_home(r'%DATA%', copy_from=config.default_data_home())"
if errorlevel 1 (>data_home.txt echo %DATA%)
echo Data folder: %DATA%
:data_done

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
