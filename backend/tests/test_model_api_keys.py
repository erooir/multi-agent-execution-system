"""Credential-file boundary checks; no model, network, or actual secrets."""

import argparse
import json
import logging

import pytest

from scripts.model_api.serve_with_keys import load_keys


@pytest.mark.parametrize("multiple", [False, True])
def test_native_string_credentials_hide_namespace_logs(tmp_path, multiple):
    values = ["synthetic-key-" + "a" * 32, "synthetic-key-" + "b" * 32]
    path = tmp_path / "keys.txt"
    path.write_text(json.dumps(values) if multiple else values[0] + "\n")
    keys = load_keys(path)
    assert [key.encode() for key in keys] == [
        value.encode() for value in (values if multiple else values[:1])
    ]
    record = logging.LogRecord(
        "vllm", logging.INFO, "", 0, "args: %s", (argparse.Namespace(api_key=keys),), None
    )
    message = record.getMessage()
    assert "redacted" in message and all(value not in message for value in values)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[]",
        '["short"]',
        '["broken"',
        json.dumps(["x" * 32, "x" * 32]),
        json.dumps(["x" * 32] * 33),
        json.dumps([None]),
    ],
)
def test_bad_credentials_fail_without_echoing_input(tmp_path, content):
    path = tmp_path / "keys.txt"
    path.write_text(content)
    with pytest.raises(ValueError, match="key file"):
        load_keys(path)
