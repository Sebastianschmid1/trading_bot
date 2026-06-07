"""
Konfiguration — hier alle Einstellungen anpassen
"""

import os
from zoneinfo import ZoneInfo

# ── Telegram ────────────────────────────────────────────────────────────────
# Hol dir deinen Token von @BotFather auf Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "DEIN_BOT_TOKEN_HIER")

# Deine Telegram Chat-ID (bekommst du von @userinfobot)
CHAT_ID = os.getenv("CHAT_ID", "DEINE_CHAT_ID_HIER")

# ── Zeitplanung ─────────────────────────────────────────────────────────────
BERLIN_TZ = ZoneInfo("Europe/Berlin")

SIGNAL_TIME_HOUR = 8       # Signale senden um...
SIGNAL_TIME_MIN  = 45      # ...08:45 Uhr

CLOSE_TIME_HOUR  = 15      # Trades schließen um...
CLOSE_TIME_MIN   = 30      # ...15:30 Uhr

# ── Aktien-Universum (S&P 500 Top-Aktien) ──────────────────────────────────
WATCHLIST = [
    # Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    "INTC", "CRM", "ORCL", "ADBE", "QCOM", "AVGO", "TXN",
    # Finance
    "JPM", "BAC", "GS", "MS", "V", "MA", "AXP",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "MRK",
    # Consumer / Industrie
    "WMT", "HD", "NKE", "MCD", "SBUX", "DIS", "BA", "CAT", "GE",
    # Energie
    "XOM", "CVX",
]

# ── Trade-Parameter ─────────────────────────────────────────────────────────
TRADE_SIZE_EUR   = 25.0    # Demo-Tradegröße in Euro
TOP_N_SIGNALS    = 5       # Wie viele Signale täglich senden

# ── Analyse-Parameter ──────────────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_OVERSOLD     = 35      # Unter diesem Wert = bullish Signal
RSI_OVERBOUGHT   = 65      # Über diesem Wert = bearish Signal
MA_SHORT         = 50
MA_LONG          = 200
MIN_SIGNAL_STRENGTH = 3    # Minimum 3/5 Sterne für ein Signal
