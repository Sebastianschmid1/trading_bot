import io
import json
import logging

from stockbot.core.logging_setup import JsonFormatter, logging_context


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
