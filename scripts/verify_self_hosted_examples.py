"""Run supplied PDFs through the real governed Agno task runtime and local model.

Uses isolated SQLite compatibility storage, never a production DB or paid ledger.
Originals, rendered pages, model replies and task traces stay outside Git.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--cases", nargs="+", choices=["B01", "B05"], default=["B01", "B05"])
    parser.add_argument("--kind", choices=["case", "vision", "both"], default="both")
    parser.add_argument("--ocr-prefix", type=Path, help="Optional privately provisioned Tesseract prefix")
    args = parser.parse_args()
    if args.ocr_prefix:
        prefix = args.ocr_prefix.resolve()
        os.environ["HANGZHI_TESSERACT_PATH"] = str(prefix / "usr/bin/tesseract")
        os.environ["TESSDATA_PREFIX"] = str(prefix / "usr/share/tesseract-ocr/5/tessdata")
        os.environ["HANGZHI_OCR_LANG"] = "eng"
    root = args.output.resolve() / (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
    root.mkdir(parents=True, mode=0o700)
    os.environ.update(
        {
            "HANGZHI_UPSTREAM_MODEL_URL": args.base_url,
            "HANGZHI_UPSTREAM_MODEL_ID": "hangzhi-qwen38",
            "HANGZHI_UPSTREAM_API_KEY": args.key_file.read_text().strip(),
            "HANGZHI_UPSTREAM_BILLING": "internal_zero",
            "HANGZHI_UPSTREAM_USAGE_PATH": str(root / "intranet-usage.sqlite3"),
            "HANGZHI_UPSTREAM_TIMEOUT_SECONDS": "300",
            "HANGZHI_CONTEXT_TOKENS": "32768",
            "HANGZHI_MODEL_SLOTS": "2",
            "HANGZHI_DATABASE_URL": "sqlite:///" + (root / "tasks.sqlite3").as_posix(),
            "WORKBENCH_DATA_DIR": str(root / "data"),
            "HANGZHI_REQUIRE_DM": "false",
            "HANGZHI_OFFLINE": "false",
            "HANGZHI_REFERENCE_BATCHES_FILE": str(root / "reference-batches.json"),
            "WORKBENCH_MCP_AUTODISCOVERY": "off",
            "AGNO_TELEMETRY": "false",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.app.config import intranet_provider

    assert intranet_provider() is not None
    from backend.app.casework.reference_batches import signed_entry
    from backend.app.casework.team import install
    from backend.app.platform import runtime, service
    from backend.app.platform.db import repo

    user = {"id": "self-hosted-acceptance", "role": "admin"}
    project = service.create_project({"name": "Qwen public PDF acceptance"}, user, repo)
    entries = []
    sources = {}
    for code in args.cases:
        path = args.examples / code / (code + ("-M01.pdf" if code == "B01" else "-M02.pdf"))
        raw = path.read_bytes()
        sources[code] = (path, raw)
        entries.append(
            signed_entry(
                {
                    "project_id": project["id"],
                    "case_id": code,
                    "version": "supplied-pdf-v1",
                    "sources": [{"sha256": hashlib.sha256(raw).hexdigest(), "source_version": "1"}],
                    "conditions": [
                        {
                            "kind": "product_count",
                            "product": "observations" if code == "B01" else "monthly",
                            "count": 2 if code == "B01" else 13,
                        }
                    ],
                }
            )
        )
        install(project["id"], user, repo, case_id=code)
    (root / "reference-batches.json").write_text(json.dumps({"schema_version": 1, "batches": entries}))

    async def run():
        rows = []
        for code in args.cases:
            path, raw = sources[code]
            base = {
                "project_id": project["id"],
                "classification": "public",
                "source_version": "1",
                "source_id": "supplied-public:" + path.stem,
                "provenance": "User-supplied public OSCE/OHCHR original; local weights only",
            }
            jobs = []
            if args.kind in {"case", "both"}:
                asset = service.create_asset(
                    {
                        **base,
                        "name": path.name,
                        "modality": "text",
                        "media_type": "application/pdf",
                        "content": None,
                    },
                    user,
                    repo,
                    file_data=raw,
                )
                goal = (
                    "按原始报告制作中文区域观察统计快报，核对两地区本期与前期违规数、变化值、爆炸作为子集的口径，"
                    "并从脚注读取观测截止时间及时区，不能以发布日期替代。保留原始英文地名。"
                    if code == "B01"
                    else "核对报告全部月度平民伤亡图表，列出各期死亡、受伤、起止日期及口径，复算总数与原报告核对，"
                    "首尾部分月份不得当成完整月比较；说明核实记录是下限，没有逐人名册则不得宣称逐人核验。"
                )
                jobs.append(
                    (
                        "case",
                        [asset["id"]],
                        {
                            "task_type": "case_analysis",
                            "case_id": code,
                            "case_scope": "reference",
                            "goal": goal,
                        },
                    )
                )
            if args.kind in {"vision", "both"}:
                import pypdfium2 as pdfium

                document = pdfium.PdfDocument(raw)
                page = 1 if code == "B01" else 3
                image = document[page - 1].render(scale=1.5).to_pil().convert("RGB")
                target = root / f"{code}-page-{page}.png"
                image.save(target)
                asset = service.create_asset(
                    {
                        **base,
                        "source_id": base["source_id"] + f":page-{page}",
                        "name": f"{code} original page {page}",
                        "modality": "image",
                        "media_type": "image/png",
                        "content": "",
                    },
                    user,
                    repo,
                    file_data=target.read_bytes(),
                )
                goal = (
                    "根据这张原始PDF页面图片抽取Donetsk和Luhansk当前与上一期停火违规数、爆炸数，"
                    "及脚注的观测截止时间和时区。用中文说明，引用页面证据。"
                    if code == "B01"
                    else "只读取上方月度伤亡图：按列的先后顺序逐期列出月份、死亡和受伤人数，"
                    "标明首尾的部分月份日期。复算死亡和受伤合计，说明统计下限和覆盖限制。"
                    "text中请用清晰的逐月表格展示所有读到的数值；不得使用下方按武器类型的图代替月度图。"
                )
                jobs.append(("vision", [asset["id"]], {"task_type": "image_understand", "goal": goal}))
            for kind, refs, extra in jobs:
                started = time.monotonic()
                body = {
                    "project_id": project["id"],
                    "name": f"{code}-{kind}-Qwen",
                    "mode": "cloud",
                    "input_refs": refs,
                    "idempotency_key": uuid.uuid4().hex,
                    "deadline_seconds": 900,
                    "max_cost_cny": 1,
                    "max_revisions": 1,
                    "allow_partial": True,
                    **extra,
                }
                try:
                    task = service.create_task(body, user, repo)
                    claim = repo.claim("qwen-example-worker")
                    assert claim and claim["id"] == task["id"]
                    result = await runtime.process_claim(repo, claim, "qwen-example-worker")
                    detail = service.task_detail(task["id"], user, repo)
                    (root / f"{code}-{kind}.json").write_text(
                        json.dumps(detail, ensure_ascii=False, indent=2, default=str)
                    )
                    row = {
                        "case": code,
                        "kind": kind,
                        "task_id": task["id"],
                        "status": result["status"],
                        "reason_code": result.get("reason_code"),
                        "usage": result.get("result", {}).get("usage"),
                        "seconds": round(time.monotonic() - started, 3),
                    }
                except Exception as exc:  # noqa: BLE001 - keep infrastructure failures distinct from task outcomes.
                    row = {
                        "case": code,
                        "kind": kind,
                        "status": "infrastructure_error",
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:500],
                    }
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                (root / "summary.json").write_text(
                    json.dumps(
                        {
                            "storage": "isolated SQLite compatibility; not DM acceptance",
                            "model": "hangzhi-qwen38",
                            "project_id": project["id"],
                            "runs": rows,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        print("REPORT_DIRECTORY", root, flush=True)
        return 0 if all(r["status"] == "succeeded" for r in rows) else 2

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
