"""Prepare the local database and start the AgroVision web application."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"


def main() -> None:
    os.chdir(BACKEND)
    os.environ.setdefault("AGROVISION_MODEL__ALLOW_DEMO", "1")
    os.environ.setdefault("AGROVISION_LOG_JSON", "0")
    os.environ.setdefault("AGROVISION_DATABASE__URL", "sqlite+aiosqlite:///./agrovision.db")

    print("[AgroVision] Veritabanı şeması hazırlanıyor...")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

    host = os.environ.get("AGROVISION_HOST", "127.0.0.1")
    port = int(os.environ.get("AGROVISION_PORT", "8000"))
    print(f"[AgroVision] Uygulama hazır: http://{host}:{port}")
    print("[AgroVision] Durdurmak için Ctrl+C kullanın.\n")

    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
