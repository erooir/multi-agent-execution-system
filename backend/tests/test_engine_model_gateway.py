"""Exercise Agno + OpenAI SDK + budget transport against a mock provider response."""

import importlib
import json

import httpx
import pytest

from backend.app.model_gateway import BudgetLedger, MeteredTransport, ModelGateway, ModelOutputTruncated


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["report_generation", "workflow_plan"])
async def test_length_finish_reason_fails_but_usage_is_settled(tmp_path, monkeypatch, purpose):
    module = importlib.import_module("backend.app.model_gateway")
    ledger = BudgetLedger(tmp_path / "charges.db")
    transports = []

    def respond(request):
        assert ledger.budget()["reserved_cny"] > 0
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-truncated",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "未完成的报告 [doc_partial"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 512, "total_tokens": 635},
            },
        )

    def transport_factory(ledger, purpose, run_id=None):
        transport = MeteredTransport(ledger, purpose, run_id, inner=httpx.MockTransport(respond))
        transports.append(transport)
        return transport

    monkeypatch.setattr(module, "get_api_key", lambda: "test-only-key")
    monkeypatch.setattr(module, "MeteredTransport", transport_factory)
    gateway = ModelGateway(ledger)
    with pytest.raises(ModelOutputTruncated, match="finish_reason=length") as caught:
        await gateway.complete(
            "测试完整性检查", purpose=purpose, max_tokens=512, json_mode=purpose == "workflow_plan"
        )
    assert caught.value.request_id
    assert transports[0].result["finish_reasons"] == ["length"]
    charge = ledger.recent()[0]
    assert charge["state"] == "settled"
    assert charge["output_tokens"] == 512
    assert ledger.budget()["spent_cny"] > 0
    assert ledger.budget()["reserved_cny"] == 0
    assert ledger.budget()["request_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("output_cap", [512, 8000])
async def test_stop_finish_reason_returns_complete_output(tmp_path, monkeypatch, output_cap):
    module = importlib.import_module("backend.app.model_gateway")
    ledger = BudgetLedger(tmp_path / "charges.db")

    def respond(request):
        assert json.loads(request.content)["max_tokens"] == output_cap
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-complete",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完整报告。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            },
        )

    monkeypatch.setattr(module, "get_api_key", lambda: "test-only-key")
    monkeypatch.setattr(module, "get_settings", lambda: {"max_output_tokens": 8000, "budget_limit_cny": 300})
    monkeypatch.setattr(
        module,
        "MeteredTransport",
        lambda ledger, purpose, run_id=None: MeteredTransport(
            ledger, purpose, run_id, inner=httpx.MockTransport(respond)
        ),
    )
    result = await ModelGateway(ledger).complete("测试正常完成", max_tokens=output_cap)
    assert result["text"] == "完整报告。"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["completion_tokens"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("output_cap,finish_reason", [(8000, "stop"), (2048, "stop"), (8000, "length")])
async def test_agent_honors_output_setting_and_meters_full_cap(
    tmp_path, monkeypatch, output_cap, finish_reason
):
    module = importlib.import_module("backend.app.model_gateway")
    ledger = BudgetLedger(tmp_path / "agent-charges.db")
    completion_tokens = output_cap if finish_reason == "length" else 5

    def respond(request):
        assert json.loads(request.content)["max_tokens"] == output_cap
        expected_reservation = ((len(request.content) + 4096) * 4 + output_cap * 16) / 1_000_000
        assert ledger.recent()[0]["reserved"] == pytest.approx(expected_reservation)
        assert ledger.recent()[0]["state"] == "reserved"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-agent",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "模型测试结果"},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": completion_tokens,
                    "total_tokens": 12 + completion_tokens,
                },
            },
        )

    monkeypatch.setattr(module, "get_api_key", lambda: "test-only-key")
    monkeypatch.setattr(
        module, "get_settings", lambda: {"max_output_tokens": output_cap, "budget_limit_cny": 300}
    )
    monkeypatch.setattr(
        module,
        "MeteredTransport",
        lambda ledger, purpose, run_id=None: MeteredTransport(
            ledger, purpose, run_id, inner=httpx.MockTransport(respond)
        ),
    )
    gateway = ModelGateway(ledger)
    if finish_reason == "length":
        with pytest.raises(ModelOutputTruncated, match="finish_reason=length"):
            await gateway.run_agent({"name": "test"}, "测试输出上限", {})
    else:
        result = await gateway.run_agent({"name": "test"}, "测试输出上限", {})
        assert result["text"] == "模型测试结果"
        assert result["finish_reason"] == "stop"
    budget = ledger.budget()
    assert budget["reserved_cny"] == 0
    assert budget["spent_cny"] == pytest.approx((12 * 4 + completion_tokens * 16) / 1_000_000)
    assert budget["output_tokens"] == completion_tokens
    assert budget["request_count"] == 1
    assert ledger.recent()[0]["state"] == "settled"
