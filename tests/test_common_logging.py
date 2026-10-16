"""Tests for JSON logging and request correlation."""

import json
import logging

from mlp_common.logging import JsonFormatter, bind_request_id, reset_request_id


def test_json_formatter_emits_contract_fields() -> None:
    """Every line contains the five mandatory structured fields and context."""
    token = bind_request_id("req-123")
    try:
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 10, "served %s", (3,), None
        )
        record.customer_count = 3
        payload = json.loads(JsonFormatter("model-api").format(record))
    finally:
        reset_request_id(token)

    assert payload["level"] == "INFO"
    assert payload["service"] == "model-api"
    assert payload["request_id"] == "req-123"
    assert payload["msg"] == "served 3"
    assert payload["customer_count"] == 3
    assert payload["ts"].endswith("+00:00")
