@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set PYTHON_CMD=py -3
) else (
    set PYTHON_CMD=python
)

%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -e ".\backend[rag]"
if errorlevel 1 goto :error

if not exist backend\.env copy backend\.env.example backend\.env >nul
if not exist backend\data\images mkdir backend\data\images
if not exist backend\models mkdir backend\models

echo.
echo Kurulum tamamlandi. Calistirmak icin run_web.bat dosyasini acin.
exit /b 0

:error
echo.
echo Kurulum sirasinda hata olustu.
exit /b 1
