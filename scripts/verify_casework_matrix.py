"""Run redistributable reduced fixtures through a dedicated offline DM HTTP deployment."""

import argparse
import json
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def verify(url, output):
    from backend.app.casework.team import catalog
    from backend.tests.test_casework_documents import samples as docs
    from backend.tests.test_casework_geo_samples import assert_expected, media_samples
    from backend.tests.test_casework_geo_samples import samples as geo

    parsed = urlsplit(url)
    if parsed.hostname != "127.0.0.1" or parsed.port in {None, 8000} or parsed.scheme != "http":
        raise ValueError("仅允许独立本机验收实例")
    private = (Path(__file__).resolve().parents[1] / ".local").resolve()
    output = output.resolve()
    if not output.is_relative_to(private) or output.exists():
        raise ValueError("使用.local内的新验收输出文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"cases": {}}
    with httpx.Client(base_url=url, trust_env=False, timeout=90) as c:

        def post(path, data):
            return c.post(path, json=data).raise_for_status().json()

        post(
            "/api/auth/login",
            {
                "username": os.getenv("HANGZHI_SMOKE_USERNAME", "admin"),
                "password": os.environ["HANGZHI_SMOKE_PASSWORD"],
            },
        )
        state = c.get("/api/v2/bootstrap").raise_for_status().json()
        if state["system"]["database"] != "dm" or not state["system"]["offline"]:
            raise RuntimeError("需要真正离线的达梦验收服务")
        pid = post("/api/v2/projects", {"name": "缩小批次验证 " + uuid.uuid4().hex[:10]})["id"]
        post("/api/v2/casework/install?project_id=" + pid, {})
        report["project_id"] = pid
        for code in sorted(catalog()):
            groups = defaultdict(list)
            refs = []
            if code.startswith("G"):
                ids = {}
                for label, data in media_samples(code).items():
                    r = (
                        c.post(
                            "/api/v2/data-assets/upload",
                            data={"project_id": pid, "source_id": label, "provenance": "synthetic pixels"},
                            files={"file": (label + ".png", data, "image/png")},
                        )
                        .raise_for_status()
                        .json()
                    )
                    ids[label] = r["id"]
                    refs.append(r["id"])
                groups["known"] = geo(code, ids)
            else:
                for rec in docs(code):
                    f = dict(rec["fields"])
                    if rec["text"]:
                        f["text"] = rec["text"]
                    groups[rec["source_id"]].append(f)
            for label, rows in groups.items():
                a = post(
                    "/api/v2/data-assets",
                    {
                        "project_id": pid,
                        "name": label,
                        "source_id": label,
                        "provenance": "synthetic reference-contract sample",
                        "modality": "structured",
                        "media_type": "application/json",
                        "content": {"records": rows},
                        "classification": "local",
                    },
                )
                refs.append(a["id"])
            req = {
                "project_id": pid,
                "name": "合成验证 " + code,
                "goal": "验算缩小批次并核对证据",
                "input_refs": refs,
                "task_type": "case_analysis",
                "case_id": code,
                "case_scope": "sample",
                "idempotency_key": code,
                "deadline_seconds": 1800,
                "allow_partial": False,
            }
            assert post("/api/v2/tasks/preflight", req)["ready"]
            t = post("/api/v2/tasks", req)
            report["cases"][code] = {"id": t["id"], "status": "accepted"}
        for code, item in report["cases"].items():
            for _ in range(1200):
                response = c.get("/api/v2/tasks/" + item["id"] + "/result").raise_for_status()
                if response.status_code == 200:
                    break
                time.sleep(1)
            result = response.json()
            item.update(status=result.get("status"), reason=result.get("reason_code"))
            if result.get("artifacts"):
                a = c.get("/api/v2/artifacts/" + result["artifacts"][0]["id"]).raise_for_status().json()
                content = a["content"]
                item.update(
                    evidence=len(content["evidence"]),
                    checks=len(content["review"]),
                    passed_checks=all(x["passed"] for x in content["review"]),
                )
                if code[0] == "G" and item["status"] == "succeeded":
                    assert_expected(code, content)
            item["calls"] = result.get("usage", {}).get("request_count")
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(code, item["status"], flush=True)
    return all(
        v["status"] == "succeeded" and v.get("passed_checks") and v["calls"] == 0
        for v in report["cases"].values()
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    raise SystemExit(0 if verify(args.url, args.output) else 2)
