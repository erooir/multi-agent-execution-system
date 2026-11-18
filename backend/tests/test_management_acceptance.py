"""Acceptance evidence from generated scans and project-scoped execution records."""

import io
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app.casework.monitor import activity, capability_usage
from backend.app.platform import service
from backend.app.platform.expert_design import ConfirmInterview, ExpertInterview, confirm, draft
from backend.app.platform.ocr import run_ocr
from backend.app.platform.quality import deterministic_quality
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_platform import ADMIN, project

db = fixtures.db


def test_low_contrast_archive_number_is_read_locally():
    image = Image.new("RGB", (1000, 240), (196, 196, 196))
    ImageDraw.Draw(image).text(
        (50, 65), "ARCHIVE 52719", fill=(177, 177, 177), font=ImageFont.load_default(size=72)
    )
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    result = run_ocr(
        [{"source_id": "synthetic-archive", "source_version": "1", "payload": stream.getvalue()}]
    )
    assert "52719" in result["content"]["images"][0]["text"].replace(" ", "")
    assert any("enhanced contrast" in w for w in result["warnings"])


@pytest.mark.parametrize(
    ("bbox", "passed"),
    [
        ([0, 0, 12, 40], True),
        ([0, 0, 13, 40], False),
        ([2, 8, 1, 9], False),
        ([-1, 0, 12, 20], False),
        (None, True),
    ],
)
def test_visual_evidence_within_display_pixels(bbox, passed):
    source = {"id": "scan", "source_version": "2"}
    data = {"text": "档案页", "evidence": [{"source_id": "scan", "source_version": "2", "bbox": bbox}]}
    result = deterministic_quality(
        {"task_type": "image_understand"},
        {"content": data, "source_refs": ["scan"], "metrics": {"display_sizes": {"scan": [12, 40]}}},
        [source],
    )
    assert next(c["passed"] for c in result["checks"] if c["name"] == "image_evidence") is passed


def test_expert_interview_confirms_separately_and_executes_with_grant(db):
    p = project(db)
    before = len(db.list("agents", p["id"]))
    preview = draft(
        ExpertInterview(
            project_id=p["id"], business="science", reason="复核资料图中文字", deliverable="transcription"
        )
    )
    assert len(db.list("agents", p["id"])) == before
    assert preview.tool_ids == ["image_ocr"]
    answers = ConfirmInterview(
        project_id=p["id"],
        business="science",
        reason="复核资料图中文字",
        deliverable="transcription",
        confirmation_id="test-review-token-0001",
    )
    saved = confirm(answers, ADMIN, db)
    assert confirm(answers, ADMIN, db)["id"] == saved["id"]
    with pytest.raises(service.DomainError, match="确认标识"):
        confirm(answers.model_copy(update={"reason": "不同原因"}), ADMIN, db)
    assert saved["status"] == "draft"
    assert saved["capabilities"] == saved["tool_ids"] == ["image_ocr"]
    service.agent_action(saved["id"], "publish", ADMIN, db)
    ver = db.get("agent_versions", saved["id"] + ":1")
    assert ver["management_profile"]["creation_method"] == "interview"


def test_usage_counts_actual_calls_and_distinct_tasks(db):
    p = project(db)
    agent = db.list("agents", p["id"])[0]
    task = db.put(
        "tasks",
        {
            "id": "acceptance-task",
            "project_id": p["id"],
            "status": "running",
            "idempotency_key": "acceptance",
            "lease_owner": "acceptance-worker",
            "lease_until": time.time() + 60,
        },
    )
    for idx in range(3):
        db.put(
            "nodes",
            {
                "id": str(idx),
                "node_id": str(idx),
                "task_id": task["id"],
                "project_id": p["id"],
                "agent_id": agent["id"],
                "agent_version": 1,
                "status": "running" if idx == 1 else "succeeded",
                "reused_from": "0" if idx == 2 else None,
            },
        )
    call = {"id": "one", "kind": "skill", "capability_id": "example", "version": "1"}
    for event in ("tool.started", "tool.completed"):
        db.event(task["id"], event, call)
    usage = capability_usage(p["id"], ADMIN, db)["items"][0]
    assert (usage["started"], usage["completed"], usage["failed"]) == (1, 1, 0)
    row = next(a for a in activity(p["id"], ADMIN, db)["agents"] if a["agent_id"] == agent["id"])
    assert (row["running"], row["attempts"], row["distinct_tasks"]) == (1, 2, 1)
    assert row["active_nodes"][0]["task_id"] == task["id"]
    db.put("tasks", {**task, "lease_until": time.time() - 10})
    row = next(a for a in activity(p["id"], ADMIN, db)["agents"] if a["agent_id"] == agent["id"])
    assert row["running"] == 0 and not row["active_nodes"]
    with pytest.raises(service.DomainError):
        capability_usage(p["id"], {"role": "operator", "id": "stranger"}, db)
