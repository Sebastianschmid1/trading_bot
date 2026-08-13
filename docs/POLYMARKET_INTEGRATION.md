# Polymarket als Ereignissensor (PM-Serie)

**Status:** Plan, noch kein Code. Erstellt 2026-08-12 (Lead).
**Einordnung:** Research-Tier-Erweiterung, additiv zur Roadmap in `docs/UMSETZUNGSPLAN.md`.
Berührt **keinen** TSAFE-Pfad und ändert bis einschließlich PM-3 **kein** Live-Verhalten.

---

## 1. Ziel und Nicht-Ziele

**Ziel:** Polymarket-Ereigniswahrscheinlichkeiten als **zusätzlicher, read-only Sensor**
neben den bestehenden Kursdaten. Er darf einen Trade **dämpfen, vertagen oder eine
menschliche Bestätigung erzwingen** — nie auslösen, nie vergrößern, nie eine Prüfung
lockern.

**Nicht-Ziele (hart, nicht verhandelbar):**

- **Kein Handel auf Polymarket.** Keine Wallet, kein Private Key, kein Polymarket-API-Key,
  kein Order-/Signing-Endpunkt, keine `py-clob-client`-Dependency mit Trading-Funktionen.
  Nur öffentliche, unauthentifizierte Lese-Endpunkte. (Polymarket-Trading ist aus
  Deutschland gesperrt; Marktdaten lesen ist erlaubt — die Sperre wird nicht umgangen.)
- **Kein zweiter Kursprovider.** Alpaca bleibt maßgeblich für Quotes/Bars/Ausführung
  (Gate P2, `market/provider_factory.get_signal_provider()` — niemals anfassen). Die
  Pyth-Referenzpreise von Polymarket werden nicht verwendet.
- **Kein LLM-generiertes Mapping.** Die Zuordnung Ereignis → Aktie ist eine manuell
  geprüfte, versionierte Datei im Repo.
- **Keine Aufweichung von Guards.** Positionslimits, Stop-Loss, Kill-Switch, Live-Sperre,
  `risk.pretrade_check` bleiben unberührt und behalten das letzte Wort.

---

## 2. Architektur-Einordnung

Polymarket ist **Research-Tier**, wie `smartmoney`, `lookup`, `llm_ranker` (Gate-P2-
Klassifizierung aus W3.2). Es lebt neben `stockbot/research/shadow.py` und `lab.py`, nicht
in `stockbot/market/`.

```
Gamma/CLOB/Data API  →  polymarket.py (IO, read-only)
                          ↓ Snapshot
                     polymarket_quality.py (rein, IO-frei)   ← Liquidität/Frische/Spread
                          ↓ EventState (usable | neutral)
                     polymarket_mapping.yaml (manuell, versioniert)
                          ↓ Ticker ← Event
                     polymarket_overlay.py (rein, IO-frei)   ← size_factor ≤ 1.0, confirm, defer
                          ↓ OverlayDecision
                     bestehender Signalpfad → risk.pretrade_check → OMS → Alpaca Paper
```

**Der Overlay ist ein Einweg-Ventil.** Er darf `size_factor` nur im Intervall `[0.25, 1.0]`
liefern und `require_confirmation`/`defer` nur auf `True` setzen — nie eine Ablehnung
aufheben, nie `size_factor > 1.0`. Das ist als Invariante in der reinen Funktion geklemmt
**und** als Test hinterlegt.

**Fail-neutral:** API nicht erreichbar, Buch dünn, Daten alt, Mapping fehlt → der Sensor
liefert `neutral` (`size_factor=1.0`, keine Confirmation). Ein Ausfall darf weder blocken
noch verstärken. Analog zum fail-open-Muster in `execution/risk_context.py`.

### Neue Dateien

| Datei | Inhalt | IO? |
|---|---|---|
| `stockbot/research/polymarket.py` | HTTP-Client (Gamma/CLOB/Data), Snapshot-Dataclass, Persistenz-Aufruf | ja |
| `stockbot/research/polymarket_quality.py` | Qualitäts-/Liquiditätsfilter, Δp-Berechnung über Zeitfenster | nein |
| `stockbot/research/polymarket_overlay.py` | `OverlayDecision` aus Signal + EventState | nein |
| `stockbot/research/polymarket_scheduler.py` | `run_repeating`-Job (Poll + Persistenz + Alarm) | ja |
| `config/polymarket_mapping.yaml` | manuell freigegebenes, versioniertes Event→Ticker-Mapping | — |
| `tests/test_polymarket*.py` | Fixture-basiert, **kein Netzwerk** | — |

Flache Module, kein neues Package — konsistent mit `shadow.py`/`shadow_scheduler.py`/`lab.py`.
(Hinweis für den Umsetzer: `stockbot.research` steht nicht in der `packages`-Liste in
`pyproject.toml`; das ist heute schon so und wird **nicht** nebenbei geändert.)

---

## 3. Datenmodell

Zwei neue Tabellen, **beide in `SCHEMA_SQL` (`core/db.py`) UND als Alembic-Migration** —
die Regel aus dem Projekt gilt ohne Ausnahme. Aktuellen Head per `alembic heads` ermitteln,
nicht raten.

**`polymarket_markets`** — was der Markt ist (ändert sich fast nie):
`id`, `condition_id` (unique), `token_id`, `slug`, `question`, `resolution_source`,
`resolution_rules` (Volltext — der Wortlaut ist die eigentliche Semantik), `end_date`,
`category`, `first_seen_at`, `last_seen_at`.

**`polymarket_snapshots`** — Zeitreihe je Abruf:
`id`, `condition_id`, `fetched_at`, `bid`, `ask`, `mid`, `spread_bps`, `depth_bid_usd`,
`depth_ask_usd`, `volume_24h_usd`, `open_interest_usd`, `trade_count_24h`,
`last_trade_at`, `usable` (bool, Ergebnis des Qualitätsfilters), `reject_code`.

**Zeitvertrag zwingend beachten:** alle Zeitstempel explizit als naiver UTC-String
`'YYYY-MM-DD HH:MM:SS'` binden (`db._utc_timestamp()`), **nie** `server_default` nutzen.
Zugriff nur über `with _database().transaction() as transaction:` mit **benannten**
Parametern. Kein rohes `_connect()`. (Diese drei Punkte haben in diesem Repo schon zweimal
Postgres-only-Bugs erzeugt — siehe `burn_in._as_utc_text`.)

**Rohdaten ab Tag 1:** der unveränderte JSON-Response (inkl. `question` und
`resolution_rules`) wird verlustfrei abgelegt — Muster von `core/raw_data_archive.py`
übernehmen (partitionierter Dateipfad + Metadatenzeile über den DB-Seam), aber als JSON
statt Parquet. Grund: Polymarket ändert Marktbeschreibungen; ohne Archiv ist jede spätere
Auswertung wertlos.

**Retention/Volumen:** bei 15-Minuten-Poll und ~50 beobachteten Märkten ≈ 4.800
Snapshot-Zeilen/Tag. Unkritisch für Postgres; die Rohdaten-JSONs bekommen dieselbe
Aufräumregel wie `raw_archive` (heute: keine — bewusst, wird bei PM-4 bewertet).

---

## 4. Phasen

Jede Phase ist ein eigener Branch `agent/PM-<n>` und ein eigener Handoff. Keine Phase
beginnt, bevor die vorige gemergt und reviewt ist.

### PM-0 — Datenschicht, read-only, wirkungslos

**Goal:** Polymarket-Marktdaten holen, bewerten und persistieren. **Kein Wiring in
irgendeinen Signal-, Order- oder Anzeigepfad.** Nach PM-0 verhält sich der Bot exakt wie
vorher.

- `polymarket.py`: Client gegen die **öffentlichen** Endpunkte — Marktdiscovery (Gamma),
  Buch/Midpoint/Preisverlauf (CLOB), Trades/Volumen (Data API). Die exakten Pfade **gegen
  `docs.polymarket.com` verifizieren**, nicht aus diesem Plan übernehmen. `requests` (schon
  Dependency, s. `market/universes.py`), explizite Timeouts, defensiver Umgang mit
  Ratelimits, keine Retries in Endlosschleife. WebSocket **nicht** in PM-0 (Polling reicht
  für Stunden-/Tages-Ereignisse — das ist die Simplicity-Entscheidung, kein Versehen).
- `polymarket_quality.py`: rein. Aus Bid/Ask → `mid`, `spread_bps`; Filter über Mindest-
  Tiefe, Mindest-24h-Volumen, Mindest-Trade-Anzahl, maximale Datenalterung, Restlaufzeit;
  Δp über mehrere Fenster (1 h / 6 h / 24 h) aus der Snapshot-Historie. Ausgabe:
  `EventState(usable: bool, probability: float, delta: dict, reject_code: str)`.
  Dünne Bücher, Einzeltrades und veraltete Daten ⇒ `usable=False`.
- DB-Schema + Alembic-Migration nach den Regeln aus §3.
- Poll-Job existiert als Funktion, ist aber **noch nicht registriert** (kein
  `run_repeating` in `bot.py`).

**Acceptance Criteria:**
1. `python -m pytest tests/test_polymarket*.py -q` grün, **ohne Netzwerkzugriff**
   (gespeicherte JSON-Fixtures; kein Test hängt an einer Sandbox-Netzsperre).
2. Kein Import von `polymarket*` in `market/`, `execution/`, `core/risk*.py`, `tgbot/`,
   `web/` — per Grep nachweisbar.
3. Neue Tabellen stehen in `SCHEMA_SQL` **und** in einer Alembic-Migration mit korrektem
   `down_revision`; `upgrade`/`downgrade` beide implementiert.
4. Qualitätsfilter lehnt ab: leeres Buch, Spread über Schwelle, Volumen unter Schwelle,
   Snapshot älter als Schwelle, Markt kurz vor Auflösung — je ein Test.
5. Ein manueller Smoke-Lauf gegen die echte API (außerhalb der Sandbox) holt ≥1 realen
   Markt und schreibt Snapshot + Rohdatei; Ergebnis wird berichtet, nicht behauptet.

**Validation:** `python -m pytest tests/test_polymarket*.py -q` + volle Suite
`python -m pytest -q` (Regressionsfreiheit) + der Smoke-Lauf aus AC 5.

### PM-1 — Mapping + Sichtbarkeit (Dashboard/Telegram), weiterhin ohne Trade-Wirkung

- `config/polymarket_mapping.yaml`: Liste von Einträgen
  `{condition_id, question_excerpt, tickers: [...], direction: adverse|supportive, weight,
  approved_by, approved_at, mapping_version}`. **Manuell befüllt**, Start mit 5–10 klar
  begründbaren Fällen (z. B. Zölle → importabhängige Titel). Ein Loader validiert das
  Schema hart und ignoriert unvollständige Einträge mit Log-Warnung.
- Poll-Job registrieren (`run_repeating`, Intervall 15 min, Default **AN** — er schreibt ja
  nur Daten).
- Dashboard-Panel „Ereignis-Sensor" (Muster: Mode-Report-Panel aus W3.4): Markt, aktuelle
  Wahrscheinlichkeit, Δ 1 h/24 h, Liquiditätsampel, Datenalter, verlinkte Ticker.
  Liquid-Glass-Komponenten aus `web/static/components.css` verwenden, kein neues Design.
- Telegram-Alarm bei Δp über Schwelle in einem liquiden, gemappten Markt (Muster:
  `core/alerts.py` + Outbox/ObservabilityConsumer). Rein informativ, **keine** Handelsaktion,
  keine Buttons.

**Acceptance Criteria:** Panel zeigt Live-Daten; Alarm feuert im Test bei simuliertem
Sprung und feuert **nicht** bei illiquidem Markt; Mapping-Loader lehnt fehlerhafte Einträge
ab; weiterhin kein Import in `execution/` oder `core/risk*.py`.

### PM-2 — Shadow-Vergleich (die eigentliche Entscheidungsgrundlage)

Nutzt die **vorhandene** Shadow-Infrastruktur (`research/shadow.py`,
`shadow_scheduler.py`, `shadow_snapshots`, Mode-Isolation aus W3.4) — es wird **kein
zweiter Bot** gebaut.

- Je Produktionssignal wird zusätzlich die Variante „mit Overlay" als Shadow-Beobachtung
  persistiert (eigene `strategy_version`, damit die Mode-Isolation die beiden sauber
  trennt).
- `polymarket_overlay.py` entsteht hier — rein, IO-frei, mit der geklemmten Invariante aus §2.
- Auswertung vergleicht über den bestehenden Report-Pfad: Rendite nach Kosten, maximaler
  Drawdown, Fehlalarme (Overlay dämpfte, Trade wäre gut gelaufen), unnötig blockierte
  Trades.

**Acceptance Criteria:** Invariantentests (`size_factor ≤ 1.0` immer; Overlay kann eine
`RiskDecision(ok=False)` nicht überstimmen; fehlende Polymarket-Daten ⇒ exakt neutrales
Ergebnis); Shadow-Zeilen beider Varianten in der DB unterscheidbar; Auswertung als
reproduzierbarer Report.

**Gate:** Läuft mindestens **6 Wochen Kalenderzeit** mit echten Signalen, bevor PM-3
überhaupt zur Debatte steht. Vorher gibt es schlicht nicht genug Ereignisse für eine
belastbare Aussage — bei ereignisgetriebenen Overlays sind das erfahrungsgemäß eine
zweistellige Zahl von Fällen, nicht hunderte.

### PM-3 — Overlay scharf im Paper-Modus (Tor, Freigabe durch den Nutzer)

Erst wenn PM-2 eine **stabile Out-of-Sample-Verbesserung** zeigt.

- Flag `POLYMARKET_OVERLAY_ENABLED` (Default **OFF**, Muster: `STRATEGY_EXITS_ENABLED`).
- Wirkung ausschließlich an zwei Punkten:
  1. **Sizing-Dämpfung:** `size_factor` wird auf das Kandidaten-Notional angewandt, *bevor*
     `risk.pretrade_check` läuft — der Risk Service prüft also weiterhin den finalen Wert
     und bleibt die letzte Instanz. `risk.py` selbst wird **nicht** angefasst (TSAFE-Pfad,
     rein und broker-frei).
  2. **Bestätigungspflicht:** `require_confirmation=True` unterdrückt `auto_accept` für
     dieses eine Signal und erzwingt den bestehenden Bestätigungsdialog (§18.1).
- **Ändert Live-Trade-Verhalten** ⇒ Deploy nur nach ausdrücklicher Freigabe.

### PM-4 — Betriebsreife (optional, erst bei Bedarf)

WebSocket-Stream statt Polling (nur wenn die 15-Minuten-Auflösung nachweislich zu grob
war), Retention/Aufräumjob für Rohdaten, Mapping-Pflege-UI. **Nicht vorab bauen.**

---

## 5. Failure Modes

| Fall | Verhalten |
|---|---|
| API nicht erreichbar / Timeout / Ratelimit | `neutral`, Warnung ins Log, kein Blocken, kein Verstärken |
| Buch dünn, Einzeltrade, alte Daten | `usable=False` ⇒ `neutral` |
| Markt kurz vor Auflösung (Preis läuft mechanisch gegen 0/1) | ausgeschlossen per Restlaufzeit-Filter |
| Mapping fehlt für den Ticker | `neutral` — kein Rateverhalten |
| Markttext/Auflösungsregel wurde geändert | Rohdatenarchiv macht es nachweisbar; Mapping-Eintrag wird ungültig, bis er neu freigegeben ist |
| Polymarket sperrt/ändert die öffentlichen Endpunkte | Sensor fällt neutral aus, Bot handelt unverändert weiter |
| Overlay-Bug | Klemmung `[0.25, 1.0]` + Invariantentest; schlimmster Fall ist eine zu kleine Position, nie eine zu große |

---

## 6. Was am 2026-08-13, 11:00 Uhr läuft

**PM-0 vollständig**, per Worker, auf Branch `agent/PM-0`. Bewusst nur PM-0: es ist die
einzige Phase ohne Designfragen und ohne jede Verhaltensänderung — sie kann ohne Rückfrage
durchlaufen. PM-1 braucht das manuell befüllte Mapping (Nutzerentscheidung), PM-2/PM-3 sind
gated.

Der Lauf endet mit einem Diff-Review durch den Lead. **Kein Merge nach `main`, kein Push,
kein Deploy** ohne den Nutzer.
