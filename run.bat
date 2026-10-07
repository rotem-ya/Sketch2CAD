@echo off
rem Sketch2CAD - start the local app (opens in the browser)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
streamlit run app\app.py
