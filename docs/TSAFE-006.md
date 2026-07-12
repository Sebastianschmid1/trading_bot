# TSAFE-006: Inventarisierung direkter Brokeraufrufe

*Stand: 2026-07-11. Grundlage für die Umstellung aller Einstiege auf die OMS-Pipeline (TSAFE-007).*

## Zusammenfassung

Derzeit existieren **zwei parallele Pfade** für Broker-Orders:

1. **OMS-Pipeline** (`stockbot/execution/oms.py`) — der geplante zentrale Orderpfad mit Idempotency,
   Risk-Service, Zustandsmaschine und Audit.
2. **Direkte Broker-Aufrufe** in `tgbot/bot.py` und `web/webapp.py` — die heutigen Pfade, die
   den Broker direkt aufrufen und dabei den gleichen `_live_block_reason`-Schutz nutzen (aber
   **keine** OMS-Idempotency, kein vollständiges Audit, keine Zustandsmaschine).

**Ziel:** Alle Einstiegs-Orders (accept_trade) müssen durch die OMS-Pipeline.
Schutz-Exits (Verkauf/Schließen) können den direkten Pfad weiter nutzen — sie sind keine
Einstiege und unterliegen keiner TSAFE-Blockierung.

## Inventar aller Broker-Call-Sites

### 1. submit_buy (Einstieg, Long)

| Ort | Zeile(n) | Aufrufer | OMS? | Live-Block |
|---|---|---|---|---|
| `oms.py:159` | OMS submit_intent | OMS | ✅ Ja | ✅ Ja (via `_live_block_reason` in client.py) |
| `bot.py:462,465,481` | `_maybe_broker_order` | Telegram (Auto-Accept) | ❌ Nein | ✅ Ja |
| `webapp.py:219,221,229` | `/accept` (Web) | Web-App | ❌ Nein | ✅ Ja |

### 2. submit_exit_order (Schutz-Exit)

| Ort | Zeile | Aufrufer | OMS? | Live-Block |
|---|---|---|---|---|
| `bot.py:1166` | `_handle_accept_sell` | Telegram (Sell-Callback) | ❌ Nein | ⚠️ **Nein** — keine Prüfung |

**Hinweis:** `submit_exit_order` hat keinen `_live_block_reason`-Check. Das ist bewusst so, denn
Schutz-Exits sollen auch bei deaktiviertem Live-Trading erlaubt sein (Plan §17.4). Allerdings
sollte geprüft werden, ob ein Exit wirklich erlaubt ist (z. B. bei deaktiviertem Broker oder
aktivem Kill-Switch).

### 3. close_position (Schließen der ganzen Position)

| Ort | Zeile | Aufrufer | OMS? | Live-Block |
|---|---|---|---|---|
| `bot.py:540` | `sell_callback` | Telegram (Verkaufen-Button) | ❌ Nein | ⚠️ Nein |
| `bot.py:558` | `sell_callback` | Telegram (Verkaufen-Button) | ❌ Nein | ⚠️ Nein |
| `webapp.py:259` | `/sell` (Web) | Web-App | ❌ Nein | ⚠️ Nein |
| `webapp.py:709` | Dashboard-Export | Web-App | ❌ Nein | ⚠️ Nein |

### 4. submit_option_buy (Options-Einstieg)

| Ort | Zeile | Aufrufer | OMS? | Live-Block |
|---|---|---|---|---|
| `bot.py:456` | `_maybe_broker_order` | Telegram (Auto-Accept) | ❌ Nein | ✅ Ja |
| `webapp.py:216` | `/accept` (Web) | Web-App | ❌ Nein | ✅ Ja |

### 5. Cancel- und Status-Operationen (Lesen/Nicht-Einstieg)

| Ort | Zeile | Operation | OMS? |
|---|---|---|---|
| `oms.py:176` | `get_order_status` | OMS | ✅ Ja |
| `bot.py:352,1077,1118` | `get_order_status` | Telegram | ❌ Nein (nur Lesen) |
| `bot.py:1154` | `cancel_order` | Telegram (Sell-Callback) | ❌ Nein |
| `webapp.py:237,270` | `get_order_status` | Web-App | ❌ Nein (nur Lesen) |

## Bewertung

- **Einstiegs-Orders** (submit_buy, submit_option_buy): Alle sind serverseitig gegen Live-Handel
  geschützt (TSAFE-001). Aber sie umgehen die OMS-Pipeline (TSAFE-007).
- **Schutz-Exits** (submit_exit_order, close_position): Keine Live-Blockade. Dies ist beabsichtigt
  (Schutz-Exits bleiben auch bei deaktiviertem Live-Trading erlaubt). Es gibt jedoch keine
  zentrale Kontrolle, welche Exit-Operationen erlaubt sind.
- **Lesende Calls** (get_order_status, cancel_order): Keine Order-Ausführung, daher unkritisch.

## Nächste Schritte

1. **TSAFE-007** (Block direct order execution): Web und Telegram auf `OMS.submit_intent` umstellen.
   `trade_svc.accept_trade` bleibt als zustandsübergreifender Befehl, aber die tatsächliche
   Broker-Ausführung läuft über den OMS.
2. **TSAFE-005** (Score-Exit): Bereits umgesetzt (TAFE-005 in bot.py:evaluate_active_trade).
3. **Exit-Governance**: Optional — Schutz-Exits könnten über den OMS gelaufen werden (mit
   gesonderter Erlaubnis), statt direkt aufgerufen zu werden.
