#!/usr/bin/env python3
"""Entrypoint für den Telegram-Bot.

Start lokal:   python run_bot.py
Auf dem Server (systemd): ExecStart=.../python /root/stockbot/run_bot.py
(WorkingDirectory = Repo-Root, damit data/ und .env gefunden werden.)
"""

from stockbot.tgbot.bot import main

if __name__ == "__main__":
    main()
