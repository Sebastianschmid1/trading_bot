"""
Konfiguration — hier alle Einstellungen anpassen
"""

import os
import socket
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()  # liest Werte aus einer .env-Datei im Projekt-Root, falls vorhanden


def _detect_lan_ip() -> str:
    """Ermittelt die lokale Netzwerk-IP (für einen vom Handy erreichbaren Dashboard-Link)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # baut keine echte Verbindung auf, ermittelt nur das Interface
        return s.getsockname()[0]
    except Exception:
        return "localhost"
    finally:
        s.close()

# ── Telegram ────────────────────────────────────────────────────────────────
# Hol dir deinen Token von @BotFather auf Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN_ENV")

# ── Verschlüsselung (für gespeicherte Broker-API-Zugangsdaten) ──────────────
# Einmalig generieren mit:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY fehlt in .env — generiere einen mit:\n"
        '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

# ── Dashboard (Web-Oberfläche) ──────────────────────────────────────────────
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))

# Basis-URL für den /dashboard-Link. Ohne explizite Vorgabe wird die LAN-IP genutzt,
# damit der Link auch vom Handy im selben WLAN funktioniert (localhost zeigt sonst
# auf das Handy selbst). Auf dem VPS: DASHBOARD_BASE_URL=https://deine-domain.de setzen.
DASHBOARD_BASE_URL = os.getenv("DASHBOARD_BASE_URL") or f"http://{_detect_lan_ip()}:{DASHBOARD_PORT}"

# Dashboard automatisch im selben Prozess wie der Bot starten (bequem lokal).
# Auf dem VPS mit separatem dashboard.service: RUN_DASHBOARD_IN_BOT=false setzen.
RUN_DASHBOARD_IN_BOT = os.getenv("RUN_DASHBOARD_IN_BOT", "true").strip().lower() in ("1", "true", "yes")

# ── Zeitplanung ─────────────────────────────────────────────────────────────
BERLIN_TZ = ZoneInfo("Europe/Berlin")

SIGNAL_TIME_HOUR = 8       # Signale senden um...  (vor US-Eröffnung)
SIGNAL_TIME_MIN  = 45      # ...08:45 Uhr

# Trades schließen NACH der US-Session (22:00 Berlin = 16:00 ET Börsenschluss),
# damit eine echte Handelssession dazwischenliegt und SL/TP auslösen können.
CLOSE_TIME_HOUR  = 22      # Trades schließen/auswerten um...
CLOSE_TIME_MIN   = 15      # ...22:15 Uhr

# ── Aktien-Universen ────────────────────────────────────────────────────────
# Erste Version: drei wählbare Bereiche. Aus Konsistenzgründen (USD, US-Handelszeit)
# sind internationale Werte als US-gelistete ADRs enthalten.

UNIVERSE_SP500 = [
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

UNIVERSE_MSCI_WORLD = [
    # USA (Schwergewichte quer durch die Sektoren)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "V",
    "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "COST", "ORCL", "MRK",
    "ABBV", "CVX", "KO", "PEP", "BAC", "CRM", "MCD", "NFLX", "ADBE", "TMO",
    # Europa / UK (US-gelistete ADRs)
    "ASML", "SAP", "NVO", "NVS", "AZN", "SHEL", "TTE", "UL", "HSBC", "BTI",
    "DEO", "RELX", "STLA", "ERIC", "BP", "GSK", "SNY", "BUD", "PHG", "SPOT",
    "ARM", "BCS", "UBS", "NGG", "ING",
    # Japan (ADRs)
    "TM", "SONY", "MUFG", "SMFG", "HMC", "TAK",
    # Kanada (US-gelistet)
    "TD", "RY", "BNS", "ENB", "CNI", "CP",
    # Australien (ADR)
    "BHP",
]

UNIVERSE_EMERGING = [
    # China
    "BABA", "PDD", "JD", "BIDU", "LI", "NIO", "XPEV", "BILI", "TCOM", "NTES",
    "TME", "YUMC", "BEKE", "ZTO", "VIPS", "MNSO", "TAL", "EDU",
    # Taiwan
    "TSM", "UMC", "CHT",
    # Indien
    "INFY", "HDB", "IBN", "WIT", "RDY", "MMYT",
    # Brasilien / LatAm
    "VALE", "PBR", "ITUB", "BBD", "NU", "MELI", "ABEV", "GGB", "XP", "STNE",
    "PAGS", "UGP", "BSBR", "VIV", "CIG", "SID",
    # Mexiko
    "AMX", "FMX", "CX",
    # Südkorea
    "KB", "SHG", "PKX",
    # Südostasien
    "SE", "GRAB",
    # Südafrika
    "GFI", "SBSW", "AU",
    # weitere (LatAm)
    "BAP", "GLOB",
]

UNIVERSES = {
    "sp500":      UNIVERSE_SP500,
    "msci_world": UNIVERSE_MSCI_WORLD,
    "emerging":   UNIVERSE_EMERGING,
}

REGION_LABELS = {
    "sp500":      "S&P 500",
    "msci_world": "MSCI World",
    "emerging":   "Emerging Markets",
}

DEFAULT_REGION = "sp500"

# Rückwärtskompatibler Alias (z. B. für analyzer-Standardwerte)
WATCHLIST = UNIVERSE_SP500

# ── Trade-Parameter ─────────────────────────────────────────────────────────
TRADE_SIZE_EUR   = 25.0    # Demo-Tradegröße in Euro
TOP_N_SIGNALS    = 5       # Standard-Anzahl Signale pro Tag
SIGNAL_COUNT_CHOICES = [1, 3, 5, 8, 10]   # Auswahlmöglichkeiten im /settings-Menü
MAX_SIGNALS      = 20      # harte Obergrenze

# ── Analyse-Parameter ──────────────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_OVERSOLD     = 35      # Unter diesem Wert = bullish Signal
RSI_OVERBOUGHT   = 65      # Über diesem Wert = bearish Signal
MA_SHORT         = 50
MA_LONG          = 200
MIN_SIGNAL_STRENGTH = 3    # Minimum 3/5 Sterne für ein Signal

# Höheres Zeitfenster: Long-Signale in einem klaren Wochen-Abwärtstrend unterdrücken
# (reduziert Fehlsignale, die gegen den übergeordneten Trend laufen).
BLOCK_WEEKLY_DOWNTREND = True

# ── ATR-basierte Stop-Loss / Take-Profit ────────────────────────────────────
# ATR (Average True Range) misst die typische Tagesschwankung. Stop-Loss und
# Take-Profit werden als Vielfaches davon vom Einstiegskurs gesetzt.
ATR_PERIOD   = 14
ATR_SL_MULT  = 1.5         # Stop-Loss  = Einstieg − 1.5 × ATR
ATR_TP_MULT  = 2.5         # Take-Profit = Einstieg + 2.5 × ATR  (→ CRV ~1:1.7)
