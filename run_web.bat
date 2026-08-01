@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Sanal ortam bulunamadi. Once setup_web.bat calistirin.
    exit /b 1
)
.venv\Scripts\python.exe start_web.py
