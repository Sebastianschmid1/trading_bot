# Konzept: Wechsel von Telegram zu Website (v1: Parallelbetrieb)

## 1. Ziel & Leitprinzip
Der Bot soll künftig **auch über eine Website** bedienbar sein (Signale ansehen & annehmen,
Einstellungen, Watchlist, Trades, Dashboard). In **Version 1 laufen Telegram und Website parallel** —
dieselbe Person kann beide Kanäle gleichzeitig nutzen, ohne dass etwas auseinanderläuft.

**Leitprinzip:** Die bestehende **SQLite-DB ([stockbot/core/db.py](../stockbot/core/db.py)) ist die einzige
Quelle der Wahrheit.** Telegram-Bot und Web-Backend lesen/schreiben denselben Datenbestand. Dadurch ist
Parallelität strukturell gelöst: Nimmt ein Nutzer ein Signal in der Web-UI an, sieht der Bot es sofort (und
umgekehrt), weil beide nur den DB-Zustand (`pending`/`active`/`closed`) spiegeln.

## 2. Ausgangslage (was schon da ist)
- **Web-Fundament vorhanden:** [stockbot/web/dashboard.py](../stockbot/web/dashboard.py) ist bereits eine
  FastAPI-App mit Per-Nutzer-Token-Link (`dashboard_token` in der DB), Routen `index`,
  `dashboard_page(token)`, `dashboard_data(token)`, `dashboard_analyze(token, ticker)` und
  `build_dashboard_data(user, strategy, days)`. Läuft heute optional im Bot-Prozess
  (`RUN_DASHBOARD_IN_BOT`) oder eigenständig (`run_dashboard.py`).
- **Domänenlogik** steckt heute teils in den Telegram-Handlern ([stockbot/tgbot/bot.py](../stockbot/tgbot/bot.py)):
  `button_handler` (Ja/Nein/Hebel/Verkaufen), `send_daily_signals`, `monitor_trades`, `close_and_evaluate`,
  `_settings_view`, `cmd_watchadd/-del`, Broker-Order (`_maybe_broker_order`).
- **Persistenz & Dienste** sind schon sauber getrennt: `db`, `market`, `backtest`, `broker`, `ai`.

→ Der Wechsel ist **kein Neubau**, sondern: (a) Geschäftslogik aus den Telegram-Handlern in eine
gemeinsame Service-Schicht heben, (b) eine Web-API + UI darauf setzen, (c) Web-Auth + Web-Benachrichtigungen.

## 3. Zielarchitektur v1
```
                ┌─────────────────────────────┐
                │   Job-Engine (1 Prozess)     │  send_daily_signals · monitor_trades
                │   APScheduler / PTB-JobQueue │  close_and_evaluate · scan_smart_money
                └───────────────┬──────────────┘
                                │ schreibt Domänen-Zustand
                    ┌───────────▼───────────┐
                    │   stockbot.core.db     │  ← Single Source of Truth (SQLite)
                    │   + stockbot.services  │  ← NEU: gemeinsame Aktionen
                    └───┬───────────────┬────┘
          liest/schreibt│               │liest/schreibt
              ┌─────────▼──────┐   ┌────▼─────────────────┐
              │ Telegram-Bot   │   │ Web-Backend (FastAPI)│
              │ (tgbot/bot.py) │   │ REST + SSE + Auth    │
              └────────────────┘   └────┬─────────────────┘
                                        │ HTTP/JSON, SSE
                                   ┌────▼─────────────┐
                                   │ Web-Frontend (UI)│
                                   └──────────────────┘
```
Beide Kanäle rufen **dieselben** Service-Funktionen auf → keine Logik-Duplikate.

## 4. Kernschritt: gemeinsame Service-Schicht (`stockbot/services/`)
Die heute in `bot.py` verdrahtete Logik in framework-neutrale Funktionen extrahieren (keine Telegram-/HTTP-
Objekte, nur `user_id` + Werte rein, DB-Effekt + Ergebnis raus). Vorschlag `stockbot/services/trades.py`,
`settings.py`, `signals.py`:
- `accept_signal(user_id, ticker, leverage=None) -> Trade` (heute in `button_handler`/`accept`)
- `reject_signal(user_id, ticker)`
- `sell_trade(user_id, ticker) -> ClosedTrade` (+ optionale Broker-Order via `broker.client`)
- `set_leverage / set_sl_tp / toggle_strategy / toggle_region / set_trade_size` (heute `_settings_view`-Aktionen)
- `add_watchlist / del_watchlist` (heute `cmd_watchadd/-del`, inkl. Validierung über `market.lookup`)
- `current_signals(user_id)`, `active_trades(user_id)` (lesend, ergänzt `build_dashboard_data`)

Bot-Handler und Web-Endpunkte werden damit zu **dünnen Adaptern** über denselben Services.
→ Garantiert, dass Telegram und Web *immer dasselbe* tun (Voraussetzung für Parallelbetrieb).

## 5. Feature-Mapping Telegram → Web
| Telegram heute | Web v1 |
|----------------|--------|
| `/start` Onboarding-Dialog | Onboarding-Formular (Trade-Größe, optional Broker) |
| Tagessignal + JA/NEIN/Hebel-Buttons | Signal-Karten im Feed + Annehmen/Ablehnen/Hebel-Auswahl |
| `/settings` (Körbe, SL/TP, Hebel, Auto-Accept …) | Einstellungs-Seite (Formular/Toggles) |
| `/watchlist`, `/watchadd`, `/watchdel` | Watchlist-Seite (Suche + „Meinten Sie?") |
| `/profile`, `/dashboard` | Konto-/Dashboard-Seite (baut auf vorhandenem Dashboard auf) |
| `/signals`, `/evaluate`, `/teststrat`, `/top5trade` | Buttons „Jetzt analysieren / auswerten / Backtest" |
| Auswertungs-/Schließungs-Nachrichten | In-App-Benachrichtigungen + Verlaufsliste |

## 6. Authentifizierung
Bestehende Nutzer sind über die Telegram-`user_id` identifiziert — daran andocken:
- **Empfohlen v1:** **„Login mit Telegram"** (offizielles Telegram-Login-Widget). Verifiziert per Hash,
  liefert die Telegram-`user_id` → mappt direkt auf den bestehenden DB-Nutzer. Kein neues Identitätssystem,
  nahtlose Brücke für alle Bestandsnutzer.
- **Sofort nutzbar als Bootstrap:** der vorhandene `dashboard_token`-Link funktioniert schon als
  passwortloser Zugang → daraus eine Web-Session (signiertes Cookie / JWT) erzeugen.
- **Reine Web-Neukunden (später):** Magic-Link per E-Mail (neue Spalte `email`), gleicher User-Datensatz.

Sessions als HTTP-only-Cookie (signiert) oder JWT; CSRF-Schutz für state-ändernde Endpunkte; HTTPS Pflicht
(Reverse-Proxy/Caddy/nginx auf dem Strato-VM).

## 7. Benachrichtigungen (Signale „pushen")
Telegram bringt Push „gratis"; die Website braucht Ersatz:
- **v1 einfach & robust:** **Server-Sent Events (SSE)** vom FastAPI-Backend (`/api/stream`) → Live-Feed im
  Browser, solange die Seite offen ist; zusätzlich In-App-Benachrichtigungsliste (DB-Tabelle, s. u.).
- **Telegram bleibt der zuverlässige Push-Kanal** in v1 → niemand verpasst Signale.
- **Später:** Web-Push (Service-Worker, VAPID) für echte Push-Notifications auch bei geschlossener Seite.

**Doppel-Benachrichtigung vermeiden:** neue Spalte `notify_channel` (`telegram` | `web` | `both`, Default
`both` in v1). Die Job-Engine schreibt das Signal in die DB und stellt es **beiden** Kanälen zur Verfügung;
der Nutzer entscheidet später pro Kanal.

## 8. Job-/Signal-Engine in v1
Bleibt **wie heute im Bot-Prozess** (PTB-JobQueue) — kein Risiko-Umbau. Wichtig: die Jobs schreiben
ausschließlich **DB-Zustand** (pending/active/closed, Ticks), die Web-API liest denselben Zustand. Einzige
Ergänzung: nach Signalerzeugung zusätzlich ein **SSE-Event** auslösen (z. B. via leichtem Pub/Sub:
DB-Flag/Tabelle, die das Web-Backend pollt, oder Redis/in-memory bei gemeinsamem Prozess).
*Spätere Phase:* Engine aus dem Bot in einen eigenständigen „core-runner"-Prozess lösen, Bot + Web werden
beide zu reinen Frontends.

## 9. Datenmodell — additive Migrationen (`_migrate` in db.py)
Nur ergänzen (Muster wie bei `watchlist`):
- `users.email TEXT` (optional, für Magic-Link später)
- `users.web_auth` / Session-Handling (oder separate Tabelle `sessions(token, user_id, expires)`)
- `users.notify_channel TEXT DEFAULT 'both'`
- `notifications(id, user_id, ts, type, title, body, read)` — In-App-Benachrichtigungen/Verlauf
Bestehendes Schema (`users`, `trades`, `trade_ticks`) bleibt unverändert → kein Datenverlust, Bot läuft weiter.

## 10. Tech-Stack-Empfehlung (v1, schlank halten)
- **Backend:** FastAPI (schon vorhanden) — Endpunkte unter `/api/...`, Auth-Middleware, SSE.
- **Frontend:** Bewusst minimal für v1 — **server-gerendert (Jinja2-Templates) + etwas HTMX/Alpine.js**
  statt schwergewichtiger SPA. Das vorhandene [dashboard.html](../stockbot/web/static/dashboard.html) +
  Chart.js wird wiederverwendet. (SPA mit React/Vue erst, wenn nötig.)
- **Deployment:** die Website wird vom selben Server wie das Dashboard ausgeliefert (`run_dashboard.py`
  bzw. `RUN_DASHBOARD_IN_BOT=true`) hinter HTTPS-Reverse-Proxy; Bot-Dienst läuft parallel auf derselben
  `data/bot.db`. Kein separater Web-Entrypoint nötig.

## 11. Migrations-/Parallelbetrieb-Phasen
- **Phase 0 — Refactor (unsichtbar):** Service-Schicht extrahieren ([§4]), Bot-Handler darauf umstellen,
  Tests grün halten. Kein Verhaltenswechsel.
- **Phase 1 — Web-MVP (Parallel-Start):** Auth (Telegram-Login + Token-Bootstrap), Signal-Feed mit
  Annehmen/Ablehnen, Einstellungen, Watchlist, Dashboard. Telegram bleibt voll aktiv. **Beide parallel.**
- **Phase 2 — Komfort:** SSE-Live-Feed, In-App-Benachrichtigungen, Backtest-/Report-Ansichten, `notify_channel`.
- **Phase 3 — Optional:** Web-Push, Engine als eigener Prozess, Telegram nur noch als Zusatz-Kanal.

## 12. Risiken & Gegenmaßnahmen
- **Logik-Divergenz Bot vs. Web** → durch gemeinsame Service-Schicht ([§4]) ausgeschlossen.
- **Race Conditions** (gleichzeitig Web + Telegram auf denselben Trade) → SQLite-Transaktionen +
  Idempotenz/`UNIQUE (user_id, trade_date, ticker)` (existiert bereits) nutzen; Aktionen prüfen den
  aktuellen Status vor dem Schreiben.
- **SQLite bei mehr Schreib-Last** → `WAL`-Modus aktivieren; bei Wachstum Migration auf Postgres erwägen.
- **Sicherheit** (Web = öffentlich erreichbar): HTTPS, signierte Sessions, CSRF, Rate-Limiting,
  Broker-Keys bleiben verschlüsselt ([db.encrypt]); kein Klartext im Frontend.
- **Doppel-Benachrichtigung** → `notify_channel` ([§7]).

## 13. v1-Scope (MVP) — konkret
**Drin:** Telegram-Login, Signal-Feed mit Annehmen/Ablehnen/Hebel, Einstellungen, Watchlist (mit Validierung),
Profil + Dashboard (vorhandenes wiederverwenden), „jetzt analysieren/auswerten"-Buttons; Telegram läuft
unverändert parallel; gemeinsame Service-Schicht.
**Bewusst NICHT in v1:** Web-Push bei geschlossener Seite, eigenständiger Engine-Prozess, reine
E-Mail/Passwort-Registrierung, native Mobile-App, SPA-Framework.

## 14. Grobe Aufwandseinschätzung
- Phase 0 (Service-Refactor + Tests): **mittel** — der wertvollste, risikoärmste Schritt.
- Phase 1 (Web-MVP): **mittel–groß** — Auth + 4–5 Seiten/Endpunkt-Gruppen, baut auf vorhandenem FastAPI/Dashboard auf.
- Phase 2/3: **inkrementell**, jederzeit pausierbar (Telegram trägt weiter).

## 15. Umsetzungsstand (umgesetzt)
- **Phase 0 ✅** — Service-Schicht `stockbot/services/` (trades, settings, watchlist, notifications);
  Telegram-Handler sind dünne Adapter darüber. Tests: `tests/test_services.py`.
- **Phase 1 ✅** — Web-App `stockbot/web/webapp.py` (+ Jinja-Templates), Auth `stockbot/web/auth.py`
  (DB-Sessions: Login per Dashboard-Token **und** „Login mit Telegram" via HMAC). Seiten: `/login`,
  `/app` (Annehmen/Ablehnen/Hebel/Verkaufen), `/app/settings`, `/app/watchlist`. In dieselbe FastAPI-App
  wie das Dashboard eingehängt → ein Server (`run_dashboard.py`). Tests: `tests/test_webapp.py`.
- **Phase 2 ✅ (Kern)** — `notify_channel` (DB) + In-App-`notifications` (DB) + Service `notify()`;
  Seite `/app/notifications` mit **SSE-Live-Feed** (`/app/stream`); die Jobs `send_daily_signals` und
  `close_and_evaluate` schreiben zusätzlich In-App-Mitteilungen. Telegram bleibt Push-Kanal.
- **Phase 3 (teilweise)** — Deployment als eigener Dienst ist bereits über `dashboard.service`/
  `run_dashboard.py` abgedeckt (serviert die komplette Website). **Offen/optional:** echtes Web-Push
  bei geschlossener Seite (Service-Worker + VAPID) und die Auslagerung der Job-Engine in einen eigenen
  Prozess — bewusst nicht umgesetzt (Engine läuft weiter stabil im Bot-Prozess).

---
*Demo-Kontext: Es wird (noch) kein echtes Geld gehandelt. Vor Echtgeld/öffentlichem Web-Betrieb gelten
zusätzliche Pflichten (Sicherheit, Datenschutz/DSGVO, ggf. Regulierung) — vorab prüfen.*
