import asyncio

from stockbot.core.domain import Mode, Order, OrderStatus, Position, PositionStatus
from stockbot.core.post_trade_risk import scan_open_positions
from stockbot.execution.post_trade_scan import run_post_trade_scan


def _position(*, status=PositionStatus.OPEN, qty=2.0, ticker="AAPL"):
    return Position(
        id=1, user_id=42, ticker=ticker, strategy_version_id=1, mode=Mode.PAPER,
        status=status, qty=qty,
    )


def _order(*, side="sell", ticker="AAPL"):
    return Order(
        id=2, trade_intent_id=3, user_id=42, ticker=ticker, side=side,
        status=OrderStatus.SUBMITTED,
    )


def test_scan_protected_open_position_has_no_finding():
    assert scan_open_positions([_position()], [_order()]) == []


def test_scan_unprotected_open_position_has_finding():
    findings = scan_open_positions([_position()], [])
    assert [(item.user_id, item.ticker, item.decision.code) for item in findings] == [
        (42, "AAPL", "protective_order_missing")
    ]


def test_scan_ignores_closed_position():
    assert scan_open_positions([_position(status=PositionStatus.CLOSED)], []) == []


def test_scan_short_position_requires_buy_order():
    assert len(scan_open_positions([_position(qty=-2)], [_order(side="sell")])) == 1
    assert scan_open_positions([_position(qty=-2)], [_order(side="buy")]) == []


class FakePersistence:
    def __init__(self, *, protected: bool):
        self.protected = protected

    def get_post_trade_risk_rows(self):
        positions = [{
            "id": 1, "user_id": 42, "ticker": "AAPL", "direction": "long",
            "signal_json": "{}", "broker_filled_qty": 2.0, "entry": 100.0,
            "created_at": "2026-07-15 10:00:00",
        }]
        orders = []
        if self.protected:
            orders.append({
                "id": 2, "trade_intent_id": 3, "user_id": 42, "ticker": "AAPL",
                "side": "sell", "status": "submitted", "qty": 2.0,
            })
        return positions, orders


def test_job_sends_one_bundled_admin_message_for_finding():
    messages = []

    async def notifier(message):
        messages.append(message)

    previous = set()
    asyncio.run(run_post_trade_scan(
        persistence=FakePersistence(protected=False), notifier=notifier,
        previous_findings=previous,
    ))
    asyncio.run(run_post_trade_scan(
        persistence=FakePersistence(protected=False), notifier=notifier,
        previous_findings=previous,
    ))
    assert len(messages) == 1
    assert "AAPL" in messages[0]


def test_job_sends_no_message_when_all_positions_are_protected():
    messages = []

    async def notifier(message):
        messages.append(message)

    asyncio.run(run_post_trade_scan(
        persistence=FakePersistence(protected=True), notifier=notifier,
        previous_findings=set(),
    ))
    assert messages == []
