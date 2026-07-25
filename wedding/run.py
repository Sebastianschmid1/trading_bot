#!/usr/bin/env python3
"""Entrypoint der Hochzeits-Galerie.

Start lokal:   python wedding/run.py
Auf dem Server (systemd): ExecStart=/opt/stockbot/venv/bin/python /opt/stockbot/wedding/run.py

Konfiguration ausschließlich über Env-Vars (siehe wedding/README.md):
WEDDING_BIND, WEDDING_PORT, WEDDING_ROOT_PATH, WEDDING_DATA_DIR,
WEDDING_USERS_FILE, WEDDING_SECRET_FILE, WEDDING_COOKIE_SECURE, WEDDING_MAX_BYTES.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Beim Aufruf als Skript liegt nur wedding/ im sys.path — das Repo-Root ergänzen,
# damit der uvicorn-Import-String "wedding.app:app" auflösbar ist.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn  # noqa: E402  (erst nach dem sys.path-Fix importieren)

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8100


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    bind = (os.getenv("WEDDING_BIND") or DEFAULT_BIND).strip() or DEFAULT_BIND
    try:
        port = int((os.getenv("WEDDING_PORT") or "").strip() or DEFAULT_PORT)
    except ValueError:
        logging.warning("WEDDING_PORT ist keine Zahl — nutze %d.", DEFAULT_PORT)
        port = DEFAULT_PORT
    root_path = (os.getenv("WEDDING_ROOT_PATH") or "").strip().rstrip("/")

    logging.info(
        "Hochzeits-Galerie startet auf http://%s:%d%s/ (Daten: %s)",
        bind,
        port,
        root_path,
        os.getenv("WEDDING_DATA_DIR") or "./data/wedding",
    )
    uvicorn.run(
        "wedding.app:app",
        host=bind,
        port=port,
        root_path=root_path,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level="info",
    )


if __name__ == "__main__":
    main()
