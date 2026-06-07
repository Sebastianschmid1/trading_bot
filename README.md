# 📈 Stock Signal Telegram Bot

Tägliche Aktienempfehlungen per Telegram mit Demo-Trade-Tracking.

## Features
- **8:45 Uhr**: 5 S&P 500 Aktienempfehlungen mit technischer Analyse
- **JA/NEIN Buttons**: Trade annehmen oder ablehnen
- **15:30 Uhr**: Automatische Auswertung mit P&L-Berechnung
- **Demo-Modus**: Kein echtes Geld, nur Tracking

## Analyse-Indikatoren
| Indikator | Bedeutung |
|-----------|-----------|
| RSI | Über-/Unterkauft-Niveau (< 35 = bullish) |
| MACD | Momentum und Trendwechsel |
| MA50/MA200 | Kurz-/Langfristiger Trend |
| Volumen | Bestätigung durch Handelsinteresse |

---

## Setup (5 Minuten)

### 1. Telegram Bot erstellen
1. Öffne Telegram → suche `@BotFather`
2. Sende `/newbot` → Namen vergeben
3. Token kopieren

### 2. Deine Chat-ID herausfinden
1. Starte deinen Bot (einmal `/start` senden)
2. Öffne: `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates`
3. `chat.id` aus der Antwort kopieren

### 3. Installation
```bash
cd stockbot
pip install -r requirements.txt
```

### 4. Konfiguration
```bash
# Linux/Mac:
export TELEGRAM_TOKEN="dein_token_hier"
export CHAT_ID="deine_chat_id_hier"

# Windows:
set TELEGRAM_TOKEN=dein_token_hier
set CHAT_ID=deine_chat_id_hier
```

Oder direkt in `config.py` eintragen.

### 5. Testen
```bash
# Nur Analyse anzeigen (kein Telegram):
python test_bot.py analyze

# Signale sofort an Telegram senden:
python test_bot.py signals

# Aktive Trades sofort auswerten:
python test_bot.py evaluate
```

### 6. Bot starten
```bash
python bot.py
```

---

## Beispiel-Nachricht

```
📊 NVDA — 🟢 LONG
━━━━━━━━━━━━━━━━━━
💰 Kurs: $875.40
📈 Signal-Stärke: ████░ (4/5)
🔍 Begründung:
  • RSI: 32.1 → Überverkauft 📉
  • MACD: Bullish Crossover ✅
  • Trend (MA50/200): Starker Aufwärtstrend 📈
  • Volumen: 1.8x Durchschnitt — Hohes Interesse 🔥
━━━━━━━━━━━━━━━━━━
💶 Demo-Trade: 25€ LONG
⏱ Schließung: 15:30 Uhr

[ ✅ JA — Demo-Trade starten ] [ ❌ NEIN ]
```

## Beispiel-Auswertung (15:30 Uhr)

```
📋 Tagesauswertung Demo-Trades
━━━━━━━━━━━━━━━━━━
✅ Gewinner: 3 | ❌ Verlierer: 2
🟢 Gesamt P&L: +1.87€
━━━━━━━━━━━━━━━━━━
🟢 NVDA: $875.40 → $891.20 | +1.8% (+0.45€)
🟢 AAPL: $189.30 → $192.10 | +1.5% (+0.37€)
🟢 AMD:  $142.50 → $145.80 | +2.3% (+0.58€)
🔴 TSLA: $245.60 → $241.20 | -1.8% (-0.45€)
🔴 META: $512.40 → $507.90 | -0.9% (-0.23€)
```

---

## Auf einem Server dauerhaft laufen lassen

```bash
# Mit screen (einfach):
screen -S stockbot
python bot.py
# Ctrl+A dann D zum Loslösen

# Mit systemd (empfohlen für VPS):
# Erstelle /etc/systemd/system/stockbot.service
```

## Hinweis
Dies ist ein **Demo-Bot** — es wird kein echtes Geld gehandelt.
Für echte Trades: Alpaca API-Keys in config.py eintragen und
die `activate_trade()` Funktion in tracker.py erweitern.
