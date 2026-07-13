# Strategie-Klassifizierung und V1-Auswahl (STRAT-002)

*Stand: 2026-07-13. Grundlage: `docs/STRATEGY_INVENTORY.md` und die verbindliche
Owner-Entscheidung für Plan §13.2/§13.3.*

## Verbindliche V1-Auswahl

V1 führt genau eine produktiv neu wählbare Strategie pro Familie:

| Key | Familie | Produktiv | Begründung |
|---|---|---:|---|
| `standard` | `intraday_momentum` | ja | Multi-Timeframe-Momentum-Benchmark für die Intraday-Familie. |
| `adx_trend` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `rsi_revert` | `research_only` | nein | Research-only; `bb_revert` vertritt die Mean-Reversion-Familie mit mehr Backtest-Trades. |
| `breakout` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `ma_trend` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `high52` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `high52_wide` | `research_only` | nein | Research-only als eng verwandte Variante von `high52`, nicht als zusätzliche V1-Familie. |
| `tsmom` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `lowvol` | `research_only` | nein | Research-only; die defensive Faktorhypothese erhält in V1 keine eigene Produktionsfamilie. |
| `faber` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `streversal` | `research_only` | nein | Research-only; `bb_revert` ist die ausgewählte Mean-Reversion-Strategie. |
| `frog` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `bb_revert` | `mean_reversion` | ja | Klare Bollinger-%B-Rücklaufhypothese und die meisten Backtest-Trades der Mean-Reversion-Kandidaten. |
| `adx_mfi` | `research_only` | nein | Research-only, um maximal drei produktive Familien gemäß Plan §13.3 einzuhalten. |
| `supertrend` | `research_only` | nein | Bleibt Code-Basis und feste Referenz-/Seed-Implementierung von `ai_adaptive`, wird aber nicht mehr separat als Produktionsstrategie geführt. |
| `ai_adaptive` | `swing_trend` | ja | SuperTrend-geseedete Swing-Trend-Strategie mit kontrolliertem, walk-forward geprüftem Lab-Loop. |

Keine Registry-Strategie wird in STRAT-002 als `deprecated` eingestuft. Die 13 nicht produktiven
Keys bleiben im Backtest- und Strategie-Labor verfügbar.

## Übergang für bestehende Nutzer

Telegram- und Web-Einstellungen bieten für neue Live-/Paper-Auswahlen nur die drei produktiven
Keys an. Derselbe serverseitige Settings-Service lehnt manipulierte Requests und den
`/addstrat`-Befehl ab, wenn sie eine bislang nicht aktive Research-only-Strategie hinzufügen
wollen.

Bereits gespeicherte Research-only-Keys werden bewusst weder migriert noch beim Laden oder bei der
Signalerzeugung herausgefiltert. Bestandsnutzer laufen deshalb ohne stillen Strategiewechsel weiter
und können einen solchen Key weiterhin ausdrücklich per `/addstrat <key>` entfernen. Diese
Grandfathering-Regel ist eine bewusste Übergangslücke; eine spätere Zwangsmigration wäre ein eigener,
größerer Produktschritt.
