from types import SimpleNamespace

import pytest

from stockbot.core.settings import ConfigError, Settings, validate_config


def _config(**overrides):
    values = {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": False,
        "ALLOW_MARGIN": False,
        "MAX_LEVERAGE": 1.0,
        "DEFAULT_LEVERAGE": 1.0,
        "DB_BACKEND": "sqlite",
        "POSTGRES_DSN": "",
        "ALPACA_PAPER": True,
        "ALPACA_API_KEY": None,
        "ALPACA_API_SECRET": None,
        "DASHBOARD_BASE_URL": "http://localhost:8000",
        "COOKIE_SECURE": False,
        "HSTS_ENABLED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_valid_default_config_returns_settings():
    settings = validate_config(_config())

    assert isinstance(settings, Settings)
    assert settings.trading_mode == "paper"
    assert settings.max_leverage == 1.0


def test_paper_margin_with_increased_leverage_is_valid():
    settings = validate_config(
        _config(ALLOW_MARGIN=True, MAX_LEVERAGE=5.0, DEFAULT_LEVERAGE=2.0)
    )

    assert settings.max_leverage == 5.0


def test_increased_leverage_without_margin_is_rejected():
    with pytest.raises(ConfigError, match="ALLOW_MARGIN=true"):
        validate_config(_config(MAX_LEVERAGE=5.0))


def test_unknown_trading_mode_is_rejected():
    with pytest.raises(ConfigError, match="TRADING_MODE"):
        validate_config(_config(TRADING_MODE="broken"))


def test_unknown_log_format_is_rejected():
    with pytest.raises(ConfigError, match="LOG_FORMAT"):
        validate_config(_config(LOG_FORMAT="xml"))


@pytest.mark.parametrize(
    "dsn", ["", "not-a-dsn", "mysql://localhost/stockbot", "postgresql://"]
)
def test_postgres_requires_postgresql_dsn(dsn):
    with pytest.raises(ConfigError, match="POSTGRES_DSN"):
        validate_config(_config(DB_BACKEND="postgres", POSTGRES_DSN=dsn))


def test_live_leverage_is_rejected_even_with_margin_enabled():
    with pytest.raises(ConfigError, match="Live-Handel"):
        validate_config(
            _config(
                TRADING_MODE="live",
                LIVE_TRADING_ENABLED=True,
                ALLOW_MARGIN=True,
                MAX_LEVERAGE=2.0,
                ALPACA_PAPER=False,
                ALPACA_API_KEY="key",
                ALPACA_API_SECRET="secret",
            )
        )


def test_live_without_alpaca_keys_is_rejected():
    with pytest.raises(ConfigError, match="ALPACA_API_KEY"):
        validate_config(
            _config(
                TRADING_MODE="live", LIVE_TRADING_ENABLED=True, ALPACA_PAPER=False
            )
        )


def test_live_paper_and_https_security_mismatches_warn(caplog):
    settings = validate_config(
        _config(
            TRADING_MODE="live",
            LIVE_TRADING_ENABLED=True,
            ALPACA_PAPER=True,
            ALPACA_API_KEY="key",
            ALPACA_API_SECRET="secret",
            DASHBOARD_BASE_URL="https://dashboard.example",
            COOKIE_SECURE=False,
            HSTS_ENABLED=False,
        )
    )

    assert settings.live_trading_enabled is True
    assert "ALPACA_PAPER=true" in caplog.text
    assert "HTTPS-Dashboard" in caplog.text
