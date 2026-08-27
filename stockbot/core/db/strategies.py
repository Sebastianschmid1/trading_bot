"""Strategie-Konfigurationen und Strategieversionen.

Zwei verschiedene Dinge mit ähnlichem Namen: Eine *Konfiguration* sind die im Web-Editor
pflegbaren, jederzeit änderbaren Parameter einer Strategie. Eine *Strategieversion* ist
davon getrennt und unveränderlich — ein Snapshot aus Inhaltshash und Code-Commit, der an
jedem erzeugten Signal hängt, damit später nachvollziehbar bleibt, welcher Stand es
erzeugt hat.
"""

import hashlib
import json

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# ── Strategie-Konfiguration (Web-Editor + Backtest/Live-Overrides) ────────────

def _strategy_config_to_dict(row) -> dict:
    params = {}
    try:
        params = json.loads(row["params_json"] or "{}") if row and row["params_json"] else {}
    except Exception:
        params = {}
    return {
        "key": row["key"],
        "label": row["label"],
        "description": row["description"],
        "params": params,
        "enabled": bool(row["enabled"]),
        "updated_at": row["updated_at"],
    }


def list_strategy_configs() -> list[dict]:
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT key, label, description, params_json, enabled, updated_at FROM strategy_configs ORDER BY key ASC"
        )
    return [_strategy_config_to_dict(r) for r in rows]


def get_strategy_config(key: str) -> dict | None:
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT key, label, description, params_json, enabled, updated_at "
            "FROM strategy_configs WHERE key = :key", {"key": key}
        )
    return _strategy_config_to_dict(row) if row else None


def upsert_strategy_config(key: str, label: str, description: str, params: dict | None = None,
                          enabled: bool = True) -> dict:
    params = params or {}
    updated_at = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO strategy_configs (key, label, description, params_json, enabled, updated_at)
               VALUES (:key, :label, :description, :params_json, :enabled, :updated_at)
               ON CONFLICT(key) DO UPDATE SET
                   label = excluded.label,
                   description = excluded.description,
                   params_json = excluded.params_json,
                   enabled = excluded.enabled,
                   updated_at = excluded.updated_at""",
            {"key": key, "label": label, "description": description,
             "params_json": json.dumps(params, default=str),
             "enabled": 1 if enabled else 0, "updated_at": updated_at},
        )
    row = get_strategy_config(key)
    return row or {"key": key, "label": label, "description": description, "params": params, "enabled": enabled}


def search_strategy_configs(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    rows = list_strategy_configs()
    if not q:
        return rows
    out = []
    for row in rows:
        blob = " ".join([
            row["key"], row["label"], row["description"],
            json.dumps(row.get("params") or {}, sort_keys=True),
        ]).lower()
        if q in blob:
            out.append(row)
    return out


# ── Strategieversionierung persistent (STRAT-003 / W3.3 → Gate P5) ───────────

_STRATEGY_VERSION_CACHE: dict[str, int] = {}


_STRATEGY_VERSIONS_BOOTSTRAPPED = False


def _current_code_commit() -> str:
    """Best-effort Code-Commit für die Strategie-Snapshots (aus ``CODE_COMMIT``, sonst 'unknown')."""
    import os
    return (os.getenv("CODE_COMMIT") or "unknown").strip() or "unknown"


def _strategy_content_hash(*parts) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def publish_strategy_version(strategy_key: str, snapshot) -> int:
    """Persistiert einen unveränderlichen ``StrategyVersion``-Snapshot append-only in
    ``strategy_versions``. Idempotent über (strategy_key, content_hash): identischer Inhalt liefert
    die bestehende id, geänderter Inhalt hängt die nächste fortlaufende Version an. Gibt die
    persistente id zurück (== ``Signal.strategy_version_id``)."""
    params_json = json.dumps(dict(snapshot.params or {}), sort_keys=True, default=str)
    cost_model_json = json.dumps(dict(snapshot.cost_model or {}), sort_keys=True, default=str)
    content_hash = _strategy_content_hash(
        strategy_key, snapshot.strategy_id, params_json, snapshot.feature_version,
        snapshot.universe_version, snapshot.entry_rules, snapshot.exit_rules,
        cost_model_json, snapshot.code_commit)
    with db._database().transaction() as transaction:
        existing = transaction.one(
            "SELECT id FROM strategy_versions WHERE strategy_key = :k AND content_hash = :h",
            {"k": strategy_key, "h": content_hash})
        if existing:
            return int(existing["id"])
        last = transaction.one(
            "SELECT MAX(version) AS v FROM strategy_versions WHERE strategy_key = :k",
            {"k": strategy_key})
        next_version = int(last["v"]) + 1 if last and last["v"] is not None else 1
        new_id = transaction.insert_id(
            """INSERT INTO strategy_versions (strategy_key, strategy_id, version, params_json,
                    feature_version, universe_version, entry_rules, exit_rules, cost_model_json,
                    release_status, code_commit, content_hash, created_at)
               VALUES (:strategy_key, :strategy_id, :version, :params_json, :feature_version,
                    :universe_version, :entry_rules, :exit_rules, :cost_model_json,
                    :release_status, :code_commit, :content_hash, :created_at)""",
            {"strategy_key": strategy_key, "strategy_id": int(snapshot.strategy_id),
             "version": next_version, "params_json": params_json,
             "feature_version": snapshot.feature_version,
             "universe_version": snapshot.universe_version,
             "entry_rules": snapshot.entry_rules, "exit_rules": snapshot.exit_rules,
             "cost_model_json": cost_model_json, "release_status": snapshot.release_status,
             "code_commit": snapshot.code_commit, "content_hash": content_hash,
             "created_at": db._utc_timestamp()})
    return int(new_id)


def get_strategy_version(version_id: int) -> dict | None:
    """Unveränderlicher Strategie-Snapshot zu einer persistenten id (oder None)."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM strategy_versions WHERE id = :id", {"id": version_id})
    if not row:
        return None
    return {
        "id": int(row["id"]), "strategy_key": row["strategy_key"],
        "strategy_id": int(row["strategy_id"]), "version": int(row["version"]),
        "params": json.loads(row["params_json"]),
        "feature_version": row["feature_version"], "universe_version": row["universe_version"],
        "entry_rules": row["entry_rules"], "exit_rules": row["exit_rules"],
        "cost_model": json.loads(row["cost_model_json"]),
        "release_status": row["release_status"], "code_commit": row["code_commit"],
        "created_at": row["created_at"],
    }


def _latest_strategy_version_id(strategy_key: str) -> int | None:
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id FROM strategy_versions WHERE strategy_key = :k "
            "ORDER BY version DESC LIMIT 1", {"k": strategy_key})
    return int(row["id"]) if row else None


def ensure_strategy_versions_published(code_commit: str | None = None) -> dict[str, int]:
    """Publiziert die produktiven V1-Strategien idempotent und liefert ``{strategy_key: id}``.
    Am Start (bot.main/dashboard.run) und lazy beim ersten Signal aufrufbar."""
    global _STRATEGY_VERSIONS_BOOTSTRAPPED
    from stockbot.core.strategy_registry import StrategyVersionRegistry
    from stockbot.market import strategies
    commit = code_commit or _current_code_commit()
    registry = StrategyVersionRegistry()
    mapping: dict[str, int] = {}
    for key, strat in strategies.REGISTRY.items():
        if not getattr(strat, "production", False):
            continue
        snapshot = registry.snapshot_from_registry(key, code_commit=commit)
        mapping[key] = publish_strategy_version(key, snapshot)
    _STRATEGY_VERSION_CACHE.update(mapping)
    _STRATEGY_VERSIONS_BOOTSTRAPPED = True
    return mapping


def resolve_strategy_version_id(strategy_key: str) -> int | None:
    """Persistente ``strategy_version_id`` für einen Strategie-Key (nur produktive Strategien).
    In-Prozess gecacht; bootet die Registry **höchstens einmal** lazy, falls die Tabelle noch leer
    ist. Nicht-produktive Keys → None (ohne den Bootstrap zu wiederholen — sonst würden bei einem
    abweichenden Default-Commit Duplikat-Versionen entstehen)."""
    if strategy_key in _STRATEGY_VERSION_CACHE:
        return _STRATEGY_VERSION_CACHE[strategy_key]
    version_id = _latest_strategy_version_id(strategy_key)
    if version_id is not None:
        _STRATEGY_VERSION_CACHE[strategy_key] = version_id
        return version_id
    if not _STRATEGY_VERSIONS_BOOTSTRAPPED:
        ensure_strategy_versions_published()
        return _STRATEGY_VERSION_CACHE.get(strategy_key)
    return None


def _with_strategy_version(signal: dict) -> dict:
    """Ergänzt ein Signal um seine persistente ``strategy_version_id`` (Gate P5). Bricht nie —
    bei Fehlern oder Nicht-Produktionsstrategien bleibt das Signal unverändert."""
    if signal.get("strategy_version_id"):
        return signal
    try:
        version_id = resolve_strategy_version_id(signal.get("strategy") or "standard")
    except Exception as e:
        db.log.warning(f"strategy_version_id nicht auflösbar: {e}")
        return signal
    if version_id is None:
        return signal
    return {**signal, "strategy_version_id": version_id}
