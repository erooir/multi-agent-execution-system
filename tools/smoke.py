"""Real public/synthetic acceptance. Never resets either lifetime or development ledger."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from backend.app.model_gateway import BudgetLedger, model_gateway
from backend.app.platform.db import repo

REPORT_ROOT = Path(__file__).resolve().parents[1] / ".local"


def development_ledger():
    path = REPORT_ROOT / "development-test.sqlite3"
    created = not path.exists()
    ledger = BudgetLedger(path)
    if created:
        prior = model_gateway.ledger.usage()["total_cny"]
        if prior:
            identifier = ledger.reserve(prior, "prior-local-validation-baseline", "baseline", limit=10)
            ledger.uncertain(identifier)
    return ledger


def run(case, client, project, development):
    fixture = (
        [{"team": "A", "amount": 2}, {"team": "B", "amount": 4}, {"team": "A", "amount": 3}]
        if case != "evidence"
        else "合成公开资料：航智验证项目于2026年10月启动，负责人张明，目标是实现资料提取与摘要。尚未确定交付日期。"
    )
    response = client.post(
        "/api/v2/data-assets",
        json={
            "project_id": project["id"],
            "name": f"合成公开验收-{case}",
            "modality": "text" if case == "evidence" else "structured",
            "media_type": "text/plain" if case == "evidence" else "application/json",
            "content": fixture,
            "source_id": f"synthetic:iteration:{uuid.uuid4().hex}",
            "provenance": "本地生成的公开合成验收样本",
            "classification": "public",
        },
    )
    response.raise_for_status()
    source = response.json()
    goals = {
        "evidence": "使用证据分析技能，提取项目启动时间、负责人、目标并总结，准确引用原文，交付日期缺失请标明。",
        "aggregate": "使用表格分析技能，通过MCP统计amount按team分组的合计，并说明各组结果。不要凭心算回答。",
        "filter": "通过MCP筛选team为A的记录并按amount降序排列，概括结果并引用计算证据。",
    }
    request = {
        "project_id": project["id"],
        "name": f"真实模型验收-{case}",
        "goal": goals[case],
        "task_type": "text_analysis",
        "mode": "cloud",
        "input_refs": [source["id"]],
        "max_cost_cny": 1,
        "max_revisions": 1,
        "deadline_seconds": 300,
        "idempotency_key": uuid.uuid4().hex,
    }
    check = client.post("/api/v2/tasks/preflight", json=request)
    check.raise_for_status()
    reservation = development.reserve(1, "development-test", request["idempotency_key"], limit=10)
    task_id = None
    try:
        response = client.post("/api/v2/tasks", json=request)
        response.raise_for_status()
        task_id = response.json()["id"]
        for _ in range(320):
            response = client.get(f"/api/v2/tasks/{task_id}")
            response.raise_for_status()
            task = response.json()
            if task["status"] in {"succeeded", "partial", "failed", "cancelled"}:
                break
            time.sleep(1)
        else:
            client.post(f"/api/v2/tasks/{task_id}/cancel")
            raise RuntimeError("smoke_timeout")
        usage = task["result"]["usage"]
        if usage.get("accounting_complete"):
            development.settle(
                reservation,
                {"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"]},
            )
        else:
            development.uncertain(reservation)
        calls = task.get("tool_calls", [])
        report = {
            "case": case,
            "task_id": task_id,
            "status": task["status"],
            "reason_code": task.get("reason_code"),
            "usage": usage,
            "calls": [
                {key: call[key] for key in ("capability_id", "kind", "status", "summary")} for call in calls
            ],
            "quality_passed": task.get("quality_verdict", {}).get("passed"),
            "development_budget": development.budget(10),
        }
        (REPORT_ROOT / f"iteration-smoke-{case}-{task_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
        assert task["status"] == "succeeded", f"Task {task_id} failed: {task.get('reason_code')}"
        expected = (
            "mcp.tables.aggregate"
            if case == "aggregate"
            else "mcp.tables.transform"
            if case == "filter"
            else "data.locate_evidence"
        )
        assert any(call["capability_id"] == expected and call["status"] == "completed" for call in calls), (
            "Expected real capability call missing"
        )
        if case in {"evidence", "aggregate"}:
            assert any(call["kind"] == "skill" and call["status"] == "completed" for call in calls)
        artifact = client.get(f"/api/v2/artifacts/{task['result']['artifacts'][0]['id']}")
        artifact.raise_for_status()
        assert artifact.json()["content"]["citations"]
        if case != "evidence":
            stored = repo.get("tasks", task_id)
            results = [
                stored["tool_checkpoints"][call["id"]]["result"]
                for call in calls
                if call["capability_id"] == expected and call["status"] == "completed"
            ]
            if case == "aggregate":
                assert any(
                    {group["group"]: group["value"] for group in result["groups"]} == {"A": 5, "B": 4}
                    for result in results
                )
            else:
                assert any(
                    result["records"] == [{"team": "A", "amount": 3}, {"team": "A", "amount": 2}]
                    for result in results
                )
        return report
    except BaseException:
        # Unacknowledged acceptance may still run: preserve the whole development reservation.
        development.uncertain(reservation)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case", choices=["all", "evidence", "aggregate", "filter", "browser"], default="all"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = os.environ.get("HANGZHI_SMOKE_PASSWORD")
    if not password:
        raise SystemExit("请先在本次终端设置 HANGZHI_SMOKE_PASSWORD，不在源码保存本机密码")
    development = development_ledger()
    with httpx.Client(
        base_url=args.base_url, timeout=20, trust_env=False, headers={"Origin": args.base_url}
    ) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": args.username, "password": password},
        )
        response.raise_for_status()
        projects = client.get("/api/v2/projects").json()
        project = next((item for item in projects if item["name"] == "本轮真实验收"), None)
        if project is None:
            response = client.post(
                "/api/v2/projects",
                json={"name": "本轮真实验收", "description": "仅含合成公开资料及真实验收记录"},
            )
            response.raise_for_status()
            project = response.json()
        # Fresh projects initially contain fixed executors; enable versioned tool agents explicitly.
        from scripts.enable_agent_tools import enable

        user = client.get("/api/auth/me").json()
        enable(repo, user)
        if args.case == "browser":
            reservation = development.reserve(1, "development-test-browser", uuid.uuid4().hex, limit=10)
            result_file = REPORT_ROOT / f"browser-cloud-{uuid.uuid4().hex}.json"
            env = {key: value for key, value in os.environ.items() if "DEEPSEEK" not in key.upper()}
            env.update(
                HANGZHI_BROWSER_CLOUD_SMOKE="1",
                HANGZHI_BROWSER_TASK_FILE=str(result_file),
                HANGZHI_BROWSER_URL=args.base_url,
            )
            try:
                npm = "npm.cmd" if os.name == "nt" else "npm"
                completed = subprocess.run(
                    [npm, "run", "test:browser", "--", "--grep", "云端浏览器"],
                    cwd=REPORT_ROOT.parent / "frontend",
                    env=env,
                    timeout=390,
                    check=False,
                )
                record = json.loads(result_file.read_text(encoding="utf-8"))
                task = client.get(f"/api/v2/tasks/{record['task_id']}").json()
                usage = task["result"]["usage"]
                if usage["accounting_complete"]:
                    development.settle(
                        reservation,
                        {"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"]},
                    )
                else:
                    development.uncertain(reservation)
                report = {
                    "case": "browser",
                    "task_id": task["id"],
                    "status": task["status"],
                    "usage": usage,
                    "development_budget": development.budget(10),
                    "browser_exit_code": completed.returncode,
                }
                result_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False), flush=True)
                assert completed.returncode == 0 and task["status"] == "succeeded"
            finally:
                development.uncertain(reservation)
            return
        for case in ["evidence", "aggregate", "filter"] if args.case == "all" else [args.case]:
            run(case, client, project, development)


if __name__ == "__main__":
    main()
