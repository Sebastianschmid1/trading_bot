"""Deklarative, IO-freie Alarmregeln und ein duennen Notifier-Adapter."""

from __future__ import annotations

import asyncio
import inspect
import operator
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


HEARTBEAT_MAX_SECONDS = 300.0
LATENCY_P95_MAX_SECONDS = 5.0
QUOTE_AGE_MAX_SECONDS = 60.0
REJECT_RATE_MAX = 0.30
REJECT_RATE_MIN_ORDERS = 10
BROKER_POLL_LAG_MAX_SECONDS = 120.0


@dataclass(frozen=True)
class AlertRule:
    metric: str
    comparator: str
    threshold: float
    severity: str
    window: str | None = None
    statistic: str = "value"
    min_count: int = 0

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError(f"Unbekannte Alarm-Schwere: {self.severity}")
        if self.comparator not in {">", ">=", "<", "<=", "==", "!="}:
            raise ValueError(f"Unbekannter Vergleich: {self.comparator}")


@dataclass(frozen=True)
class FiredAlert:
    rule: AlertRule
    value: float

    @property
    def key(self) -> tuple[str, str, float, str]:
        return (self.rule.metric, self.rule.statistic, self.rule.threshold, self.rule.severity)


DEFAULT_RULES = (
    AlertRule("stockbot_availability_heartbeat_age_seconds", ">", HEARTBEAT_MAX_SECONDS, "critical"),
    AlertRule("stockbot_feed_latency_seconds", ">", LATENCY_P95_MAX_SECONDS, "warning", statistic="p95"),
    AlertRule("stockbot_quote_age_seconds", ">", QUOTE_AGE_MAX_SECONDS, "critical", statistic="max"),
    AlertRule("stockbot_order_latency_seconds", ">", LATENCY_P95_MAX_SECONDS, "warning", statistic="p95"),
    AlertRule("stockbot_orders_total", ">", REJECT_RATE_MAX, "warning", window="window", statistic="reject_rate", min_count=REJECT_RATE_MIN_ORDERS),
    AlertRule("stockbot_reconciliation_findings_total", ">", 0.0, "critical", window="last_run", statistic="last_delta"),
    AlertRule("stockbot_broker_poll_lag_seconds", ">", BROKER_POLL_LAG_MAX_SECONDS, "critical"),
    AlertRule("stockbot_positions_without_stop", ">", 0.0, "critical"),
    AlertRule("stockbot_kill_switch_active", "==", 1.0, "info"),
)


_COMPARATORS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt,
    "<=": operator.le, "==": operator.eq, "!=": operator.ne,
}


def _counter_value(metric: Mapping[str, Any], **labels: str) -> float:
    for sample in metric.get("samples", ()):
        if sample.get("labels", {}) == labels:
            return float(sample.get("value", 0.0))
    return 0.0


def _percentile(sample: Mapping[str, Any], quantile: float) -> float:
    count = int(sample.get("count", 0))
    if count == 0:
        return 0.0
    rank = max(1, int(count * quantile + 0.999999))
    for upper_bound, bucket_count in sample.get("buckets", {}).items():
        if int(bucket_count) >= rank:
            return float(upper_bound)
    return float(sample.get("max", 0.0))


def _rule_value(metric: Mapping[str, Any], rule: AlertRule) -> tuple[float, int]:
    samples = metric.get("samples", ())
    if rule.statistic == "reject_rate":
        submitted = _counter_value(metric, outcome="submitted")
        rejected = _counter_value(metric, outcome="rejected")
        return (rejected / submitted if submitted else 0.0), int(submitted)
    if not samples:
        return 0.0, 0
    sample = samples[0]
    if rule.statistic == "p95":
        return _percentile(sample, 0.95), int(sample.get("count", 0))
    if rule.statistic == "max":
        return float(sample.get("max", 0.0)), int(sample.get("count", 0))
    if rule.statistic == "last_delta":
        return float(sample.get("last_delta", 0.0)), 1
    return float(sample.get("value", 0.0)), 1


def evaluate_alerts(
    snapshot: Mapping[str, Mapping[str, Any]], rules: Iterable[AlertRule] = DEFAULT_RULES,
) -> list[FiredAlert]:
    """Wertet einen bereits erhobenen Snapshot deterministisch und ohne IO aus."""
    fired: list[FiredAlert] = []
    for rule in rules:
        metric = snapshot.get(rule.metric)
        if metric is None:
            continue
        value, count = _rule_value(metric, rule)
        if count >= rule.min_count and _COMPARATORS[rule.comparator](value, rule.threshold):
            fired.append(FiredAlert(rule, value))
    return fired


class AlertNotifier:
    """Synchron aufrufbarer Adapter; meldet nur neu hinzugekommene Alarme."""

    def __init__(self, notifier: Callable[[str], Any]):
        self._notifier = notifier
        self._previous: set[tuple[str, str, float, str]] = set()

    def notify(self, alerts: Iterable[FiredAlert]) -> list[FiredAlert]:
        alerts = list(alerts)
        current = {alert.key for alert in alerts}
        new_alerts = [alert for alert in alerts if alert.key not in self._previous]
        self._previous = current
        if not new_alerts:
            return []
        message = "\n".join(
            f"[{alert.rule.severity}] {alert.rule.metric}: {alert.value:g} {alert.rule.comparator} {alert.rule.threshold:g}"
            for alert in new_alerts
        )
        result = self._notifier(message)
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)
        return new_alerts
