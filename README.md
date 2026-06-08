# 📈 Stock Signal Telegram Bot

Tägliche Aktienempfehlungen per Telegram mit Demo-Trade-Tracking.

## Features
- **8:45 Uhr**: tägliche Aktienempfehlungen mit technischer Analyse (Bereich wählbar: S&P 500 / MSCI World / Emerging Markets)
- **JA/NEIN Buttons**: Trade annehmen oder ablehnen
- **22:15 Uhr**: Automatische Auswertung mit P&L-Berechnung (inkl. SL/TP)
- **🐳 Smart-Money** (`/top5trade`): was große Trader (Insider + Institutionen) zuletzt gekauft haben; fließt auch ins Signal-Ranking ein
- **Demo-Modus**: Kein echtes Geld, nur Tracking

## Analyse-Indikatoren
| Indikator | Bedeutung |
|-----------|-----------|
| RSI | Über-/Unterkauft-Niveau (< 35 = bullish) |
| MACD | Momentum und Trendwechsel |
| MA50/MA200 | Kurz-/Langfristiger Trend |
| Wochentrend | Übergeordneter Trend (Filter gegen Abwärtstrend) |
| Volumen | Bestätigung durch Handelsinteresse |
| Level | Support/Widerstand (wie oft getestet) |
| 🐳 Smart-Money | Insider (Form 4) + Institutionen (13F): Netto-Käufe großer Trader |

---

## Setup (5 Minuten)

### 1. Telegram Bot erstellen
1. Öffne Telegram → suche `@BotFather`
2. Sende `/newbot` → Namen vergeben
3. Token kopieren

### 2. Installation
```bash
cd stockbot
pip install -r requirements.txt
```

### 3. Konfiguration (`.env`)

Kopiere `.env.example` zu `.env` und trage Token sowie einen Verschlüsselungs­schlüssel ein:

```env
TELEGRAM_TOKEN_ENV=dein_token_hier
ENCRYPTION_KEY=generierten_schluessel_hier_einfuegen
```

Den `ENCRYPTION_KEY` einmalig generieren (er verschlüsselt die hinterlegten Broker-Zugangsdaten in der DB):
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Bot starten

```bash
python bot.py
```

### 5. Registrieren (pro Nutzer)

Der Bot ist **multi-user**: Jede Person registriert sich selbst per geführtem Setup-Dialog — eine zentrale Chat-ID gibt es nicht mehr.

1. Eigenen Chat mit dem Bot öffnen und `/start` senden
2. Dem Dialog folgen:
   - Demo-Trade-Größe in € festlegen (z. B. `25`)
   - Optional eine echte Trading-Plattform verbinden (z. B. `alpaca`) — API-Key/-Secret werden verschlüsselt gespeichert (`ENCRYPTION_KEY`) und die Nachrichten danach automatisch gelöscht
   - Mit `ja`/`nein`, `/skip` oder `/cancel` durch den Dialog steuern
3. Ab sofort erhält dieser Account täglich eigene Signale mit der eingestellten Trade-Größe

### 6. Testen

```bash
# Nur Analyse anzeigen (kein Telegram):
python test_bot.py analyze

# Testnachricht an den ersten registrierten Nutzer senden:
python test_bot.py telegram

# Signale sofort an alle registrierten Nutzer senden:
python test_bot.py signals

# Aktive Trades des ersten registrierten Nutzers sofort auswerten:
python test_bot.py evaluate
```

Hinweis: Für die Test-Modi `telegram`/`signals`/`evaluate` muss zuerst mindestens ein Account per `/start` registriert sein.

---

## 📊 Web-Dashboard

Zusätzlich zum Telegram-Bot gibt es ein Web-Dashboard mit Equity-Kurve, Trefferquote, P&L pro Ticker und aktiven Trades — pro Nutzer über einen privaten Token-Link.

**Das Dashboard startet automatisch mit dem Bot** (`python bot.py`) — du brauchst lokal keinen zweiten Prozess. Im Telegram-Bot `/dashboard` senden → du bekommst deinen persönlichen Link.

Der Link nutzt automatisch die **LAN-IP** dieses Rechners (z. B. `http://192.168.x.x:8000/dashboard/<token>`), funktioniert also auch **vom Handy im selben WLAN**. (`localhost` würde auf dem Handy auf das Handy selbst zeigen — deshalb die LAN-IP.)

Konfiguration über die `.env`:

- `DASHBOARD_BASE_URL` — leer lassen für Auto-LAN-IP; auf dem VPS deine Domain/öffentliche IP eintragen.
- `RUN_DASHBOARD_IN_BOT=false` — wenn das Dashboard als eigener Dienst laufen soll.

Auf einem Server entweder gebündelt mit dem Bot lassen, oder getrennt per `deploy/dashboard.service` (dann `RUN_DASHBOARD_IN_BOT=false`) dauerhaft betreiben:

```bash
python dashboard.py   # nur nötig, wenn separat vom Bot betrieben
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

## Beispiel-Auswertung (nach US-Börsenschluss)

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

## Deployment auf einer Ubuntu-VM (z. B. Strato V-Server)

Empfohlenes OS: **Ubuntu 24.04** (ohne Plesk/n8n).

**Einmalig einrichten** (als root auf der VM, nachdem du das Repo per SSH geklont hast):

```bash
git clone git@github.com:<dein-user>/trading_bot.git /root/stockbot
cd /root/stockbot
bash deploy/setup_server.sh
# danach TELEGRAM_TOKEN_ENV in die .env eintragen und neu starten:
nano .env
systemctl restart stockbot
```

`setup_server.sh` installiert git/Python, legt das venv an, installiert die Dependencies,
erzeugt die `.env` (inkl. automatisch generiertem `ENCRYPTION_KEY`) und richtet den
`stockbot`-systemd-Dienst ein. Das **Dashboard läuft im Bot-Prozess mit** (Port 8000) —
trage in der `.env` `DASHBOARD_BASE_URL=http://DEINE-SERVER-IP:8000` ein und öffne den Port
(`ufw allow 8000`), damit der `/dashboard`-Link von außen erreichbar ist.

**Status & Logs:**

```bash
systemctl status stockbot
journalctl -u stockbot -f
```

**Updates einspielen** (vom lokalen PC, nach `git push`): `SERVER_HOST` in `deploy.sh`
anpassen und `bash deploy.sh` ausführen — das macht auf dem Server `git pull` +
Dependencies + `systemctl restart stockbot`.

> Dashboard als eigener Dienst gewünscht? `deploy/dashboard.service` installieren und in der
> `.env` `RUN_DASHBOARD_IN_BOT=false` setzen (sonst Portkonflikt auf 8000).

## Hinweis
Dies ist ein **Demo-Bot** — es wird kein echtes Geld gehandelt.
Für echte Trades später: hinterlegte Broker-API-Keys (Alpaca, per Onboarding verschlüsselt
gespeichert) nutzen und die Order-Ausführung ergänzen.
