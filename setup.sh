#!/bin/bash
echo "============================================"
echo " Figion — Kurulum Basliyor"
echo "============================================"

python3 --version >/dev/null 2>&1 || { echo "HATA: Python3 bulunamadi."; exit 1; }

echo "[1/3] Sanal ortam olusturuluyor..."
python3 -m venv venv
source venv/bin/activate

echo "[2/3] Gerekli kutuphaneler yukleniyor..."
pip install --upgrade pip -q
pip install -r requirements.txt

echo "[3/3] Klasorler olusturuluyor..."
mkdir -p data/images data/logs data/exports models

echo ""
echo "============================================"
echo " Kurulum tamamlandi!"
echo " Calistirmak icin:  bash run.sh"
echo "============================================"
