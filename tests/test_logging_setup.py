import io
import json
import logging

from stockbot.core.logging_setup import (
    JsonFormatter, RedactingFilter, configure_logging, logging_context,
)


def _logger_output(message, *args, **kwargs):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="test-service"))
    logger = logging.getLogger("test.json.logging")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info(message, *args, **kwargs)
    return json.loads(stream.getvalue())


def test_json_formatter_emits_required_fields_and_context():
    with logging_context(trace_id="trace-123", user_id=987654321):
        payload = _logger_output(
            "Order verarbeitet", extra={"entity_id": "order-7", "event_type": "submitted"}
        )

    assert payload == {
        "timestamp": payload["timestamp"],
        "service": "test-service",
        "severity": "INFO",
        "trace_id": "trace-123",
        "user_id": payload["user_id"],
        "entity_id": "order-7",
        "event_type": "submitted",
        "message": "Order verarbeitet",
    }
    assert payload["user_id"] != "987654321"
    assert len(payload["user_id"]) == 24


def test_json_formatter_drops_extra_secrets_and_redacts_message_values():
    raw_id = "123456789"
    api_key = "super-secret-key"
    token = "telegram-token-value"
    with logging_context(user_id=raw_id):
        payload = _logger_output(
            "api_key=%s token: %s Authorization=Bearer %s user_id=%s",
            api_key, token, token, raw_id,
            extra={"alpaca_api_secret": "must-not-be-serialized"},
        )

    serialized = json.dumps(payload)
    assert raw_id not in serialized
    assert api_key not in serialized
    assert token not in serialized
    assert "must-not-be-serialized" not in serialized
    assert "[REDACTED]" in payload["message"]


def test_redacting_filter_hides_telegram_token_in_text_format(caplog):
    """Das Bot-Token steht als Pfadsegment in der URL — Produktion loggt im Textformat.

    Ohne Filter landete `https://api.telegram.org/bot<TOKEN>/getUpdates` bei jedem einzelnen
    Telegram-Aufruf im Klartext im Journal (gemessen: 60 Treffer in 10 Minuten).
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("test.text.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("HTTP Request: POST https://api.telegram.org/bot8835679714:AAF-secret_x/getUpdates")
    logger.info("api_key=%s", "super-secret-key")

    output = stream.getvalue()
    assert "AAF-secret_x" not in output
    assert "8835679714" not in output
    assert "/bot[REDACTED]/getUpdates" in output
    assert "super-secret-key" not in output


def test_configure_logging_installs_redaction_and_silences_url_libraries():
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []                      # Startzustand des Prozesses nachstellen
    try:
        configure_logging(log_format="text")
        assert root.handlers, "configure_logging muss Handler setzen"
        for handler in root.handlers:
            assert any(isinstance(f, RedactingFilter) for f in handler.filters), \
                "jeder Handler braucht die Schwärzung, sonst leckt das Textformat"
        for chatty in ("httpx", "httpcore"):
            assert logging.getLogger(chatty).level == logging.WARNING
    finally:
        root.handlers = saved
