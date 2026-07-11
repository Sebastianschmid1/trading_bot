# Inventar: direkte Broker-Aufrufe (TSAFE-006)

*Stand: 2026-07-11. Ziel: alle Stellen erfassen, an denen echte Broker-Aktionen ausgelöst werden,
als Grundlage für das zentrale OMS (Phase 4) — künftig darf KEIN Web-/Telegram-/Scheduler-Pfad mehr
direkt beim Broker ordern, sondern nur noch über TradeIntent → Risk → OMS.*

## 1. Broker-Adapter — einziger Ort mit echten Alpaca-SDK-Aufrufen

`stockbot/broker/client.py` kapselt sämtliche SDK-Aufrufe. Alle anderen Module rufen nur diese Funktionen.

| Funktion | SDK-Aufruf | Art | Phase-0-Gate |
|---|---|---|---|
| `submit_buy` | `client.submit_order` | **Einstieg** (Market/Limit, long) | Live-Kill-Switch (`_live_block_reason`) |
| `submit_option_buy` | `client.submit_order` | **Einstieg** (Optionskontrakt) | `ALLOW_OPTIONS`-Sperre + Live-Kill-Switch |
| `submit_exit_order` | `client.submit_order` | **Exit** (SELL/BUY-to-cover) | — (Schutz-Exit, bleibt erlaubt) |
| `close_position` | `client.close_position` | **Exit** (Position schließen) | — (Schutz-Exit, bleibt erlaubt) |
| `cancel_order` | `client.cancel_order_by_id` | Storno offener Order | — |
| `list_positions` / `get_position` / `position_exists` | `get_all_positions` / `get_open_position` | Lesen | — |
| `get_order_status` | `get_order_by_id` | Lesen | — |
| `account_summary` / `health_check` | `get_account` / `get_clock` | Lesen | — |

## 2. Aufrufer der Einstiegs-Order-Funktionen (müssen künftig über OMS)

**Telegram** — `stockbot/tgbot/bot.py`
- `bot.py:453` `broker.submit_option_buy` (Options-Einstieg) *— in V1 durch ALLOW_OPTIONS/Hebel-Deckel unerreichbar*
- `bot.py:459` / `:462` `broker.submit_buy` (ganze Aktien, Limit/Ext bzw. Market)
- `bot.py:478` `broker.submit_buy` (Bruchteil/Notional)
- Alle drei liegen in `_maybe_broker_order` (Order-Ausführung nach Signal-Annahme).

**Web** — `stockbot/web/webapp.py`
- `webapp.py:216` `broker.submit_option_buy`
- `webapp.py:219` / `:221` / `:229` `broker.submit_buy` (Limit/Ext, Market-qty, Notional)
- In `_execute_broker_order_for_web`.

## 3. Aufrufer der Exit-/Storno-Funktionen (Schutz-Exits — bleiben zunächst erlaubt)

- `bot.py:537`, `bot.py:555` `broker.close_position` (`_maybe_broker_close` / manuelles Verkaufen)
- `bot.py:1143` `broker.cancel_order`, `bot.py:1155` `broker.submit_exit_order` (Repricing stale Limit-Order im Monitor)
- `webapp.py:259` `broker.close_position` (Web „verkaufen")
- `webapp.py:697` `broker.close_position` (Web-Bulk/„alle schließen")

## 4. Scheduler/Hintergrund

- `stockbot/broker/reconcile.py:90/105/143/212` — `broker.list_positions` / `position_exists`
  (nur Lesen + Abgleich; schließt Positionen nicht selbst, meldet Drift).
- `close_and_evaluate` (Tagesjob) ruft Exits indirekt über `_maybe_broker_close`.

## 5. Bestehende Phase-0-Absicherungen

- **Live-Kill-Switch** (TSAFE-001): `submit_buy`/`submit_option_buy` lehnen Live-Orders serverseitig ab,
  solange `LIVE_TRADING_ENABLED` false; Broker läuft sonst erzwungen Paper.
- **Hebel-Deckel** (TSAFE-002): `_maybe_broker_order` (Telegram) und `_execute_broker_order_for_web`
  lehnen Orders mit Hebel > `MAX_LEVERAGE` ab; `db.set_leverage`/`set_trade_leverage` klemmen hart.
- **Optionsverbot** (TSAFE-003): `submit_option_buy` lehnt bei `ALLOW_OPTIONS=false` ab.
- **Budget** (TSAFE-004): Order überschreitet das Budget nie (kein Aufrunden auf ganze Aktien).

## 6. Nächster Schritt (Phase 4, OMS)

Einstiegs-Orders aus Telegram/Web durch **TradeIntent** ersetzen; die tatsächliche `submit_*`-Ausführung
wandert hinter das OMS (Idempotency, Zustandsmaschine, Broker-Event-Verarbeitung, Reconciliation).
Der zentrale Vorprüf-Seam dafür ist `stockbot/core/risk.py::pretrade_check` (TSAFE-007).
