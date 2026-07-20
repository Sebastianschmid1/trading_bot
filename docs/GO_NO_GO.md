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
| 3.3 | Incident-Prozess beschrieben: wer schaltet den Kill-Switch, wie wird eskaliert |
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
