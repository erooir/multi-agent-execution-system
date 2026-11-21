"""HTTP protocol doubles for the model client; no business DB or paid API."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from backend.app import config
from backend.app import model_gateway as gateway
from scripts.probe_model_api import Probe, synthetic_image


@pytest.fixture
def model_endpoint(monkeypatch, tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append((self.path, self.headers.get("Authorization"), payload))
            message = {"role": "assistant", "content": '{"result":84}'}
            finish = "stop"
            if payload.get("tools") and payload["messages"][-1]["role"] != "tool":
                message.update(
                    content=None,
                    tool_calls=[
                        {
                            "id": "safe-multiply-1",
                            "type": "function",
                            "function": {
                                "name": payload["tools"][0]["function"]["name"],
                                "arguments": '{"a":12,"b":7}',
                            },
                        }
                    ],
                )
                finish = "tool_calls"
            body = json.dumps(
                {
                    "id": "synthetic-completion",
                    "object": "chat.completion",
                    "created": 123456,
                    "model": "hangzhi-qwen38",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name, value in {
        "HANGZHI_UPSTREAM_MODEL_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "HANGZHI_UPSTREAM_MODEL_ID": "hangzhi-qwen38",
        "HANGZHI_UPSTREAM_BILLING": "internal_zero",
        "HANGZHI_UPSTREAM_API_KEY": "dedicated-synthetic-key",
        "DEEPSEEK_API_KEY_ZX": "must-not-send",
        "HANGZHI_OFFLINE": "false",
        "HANGZHI_UPSTREAM_TIMEOUT_SECONDS": "17",
        "HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS": "600",
        "HANGZHI_UPSTREAM_TEMPERATURE": "0.2",
        "HANGZHI_UPSTREAM_TOP_P": "0.9",
        "HANGZHI_UPSTREAM_ENABLE_THINKING": "false",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "HTTP_PROXY": "http://127.0.0.1:1",
    }.items():
        monkeypatch.setenv(name, value)

    # Supply settings as independent input; never initialize business storage.
    monkeypatch.setattr(gateway, "get_settings", lambda: {"max_output_tokens": 700})
    ledger = gateway.BudgetLedger(tmp_path / "synthetic-only.sqlite3")
    client = gateway.ModelGateway(ledger)
    try:
        yield client, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.asyncio
async def test_image_json_uses_configured_generation_without_cloud_fields(model_endpoint):
    client, seen = model_endpoint
    before = client.ledger.budget()
    with gateway.request_scope("image", 1, True):
        reply = await client.complete("返回JSON", images=[synthetic_image()], json_mode=True)
    assert json.loads(reply["text"]) == {"result": 84}
    path, auth, payload = seen[0]
    assert path == "/v1/chat/completions" and auth == "Bearer dedicated-synthetic-key"
    assert payload["model"] == "hangzhi-qwen38" and payload["max_tokens"] == 600
    assert payload["temperature"] == 0.2 and payload["top_p"] == 0.9
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["messages"][0]["role"] == "system"
    assert all(message["role"] != "developer" for message in payload["messages"])
    assert payload["response_format"] == {"type": "json_object"}
    assert any(
        item.get("type") == "image_url"
        for m in payload["messages"]
        if isinstance(m["content"], list)
        for item in m["content"]
    )
    assert "thinking" not in payload and "use_thinking" not in payload
    assert reply["cost_cny"] == 0 and reply["provider"] == "intranet"
    assert client.ledger.budget() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(500, 256), (100, 100)])
async def test_application_and_caller_output_caps_still_apply(
    model_endpoint, monkeypatch, requested, expected
):
    client, seen = model_endpoint
    monkeypatch.setattr(gateway, "get_settings", lambda: {"max_output_tokens": 256})
    await client.complete("synthetic", max_tokens=requested)
    assert seen[0][2]["max_tokens"] == expected


@pytest.mark.asyncio
async def test_intranet_output_above_cloud_cap_is_configurable(model_endpoint, monkeypatch):
    client, seen = model_endpoint
    monkeypatch.setenv("HANGZHI_CONTEXT_TOKENS", "32768")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS", "16000")
    monkeypatch.setattr(gateway, "get_settings", lambda: {"max_output_tokens": 16000})
    await client.complete("synthetic", max_tokens=12000)
    assert seen[0][2]["max_tokens"] == 12000
    assert config.MAX_OUTPUT_TOKENS == 8000


def test_intranet_limit_must_leave_space_in_context(model_endpoint, monkeypatch):
    monkeypatch.setenv("HANGZHI_CONTEXT_TOKENS", "32768")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS", "32768")
    with pytest.raises(ValueError):
        config.intranet_provider()


def test_internal_settings_do_not_replace_paid_cloud_output_policy(model_endpoint, monkeypatch):
    saved = {"id": "main", "max_output_tokens": 6000, "budget_limit_cny": 100}

    def save(_kind, value):
        saved.update(value)

    monkeypatch.setitem(
        sys.modules,
        "backend.app.storage",
        SimpleNamespace(store=SimpleNamespace(get=lambda *_: dict(saved), save=save)),
    )
    monkeypatch.setenv("HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS", "16000")
    assert config.save_settings({"max_output_tokens": 12000})["max_output_tokens"] == 12000
    assert saved["max_output_tokens"] == 6000 and saved["budget_limit_cny"] == 100
    for key in ("MODEL_URL", "MODEL_ID", "BILLING", "API_KEY"):
        monkeypatch.delenv("HANGZHI_UPSTREAM_" + key)
    assert config.get_settings()["max_output_tokens"] == 6000
    with pytest.raises(ValueError, match="8000"):
        config.save_settings({"max_output_tokens": 12000})


@pytest.mark.asyncio
async def test_real_agno_tool_roundtrip_keeps_accounting_and_cancellation(model_endpoint):
    client, seen = model_endpoint
    executed = []

    def multiply(a: int, b: int) -> int:
        """Multiply two integers."""
        executed.append((a, b))
        return a * b

    with gateway.request_scope("tools", 1, True):
        reply = await client.run_agent({}, "计算12乘7", {"multiply": multiply})
    assert executed == [(12, 7)]
    assert json.loads(reply["text"]) == {"result": 84}
    assert len(seen) == 2 and reply["request_count"] == 2
    tool_message = next(m for m in seen[1][2]["messages"] if m["role"] == "tool")
    assistant = next(m for m in seen[1][2]["messages"] if m.get("tool_calls"))
    assert tool_message["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert tool_message["tool_call_id"] and "84" in tool_message["content"]
    assert reply["usage"] == {"prompt_tokens": 40, "completion_tokens": 20}
    assert client.ledger.budget()["request_count"] == 0
    with (
        gateway.request_scope("cancelled", 1, True, cancelled=lambda: True),
        pytest.raises(gateway.ModelRequestCancelled),
    ):
        await client.complete("不能派发")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_structured_agent_preserves_real_automatic_tool_protocol(model_endpoint):
    from pydantic import BaseModel

    class Result(BaseModel):
        result: int

    client, seen = model_endpoint
    executed = []

    def multiply(a: int, b: int) -> int:
        """Multiply integers."""
        executed.append((a, b))
        return a * b

    result = await client.run_agent({}, "返回JSON", {"multiply": multiply}, output_schema=Result)
    assert executed == [(12, 7)] and json.loads(result["text"]) == {"result": 84}
    assert "response_format" not in seen[0][2]
    assert len(seen) == 2 and all(row[1] == "Bearer dedicated-synthetic-key" for row in seen)


@pytest.mark.asyncio
async def test_inherited_offline_and_private_data_policy_remain_explicit(model_endpoint, monkeypatch):
    client, seen = model_endpoint
    with pytest.raises(PermissionError):
        await client.complete("private", evidence=[{"classification": "local"}])
    monkeypatch.setenv("HANGZHI_OFFLINE", "true")
    with pytest.raises(gateway.ModelCallError, match="离线"):
        await client.complete("offline")
    assert not seen


@pytest.mark.parametrize(
    "name,value",
    [
        ("TIMEOUT_SECONDS", "nan"),
        ("TIMEOUT_SECONDS", "0"),
        ("MAX_OUTPUT_TOKENS", "1008001"),
        ("MAX_TASK_REQUESTS", "-1"),
        ("TEMPERATURE", "inf"),
        ("TOP_P", "0"),
        ("ENABLE_THINKING", "maybe"),
    ],
)
def test_invalid_generation_fails_before_dispatch(model_endpoint, monkeypatch, name, value):
    monkeypatch.setenv("HANGZHI_UPSTREAM_" + name, value)
    with pytest.raises(ValueError):
        config.intranet_provider()
    assert not model_endpoint[1]


def test_probe_rejects_truncation_and_records_failure(monkeypatch):
    probe = Probe("http://127.0.0.1:8001/v1", "synthetic", "model", 1)
    monkeypatch.setattr(
        probe,
        "request",
        lambda *_args: (
            200,
            {
                "model": "model",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "choices": [
                    {"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}
                ],
            },
        ),
    )
    probe.check("truncated", lambda: probe.chat(probe.user("test")))
    assert probe.results[0]["status"] == "failed" and "length" in probe.results[0]["detail"]


@pytest.mark.asyncio
async def test_intranet_leaves_token_admission_to_server(model_endpoint, monkeypatch):
    client, seen = model_endpoint
    monkeypatch.setenv("HANGZHI_CONTEXT_TOKENS", "32768")
    await client.complete("甲" * 12000)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_required_main_tool_then_bounded_final_json(model_endpoint):
    from pydantic import BaseModel

    class Result(BaseModel):
        result: int

    client, seen = model_endpoint

    def multiply(a: int, b: int) -> int:
        """Multiply integers."""
        return a * b

    result = await client.run_agent(
        {},
        "执行主工具后返回JSON",
        {"multiply": multiply},
        output_schema=Result,
        required_first_tool="multiply",
        auto_tool_rounds=1,
    )
    assert json.loads(result["text"]) == {"result": 84}
    assert seen[0][2]["tool_choice"] == {"type": "function", "function": {"name": "multiply"}}
    assert seen[1][2]["tool_choice"] == "none"
    assert seen[1][2]["response_format"] == {"type": "json_object"}
