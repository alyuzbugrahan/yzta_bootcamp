#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Sanal ortam bulunamadı. Önce ./setup_web.sh çalıştırın."
  exit 1
fi
exec .venv/bin/python start_web.py
