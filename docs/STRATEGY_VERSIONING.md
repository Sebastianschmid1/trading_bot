# Strategieversionierung (STRAT-003)

`stockbot.core.strategy_registry.StrategyVersionRegistry` ist der IO-freie Seam für
Strategieversionen. Er speichert veröffentlichte `StrategyVersion`-Snapshots append-only im
Prozess. Es gibt bewusst keine Update- oder Delete-API. Parameter und Kostenmodell werden beim
Publizieren rekursiv eingefroren; jede weitere Veröffentlichung derselben Strategie erhält eine
neue fortlaufende Versionsnummer.

Release-Promotions verändern den Snapshot nicht. Sie werden mit altem Status, neuem Status,
Zeitpunkt und verpflichtendem menschlichem `actor` in einer separaten append-only Historie
gespeichert. Erlaubt ist jeweils nur der nächste Schritt
`draft -> candidate -> shadow -> paper -> live`; `archived` ist von jedem aktiven Status aus
erreichbar und terminal. Die UI für die menschliche Freigabe ist nicht Bestandteil dieses Seams.

`Signal.strategy_version_id` bindet ein Signal an die `id` des Ergebnisses von `publish()`. Die
Verdrahtung in den Live-Signalerzeugungspfad folgt separat; aktuell demonstriert ein Test diese
Grenze, ohne einen noch nicht vorhandenen Persistenzpfad vorzutäuschen.

Die Registry ist nicht persistent und nicht prozessübergreifend konsistent. Persistente IDs,
Statusereignisse und konkurrierende Versionsvergabe folgen mit dem geplanten Postgres-Cutover.
