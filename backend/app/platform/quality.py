"""Deterministic gates. A model opinion can never erase these findings."""

from __future__ import annotations

import json


def text_of(asset: dict) -> str:
    content = asset.get("content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def deterministic_quality(request: dict, output: dict, inputs: list[dict]) -> dict:
    issues: list[dict] = []
    checks: list[dict] = []
    assets = {asset["id"]: asset for asset in inputs}

    def check(name: str, passed: bool, message: str, hard: bool = False):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            issues.append({"code": name, "message": message, "hard": hard})

    content = output.get("content")
    refs = output.get("source_refs", [])
    check("nonempty_output", content not in (None, "", [], {}), "产物为空", True)
    check(
        "source_refs", bool(refs) and all(ref in assets for ref in refs), "产物来源缺失或不属于冻结输入", True
    )
    policy = request.get("quality", {}) or {}
    if output.get("contract_errors"):
        check(
            "model_output_invalid",
            False,
            "模型产物不符合契约，请修正字段：" + "; ".join(output["contract_errors"]),
            True,
        )
    task_type = request.get("task_type")
    if task_type == "structured_transform":
        rows = content.get("records") if isinstance(content, dict) else None
        valid = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        check("record_schema", valid, "结构化产物必须包含对象数组 records", True)
        if valid:
            check("min_records", len(rows) >= policy.get("min_records", 1), "记录数未达到任务要求")
            fields = policy.get("required_fields", [])
            check(
                "required_fields",
                all(all(field in row and row[field] is not None for field in fields) for row in rows),
                "记录缺少要求的字段",
            )
    elif task_type in {"source_summary", "text_analysis"}:
        if output.get("method") == "extractive":
            fragments = content.get("fragments", []) if isinstance(content, dict) else []
            check("extractive_fragments", bool(fragments), "未找到可提取的来源片段", True)
            valid = True
            for fragment in fragments:
                source = assets.get(fragment.get("source_id"))
                start, end = fragment.get("start"), fragment.get("end")
                valid = valid and bool(source) and type(start) is int and type(end) is int
                if valid:
                    original = text_of(source)
                    valid = 0 <= start < end <= len(original) and original[start:end] == fragment.get("text")
            check("exact_source_spans", valid, "提取片段不能在原始来源中精确定位", True)
            text = "\n".join(fragment.get("text", "") for fragment in fragments)
        else:
            text = content.get("text", "") if isinstance(content, dict) else ""
            citations = content.get("citations", []) if isinstance(content, dict) else []

            def valid_citation(citation):
                if not isinstance(citation, dict) or citation.get("source_id") not in refs:
                    return False
                if citation.get("kind") == "derived_result":
                    return citation in output.get("derived_evidence", [])
                source = assets.get(citation.get("source_id"))
                quote = citation.get("quote")
                if (
                    not source
                    or not isinstance(quote, str)
                    or not quote.strip()
                    or quote not in text_of(source)
                ):
                    return False
                if output.get("method") == "tool_agent":
                    start, end = citation.get("start"), citation.get("end")
                    return (
                        type(start) is int
                        and type(end) is int
                        and 0 <= start < end <= len(text_of(source))
                        and text_of(source)[start:end] == quote
                        and citation.get("source_version") == source["source_version"]
                    )
                return True

            valid = (
                isinstance(citations, list) and bool(citations) and all(valid_citation(c) for c in citations)
            )
            check("grounded_citations", valid, "模型引用必须逐字存在于真实输入来源", True)
        check(
            "min_length",
            isinstance(text, str) and len(text.strip()) >= policy.get("min_length", 1),
            "文本长度未达到任务要求",
        )
    elif task_type == "image_inspect":
        images = content.get("images", []) if isinstance(content, dict) else []
        check(
            "image_metadata",
            bool(images)
            and all(
                isinstance(item, dict)
                and item.get("decoded") is True
                and type(item.get("width")) is int
                and item["width"] > 0
                and type(item.get("height")) is int
                and item["height"] > 0
                and item.get("source_id") in assets
                for item in images
            ),
            "图片必须通过真实解码并包含有效尺寸和来源",
            True,
        )
    else:
        check("supported_output", False, "没有经过验证的产物校验器", True)
    return {
        "verdict": "passed" if not issues else "failed",
        "passed": not issues,
        "issues": issues,
        "hard_failures": [issue for issue in issues if issue["hard"]],
        "checks": checks,
        "evidence_refs": list(refs),
        "reviewer": "deterministic-v2",
    }


def combine_review(deterministic: dict, model_review: dict | None = None) -> dict:
    """The separate model context supplies evidence, never overrides a hard gate."""
    result = dict(deterministic)
    if model_review is not None:
        result["model_review"] = model_review
        result["model_review_limit"] = "独立上下文评审；同一模型仍可能存在相关偏差，不代替确定性校验"
        if model_review.get("verdict") != "passed":
            result["passed"] = False
            result["verdict"] = "failed"
            result["issues"] = [
                *deterministic["issues"],
                {
                    "code": "automatic_review_failed",
                    "message": "；".join(model_review.get("issues", [])) or "自动评审未通过",
                    "suggested_changes": model_review.get("suggested_changes", []),
                    "hard": False,
                },
            ]
    return result
