import importlib
from contextlib import contextmanager
from unittest.mock import patch

with patch("socket.socket"):
    import stockbot.config as config


LEVERAGE_ENV_VARS = (
    "ALLOW_LIVE_TRADING",
    "ALLOW_MARGIN",
    "DEFAULT_LEVERAGE",
    "MAX_LEVERAGE",
    "TRADING_MODE",
)


@contextmanager
def reloaded_config(monkeypatch, **env):
    """Reload config with an isolated trading environment, then restore its import state."""
    try:
        with monkeypatch.context() as scoped:
            for name in LEVERAGE_ENV_VARS:
                scoped.delenv(name, raising=False)
            for name, value in env.items():
                scoped.setenv(name, value)
            with patch("socket.socket"):
                yield importlib.reload(config)
    finally:
        with patch("socket.socket"):
            importlib.reload(config)


def test_paper_margin_allows_configured_default_and_cap(monkeypatch):
    with reloaded_config(
        monkeypatch,
        TRADING_MODE="paper",
        ALLOW_MARGIN="true",
        MAX_LEVERAGE="5",
        DEFAULT_LEVERAGE="5",
    ) as cfg:
        assert cfg.MAX_LEVERAGE == 5.0
        assert cfg.DEFAULT_LEVERAGE == 5.0


def test_live_mode_hard_clamps_default_and_cap_to_one(monkeypatch):
    with reloaded_config(
        monkeypatch,
        TRADING_MODE="live",
        ALLOW_LIVE_TRADING="true",
        ALLOW_MARGIN="true",
        MAX_LEVERAGE="5",
        DEFAULT_LEVERAGE="5",
    ) as cfg:
        assert cfg.LIVE_TRADING_ENABLED is True
        assert cfg.MAX_LEVERAGE == 1.0
        assert cfg.DEFAULT_LEVERAGE == 1.0


def test_margin_disabled_hard_clamps_paper_leverage(monkeypatch):
    with reloaded_config(
        monkeypatch,
        TRADING_MODE="paper",
        MAX_LEVERAGE="5",
        DEFAULT_LEVERAGE="5",
    ) as cfg:
        assert cfg.MAX_LEVERAGE == 1.0
        assert cfg.DEFAULT_LEVERAGE == 1.0


def test_default_leverage_is_clamped_to_maximum(monkeypatch):
    with reloaded_config(
        monkeypatch,
        TRADING_MODE="paper",
        ALLOW_MARGIN="true",
        MAX_LEVERAGE="5",
        DEFAULT_LEVERAGE="10",
    ) as cfg:
        assert cfg.MAX_LEVERAGE == 5.0
        assert cfg.DEFAULT_LEVERAGE == 5.0


def test_leverage_defaults_remain_one_without_env_overrides(monkeypatch):
    with reloaded_config(monkeypatch) as cfg:
        assert cfg.MAX_LEVERAGE == 1.0
        assert cfg.DEFAULT_LEVERAGE == 1.0


def test_accept_signal_inherits_paper_default_leverage(monkeypatch):
    import stockbot.services.trades as trade_svc

    captured = {}
    with reloaded_config(
        monkeypatch,
        TRADING_MODE="paper",
        ALLOW_MARGIN="true",
        MAX_LEVERAGE="5",
        DEFAULT_LEVERAGE="5",
    ):
        importlib.reload(trade_svc)
        monkeypatch.setattr(trade_svc.exchange_calendar, "is_past_entry_cutoff", lambda _: False)
        monkeypatch.setattr(
            trade_svc.db,
            "add_pending",
            lambda user_id, signal, message_id: captured.update(signal),
        )
        monkeypatch.setattr(
            trade_svc.db,
            "activate_trade",
            lambda user_id, ticker, status: {"signal": dict(captured), "status": status},
        )

        result = trade_svc.accept_signal(7, {"ticker": "AAPL", "price": 100.0})

        assert result["ok"] is True
        assert result["trade"]["signal"]["leverage"] == 5.0

    # services.trades imports the default by value, so restore that module after config cleanup too.
    importlib.reload(trade_svc)
