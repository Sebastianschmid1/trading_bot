#!/usr/bin/env python3
"""Entrypoint für das Web-Dashboard (eigenständig, ohne Bot-Prozess).

Start lokal:   python run_dashboard.py
Auf dem Server (systemd): ExecStart=.../python /root/stockbot/run_dashboard.py
(WorkingDirectory = Repo-Root, damit data/ und .env gefunden werden.)
"""

import logging

from stockbot.web.dashboard import run

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run()
