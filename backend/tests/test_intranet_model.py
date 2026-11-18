"""A real HTTP socket exercises the adapter. Its fixed replies are a contract double."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.app import config
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
