"""Deterministic gates. A model opinion can never erase these findings."""

from __future__ import annotations

import json
import math


def text_of(asset: dict) -> str:
    content = asset.get("content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def deterministic_quality(request: dict, output: dict, inputs: list[dict], *, scientific_terms=False) -> dict:
    issues: list[dict] = []
    checks: list[dict] = []
    assets = {asset["id"]: asset for asset in inputs}

    def check(name: str, passed: bool, message: str, hard: bool = False):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            category = (
                "content_coverage"
                if name in {"min_records", "required_fields", "min_length"}
                else "output_contract"
            )
            issues.append({"code": name, "category": category, "message": message, "hard": hard})

    content = output.get("content")
    refs = output.get("source_refs", [])
    check("nonempty_output", content not in (None, "", [], {}), "产物为空", True)
    data_free = request.get("task_type") == "autonomous_analysis" and not inputs
    check(
        "source_refs",
        (not refs if data_free else bool(refs) and all(ref in assets for ref in refs)),
        "产物来源缺失、不属于冻结输入，或无资料任务伪造了引用",
        True,
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
    if scientific_terms and task_type == "text_analysis":
        from .scientific_terms import conflicts

        issues_found = conflicts(content, inputs)
        check(
            "scientific_source_definitions",
            not issues_found,
            " ".join(i["message"] for i in issues_found),
            True,
        )
    if task_type == "case_analysis":
        kind = output.get("type")
        check(
            "case_artifact_type",
            kind in {"case_records", "case_candidate", "case_report"} and isinstance(content, dict),
            "用例节点输出结构无效",
            True,
        )
        if kind == "case_report" and isinstance(content, dict):
            review = content.get("review")
            check(
                "case_review_present",
                isinstance(review, list)
                and bool(review)
                and content.get("case_id") == request.get("case_id"),
                "缺少独立审核或用例不一致",
                True,
            )
            for result in review or []:
                check(result["name"], result["passed"], result["message"], result["hard"])
    elif task_type == "structured_transform":
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
    elif task_type in {"source_summary", "text_analysis", "autonomous_analysis"}:
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
                if not isinstance(citation, dict):
                    return False
                if citation.get("kind") == "external_result":
                    return citation in output.get("external_evidence", [])
                if citation.get("source_id") not in refs:
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

            valid = isinstance(citations, list) and (
                not citations if data_free else bool(citations) and all(valid_citation(c) for c in citations)
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
    elif task_type == "image_ocr":
        images = content.get("images", []) if isinstance(content, dict) else []
        valid = bool(images) and {item.get("source_id") for item in images} == set(assets)
        for image in images:
            blocks = image.get("blocks", [])
            valid = valid and isinstance(image.get("text"), str) and isinstance(blocks, list)
            valid = valid and image.get("no_text") is (not blocks)
            for block in blocks:
                score = block.get("confidence")
                bbox = block.get("bbox")
                valid = valid and isinstance(block.get("text"), str)
                valid = valid and type(score) in {int, float} and math.isfinite(score) and 0 <= score <= 1
                valid = valid and isinstance(bbox, list) and len(bbox) >= 4
                valid = valid and all(
                    isinstance(point, list)
                    and len(point) == 2
                    and all(type(value) in {int, float} and math.isfinite(value) for value in point)
                    for point in bbox
                )
        check("ocr_result_schema", valid, "OCR 产物必须覆盖每张图片并包含真实坐标和置信度", True)
        text = "\n".join(image.get("text", "") for image in images)
        check("min_length", len(text.strip()) >= policy.get("min_length", 1), "识别文本长度未达到要求")
    elif task_type == "image_understand":
        text = content.get("text", "") if isinstance(content, dict) else ""
        evidence = content.get("evidence", []) if isinstance(content, dict) else []
        valid = bool(evidence)
        for item in evidence:
            source = assets.get(item.get("source_id"))
            bbox = item.get("bbox")
            valid = valid and bool(source) and item.get("source_version") == source["source_version"]
            valid = valid and (
                bbox is None
                or isinstance(bbox, list)
                and len(bbox) == 4
                and all(type(value) in {int, float} and math.isfinite(value) for value in bbox)
            )
        check("image_evidence", valid, "视觉证据必须引用真实图片和有效区域", True)
        check("min_length", len(text.strip()) >= policy.get("min_length", 1), "图像理解文本长度未达到要求")
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
                    "code": (
                        "review_protocol_failed"
                        if model_review.get("protocol_error")
                        else "automatic_review_failed"
                    ),
                    "category": (
                        "review_protocol" if model_review.get("protocol_error") else "content_coverage"
                    ),
                    "message": "；".join(model_review.get("issues", [])) or "自动评审未通过",
                    "suggested_changes": model_review.get("suggested_changes", []),
                    "hard": False,
                },
            ]
    return result
