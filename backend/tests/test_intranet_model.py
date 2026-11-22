"""A real HTTP socket exercises the adapter. Its fixed replies are a contract double."""

import base64
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app import config
from backend.app import model_gateway as gateway
from backend.app.model_gateway import BudgetLedger, ModelCallError, ModelGateway, request_scope
from backend.app.platform.resource_usage import gateway_measurements


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    seen = []
    options = {"usage": True, "redirect": False, "model": "catalog-fixture"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append((self.path, self.headers.get("Authorization"), payload))
            if options["redirect"]:
                self.send_response(307)
                self.send_header("Location", "/redirected")
                self.end_headers()
                return
            reply = options["replies"].pop(0) if options.get("replies") else "本机合同样本"
            body = {
                "id": "fixture-1",
                "object": "chat.completion",
                "created": 123456,
                "model": options["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": reply},
                    }
                ],
            }
            if options["usage"]:
                body["usage"] = {"prompt_tokens": 11, "completion_tokens": 4}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_ID", "catalog-fixture")
    monkeypatch.setenv("HANGZHI_UPSTREAM_BILLING", "internal_zero")
    monkeypatch.setenv("HANGZHI_UPSTREAM_API_KEY", "dedicated-test-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY_ZX", "cloud-key-must-stay-local")
    monkeypatch.setenv("HANGZHI_OFFLINE", "false")
    try:
        yield ModelGateway(BudgetLedger(tmp_path / "synthetic.sqlite3")), seen, options
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.asyncio
async def test_intranet_complete_and_agent_use_dedicated_key_and_meter_each_round(endpoint):
    gateway, seen, _ = endpoint
    historic = gateway.ledger.reserve(0.1, "historical", "old")
    gateway.ledger.uncertain(historic)
    original = gateway.ledger.budget()
    with request_scope("task", 1, True):
        one = await gateway.complete("测试传输")
        two = await gateway.run_agent({"name": "demo"}, "测试消息", context={"run_id": "task"})
    for result in (one, two):
        assert result["text"] == "本机合同样本" and result["model"] == "catalog-fixture"
        assert result["usage"] == {"prompt_tokens": 11, "completion_tokens": 4}
        assert result["provider"] == "intranet" and result["cost_cny"] == 0 and result["accounting_complete"]
    assert len(seen) == 2
    assert all(
        p == "/v1/chat/completions" and k == "Bearer dedicated-test-token" and b["model"] == "catalog-fixture"
        for p, k, b in seen
    )
    assert gateway.ledger.budget() == original
    assert gateway.usage("task")["request_count"] == 2
    assert gateway_measurements(gateway.ledger.path, ["task"])["intranet_requests"] == 2


@pytest.mark.asyncio
async def test_formal_text_node_and_independent_review_use_http_fixture(endpoint, monkeypatch, tmp_path):
    from backend.app.platform import runtime
    from backend.app.platform.db import Repository
    from backend.tests.test_v2_runtime import execute, seed

    gateway, seen, options = endpoint
    db = Repository(f"sqlite:///{(tmp_path / 'tasks.sqlite3').as_posix()}")
    monkeypatch.setattr(runtime, "model_gateway", gateway)
    seed(db, mode="cloud", task_type="source_summary", content="A public source")
    options["replies"] = [
        json.dumps(
            {
                "text": "Summary based on a public source",
                "citations": [{"source_id": "input-1", "quote": "A public source"}],
            }
        ),
        json.dumps(
            {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
        ),
    ]
    result = await execute(db)
    assert result["status"] == "succeeded", result.get("reason_code")
    assert result["result"]["usage"]["request_count"] == 2 and len(seen) == 2
    assert result["result"]["usage"]["input_tokens"] == 22 and result["result"]["usage"]["cost_cny"] == 0
    assert gateway.ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_no_redirect_and_missing_tokens_not_counted_as_success(endpoint):
    gateway, seen, options = endpoint
    with request_scope("task", 1, True):
        options["redirect"] = True
        with pytest.raises(ModelCallError):
            await gateway.complete("redirect")
        options.update(redirect=False, usage=False)
        with pytest.raises(ModelCallError, match="Token"):
            await gateway.complete("unknown usage")
        options.update(usage=True, model="unexpected-backend")
        with pytest.raises(ModelCallError, match="标识"):
            await gateway.complete("identity")
    assert [r[0] for r in seen] == ["/v1/chat/completions"] * 3
    assert gateway.usage("task")["uncertain_count"] == 3 and not gateway.usage("task")["accounting_complete"]
    assert gateway.ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_egress_roles_and_no_cloud_key_fallback(endpoint, monkeypatch):
    gateway, seen, _ = endpoint
    with request_scope("t", 1, False), pytest.raises(PermissionError):
        await gateway.complete("confidential")
    with pytest.raises(PermissionError):
        await gateway.complete("private", evidence=[{"classification": "local"}])
    monkeypatch.setenv("HANGZHI_OFFLINE", "true")
    with pytest.raises(ModelCallError, match="离线"):
        await gateway.complete("offline")
    monkeypatch.setenv("HANGZHI_OFFLINE", "false")
    monkeypatch.delenv("HANGZHI_UPSTREAM_API_KEY")
    with pytest.raises(ModelCallError, match="内网"):
        await gateway.complete("no credential")
    monkeypatch.setenv("HANGZHI_UPSTREAM_API_KEY", "cloud-key-must-stay-local")
    with pytest.raises(ValueError, match="独立凭据"):
        await gateway.complete("wrong credential")
    assert not seen


@pytest.mark.parametrize(
    "url",
    [
        "https://8.8.8.8/v1",
        "http://192.168.44.1/v1",
        "http://169.254.169.254/v1",
        "https://example.invalid/v1",
        "http://admin@127.0.0.1/v1",
        "http://127.0.0.1/v1?backend=x",
        "http://127.0.0.1/v1/other",
        "https://[fe80::1]/v1",
    ],
)
def test_invalid_upstream_address_or_pricing_rejected(monkeypatch, url):
    monkeypatch.setenv("HANGZHI_UPSTREAM_BILLING", "internal_zero")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_ID", "model")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_URL", url)
    with pytest.raises(ValueError):
        config.intranet_provider()
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_URL", "https://192.168.1.55:9000/v1")
    assert config.intranet_provider().model == "model"
    monkeypatch.setenv("HANGZHI_UPSTREAM_BILLING", "unpriced_paid")
    with pytest.raises(ValueError, match="未核价"):
        config.intranet_provider()


def _image_uri():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


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
        reply = await client.complete("返回JSON", images=[_image_uri()], json_mode=True)
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
