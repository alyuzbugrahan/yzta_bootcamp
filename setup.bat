@echo off
echo ============================================
echo  Figion — Kurulum Basliyor
echo ============================================

python --version >nul 2>&1
if errorlevel 1 (
    echo HATA: Python bulunamadi. Python 3.10+ yukleyin.
    pause
    exit /b 1
)

echo [1/3] Sanal ortam olusturuluyor...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/3] Gerekli kutuphaneler yukleniyor...
pip install --upgrade pip -q
pip install -r requirements.txt

echo [3/3] Klasorler olusturuluyor...
mkdir data\images 2>nul
mkdir data\logs   2>nul
mkdir data\exports 2>nul
mkdir models       2>nul

echo.
echo ============================================
echo  Kurulum tamamlandi!
echo  Calistirmak icin:  run.bat
echo ============================================
pause
