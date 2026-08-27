"""Tests für `core/corporate_action_guard.py` (OBS-CORPACT) — reine Auswertung + Dedup-State.

Der Broker-Abruf und die Symbolauswahl (offene Positionen + Watchlist) sind IO-behaftet und
werden zusammen mit dem Scheduler-Job in tests/test_intraday.py geprüft.
"""

from datetime import datetime, timezone

from stockbot.core.corporate_action_guard import CorporateActionFinding, CorporateActionNotifier, \
    find_corporate_actions
from stockbot.core.market_data import CorporateAction

SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_find_corporate_actions_reports_split_since_the_given_date():
    actions = [
        CorporateAction(ticker="KHC", action_type="split",
                        ex_date=datetime(2026, 8, 10, tzinfo=timezone.utc), value=3.0),
    ]
    findings = find_corporate_actions({"KHC": actions}, since_by_ticker={"KHC": SINCE})
    assert findings == [
        CorporateActionFinding("KHC", "split", datetime(2026, 8, 10, tzinfo=timezone.utc)),
    ]


def test_find_corporate_actions_ignores_dividends():
    actions = [
        CorporateAction(ticker="AAPL", action_type="dividend",
                        ex_date=datetime(2026, 8, 10, tzinfo=timezone.utc), value=0.25),
    ]
    assert find_corporate_actions({"AAPL": actions}, since_by_ticker={"AAPL": SINCE}) == []


def test_find_corporate_actions_ignores_actions_before_since():
    actions = [
        CorporateAction(ticker="MSFT", action_type="split",
                        ex_date=datetime(2026, 7, 1, tzinfo=timezone.utc), value=2.0),
    ]
    assert find_corporate_actions({"MSFT": actions}, since_by_ticker={"MSFT": SINCE}) == []


def test_find_corporate_actions_handles_empty_and_multiple_tickers():
    actions_by_ticker = {
        "NVDA": [],
        "KHC": [CorporateAction(ticker="KHC", action_type="reverse_split",
                                ex_date=datetime(2026, 8, 15, tzinfo=timezone.utc), value=0.1)],
    }
    findings = find_corporate_actions(
        actions_by_ticker, since_by_ticker={"NVDA": SINCE, "KHC": SINCE})
    assert len(findings) == 1
    assert findings[0].ticker == "KHC" and findings[0].action_type == "reverse_split"


def test_notifier_reports_a_finding_only_once():
    finding = CorporateActionFinding("KHC", "split", datetime(2026, 8, 10, tzinfo=timezone.utc))
    notifier = CorporateActionNotifier()

    assert notifier.new_findings([finding]) == [finding]
    # derselbe Fund im nächsten Lauf: nicht erneut gemeldet.
    assert notifier.new_findings([finding]) == []


def test_notifier_reports_a_new_finding_for_a_different_ticker():
    first = CorporateActionFinding("KHC", "split", datetime(2026, 8, 10, tzinfo=timezone.utc))
    second = CorporateActionFinding("NVDA", "split", datetime(2026, 8, 11, tzinfo=timezone.utc))
    notifier = CorporateActionNotifier()

    assert notifier.new_findings([first]) == [first]
    assert notifier.new_findings([first, second]) == [second]


def test_format_message_names_ticker_type_and_date():
    finding = CorporateActionFinding("KHC", "split", datetime(2026, 8, 10, tzinfo=timezone.utc))
    message = CorporateActionNotifier.format_message([finding])
    assert "KHC" in message and "split" in message and "2026-08-10" in message
