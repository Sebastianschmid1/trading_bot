"""
Tests für den Kill-Switch-Service (RISK-006, stockbot/core/kill_switch.py, Plan.md §11.5).
"""

from stockbot.core.kill_switch import KillSwitchService


def test_new_position_allowed_by_default():
    svc = KillSwitchService()
    assert svc.is_new_position_allowed() is True
    assert svc.is_new_position_allowed(user_id=1) is True


def test_activate_global_blocks_everyone():
    svc = KillSwitchService()
    svc.activate_global(reason="Marktturbulenz", activated_by="admin:1")
    assert svc.is_new_position_allowed() is False
    assert svc.is_new_position_allowed(user_id=1) is False
    assert svc.is_new_position_allowed(user_id=2) is False


def test_deactivate_global_restores_access():
    svc = KillSwitchService()
    svc.activate_global(reason="Test", activated_by="admin:1")
    ks = svc.deactivate_global(deactivated_by="admin:1")
    assert ks is not None and ks.active is False
    assert svc.is_new_position_allowed() is True


def test_deactivate_global_returns_none_when_not_active():
    svc = KillSwitchService()
    assert svc.deactivate_global(deactivated_by="admin:1") is None


def test_activate_user_blocks_only_that_user():
    svc = KillSwitchService()
    svc.activate_user(1, reason="Nutzeranfrage", activated_by="user:1")
    assert svc.is_new_position_allowed(user_id=1) is False
    assert svc.is_new_position_allowed(user_id=2) is True
    assert svc.is_new_position_allowed() is True   # kein user_id -> nur globaler Switch zählt


def test_deactivate_user_restores_access():
    svc = KillSwitchService()
    svc.activate_user(1, reason="Test", activated_by="user:1")
    ks = svc.deactivate_user(1, deactivated_by="user:1")
    assert ks is not None and ks.active is False
    assert svc.is_new_position_allowed(user_id=1) is True


def test_deactivate_user_returns_none_when_not_active():
    svc = KillSwitchService()
    assert svc.deactivate_user(1, deactivated_by="user:1") is None


def test_global_and_user_switch_combine():
    svc = KillSwitchService()
    svc.activate_global(reason="global", activated_by="admin:1")
    svc.deactivate_global(deactivated_by="admin:1")
    svc.activate_user(1, reason="user", activated_by="user:1")
    assert svc.is_new_position_allowed(user_id=1) is False
    assert svc.is_new_position_allowed(user_id=2) is True


def test_protective_exit_always_allowed_even_with_active_kill_switch():
    svc = KillSwitchService()
    svc.activate_global(reason="Marktturbulenz", activated_by="admin:1")
    svc.activate_user(1, reason="Nutzeranfrage", activated_by="user:1")
    assert svc.is_protective_exit_allowed() is True
    assert svc.is_protective_exit_allowed(user_id=1) is True


def test_global_status_and_user_status_reflect_current_state():
    svc = KillSwitchService()
    assert svc.global_status is None
    assert svc.user_status(1) is None

    svc.activate_global(reason="r", activated_by="admin:1")
    svc.activate_user(1, reason="r2", activated_by="user:1")
    assert svc.global_status.active is True
    assert svc.user_status(1).active is True
    assert svc.user_status(2) is None
