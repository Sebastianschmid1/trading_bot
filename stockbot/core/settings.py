"""Typisierte und zentral validierte Laufzeit-Konfiguration."""

from dataclasses import dataclass
import logging
import math
from typing import Any
from urllib.parse import urlparse

from stockbot import config


log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Die Konfiguration verletzt eine harte Start-Invariante."""


@dataclass(frozen=True, repr=False)
class Settings:
    """Sicherheits- und betriebsrelevante, bereits geparste Einstellungen."""

    trading_mode: str
    live_trading_enabled: bool
    allow_margin: bool
    max_leverage: float
    default_leverage: float
    db_backend: str
    postgres_dsn: str
    alpaca_paper: bool
    alpaca_api_key: str | None
    alpaca_api_secret: str | None
    dashboard_base_url: str
    cookie_secure: bool
    hsts_enabled: bool
    log_format: str

    def __repr__(self) -> str:
        """Zeigt betriebliche Werte, aber niemals Zugangsdaten."""
        values = (
            f"trading_mode={self.trading_mode!r}",
            f"live_trading_enabled={self.live_trading_enabled!r}",
            f"allow_margin={self.allow_margin!r}",
            f"max_leverage={self.max_leverage!r}",
            f"default_leverage={self.default_leverage!r}",
            f"db_backend={self.db_backend!r}",
            "postgres_dsn='***'",
            f"alpaca_paper={self.alpaca_paper!r}",
            "alpaca_api_key='***'",
            "alpaca_api_secret='***'",
            f"dashboard_base_url={self.dashboard_base_url!r}",
            f"cookie_secure={self.cookie_secure!r}",
            f"hsts_enabled={self.hsts_enabled!r}",
        )
        return f"Settings({', '.join(values)})"

    __str__ = __repr__


def validate_config(cfg: Any = config) -> Settings:
    """Validiert die bereits von :mod:`stockbot.config` geparsten Werte.

    Harte Fehler verweigern den Start: unbekannter Trading-Modus, ungültige
    Leverage-Grenzen, Margin-Hebel ohne Freigabe, Live-Hebel, fehlende
    Live-Alpaca-Zugangsdaten sowie eine fehlende/ungültige PostgreSQL-DSN.
    Weiche Widersprüche werden geloggt: ein aktiver Live-Modus mit erzwungenem
    Alpaca-Paperkonto und HTTPS/Cookie-/HSTS-Einstellungen, die nicht zur
    Dashboard-Basis-URL passen.

    ``cfg`` darf für hermetische Tests ein beliebiges config-artiges Objekt sein.
    Es werden keine Umgebungsvariablen erneut gelesen.
    """
    settings = Settings(
        trading_mode=cfg.TRADING_MODE,
        live_trading_enabled=cfg.LIVE_TRADING_ENABLED,
        allow_margin=cfg.ALLOW_MARGIN,
        max_leverage=cfg.MAX_LEVERAGE,
        default_leverage=cfg.DEFAULT_LEVERAGE,
        db_backend=cfg.DB_BACKEND,
        postgres_dsn=cfg.POSTGRES_DSN,
        alpaca_paper=cfg.ALPACA_PAPER,
        alpaca_api_key=cfg.ALPACA_API_KEY,
        alpaca_api_secret=cfg.ALPACA_API_SECRET,
        dashboard_base_url=cfg.DASHBOARD_BASE_URL,
        cookie_secure=cfg.COOKIE_SECURE,
        hsts_enabled=cfg.HSTS_ENABLED,
        log_format=getattr(cfg, "LOG_FORMAT", "text"),
    )

    errors: list[str] = []
    if settings.trading_mode not in {"paper", "live"}:
        errors.append("TRADING_MODE muss 'paper' oder 'live' sein")
    if settings.db_backend not in {"sqlite", "postgres"}:
        errors.append("DB_BACKEND muss 'sqlite' oder 'postgres' sein")
    if settings.log_format not in {"text", "json"}:
        errors.append("LOG_FORMAT muss 'text' oder 'json' sein")

    if not math.isfinite(settings.max_leverage) or not math.isfinite(settings.default_leverage):
        errors.append("MAX_LEVERAGE und DEFAULT_LEVERAGE müssen endliche Zahlen sein")
    elif settings.max_leverage < 1 or settings.default_leverage < 1:
        errors.append("MAX_LEVERAGE und DEFAULT_LEVERAGE müssen mindestens 1 sein")
    if settings.default_leverage > settings.max_leverage:
        errors.append("DEFAULT_LEVERAGE darf MAX_LEVERAGE nicht überschreiten")
    if settings.max_leverage > 1 and not settings.allow_margin:
        errors.append("MAX_LEVERAGE > 1 erfordert ALLOW_MARGIN=true")
    if settings.live_trading_enabled and (
        settings.max_leverage > 1 or settings.default_leverage > 1
    ):
        errors.append("Live-Handel erlaubt ausschließlich MAX_LEVERAGE=DEFAULT_LEVERAGE=1")

    if settings.db_backend == "postgres":
        dsn = settings.postgres_dsn.strip() if settings.postgres_dsn else ""
        parsed_dsn = urlparse(dsn)
        if (
            parsed_dsn.scheme not in {"postgresql", "postgresql+psycopg2"}
            or not parsed_dsn.netloc
            or parsed_dsn.path in {"", "/"}
        ):
            errors.append("DB_BACKEND=postgres erfordert eine POSTGRES_DSN mit postgresql-Schema")

    if settings.live_trading_enabled:
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            errors.append("Live-Handel erfordert ALPACA_API_KEY und ALPACA_API_SECRET")
        if settings.alpaca_paper:
            log.warning(
                "Konfigurationswarnung: LIVE_TRADING_ENABLED=true, aber ALPACA_PAPER=true"
            )

    dashboard_scheme = urlparse(settings.dashboard_base_url).scheme.lower()
    if dashboard_scheme == "http" and (settings.cookie_secure or settings.hsts_enabled):
        log.warning(
            "Konfigurationswarnung: HTTP-Dashboard mit COOKIE_SECURE oder HSTS_ENABLED"
        )
    if dashboard_scheme == "https" and (
        not settings.cookie_secure or not settings.hsts_enabled
    ):
        log.warning(
            "Konfigurationswarnung: HTTPS-Dashboard ohne COOKIE_SECURE oder HSTS_ENABLED"
        )

    if errors:
        raise ConfigError("Ungültige Konfiguration: " + "; ".join(errors))
    return settings
