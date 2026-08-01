#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e './backend[rag]'

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
fi

mkdir -p backend/data/images backend/models
echo
echo "Kurulum tamamlandı. Çalıştırmak için: ./run_web.sh"
