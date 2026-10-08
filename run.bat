@echo off
rem Sketch2CAD - start the local app (opens in the browser at http://localhost:8517)
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat call install.bat
call .venv\Scripts\activate.bat
streamlit run app\app.py
