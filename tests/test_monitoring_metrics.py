from concurrent.futures import ThreadPoolExecutor

import pytest

from stockbot.core.alerts import AlertNotifier, DEFAULT_RULES, evaluate_alerts
from stockbot.core.metrics import Counter, Gauge, Histogram, Registry


def test_counter_is_thread_safe_and_rejects_negative_values():
    counter = Counter("stockbot_test_total", label_names=("outcome",))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: counter.inc(outcome="submitted"), range(2000)))
    assert counter.snapshot() == [{
        "labels": {"outcome": "submitted"}, "value": 2000.0, "last_delta": 1.0,
    }]
    with pytest.raises(ValueError):
        counter.inc(-1, outcome="submitted")


def test_gauge_set_and_increment_are_thread_safe():
    gauge = Gauge("stockbot_test_value")
    gauge.set(4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: gauge.inc(), range(100)))
    assert gauge.snapshot()[0]["value"] == 104


def test_histogram_uses_cumulative_latency_buckets():
    histogram = Histogram("stockbot_test_latency_seconds", buckets=(0.1, 0.5, 1.0))
    for value in (0.05, 0.2, 2.0):
        histogram.observe(value)
    sample = histogram.snapshot()[0]
    assert sample == {
        "labels": {}, "count": 3, "sum": 2.25, "max": 2.0,
        "buckets": {0.1: 1, 0.5: 2, 1.0: 2},
    }


def test_registry_snapshot_and_render_do_not_expose_high_cardinality_labels():
    registry = Registry()
    orders = registry.register(Counter("stockbot_orders_total", label_names=("outcome",)))
    orders.inc(outcome="rejected")
    snapshot = registry.snapshot()
    assert snapshot["stockbot_orders_total"]["samples"][0]["labels"] == {"outcome": "rejected"}
    rendered = registry.render()
    assert "user_id" not in rendered
    assert "ticker" not in rendered


def _snapshot_for(rule, *, over: bool):
    threshold = rule.threshold
    ordinary = threshold + 1 if over else threshold
    if rule.comparator == "==":
        ordinary = threshold if over else threshold - 1
    if rule.statistic == "p95":
        count = 20
        below_limit_count = 0 if over else 20
        return {rule.metric: {"type": "histogram", "samples": [{
            "labels": {}, "count": count, "sum": ordinary * count, "max": ordinary,
            "buckets": {5.0: below_limit_count, 10.0: count},
        }]}}
    if rule.statistic == "max":
        return {rule.metric: {"type": "histogram", "samples": [{
            "labels": {}, "count": 1, "sum": ordinary, "max": ordinary, "buckets": {},
        }]}}
    if rule.statistic == "reject_rate":
        rejected = 4 if over else 3
        return {rule.metric: {"type": "counter", "samples": [
            {"labels": {"outcome": "submitted"}, "value": 10},
            {"labels": {"outcome": "rejected"}, "value": rejected},
        ]}}
    return {rule.metric: {"type": "gauge", "samples": [{
        "labels": {}, "value": ordinary, "last_delta": ordinary,
    }]}}


@pytest.mark.parametrize("rule", DEFAULT_RULES)
def test_each_default_alert_fires_only_beyond_its_threshold(rule):
    assert evaluate_alerts(_snapshot_for(rule, over=True), [rule])
    assert evaluate_alerts(_snapshot_for(rule, over=False), [rule]) == []


def test_reject_rate_requires_ten_submitted_orders():
    rule = next(rule for rule in DEFAULT_RULES if rule.statistic == "reject_rate")
    snapshot = {rule.metric: {"type": "counter", "samples": [
        {"labels": {"outcome": "submitted"}, "value": 9},
        {"labels": {"outcome": "rejected"}, "value": 9},
    ]}}
    assert evaluate_alerts(snapshot, [rule]) == []


def test_notifier_deduplicates_and_supports_async_callable():
    messages = []

    async def notifier(message):
        messages.append(message)

    rule = DEFAULT_RULES[0]
    alert = evaluate_alerts(_snapshot_for(rule, over=True), [rule])
    adapter = AlertNotifier(notifier)
    assert len(adapter.notify(alert)) == 1
    assert adapter.notify(alert) == []
    assert len(messages) == 1

    adapter.notify([])
    assert len(adapter.notify(alert)) == 1
    assert len(messages) == 2
