"""
Tests für die zentrale Pre-Trade-Risk-Vorprüfung (stockbot.core.risk).
Phase 0 / TSAFE-007 — harte Invarianten: Live-Kill-Switch, Hebel-Deckel, Optionsverbot.
"""

from stockbot.core import risk
from stockbot import config


def test_allows_plain_paper_share_order():
    d = risk.pretrade_check(leverage=1.0, is_option=False, is_live_account=False)
    assert d.ok is True


def test_blocks_live_account_when_live_disabled():
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = False
    try:
        d = risk.pretrade_check(is_live_account=True)
        assert d.ok is False and d.code == "live_blocked"
    finally:
        config.LIVE_TRADING_ENABLED = orig


def test_blocks_leverage_over_max():
    d = risk.pretrade_check(leverage=config.MAX_LEVERAGE + 1)
    assert d.ok is False and d.code == "leverage_blocked"


def test_blocks_options_when_disabled():
    orig = config.ALLOW_OPTIONS
    config.ALLOW_OPTIONS = False
    try:
        d = risk.pretrade_check(is_option=True)
        assert d.ok is False and d.code == "options_blocked"
    finally:
        config.ALLOW_OPTIONS = orig


def test_live_account_allowed_when_live_enabled():
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = True
    try:
        assert risk.pretrade_check(leverage=1.0, is_live_account=True).ok is True
    finally:
        config.LIVE_TRADING_ENABLED = orig
