"""Zentrale Logging-Konfiguration mit optionalem JSON-Ausgabeformat."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import re
import secrets
from typing import Iterator


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_trace_id: ContextVar[str | None] = ContextVar("log_trace_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("log_user_id", default=None)
_pseudonym_key = secrets.token_bytes(32)

_SECRET_RE = re.compile(
    r"(?i)(?P<name>api[_-]?key|api[_-]?secret|token|authorization|password|"
    r"telegram[_-]?id|user[_-]?id)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>(?:bearer\s+)?[^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def _redact(value: object) -> str:
    text = str(value)
    text = _SECRET_RE.sub(lambda match: f"{match.group('name')}{match.group('sep')}[REDACTED]", text)
    return _BEARER_RE.sub("Bearer [REDACTED]", text)


def pseudonymize_user_id(user_id: object) -> str:
    """Erzeugt ein stabiles, nicht umkehrbares Nutzer-Pseudonym für den Prozess."""
    digest = hmac.new(_pseudonym_key, str(user_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:24]


def new_trace_id() -> str:
    """Erzeugt eine zufällige Vorgangs-ID ohne fachliche oder personenbezogene Daten."""
    return secrets.token_hex(16)


@contextmanager
def logging_context(*, trace_id: str | None = None, user_id: object | None = None) -> Iterator[None]:
    """Bindet Vorgangs- und pseudonymisierte Nutzer-ID an den aktuellen Ausführungskontext."""
    trace_token = _trace_id.set(trace_id)
    user_token = _user_id.set(pseudonymize_user_id(user_id) if user_id is not None else None)
    try:
        yield
    finally:
        _user_id.reset(user_token)
        _trace_id.reset(trace_token)


class JsonFormatter(logging.Formatter):
    """Formatiert genau die freigegebenen strukturierten Logfelder als JSON."""

    def __init__(self, *, service: str = "stockbot") -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "service": self.service,
            "severity": record.levelname,
            "trace_id": _trace_id.get(),
            "user_id": _user_id.get(),
            "entity_id": _redact(getattr(record, "entity_id", "")) or None,
            "event_type": _redact(getattr(record, "event_type", "")) or None,
            "message": _redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    *, log_format: str = "text", log_file: str | None = None,
    service: str = "stockbot", level: int = logging.INFO,
    pseudonym_key: str | bytes | None = None,
) -> None:
    """Konfiguriert Root-Logging zentral; Text bleibt der kompatible Standard."""
    global _pseudonym_key
    if pseudonym_key:
        _pseudonym_key = (
            pseudonym_key.encode("utf-8") if isinstance(pseudonym_key, str) else pseudonym_key
        )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.insert(0, logging.FileHandler(log_file, encoding="utf-8"))
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonFormatter(service=service)
    else:
        formatter = logging.Formatter(TEXT_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers)
