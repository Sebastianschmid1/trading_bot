"""Domänenobjekte (Phase 1 / PLAT-001, siehe docs/Plan.md §9.2, docs/PLAN_CHECKLIST.md Phase 1).

Reine, IO-freie Datencontainer für das Zielmodell aus Plan.md — noch NICHT an eine DB
gebunden (keine ORM-Mapper, kein Tabellenzugriff) und von KEINEM Live-Codepfad genutzt.
`stockbot/core/db.py` (SQLite) bleibt bis zum dokumentierten Cutover alleinige Quelle der
Wahrheit für Bot/Web/Telegram.

Zweck: die im Plan geforderten Entitäten als konkrete, typisierte Objekte festhalten, bevor
darauf die Zustandsmaschinen (nächste Checklisten-Punkte: Signal/Order/Position-Übergänge +
zentrale Validierung) und später die ORM-Persistenz aufsetzen. Felder folgen den in Plan.md
explizit genannten Listen (§9.2 Domänenobjekte, §9.4 Audit-Log, §11.1 RiskProfile, §12.1
TradeIntent); wo Plan.md keine Feldliste vorgibt (BrokerConnection, Strategy, StrategyVersion,
Signal, SignalCandidate, Order, OrderEvent, Fill, Position, PositionEvent, KillSwitch), sind
die Felder aus den zugehörigen Abschnitten (Zustandsmaschinen §9.3, OMS §12, Moduskennzeichnung
RES-002, Strategieversionierung STRAT-003, Kill-Switch-Service §11.5) sowie dem bestehenden
`DB-Migrationsmapping` in der Checkliste abgeleitet.

`RiskDecision` ist bereits als Phase-0-Seam in `stockbot/core/risk.py` definiert
(`pretrade_check`) — hier NICHT neu definiert, um keine zwei konkurrierenden Typen zu haben.
Phase 3 (RISK-002/RISK-003) erweitert dort um Sizing-Outputs und Prüf-Historie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── Moduskennzeichnung (RES-002) ─────────────────────────────────────────────

class Mode(str, Enum):
    BACKTEST = "backtest"
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


# ── Zustandsmaschinen (Plan.md §9.3) — Werte, keine Übergangslogik ──────────

class SignalStatus(str, Enum):
    GENERATED = "generated"
    FILTERED = "filtered"
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BLOCKED_BY_RISK = "blocked_by_risk"
    ORDER_CREATED = "order_created"


class OrderStatus(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACCEPTED_BY_BROKER = "accepted_by_broker"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(str, Enum):
    PENDING_OPEN = "pending_open"
    OPEN = "open"
    PENDING_CLOSE = "pending_close"
    CLOSED = "closed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


# ── Entitäten ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class User:
    """Identität + Präferenzen. Broker-Zugangsdaten wandern in `BrokerConnection`
    (DB-Migrationsmapping: "broker keys→broker_connections")."""
    user_id: int
    username: str | None = None
    market_region: str = "sp500"
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class RiskProfile:
    """Felder exakt nach Plan.md §11.1."""
    user_id: int
    account_risk_per_trade_pct: float = 0.25
    daily_loss_limit_pct: float = 1.00
    max_open_positions: int = 5
    max_position_pct: float = 100.0
    max_sector_exposure_pct: float = 100.0
    max_correlated_exposure_pct: float = 100.0
    max_daily_new_exposure_pct: float = 100.0
    max_spread_bps: float = 50.0
    max_quote_age_seconds: int = 60
    min_average_dollar_volume: float = 0.0
    earnings_blackout_days: int = 0
    allow_overnight: bool = True
    allowed_strategies: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrokerConnection:
    """Verschlüsselte Broker-Zugangsdaten je Nutzer (löst `users.broker_api_key/_secret`
    ab). `api_key`/`api_secret` bleiben verschlüsselt (wie bisher über `db.encrypt`)."""
    id: int | None
    user_id: int
    broker_platform: str
    api_key: bytes | None = None
    api_secret: bytes | None = None
    paper: bool = True
    connected_at: str | None = None
    revoked_at: str | None = None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Strategy:
    """Eine Strategiefamilie (max. 3 in V1, siehe STRAT-002)."""
    id: int | None
    key: str
    family: str          # "intraday_momentum" | "swing_trend" | "mean_reversion" | "research_only" | "deprecated"
    label: str
    description: str = ""


@dataclass(frozen=True)
class StrategyVersion:
    """Unveränderlich veröffentlichte Version einer Strategie (STRAT-003)."""
    id: int | None
    strategy_id: int
    version: int
    params: dict = field(default_factory=dict)
    feature_version: str = ""
    universe_version: str = ""
    entry_rules: str = ""
    exit_rules: str = ""
    cost_model: dict = field(default_factory=dict)
    release_status: str = "draft"   # draft|candidate|shadow|paper|live|archived
    code_commit: str = ""
    created_at: str | None = None


@dataclass(frozen=True)
class SignalCandidate:
    """Vorstufe eines `Signal` vor Filterung/Veröffentlichung (Portfolio-Allocator-Input,
    siehe STRAT-006)."""
    id: int | None
    strategy_version_id: int
    ticker: str
    direction: str
    raw_score: float
    mode: Mode
    generated_at: str | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True)
class Signal:
    """Moduskennzeichnung Pflicht (RES-002). `status` folgt der Zustandsmaschine in
    Plan.md §9.3. `data_*`-Felder decken Plan.md §10.3 „Datenherkunft je Berechnung speichern"
    (DATA-005) vollständig ab: Provider, Feed, Abrufzeit, Exchange-Zeit, Datenversion,
    Qualitätsstatus — siehe `stockbot/core/market_data.py::Quote` für dieselben Provider-/Feed-/
    Zeit-Felder auf Roh-Quote-Ebene (DATA-003)."""
    id: int | None
    strategy_version_id: int
    ticker: str
    direction: str
    mode: Mode
    status: SignalStatus = SignalStatus.GENERATED
    candidate_id: int | None = None
    data_provider: str | None = None
    data_feed: str | None = None
    data_fetched_at: str | None = None
    data_exchange_time: str | None = None
    data_version: str | None = None
    data_quality_status: str | None = None
    generated_at: str | None = None
    published_at: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class TradeIntent:
    """Felder exakt nach Plan.md §12.1 — einziges Artefakt, das Web/Telegram erzeugen."""
    user_id: int
    signal_id: int
    requested_action: str
    accepted_exit_policy: str
    source_channel: str
    created_at: str
    idempotency_key: str


@dataclass(frozen=True)
class Order:
    """`status` folgt der Zustandsmaschine in Plan.md §9.3."""
    id: int | None
    trade_intent_id: int
    user_id: int
    ticker: str
    side: str
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.CREATED
    broker_order_id: str | None = None
    client_order_id: str | None = None
    idempotency_key: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class OrderEvent:
    """Ein Brokerereignis (§12.5): accepted|rejected|partial_fill|fill|cancelled|expired|replaced."""
    id: int | None
    order_id: int
    event_type: str
    occurred_at: str
    broker_event_id: str | None = None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    id: int | None
    order_id: int
    qty: float
    price: float
    filled_at: str
    fee: float = 0.0
    broker_fill_id: str | None = None


@dataclass(frozen=True)
class Position:
    """`status` folgt der Zustandsmaschine in Plan.md §9.3."""
    id: int | None
    user_id: int
    ticker: str
    strategy_version_id: int
    mode: Mode
    status: PositionStatus = PositionStatus.PENDING_OPEN
    qty: float = 0.0
    avg_entry_price: float | None = None
    opened_at: str | None = None
    closed_at: str | None = None


@dataclass(frozen=True)
class PositionEvent:
    id: int | None
    position_id: int
    event_type: str
    from_status: str | None
    to_status: str
    occurred_at: str
    note: str | None = None


@dataclass(frozen=True)
class KillSwitch:
    """Backing-Modell für den Kill-Switch-Service (Plan.md §11.5). `scope="global"`
    ⇒ `user_id` ist `None`."""
    id: int | None
    scope: str            # "global" | "user"
    active: bool
    reason: str
    activated_by: str
    activated_at: str
    user_id: int | None = None
    deactivated_by: str | None = None
    deactivated_at: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """Felder exakt nach Plan.md §9.4. Append-only — kein Update/Delete auf diesem Typ."""
    event_id: str
    timestamp: str
    user_id: int | None
    actor: str
    entity_type: str
    entity_id: str
    action: str
    old_state: str | None
    new_state: str | None
    trace_id: str
    source_channel: str
    metadata: dict = field(default_factory=dict)
