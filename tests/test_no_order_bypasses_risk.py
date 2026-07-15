import ast
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core.domain import Mode, Signal, SignalStatus, TradeIntent
from stockbot.core.risk import RiskDecision
from stockbot.execution.oms import OrderManagementSystem


SOURCE_ROOT = Path(__file__).parents[1] / "stockbot"

# Einziger Entry-Submit plus technische Broker-Adapter-Weiterleitungen. Der SELL-Aufruf ist
# ein Schutz-Exit und läuft laut Konzept §17.4 bewusst nicht durch pretrade_check.
ALLOWED_ORDER_CALLS = Counter({
    ("execution/oms.py", "self.broker.submit_buy"): 1,
    ("execution/broker_adapter.py", "broker_client.submit_buy"): 1,
    ("execution/broker_adapter.py", "broker_client.submit_exit_order"): 1,
})


def _attribute_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_production_order_calls_are_limited_to_reviewed_boundaries():
    found = Counter()
    locations = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"submit_buy", "submit_exit_order"}:
                continue
            qualified_name = _attribute_name(node.func)
            found[(relative, qualified_name)] += 1
            locations.append(f"{relative}:{node.lineno} ({qualified_name})")

    assert found == ALLOWED_ORDER_CALLS, (
        "Direkter Broker-Order-Aufruf außerhalb der geprüften OMS-/Schutz-Exit-Grenzen. "
        f"Gefunden: {', '.join(locations) or 'keine'}"
    )


def _signal():
    return Signal(
        id=17, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED, expires_at="2099-01-01T00:00:00+00:00",
    )


def _intent(key):
    return TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="test",
        created_at=datetime(2026, 7, 16, tzinfo=timezone.utc).isoformat(),
        idempotency_key=key,
    )


class _Persistence:
    def __init__(self):
        self.orders = {}

    def get_order_by_idempotency_key(self, key):
        return None

    def create_oms_order(self, intent, **kwargs):
        row = {
            "id": 1, "trade_intent_id": 1, "user_id": intent.user_id,
            "ticker": kwargs["ticker"], "side": "buy", "qty": kwargs["qty"],
            "notional": kwargs["notional"], "limit_price": kwargs["limit_price"],
            "status": "created", "broker_order_id": None, "client_order_id": "oms-1",
            "idempotency_key": intent.idempotency_key, "created_at": None, "updated_at": None,
        }
        self.orders[1] = row
        return row, True

    def transition_oms_order(self, order_id, *, to_status, **kwargs):
        self.orders[order_id]["status"] = to_status
        if kwargs.get("broker_order_id"):
            self.orders[order_id]["broker_order_id"] = kwargs["broker_order_id"]
        return self.orders[order_id]


class _Broker:
    def __init__(self):
        self.submissions = []

    def submit_buy(self, symbol, **kwargs):
        self.submissions.append((symbol, kwargs))
        return {"ok": True, "id": "paper-1", "status": "filled"}


def _service(broker, risk_checker):
    return OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), broker_adapter=broker,
        risk_checker=risk_checker,
        order_planner=lambda *args, **kwargs: {"kind": "shares", "qty": 1.0, "notional": 100.0},
        persistence=_Persistence(),
    )


def test_rejected_intent_never_reaches_broker():
    broker = _Broker()
    service = _service(
        broker,
        lambda **kwargs: RiskDecision(False, "Risk Service lehnt ab.", "risk_rejected"),
    )

    result = service.submit_intent(_intent("risk:reject"), price=100.0, trade_size=100.0)

    assert result.ok is False
    assert result.code == "risk_rejected"
    assert broker.submissions == []


def test_allowed_intent_reaches_broker_exactly_once():
    broker = _Broker()
    service = _service(broker, lambda **kwargs: RiskDecision(True))

    result = service.submit_intent(_intent("risk:allow"), price=100.0, trade_size=100.0)

    assert result.ok is True
    assert len(broker.submissions) == 1

