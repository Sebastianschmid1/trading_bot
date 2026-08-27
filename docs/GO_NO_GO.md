# Go/No-Go — Paper-Freigabe (Tor T5, Gate P10)

Diese Checkliste ist das **menschliche Tor** am Ende von Welle W8. Sie wird nicht
automatisch erfüllt: die Code-Belege sind messbar (unten), die Entscheidung ist es nicht.
Ohne abgezeichnete Liste bleibt Live gesperrt (TSAFE-001 unverändert).

## 1. Vorbedingungen (technisch, automatisch prüfbar)

| # | Punkt | Beleg |
|---|-------|-------|
| 1.1 | Unit-Tests grün | `python -m pytest` |
| 1.2 | Integrationstests Signal→Freigabe→Risk→Order grün | `tests/test_oms.py`, `tests/test_no_order_bypasses_risk.py` |
| 1.3 | Replay-Suite grün | `tests/test_replay_suite.py` |
| 1.4 | Failure-Injection-Suite grün | `tests/test_failure_injection.py` |
| 1.5 | Keine doppelten Orders in Wiederholungstests | `test_replay_of_the_same_event_sequence_is_idempotent`, `test_duplicate_intent_submission_reuses_the_first_order` |
| 1.6 | Keine unkontrollierte Order bei Feed-/Brokerfehler | `test_feed_offline_blocks_the_order…`, `test_broker_timeout…`, `test_stale_market_data…` |
| 1.7 | Kill-Switch in Integrationstests bestätigt | `tests/test_kill_switch.py`, `tests/test_kill_switch_persist.py` |
| 1.8 | Backup **und Restore** verifiziert | `pg-backup.timer` auf dem VPS + `pg_restore --list` gegen ein echtes Backup |

## 2. Burn-in-Kennzahlen (Kalenderzeit)

Erhoben mit `stockbot.core.burn_in.build_burn_in_report(since, until)` über den
Burn-in-Zeitraum; Ausgabe via `format_burn_in_report`.

| # | Kriterium | Sollwert |
|---|-----------|----------|
| 2.1 | Zeitraum | mehrere Marktwochen, **≥ 1 Feiertag/Half-Day** (Labor Day 2026-09-07 als natürlicher Kandidat) |
| 2.2 | `duplicate_orders` | **0** |
| 2.3 | `duplicate_broker_events` | **0** |
| 2.4 | `dead_letter_events` | **0** (jedes Dead-Letter einzeln geklärt und dokumentiert) |
| 2.5 | `reconciliation_findings` | **0** ungeklärt |
| 2.6 | `error_rate` | dokumentiert, jede Ablehnungsursache erklärbar (Risk-Block ist kein Fehler) |
| 2.7 | Budgetüberschreitung | keine — Positionsgröße nie über dem konfigurierten Budget |
| 2.8 | Regime-Abdeckung | mind. eine ruhige und eine volatile Phase im Zeitraum |

`report.clean == True` fasst 2.2–2.5 zusammen. **`clean` allein ist kein Go** —
2.1/2.6–2.8 sind menschlich zu beurteilen.

## 3. Betrieb

| # | Punkt |
|---|-------|
| 3.1 | Kein Dienst läuft als Root-Anwendung mit Broker-Vollzugriff; Secrets nicht im Repo |
| 3.2 | Alarme (W4.2) haben im Burn-in nachweislich gefeuert — mindestens einmal getestet |
| 3.3 | Incident-Prozess beschrieben: wer schaltet den Kill-Switch, wie wird eskaliert — [RUNBOOK.md](RUNBOOK.md) |
| 3.4 | Nutzer kann den Brokerzugriff sofort widerrufen (Einstellungen → Verbindung entfernen) |

## 4. Entscheidung

```
Zeitraum Burn-in:      ____________  bis  ____________
Report (clean):        [ ] ja   [ ] nein
Offene Befunde:        _________________________________________________
Entscheidung:          [ ] GO (Paper bleibt, Live bleibt gesperrt)
                       [ ] NO-GO — Begründung: ______________________
Abgezeichnet von:      ____________________   Datum: ____________
```

**Wichtig:** Ein „GO" hier gibt **nicht** Live frei. Live ist eine separate Entscheidung
(Phase 11, Canary) mit eigener regulatorischer Einordnung.

---

## Stand 2026-08-27 — ausgefüllt vom Lead, **nicht** abgezeichnet

Erhoben gegen die Produktions-DB und die laufende Suite. Die Unterschrift bleibt beim
Betreiber; das hier ist die Vorlage, nicht die Entscheidung.

### 1. Vorbedingungen — **alle erfüllt**

| # | Ergebnis |
|---|---|
| 1.1 | ✅ 1.554 passed, 29 skipped |
| 1.2 | ✅ `test_oms.py` + `test_no_order_bypasses_risk.py` grün |
| 1.3 | ✅ `test_replay_suite.py` grün |
| 1.4 | ✅ `test_failure_injection.py` grün |
| 1.5 | ✅ Idempotenz-/Doppelklick-Tests grün |
| 1.6 | ✅ Feed-offline-/Broker-Timeout-/Stale-Quote-Tests grün |
| 1.7 | ✅ `test_kill_switch.py` + `test_kill_switch_persist.py` grün |
| 1.8 | ✅ `pg-backup.timer` aktiv (letzter Lauf 27.08. 03:33 UTC), `pg_restore --list` liest 195 Einträge aus dem heutigen Dump |

Die 29 Skips sind Postgres-Contract-Tests ohne lokalen Postgres — am VPS separat real
gefahren (47 passed). Ein Skip ist kein Grün, deshalb steht es hier.

### 2. Burn-in-Kennzahlen (2026-07-21 … 2026-08-27)

| # | Sollwert | Ist | |
|---|---|---|---|
| 2.1 | ≥ 1 Feiertag/Half-Day | **keiner im Zeitraum** | ❌ |
| 2.2 | `duplicate_orders` = 0 | 0 | ✅ |
| 2.3 | `duplicate_broker_events` = 0 | 0 | ✅ |
| 2.4 | `dead_letter_events` = 0 | 0 | ✅ |
| 2.5 | `reconciliation_findings` = 0 | 0 | ✅ |
| 2.6 | Fehlerquote dokumentiert, jede Ursache erklärbar | siehe unten | ⚠️ |
| 2.7 | keine Budgetüberschreitung | `max_position_pct = 100`, Hebel per TSAFE-002 gesperrt | ✅ |
| 2.8 | ruhige **und** volatile Phase | nicht ausgewertet | ❓ |

**Zu 2.6 — hier ist der Bericht selbst das Problem (Befund 17).** Die automatische Kennzahl
meldet „44 eingereicht, 0 abgelehnt, 0,00 %, Gate P10 sauber", weil sie nur die
`orders`-Tabelle liest. Im selben Fenster stehen **524** Trades auf `broker_failed`:

- **331 `submit_failed`** — echte Einreichungsfehler, aber ausschließlich zwischen dem
  21.07. und dem **10.08.**; seither keiner mehr.
- **134 `spread_wide`**, **40 `max_positions_reached`**, **19 `quote_stale`** — Risiko-Blocks,
  laut Kriterium 2.6 ausdrücklich keine Fehler.

Damit ist die Ursache jeder Ablehnung erklärbar — aber **nicht** durch den erzeugten Bericht.
Vor einer Abzeichnung gehört entschieden, ob die Kennzahl `trades.broker_status` mitlesen soll.

### 3. Betrieb

| # | Ist | |
|---|---|---|
| 3.1 | Dienst läuft als **root** (= offenes Tor T1) | ❌ |
| 3.2 | Alarmpfad erst am 27.08. verdrahtet, hat im Burn-in nie gefeuert | ❌ |
| 3.3 | `docs/RUNBOOK.md` existiert | ✅ |

### Was einer Abzeichnung heute im Weg steht

1. **2.1** — kein Feiertag/Half-Day im Zeitraum. Der natürliche Kandidat ist **Labor Day,
   Montag 2026-09-07**; danach ist das Kriterium ohne weiteres Zutun erfüllt.
2. **3.1** — Tor T1 (Dienst als eigener Nutzer statt root).
3. **3.2** — der Alarmpfad muss mindestens einmal nachweislich ausgelöst haben.
4. **2.6** — Entscheidung, ob der Bericht `broker_status` einbezieht (Befund 17).

2.2 bis 2.5 und der gesamte Abschnitt 1 sind sauber. Der Burn-in läuft weiter; nach dem
07.09. ist 2.1 zu, und dann hängt T5 nur noch an den betrieblichen Punkten.
