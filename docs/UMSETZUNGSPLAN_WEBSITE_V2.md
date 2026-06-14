# Umsetzungsplan — Website v2 (Signale on-demand, Asset-Klassen, Sicherheit)

> **Umsetzungsstand (2026-06-14):** Schritte 1–7 sind **implementiert** (221 Tests grün).
> - **1 Sicherheit-Grundgerüst:** Cookie-`secure` (`config.COOKIE_SECURE`), Security-Header-Middleware
>   (CSP/HSTS/X-Frame-Options/…), TLS-Reverse-Proxy-Vorlage [deploy/Caddyfile](../deploy/Caddyfile).
> - **2 Asset-Klassen-Registry + Profile:** [asset_classes.py](../stockbot/market/asset_classes.py),
>   Profil durch den Analyzer gefädelt (rückwärtskompatibel), `users.asset_pref`.
> - **3 Dropdown „Aktien" zuerst:** auf `/app`, persistiert pro Nutzer.
> - **4/5/6 ETFs/Krypto/Rohstoffe:** als Registry-Einträge + Körbe in config; ETFs/Rohstoffe handelbar
>   (us_equity), Krypto vorerst Demo/Tracking (echter Alpaca-Krypto-Handel = Folge-Ticket).
> - **2-/Punkt-2 On-Demand-Signale + 7-Tage-Chart:** `POST /app/scan` + Inline-SVG-Sparkline.
> - **1-/Punkt-1 Dashboard→App-Link:** Button im Dashboard.
> - **7 Härtung:** CSRF (Origin-Abgleich), Rate-Limit, Session-Cleanup + „Überall abmelden".
>
> **Noch offen / bewusst Folge-Tickets:** echter TLS-Betrieb auf dem Server (Domain + Caddy ausrollen),
> echter Alpaca-**Krypto-Live-Handel** (Symbol-Mapping `BTC/USD`, 24/7-Monitoring/kein EOD-Close).
>
> ---


> Aufbauend auf der bestehenden Web-App ([stockbot/web/webapp.py](../stockbot/web/webapp.py),
> Service-Schicht [stockbot/services/](../stockbot/services/)) und dem Telegram-Bot. Alles läuft
> **parallel**, eine DB, eine Service-Schicht. Dieses Dokument ist der Umsetzungsplan für die
> sieben gewünschten Punkte — Reihenfolge = empfohlene Bearbeitungsreihenfolge.

## Überblick & Status quo (was es heute gibt)

- Signale entstehen **nur zeitgesteuert** (Tagesjob `send_daily_signals`) und landen als *pending trades*
  in der DB; die Website (`/app`) zeigt nur diese vorbereiteten Signale an, **kein On-Demand-Anfordern**.
- Die Analyse-Engine [analyzer.py](../stockbot/market/analyzer.py) (`analyze_universe`/`analyze_ticker`)
  ist **instrument-agnostisch**: sie braucht nur OHLCV je Timeframe (yfinance). Parameter sind aber auf
  **US-Aktien** getunt (Intraday-TFs 5m/15m/1h/1d mit `prepost=True`, Wochentrend-Filter, Volumen-RVOL).
- Universen ([config.py](../stockbot/config.py), [universes.py](../stockbot/market/universes.py)) sind
  **nur Aktien** (sp500 / msci_world / emerging).
- Login: Session-Cookie (httponly, samesite=lax) via **Dashboard-Token** oder **Telegram-HMAC**
  ([auth.py](../stockbot/web/auth.py)). Token (`token_urlsafe(24)`) & Session (`token_urlsafe(32)`)
  haben gute Entropie. **Kein HTTPS-Zwang, keine Security-Header, Token steht in der URL.**

---

## 1. Dashboard-Link zur Website

**Ist:** Die App verlinkt bereits zum Dashboard (`/app/dashboard` → `/dashboard/{token}`), aber der
umgekehrte Weg (Read-only-**Dashboard** → interaktive **Web-App**) fehlt.

**Umsetzung (klein):**
- In der Dashboard-Vorlage ([stockbot/web/dashboard.py](../stockbot/web/dashboard.py),
  `dashboard_page` / zugehöriges HTML) einen Button **„➡ Zur Web-App"** ergänzen, der auf `/app` zeigt.
  Da der Nutzer das Dashboard über seinen Token öffnet, aber `/app` ein Session-Cookie braucht:
  Link auf **`/auth/token?token={token}`** setzen (legt Session an → leitet nach `/app`). So ist der
  Übergang nahtlos, auch wenn noch keine Session existiert.
- Symmetrisch im Bot: `/website` (existiert) + im `/dashboard`-Text einen Hinweis „Interaktiv: /website".

**Aufwand:** ~30 Min. **Risiko:** keine Logikänderung.

---

## 2. Signale auf der Website anfordern (On-Demand) + volle Infos + 7-Tage-Chart

Ziel: Auf `/app` ein **„🔄 Signale anfordern"**-Knopf, der live die Analyse für die gewählte
Asset-Klasse/Region rechnet und **dieselben Infos wie im Telegram-Bot** zeigt, plus pro Signal eine
**Grafik des Kursverlaufs der letzten 7 Tage**.

**Backend:**
- Neue Route `POST /app/scan` (async): liest `asset`/`region` des Nutzers, ruft
  `analyze_universe(tickers, generate=…)` über `run_in_threadpool` (blockiert den Event-Loop nicht,
  Analyse dauert ~15–20 s). Ergebnis = volle Signal-Dicts aus `analyze_ticker` (enthalten bereits
  `strength`, `rsi_comment`, `macd_comment`, `trend_comment`, `volume_comment`, `weekly_comment`,
  `stop_loss/take_profit`, Support/Resistance, `tf_scores`).
- **Caching:** Ergebnis je Nutzer kurz (z. B. 5–10 Min) cachen (in-memory oder `data/`-Datei), damit
  Seiten-Reloads nicht jedes Mal neu rechnen. Optional Fortschritts-Hinweis via vorhandenem SSE-Kanal.
- **Annehmen aus On-Demand:** Ein angefordertes Signal ist noch kein *pending trade*. Neue Service-Funktion
  `trades.accept_signal(user_id, signal_dict)` → schreibt es als pending **und** akzeptiert es
  (oder direkt aktiv), damit Monitoring/Auswertung wie gewohnt greifen. Reuse der bestehenden
  `accept_trade`-Logik; nur der Pending-Eintrag wird zuvor aus dem Signal-Dict erzeugt.

**7-Tage-Chart (empfohlen: leichtgewichtig, ohne neue Dependency):**
- Neuer Helfer `analyzer.price_history(ticker, days=7)` → Liste täglicher Closes (yfinance
  `period="1mo", interval="1d"`, letzte 7 Handelstage; Wochenend-robust). (Alternativ den bestehenden
  `factor_history` mitnutzen — der liefert schon `price` je Tag.)
- Route `GET /app/chart/{ticker}` → JSON `{dates, closes}`.
- Im Template eine **Inline-SVG-Sparkline** clientseitig zeichnen (kein Chart.js/matplotlib nötig,
  kein zusätzliches Paket, schnell, mobilfreundlich). Grün/rot je nach 7-Tage-Richtung.
  *Fallback-Option*, falls reichere Charts gewünscht: serverseitiges PNG via matplotlib (bereits
  als Dependency vorhanden) unter `docs/`/`data/` — aber das ist schwerer und wird hier nicht empfohlen.

**Template:** `app.html` erweitern um eine vollständige **Signal-Karte** (analog Telegram `_signal_card`):
Stärke-Balken, alle Begründungs-Zeilen, SL/TP, S/R, darunter die SVG-Sparkline + Annehmen/Ablehnen/Hebel.

**Tests:** `price_history`/`accept_signal` (Service, offline gemockt) + Webapp-Route `/app/scan`
(yfinance gemockt, prüft Render der vollen Karte). **Aufwand:** ~1 Tag.

---

## 3. „Signale" als Dropdown — erster Punkt „Aktien"

Auf `/app` einen **Asset-Klassen-Dropdown** oben einführen (Default **Aktien**). Die Auswahl bestimmt
Universum + Signal-Profil für „Signale anfordern" (Punkt 2).

**Architektur — eine zentrale Registry** (vermeidet Spezialfälle, macht ETF/Crypto/Rohstoff zu reinen
Daten-Einträgen):

```python
# stockbot/market/asset_classes.py  (neu)
AssetClass(key, label, tickers_fn, generate, profile)
ASSET_CLASSES = {
  "stocks":     AssetClass("stocks", "Aktien",       <region-universe>, analyze_ticker, STOCK_PROFILE),
  # "etf":      …  (Punkt 4)
  # "crypto":   …  (Punkt 5)
  # "commodity":…  (Punkt 6)
}
```

- `profile` bündelt die instrument-spezifischen Parameter (TFs, `prepost`, Wochentrend-Filter an/aus,
  RSI-Schwellen, Smart-Money an/aus, EOD-Schließen, Monitoring 24/7). Heute sind diese global in
  `config.py` — sie werden in benannte Profile gehoben; **Default-Profil = aktuelle Aktien-Werte**
  (keine Verhaltensänderung für Bestand).
- Dropdown rendert nur die **registrierten** Klassen → ETF/Crypto/Rohstoff erscheinen automatisch,
  sobald ihr Eintrag existiert. State über Query-Param `?asset=stocks` (+ in der Session/DB merken).
- Persistenz: Spalte `users.asset_pref` (additive Migration, Default `stocks`) — analog zu `watchlist`.

**Aufwand:** ~0.5 Tag (Registry + Dropdown + Profile-Refactor). Danach sind 4/5/6 v. a. „Korb + Profil".

---

## 4. ETFs (Website only)

**a) Eigene Signal-Logik nötig?** → **Nein, weitgehende Wiederverwendung.** ETFs liefern dieselben
OHLCV-Daten; RSI/MACD/MA/ATR/Volumen/Support-Resistance sind 1:1 gültig. Anpassungen:
- **Smart-Money aus** (Insider/13F gibt es für ETFs nicht → Re-Ranking-Komponente deaktivieren).
- ETFs sind weniger volatil → optional **`MIN_SIGNAL_STRENGTH` etwas niedriger**.
- Handelszeiten/`prepost` wie Aktien → unverändert. → **eigenes Profil, keine eigene Engine.**

**b) Dropdown-Eintrag „ETFs":** Registry-Eintrag `etf` mit kuratiertem ETF-Korb.
- **Universum:** neuer Korb `UNIVERSE_ETF` in config (liquide, Alpaca-handelbar): z. B. SPY, QQQ, IWM,
  DIA, VTI, VOO, XLK, XLF, XLE, XLV, SMH, ARKK, EEM, EFA, TLT, HYG, GLD … (Optional später Auto-Quelle.)
- **Handelbarkeit:** Alpaca führt ETFs als `us_equity` (`tradable=True`) → bestehender Kauf-Pfad
  (`submit_buy` notional/Bruchteile) funktioniert unverändert; `get_asset_info` bestätigt schon heute ETFs.

**Aufwand:** ~0.5 Tag (Korb + Profil + Registry-Eintrag + Test). **Reifegrad: produktionsnah.**

---

## 5. Krypto (Website only)

**a) Eigene Signal-Logik nötig?** → **Teilweise — eigenes Profil + ein paar gezielte Anpassungen,
aber dieselbe Indikator-Engine.** Unterschiede zu Aktien:
- **24/7-Handel:** kein `prepost`, keine US-Sessionzeiten; **Wochentrend-Filter** (`calc_weekly_trend`
  rechnet auf Handelswochen) muss für 7-Tage-Wochen geprüft/angepasst werden.
- **Volatilität deutlich höher** → RSI-Schwellen (`RSI_OVERSOLD/OVERBOUGHT`) und `MIN_SIGNAL_STRENGTH`
  neu kalibrieren; ATR-Multiplikatoren ggf. weiter.
- **Kein Smart-Money** (Insider/13F nicht anwendbar) → aus.
- **EOD-Schließung sinnlos** (kein Tagesschluss) → Profil setzt `eod_close=False`; **Monitoring/SL/TP
  muss 24/7 laufen** (heute an US-Session orientiert → Scheduler-Anpassung für Crypto-Trades).
- **Datenquelle:** yfinance `BTC-USD`, `ETH-USD` … (Intraday-TFs verfügbar). Volumen vorhanden,
  RVOL-Logik bleibt nutzbar.

**b) Dropdown-Eintrag „Krypto":** Registry-Eintrag `crypto` mit Krypto-Korb (BTC, ETH, SOL, …).
- **Handelbarkeit (Alpaca):** Krypto wird unterstützt (`asset_class=crypto`, 24/7, Bruchteile/Notional),
  **aber eigenes Symbolformat** (`BTC/USD` statt yfinance `BTC-USD`) → Mapping yfinance↔Alpaca nötig;
  `submit_buy` ggf. um Crypto-`OrderClass`/TimeInForce.GTC erweitern. **Vor Live-Handel separat verifizieren.**
- Realistischer erster Schritt: **Krypto-Signale + Demo-Tracking** (yfinance-Preise) anbieten; echter
  Alpaca-Krypto-Kauf als Folge-Ticket nach Verifikation.

**Aufwand:** ~1–1.5 Tage (Profil, Wochentrend-/Schwellen-Anpassung, 24/7-Monitoring, Demo). Live-Alpaca
extra. **Reifegrad: Demo gut machbar, Live-Handel braucht Zusatzarbeit.**

---

## 6. Rohstoffhandel (Website only)

**a) Eigene Signal-Logik nötig?** → **Empfehlung: über Rohstoff-ETFs abbilden → dann KEINE eigene Logik**
(nutzt den ETF-Pfad aus Punkt 4 vollständig). Direkte **Futures** (z. B. `GC=F`, `CL=F`) hätten
Sonderprobleme — eigene Sessionzeiten, **Contango/Rollover** der Kontrakte, andere Hebel-/Margin-Logik —
und sind **bei Alpaca nicht handelbar**. Daher:
- **Handelbarer Weg = Rohstoff-ETFs** (Alpaca `us_equity`, `tradable=True`): GLD/IAU (Gold), SLV (Silber),
  USO (Öl), UNG (Gas), DBC/PDBC (breit), DBA (Agrar), CPER (Kupfer). → Indikator-Engine + ETF-Profil 1:1.
- **Optional, nur als Analyse/Info (nicht handelbar):** yfinance-Futures (`GC=F`, `CL=F`) als
  Vergleichswert anzeigen — ohne Order-Anbindung. Echter Futures-Handel ist hier **out of scope**
  (anderer Broker + Rollover-Handling nötig).

**b) Dropdown-Eintrag „Rohstoffe":** Registry-Eintrag `commodity` mit Rohstoff-ETF-Korb
(`UNIVERSE_COMMODITY`), Profil = ETF-Profil. Erscheint automatisch im Dropdown (Punkt 3).

**Aufwand:** ~0.5 Tag (Korb + Registry-Eintrag, Profil von ETF geerbt). **Reifegrad: produktionsnah
(als ETFs).**

### Zusammenfassung 4–6

| Klasse | Eigene Signal-Logik? | Datenquelle | Alpaca-handelbar | Aufwand |
|---|---|---|---|---|
| ETFs | Nein (Profil: Smart-Money aus) | yfinance | Ja (us_equity) | ~0.5 Tag |
| Krypto | Teilweise (24/7, Schwellen, kein EOD) | yfinance `BTC-USD` | Ja, eigenes Symbolformat/Pfad | ~1–1.5 Tage + Live extra |
| Rohstoffe | Nein (via ETFs = ETF-Profil) | yfinance ETFs (Futures nur Info) | Ja (als ETFs); Futures nein | ~0.5 Tag |

---

## 7. Sicherheit — Unbefugte dürfen keinen Zugriff haben

**Bereits gut:** alle `/app*`-Routen prüfen `current_user`; SSE ebenso; Tokens/Sessions haben hohe
Entropie (`token_urlsafe` 24/32); Session-Cookie httponly+samesite=lax mit DB-Ablauf; Telegram-Login
HMAC-verifiziert mit `compare_digest` + 1-Tag-Fenster; Broker-Keys Fernet-verschlüsselt.

**Lücken & Maßnahmen (nach Priorität):**

1. **Kein HTTPS / Klartext-Transport (höchste Prio).** Auf dem VPS läuft Port 8000 ungesichert
   (`ufw allow 8000`). Session-Cookie & Token sind im Klartext abgreifbar.
   → **Reverse-Proxy mit TLS** (Caddy oder nginx + Let's Encrypt), **Port 8000 nach außen schließen**
   (nur localhost), `DASHBOARD_BASE_URL=https://…`, **Cookie `secure=True`** setzen (in `auth.login_response`).
2. **Token in der URL** (`/auth/token?token=`, `/dashboard/{token}`). URLs landen in Server-Access-Logs,
   Browser-History, Referer. → Dashboard-Token als reinen **Bootstrap** behandeln: nach erstem Gebrauch
   in eine Session tauschen (passiert bereits) und in Doku darauf hinweisen, Links nicht zu teilen.
   Optional: **Token-Rotation** (`/settings` „Link neu erzeugen") + ggf. Referrer-Policy-Header.
3. **Security-Header fehlen.** → Middleware ergänzen: `Strict-Transport-Security` (nach TLS),
   `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
   schlanke `Content-Security-Policy` (Inline-Styles sind bereits im `base.html`, CSP entsprechend).
4. **CSRF.** Schreibende POSTs sind nur durch `samesite=lax` geschützt (blockt zwar Cross-Site-POSTs,
   aber dünn). → Entweder **`samesite=strict`** für das Session-Cookie oder ein **CSRF-Token** pro
   Formular (verstecktes Feld, serverseitig geprüft). Empfehlung: CSRF-Token in den `/app/*`-POSTs.
5. **Rate-Limiting / Brute-Force.** `/auth/token` & `/auth/telegram` ohne Drosselung. Tokens sind zwar
   ~192 Bit (praktisch nicht ratebar), trotzdem **einfaches Rate-Limit** (z. B. pro IP) gegen Missbrauch.
6. **Session-Hygiene.** 30-Tage-Sessions ohne Rotation/Cleanup. → periodisch abgelaufene Sessions löschen,
   „Überall abmelden" (alle Sessions eines Nutzers invalidieren), Session-Rotation nach Login.
7. **Verifikation:** Auth-Tests erweitern — jede `/app*`- und neue `/app/scan|chart`-Route ohne Cookie →
   303 auf `/login`; manipuliertes/abgelaufenes Session-Token → kein Zugriff; HTTPS-Cookie-Flags gesetzt.

**Aufwand:** TLS/Proxy ~0.5 Tag (Server-Config) + Header/CSRF/Cookie-Flags/Cleanup ~0.5 Tag.

---

## Empfohlene Reihenfolge & grober Gesamtaufwand

1. **Sicherheit Grundgerüst** (TLS + Cookie-`secure` + Header) — Voraussetzung für öffentlichen Betrieb. (~1 Tag)
2. **Asset-Klassen-Registry + Profile-Refactor** (Punkt 3) — Fundament für 4/5/6. (~0.5 Tag)
3. **On-Demand-Signale + 7-Tage-Chart** (Punkt 2). (~1 Tag)
4. **Dashboard→App-Link** (Punkt 1). (~0.5 Tag, jederzeit einschiebbar)
5. **ETFs** (Punkt 4) → **Rohstoffe als ETFs** (Punkt 6). (~1 Tag zusammen)
6. **Krypto Demo** (Punkt 5), Live-Alpaca als Folge-Ticket. (~1–1.5 Tage)
7. **Sicherheit Härtung** (CSRF, Rate-Limit, Session-Cleanup). (~0.5 Tag)

**Gesamt: ~5–6 Personentage** für v2 (ohne Krypto-Live-Handel).

## Nicht im Umfang / spätere Tickets
- Echter Alpaca-**Krypto-Live-Handel** (Symbol-Mapping, OrderClass) — separates Ticket nach Verifikation.
- Echter **Futures-Handel** (anderer Broker, Rollover) — bewusst ausgeschlossen; Rohstoffe via ETFs.
- Keine Änderung an der bestehenden Telegram-Tageslogik; On-Demand ist additiv.
