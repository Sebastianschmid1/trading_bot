"""
Konfiguration — hier alle Einstellungen anpassen
"""

import os
import socket
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from stockbot.paths import ENV_FILE

load_dotenv(ENV_FILE)  # liest Werte aus der .env im Projekt-Root, falls vorhanden


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

# ── Web-Sicherheit ───────────────────────────────────────────────────────────
# Session-Cookie nur über HTTPS senden (secure-Flag). Default = automatisch AN, sobald
# DASHBOARD_BASE_URL auf https zeigt (sonst würde der Login lokal über http brechen).
# Hinter einem TLS-Reverse-Proxy (siehe deploy/Caddyfile) zwingend AN — explizit per
# COOKIE_SECURE=true erzwingbar.
_cookie_secure_env = os.getenv("COOKIE_SECURE")
COOKIE_SECURE = (
    _cookie_secure_env.strip().lower() in ("1", "true", "yes")
    if _cookie_secure_env is not None
    else DASHBOARD_BASE_URL.lower().startswith("https")
)
# HSTS-Header (Strict-Transport-Security) nur senden, wenn TLS aktiv ist — sonst sperrt
# man sich lokal über http aus. Folgt standardmäßig COOKIE_SECURE.
HSTS_ENABLED = os.getenv("HSTS_ENABLED", str(COOKIE_SECURE)).strip().lower() in ("1", "true", "yes")

# ── Zeitplanung ─────────────────────────────────────────────────────────────
BERLIN_TZ = ZoneInfo("Europe/Berlin")

SIGNAL_TIME_HOUR = 15      # Signale senden um...  (nach US-Eröffnung → Live-Kurse)
SIGNAL_TIME_MIN  = 35      # ...15:35 Uhr (5 Min nach US-Open)

# Trades schließen NACH der US-Session (22:00 Berlin = 16:00 ET Börsenschluss),
# damit eine echte Handelssession dazwischenliegt und SL/TP auslösen können.
CLOSE_TIME_HOUR  = 22      # Trades schließen/auswerten um...
CLOSE_TIME_MIN   = 15      # ...22:15 Uhr

# Tagesende-Schließung pro Nutzer:
#   True  = alle Trades am Tagesende (22:15) schließen (bisheriges Verhalten).
#   False = Trades über Nacht halten, nur per SL/TP/Liquidation schließen — Backtests zeigen,
#           dass v. a. Trendfolge-Strategien das EOD-Schließen die ganze Kante kostet.
DEFAULT_EOD_CLOSE = True
# Sicherheits-Höchsthaltedauer (Kalendertage) für über Nacht gehaltene Trades:
HOLD_MAX_DAYS     = 14

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

# ── Weitere Anlageklassen (Website-Dropdown) ─────────────────────────────────
# ETFs: liquide, bei Alpaca als us_equity handelbar; gleiche Analyse-Engine wie Aktien
# (nur Smart-Money entfällt). Datenquelle yfinance.
UNIVERSE_ETF = [
    # Breit / US-Indizes
    "SPY", "VOO", "IVV", "QQQ", "DIA", "IWM", "VTI", "RSP",
    # Sektoren
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "SMH", "SOXX",
    # Themen / Wachstum
    "ARKK", "IGV", "XBI", "TAN", "LIT",
    # International
    "EFA", "EEM", "VEA", "VWO", "EWJ", "FXI", "INDA", "EWZ",
    # Anleihen / Sonstiges
    "TLT", "IEF", "HYG", "LQD",
]

# Rohstoffe: über handelbare Rohstoff-ETFs abgebildet (Alpaca us_equity). Echte Futures
# (GC=F, CL=F) sind bewusst NICHT enthalten (Rollover/Sessions, bei Alpaca nicht handelbar).
UNIVERSE_COMMODITY = [
    "GLD", "IAU",        # Gold
    "SLV",               # Silber
    "USO", "BNO",        # Öl (WTI/Brent)
    "UNG",               # Erdgas
    "DBC", "PDBC", "GSG",  # breite Rohstoffkörbe
    "DBA",               # Agrar
    "CPER",              # Kupfer
    "PPLT", "PALL",      # Platin / Palladium
]

# Krypto: yfinance-Symbole (24/7-Handel). Vorerst Demo/Tracking auf der Website; echter
# Alpaca-Krypto-Handel (Symbolformat BTC/USD, 24/7) ist ein separater Folgeschritt.
UNIVERSE_CRYPTO = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD",
    "DOGE-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "MATIC-USD", "LTC-USD",
]

# Voll-Universum (automatisch geladene Vollliste, z. B. ~500 S&P-500-Werte) standardmäßig AN.
# Aus → kuratierter Korb aus config.py (UNIVERSE_*) → kleiner & schnellere Analyse.
DEFAULT_AUTO_UNIVERSE = True

# Rückwärtskompatibler Alias (z. B. für analyzer-Standardwerte)
WATCHLIST = UNIVERSE_SP500

# ── Trade-Parameter ─────────────────────────────────────────────────────────
TRADE_SIZE_EUR   = 25.0    # Demo-Tradegröße in Euro
TRADE_SIZE_CHOICES = [25, 50, 100, 250, 500, 1000]   # Schnellauswahl im /settings-Menü (€)
TOP_N_SIGNALS    = 5       # Standard-Anzahl Signale pro Tag
SIGNAL_COUNT_CHOICES = [1, 3, 5, 8, 10]   # Auswahlmöglichkeiten im /settings-Menü
MAX_SIGNALS      = 20      # harte Obergrenze

# ── Backtest-Parameter ─────────────────────────────────────────────────────
# Parallelität des Backtests (Ticker werden über Prozesse verteilt). 0 = auto (alle Kerne).
# Auf dem VPS bei Bedarf in der .env deckeln (z. B. BACKTEST_JOBS=2), damit der Live-Bot
# während eines großen Backtests responsiv bleibt.
BACKTEST_JOBS    = int(os.getenv("BACKTEST_JOBS", "0") or 0)

# Transaktionskosten im Backtest: Prozent des Positionswerts JE SEITE (Einstieg + Ausstieg),
# als Pauschale für Spread + Slippage (Kommission ist bei Alpaca 0). 0.05 = 5 Basispunkte.
# Ohne Kosten driftet jede Optimierung systematisch zu mehr/engeren Trades, weil Trades im
# Modell gratis wären. Per BACKTEST_COST_PCT=0 abschaltbar (z. B. für Vergleichs-Läufe).
BACKTEST_COST_PCT = float(os.getenv("BACKTEST_COST_PCT", "0.05"))

# ── Analyse-Parameter ──────────────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_OVERSOLD     = 35      # Unter diesem Wert = bullish Signal
RSI_OVERBOUGHT   = 65      # Über diesem Wert = bearish Signal
MA_SHORT         = 50
MA_LONG          = 200

# Höheres Zeitfenster: Long-Signale in einem klaren Wochen-Abwärtstrend unterdrücken
# (reduziert Fehlsignale, die gegen den übergeordneten Trend laufen).
BLOCK_WEEKLY_DOWNTREND = True

# ── Multi-Timeframe-Signalstärke (0–100) ────────────────────────────────────
# Die Signalstärke wird aus mehreren Zeiträumen gewichtet zusammengesetzt.
# Prinzip: Recency-Weighting (aktuellere/kürzere Zeiträume zählen mehr — für
# Intraday-Trades am aussagekräftigsten) + Multi-Timeframe-Confluence
# (längere TF = Trend/Kontext, kürzere = Auslöser). Da Trades max. 1 Tag laufen,
# sind die Zeiträume bewusst intraday gewählt.
# Frei anpassbar (auch durch eine KI): einfach Intervalle/Gewichte ändern;
# Gewichte werden intern normiert (müssen nicht exakt 1.0 ergeben).
SIGNAL_TIMEFRAMES = [
    {"interval": "5m",  "period": "5d",  "weight": 0.40},
    {"interval": "15m", "period": "5d",  "weight": 0.30},
    {"interval": "1h",  "period": "1mo", "weight": 0.20},
    {"interval": "1d",  "period": "1y",  "weight": 0.10},
]

# Gewichtung der Stärke-Komponenten INNERHALB eines Zeitraums (0–1, intern normiert).
# Volumen ist im Intraday-Handel ein zentrales Bestätigungssignal: relatives Volumen
# (RVOL) bestätigt Ausbrüche, zeigt Liquidität und institutionelles Interesse. Ohne
# Volumen ist ein Kursimpuls leicht ein Fehlausbruch. Es ist daher hier deutlich höher
# gewichtet als zuvor. Frei anpassbar (auch durch eine KI).
STRENGTH_WEIGHTS = {
    "rsi":    0.20,
    "macd":   0.25,
    "trend":  0.25,
    "volume": 0.30,
}
MIN_SIGNAL_STRENGTH    = 55.0   # 0–100: Mindeststärke für ein gültiges Signal
SIGNAL_CLOSE_THRESHOLD = 35.0   # 0–100: darunter wird ein aktiver Trade automatisch geschlossen

# 60-Sekunden-Überwachung aktiver Trades
MONITOR_INTERVAL_SEC = 60

# Intraday-Signal-Scan: während der Handelszeit alle 30 Min nach NEUEN Signalen suchen und
# sie per Telegram + Website pushen (über den Eröffnungs-Scan hinaus, ohne top_n-Deckel).
INTRADAY_SCAN_INTERVAL_SEC = 30 * 60

# ── ATR-basierte Stop-Loss / Take-Profit ────────────────────────────────────
# ATR (Average True Range) misst die typische Tagesschwankung. Stop-Loss und
# Take-Profit werden als Vielfaches davon vom Einstiegskurs gesetzt.
ATR_PERIOD   = 14
ATR_SL_MULT  = 1.5         # Stop-Loss  = Einstieg − 1.5 × ATR   (= Modus "normal")
ATR_TP_MULT  = 2.5         # Take-Profit = Einstieg + 2.5 × ATR  (→ CRV ~1:1.7)

# SL/TP-Modi (ATR-Vielfache: (Stop-Loss-Faktor, Take-Profit-Faktor)) — pro Nutzer wählbar:
#   aus       = keine festen SL/TP-Grenzen (nur Liquidation & Signal-Verfall schließen)
#   passiv    = enge Stops, kleine schnelle Ziele
#   normal    = ausgewogen (bisheriger Standard)
#   aggressiv = weite Stops, große Ziele (lässt laufen)
SL_TP_MODES = {
    "aus":       (None, None),
    "passiv":    (1.0, 1.5),
    "normal":    (1.5, 2.5),
    "aggressiv": (2.5, 4.0),
}
DEFAULT_SL_TP_MODE = "normal"

# ── Hebel (Leverage) ─────────────────────────────────────────────────────────
# Höherer Hebel = höherer Gewinn/Verlust UND schnellere Liquidation:
# Long wird bei einem Kursverlust von 1/Hebel liquidiert (Hebel 10 → schon bei −10 %).
LEVERAGE_CHOICES = [1, 1.5, 2, 3, 5, 10]
DEFAULT_LEVERAGE = 1.0

# ── Smart-Money (was große Trader handeln: Insider + Institutionen) ──────────
# Der Voll-Scan (1 yfinance-Abfrage pro Aktie) dauert Minuten und läuft daher
# nachts als Hintergrund-Job; /top5trade und /signals lesen nur den Cache.
SMARTMONEY_SCAN_HOUR       = 23     # nächtlicher Scan um...
SMARTMONEY_SCAN_MIN        = 0      # ...23:00 Uhr (nach der 22:15-Auswertung)
SMARTMONEY_CACHE_MAX_AGE_H = 36     # Cache gilt so lange als „frisch"
SMARTMONEY_W_TECH  = 1.0           # Gewicht der technischen Stärke beim /signals-Re-Ranking
SMARTMONEY_W_SMART = 0.5           # Gewicht des Smart-Money-Scores beim /signals-Re-Ranking

# Täglicher Labor-Optimierungslauf: erzeugt nur pending.json, niemals Live-Parameter.
# Default bewusst während regulärer US-Handelszeit: Mo–Fr 16:00 Berlin (= i.d.R. 10:00 ET).
LAB_DAILY_OPTIMIZATION = os.getenv(
    "LAB_DAILY_OPTIMIZATION", os.getenv("LAB_WEEKLY_OPTIMIZATION", "true")
).strip().lower() in ("1", "true", "yes")
LAB_DAILY_DAYS = tuple(int(x) for x in os.getenv("LAB_DAILY_DAYS", "0,1,2,3,4").split(",") if x.strip())  # 0=Mo … 6=So
LAB_DAILY_HOUR = int(os.getenv("LAB_DAILY_HOUR", os.getenv("LAB_WEEKLY_HOUR", "16")))
LAB_DAILY_MIN = int(os.getenv("LAB_DAILY_MIN", os.getenv("LAB_WEEKLY_MIN", "0")))

# Rückwärtskompatible Aliase für ältere Imports/Deploy-Umgebungen.
LAB_WEEKLY_OPTIMIZATION = LAB_DAILY_OPTIMIZATION
LAB_WEEKLY_DAY = 6
LAB_WEEKLY_HOUR = LAB_DAILY_HOUR
LAB_WEEKLY_MIN = LAB_DAILY_MIN

# Täglicher Broker-Vollabgleich: Bot-DB ↔ offene Alpaca-Positionen.
# 12:00 Berlin liegt vor US-Open und repariert Drift, bevor neue US-Signale/Orders laufen.
BROKER_RECONCILE_HOUR = int(os.getenv("BROKER_RECONCILE_HOUR", "12"))
BROKER_RECONCILE_MIN = int(os.getenv("BROKER_RECONCILE_MIN", "0"))

# ── LLM-Ranking (Claude Haiku) ───────────────────────────────────────────────
# Ein LLM rankt die Signale anhand aller Metadaten + Fundamentaldaten (Geschäftsberichte)
# + News/Analysten. Bewusst nur Haiku zur Kostenersparnis (eine gebündelte Anfrage pro Lauf).
# API-Key NUR aus .env (gitignored) — niemals committen/loggen.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_MODEL         = "claude-haiku-4-5"      # exakt — kein Datum-Suffix
# KI-Signal-Ranking standardmäßig AUS — im Dauerbetrieb zu teuer. Bei Bedarf per
# LLM_RANK_SIGNALS=true (in .env) reaktivierbar; benötigt zusätzlich einen ANTHROPIC_API_KEY.
LLM_RANK_ENABLED  = bool(ANTHROPIC_API_KEY) and \
    os.getenv("LLM_RANK_SIGNALS", "false").strip().lower() in ("1", "true", "yes")
LLM_MAX_SIGNALS   = 8                        # Obergrenze Signale pro LLM-Anfrage (Kosten)

# ── Alpaca (Trading API — eigenes Konto) ─────────────────────────────────────
# Keys NUR aus .env (gitignored). Standard = PAPER (kein echtes Geld). Ausführung ist
# zusätzlich pro Nutzer schaltbar (users.broker_exec) und standardmäßig AUS.
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_PAPER      = os.getenv("ALPACA_PAPER", "true").strip().lower() in ("1", "true", "yes")
ALPACA_ENABLED    = bool(ALPACA_API_KEY and ALPACA_API_SECRET)
# Erweiterte Handelszeiten (US Pre-/After-Market). Monitoring/Signal-Fenster nutzen dann
# 4:00–20:00 ET statt 9:30–16:00 ET. yfinance-Extended-Daten sind dünn — Nutzen teilweise.
EXTENDED_HOURS    = os.getenv("EXTENDED_HOURS", "true").strip().lower() in ("1", "true", "yes")

# ── Trading-Modus & globale Sicherheits-Flags (Phase 0 / TSAFE-001) ──────────
# Globaler Kill-Switch gegen ungewollten Live-Handel. Diese Flags sind die EINZIGE Stelle,
# die echten Geldhandel ermöglichen darf — kein UI-/Telegram-Schalter kann Live aktivieren.
# Solange Live nicht ausdrücklich freigeschaltet ist, werden Live-Orders serverseitig abgelehnt
# und der Broker-Layer läuft erzwungen im Paper-Modus.
TRADING_MODE       = os.getenv("TRADING_MODE", "paper").strip().lower()   # "paper" | "live"
ALLOW_LIVE_TRADING = os.getenv("ALLOW_LIVE_TRADING", "false").strip().lower() in ("1", "true", "yes")
ALLOW_MARGIN       = os.getenv("ALLOW_MARGIN", "false").strip().lower() in ("1", "true", "yes")
ALLOW_OPTIONS      = os.getenv("ALLOW_OPTIONS", "false").strip().lower() in ("1", "true", "yes")
ALLOW_SHORTS       = os.getenv("ALLOW_SHORTS", "false").strip().lower() in ("1", "true", "yes")
try:
    MAX_LEVERAGE = float(os.getenv("MAX_LEVERAGE", "1") or 1)
except ValueError:
    MAX_LEVERAGE = 1.0

# Abgeleitetes Gate: Live-Handel ist NUR aktiv, wenn beide Bedingungen bewusst gesetzt sind.
# Der Broker-Layer nutzt dies als serverseitige Order-Sperre.
LIVE_TRADING_ENABLED = ALLOW_LIVE_TRADING and TRADING_MODE == "live"

# Harte Absicherung: Ist Live nicht freigeschaltet, wird der Broker zwingend auf Paper gestellt —
# selbst wenn versehentlich ALPACA_PAPER=false in der .env steht. So kann kein Standard-/Nutzer-Client
# ein echtes Geldkonto treffen, bevor Live bewusst (TRADING_MODE=live + ALLOW_LIVE_TRADING=true) aktiv ist.
if not LIVE_TRADING_ENABLED:
    ALPACA_PAPER = True

# ── Options (gehebelte Trades über Long-Calls statt gehebelter Aktien) ───────
# Bei Hebel > 1 kauft der Bot für den Trade-Wert eine Option mit ~diesem Hebel (Omega),
# statt Trade-Wert × Hebel an Aktien. Verfallsfenster (Tage bis Expiry) für die Kontraktwahl:
OPTION_TARGET_DTE_MIN = int(os.getenv("OPTION_TARGET_DTE_MIN", "30"))
OPTION_TARGET_DTE_MAX = int(os.getenv("OPTION_TARGET_DTE_MAX", "45"))
OPTION_TYPE           = os.getenv("OPTION_TYPE", "call").strip().lower()   # long-only: call

# Optionaler Admin-Chat (Telegram-User-ID) für zentrale Fehler-/Abgleich-Logs. Leer = aus.
_admin = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID = int(_admin) if _admin.lstrip("-").isdigit() else None

# Pfad der Bot-Logdatei (gemeinsam von bot.py geschrieben und vom Web-Log-Download gelesen).
LOG_FILE = os.getenv("LOG_FILE", "logs/bot.log")

# Sizing: Ist eine Aktie nur etwas teurer als die Trade-Größe (Budget), kauft der Bot 1 GANZE
# Aktie statt eines Bruchteils — bis zu diesem Faktor des Budgets (1.0 = nie aufrunden).
# Vorteil: ganze Aktien sind auch außerhalb der regulären Handelszeit als Limit handelbar.
SHARE_ROUNDUP_FACTOR = float(os.getenv("SHARE_ROUNDUP_FACTOR", "1.5"))
