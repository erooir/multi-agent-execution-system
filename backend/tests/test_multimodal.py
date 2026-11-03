"""Formal OCR and native Agno vision paths with generated public images."""

import hashlib
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app import model_gateway as gateway_module
from backend.app.model_gateway import BudgetLedger
from backend.app.platform import runtime
from backend.app.platform.contracts import ImageUnderstandingResult
from backend.app.platform.db import Repository
from backend.tests.test_v2_runtime import execute, seed


@pytest.fixture
def repository(tmp_path, monkeypatch):
    from backend.app.platform import service

    repository = Repository(f"sqlite:///{(tmp_path / 'platform.sqlite3').as_posix()}")
    monkeypatch.setattr(
        runtime, "model_gateway", SimpleNamespace(ledger=BudgetLedger(tmp_path / "budget.sqlite3"))
    )
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path)
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path)
    return repository


def _png(text="INVOICE 12345", color="white"):
    image = Image.new("RGB", (1000, 240), color)
    draw = ImageDraw.Draw(image)
    draw.text((50, 65), text, fill="black", font=ImageFont.load_default(size=72))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_task(repository, task_type, mode, payload):
    task = seed(repository, task_type=task_type, mode=mode, content="")
    asset = task["snapshot"]["inputs"][0]
    path = repository.engine.url.database
    from pathlib import Path

    target = Path(path).parent / "image.png"
    target.write_bytes(payload)
    asset.update(
        modality="image",
        media_type="image/png",
        content=None,
        content_ref=str(target),
        checksum=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
    )
    repository.put("data_assets", asset)
    repository.put("tasks", task)
    return task


async def test_formal_local_ocr_task_publishes_real_text(repository):
    _image_task(repository, "image_ocr", "local", _png())
    result = await execute(repository)
    assert result["status"] == "succeeded", result.get("result")
    artifact = repository.list("artifacts", task_id="task-1")[0]
    assert artifact["method"] == "rapidocr_onnxruntime"
    assert "12345" in artifact["content"]["images"][0]["text"].replace(" ", "")
    assert artifact["content"]["images"][0]["blocks"][0]["confidence"] > 0


async def test_formal_cloud_vision_and_independent_review_receive_images(repository, monkeypatch):
    _image_task(repository, "image_understand", "cloud", _png("BLUE 42"))
    calls = []

    async def complete(prompt, **kwargs):
        images = kwargs.get("images")
        assert images and images[0]["content"].startswith(b"\x89PNG")
        calls.append(kwargs["purpose"])
        if kwargs["purpose"] == "v2_image_understanding":
            content = {
                "text": "图片包含文字BLUE 42。",
                "evidence": [{"source_id": "input-1", "source_version": "1", "bbox": None}],
                "missing_information": [],
            }
        else:
            content = {
                "verdict": "passed",
                "issues": [],
                "evidence_refs": ["input-1"],
                "suggested_changes": [],
            }
        return {"text": json.dumps(content), "request_id": "fixture", "model": "deepseek-flash"}

    monkeypatch.setattr(runtime.model_gateway, "complete", complete, raising=False)
    result = await execute(repository)
    assert result["status"] == "succeeded", result.get("result")
    assert calls == ["v2_image_understanding", "v2_independent_review"]
    artifact = repository.list("artifacts", task_id="task-1")[0]
    assert artifact["content"]["evidence"][0]["source_id"] == "input-1"


@pytest.mark.asyncio
async def test_native_agno_serializes_controlled_image_bytes(tmp_path, monkeypatch):
    from backend.app.config import MODEL_ID

    payloads = []

    async def provider(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture-vision",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "text": "合成图片",
                                    "evidence": [
                                        {"source_id": "fixture", "source_version": "1", "bbox": None}
                                    ],
                                    "missing_information": [],
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 20},
            },
        )

    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "explicit-synthetic-fixture")
    original = gateway_module.MeteredTransport
    monkeypatch.setattr(
        gateway_module,
        "MeteredTransport",
        lambda *args, **kwargs: original(*args, **kwargs, inner=httpx.MockTransport(provider)),
    )
    ledger = BudgetLedger(tmp_path / "vision-budget.sqlite3")
    with gateway_module.request_scope("fixture-vision", 1, True):
        result = await gateway_module.ModelGateway(ledger).complete(
            "Describe the synthetic fixture",
            images=[{"content": _png("VISION 9"), "mime_type": "image/png"}],
            output_schema=ImageUnderstandingResult,
        )
    content = payloads[0]["messages"][-1]["content"]
    image_blocks = [item for item in content if item.get("type") == "image_url"]
    assert image_blocks and image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert json.loads(result["text"])["evidence"][0]["source_id"] == "fixture"
    assert ledger.usage("fixture-vision")["accounting_complete"]
