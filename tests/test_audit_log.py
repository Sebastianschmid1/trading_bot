"""
Tests für den Audit-Log Store (PLAT-002, stockbot/core/audit_log.py, Plan.md §9.4).
"""

from stockbot.core.audit_log import AuditLog
from stockbot.core.domain import AuditEvent


def test_record_builds_event_with_generated_id_and_timestamp():
    log = AuditLog()
    event = log.record(
        actor="risk_service",
        entity_type="order",
        entity_id="42",
        action="reject",
        old_state="validated",
        new_state="rejected",
        trace_id="trace-1",
        source_channel="web",
        user_id=7,
        metadata={"reason": "max_leverage"},
    )
    assert isinstance(event, AuditEvent)
    assert event.event_id
    assert event.timestamp
    assert event.user_id == 7
    assert event.metadata == {"reason": "max_leverage"}


def test_record_appends_to_events_in_order():
    log = AuditLog()
    first = log.record(
        actor="a", entity_type="order", entity_id="1", action="create",
        old_state=None, new_state="created", trace_id="t1", source_channel="telegram",
    )
    second = log.record(
        actor="a", entity_type="order", entity_id="1", action="validate",
        old_state="created", new_state="validated", trace_id="t1", source_channel="telegram",
    )
    assert log.events == (first, second)
    assert len(log) == 2


def test_append_accepts_a_preconstructed_event():
    log = AuditLog()
    event = AuditEvent(
        event_id="fixed-id", timestamp="2026-07-12T00:00:00+00:00", user_id=None,
        actor="system", entity_type="kill_switch", entity_id="global", action="activate",
        old_state="inactive", new_state="active", trace_id="t2", source_channel="admin",
    )
    assert log.append(event) is event
    assert log.events == (event,)


def test_events_for_entity_filters_by_type_and_id():
    log = AuditLog()
    log.record(
        actor="a", entity_type="order", entity_id="1", action="create",
        old_state=None, new_state="created", trace_id="t1", source_channel="web",
    )
    log.record(
        actor="a", entity_type="order", entity_id="2", action="create",
        old_state=None, new_state="created", trace_id="t3", source_channel="web",
    )
    matched = log.events_for_entity("order", "1")
    assert len(matched) == 1
    assert matched[0].entity_id == "1"


def test_events_property_returns_a_copy_not_the_live_list():
    log = AuditLog()
    log.record(
        actor="a", entity_type="order", entity_id="1", action="create",
        old_state=None, new_state="created", trace_id="t1", source_channel="web",
    )
    snapshot = log.events
    log.record(
        actor="a", entity_type="order", entity_id="1", action="validate",
        old_state="created", new_state="validated", trace_id="t1", source_channel="web",
    )
    assert len(snapshot) == 1
    assert len(log.events) == 2


def test_audit_log_exposes_no_update_or_delete_method():
    log = AuditLog()
    assert not hasattr(log, "update")
    assert not hasattr(log, "delete")
    assert not hasattr(log, "remove")
