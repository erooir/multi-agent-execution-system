"""Independent product/evidence checks over immutable source records."""

import json

from .records import extract
from .team import catalog, frozen_profile


def loc_key(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, allow_nan=False)


def audit(code, candidate, records):
    by_location = {}
    for row in records:
        key = (row["source_id"], str(row["source_version"]), loc_key(row["location"]))
        by_location.setdefault(key, []).append(row)
    evidence = candidate.get("evidence")
    products = candidate.get("products")
    gaps = candidate.get("gaps")
    checks = []

    def check(name, ok, message, hard=False):
        checks.append({"name": name, "passed": bool(ok), "message": message, "hard": hard})

    check(
        "case_contract",
        isinstance(products, dict)
        and isinstance(evidence, list)
        and isinstance(gaps, list)
        and all(isinstance(g, str) and g.strip() for g in gaps),
        "产品、证据和缺口的结构必须有效",
        True,
    )
    evidence = evidence if isinstance(evidence, list) else []
    products = products if isinstance(products, dict) else {}
    seen = set()
    bad = 0
    for e in evidence:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"] or e["id"] in seen:
            bad += 1
            continue
        seen.add(e["id"])
        loc = e.get("location")
        if not isinstance(loc, dict) or not loc:
            bad += 1
            continue
        matches = by_location.get((e.get("source_id"), str(e.get("source_version")), loc_key(loc)), [])
        if code.startswith("G"):
            idx = e["id"][1:] if e["id"].startswith("g") else ""
            if (
                not idx.isascii()
                or not idx.isdigit()
                or len(idx) > 7
                or not 1 <= int(idx) <= len(records)
                or records[int(idx) - 1] not in matches
            ):
                bad += 1
                continue
        if not matches:
            bad += 1
            continue
        quote = e.get("quote")
        if quote and (
            not isinstance(quote, str)
            or not any(
                quote
                in (
                    json.dumps(rec["fields"], ensure_ascii=False, sort_keys=True)
                    if e.get("kind") == "record"
                    else rec["text"]
                )
                for rec in matches
            )
        ):
            bad += 1
    check("evidence_locations", not bad, "证据编号、版本、位置或引用与冻结输入不符", True)
    check("has_evidence", bool(seen), "没有可交付的来源证据", True)
    referenced = []

    def visit(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "evidence_ids":
                    if isinstance(v, list):
                        referenced.extend(v)
                    else:
                        referenced.append(None)
                else:
                    visit(v)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)

    visit(products)
    check(
        "product_evidence_refs",
        all(isinstance(x, str) and x in seen for x in referenced),
        "产品引用了不存在的证据编号",
        True,
    )
    expected = catalog()[code]["outputs"]

    def substantive(obj):
        if isinstance(obj, dict):
            return any(substantive(v) for v in obj.values())
        if isinstance(obj, list):
            return any(substantive(v) for v in obj)
        return obj is not None and (not isinstance(obj, str) or bool(obj.strip()))

    check(
        "usable_product_content",
        any(substantive(products.get(k)) for k in expected),
        "没有包含可交付内容的产品",
        not bool(gaps),
    )
    missing = [k for k in expected if k not in products]
    check("required_products", not missing, "缺少约定产品：" + "、".join(missing))
    check("case_gaps", not gaps, "；".join(gaps or [])[:1500] or "没有材料缺口")
    if code.startswith("G"):
        from .geo_audit import audit_products
    else:
        from .document_cases import audit_products
    checks.extend(audit_products(code, products, records))
    if code == "B03":
        from .vision import audit_archive

        checks.extend(audit_archive(candidate, records))
    return checks


def execute(
    stage, profile, inputs, previous, read_bytes, feedback, revision, checkpoint=None, *, repair_plan=None
):
    from .control import guarded
    from .provider_versions import bind

    with guarded(checkpoint), bind(profile["providers"]):
        return _execute(stage, profile, inputs, previous, read_bytes, feedback, revision, repair_plan)


def _execute(stage, profile, inputs, previous, read_bytes, feedback, revision, repair_plan):
    code = profile["case_id"]
    if frozen_profile(code)["signature"] != profile["signature"]:
        raise ValueError("用例合同或算法版本已变化，须重新受理任务")
    refs = [a["id"] for a in inputs]
    common = {"media_type": "application/json", "source_refs": refs, "warnings": []}
    action = repair_plan["action"] if repair_plan else None
    targets = repair_plan["targets"] if repair_plan else []
    if action:
        from .repair_plan import allowed_action

        if not allowed_action(code, action):
            raise ValueError("修复动作超出冻结用例的授权范围")
    if stage == "prepare":
        records, gaps = extract(
            inputs,
            read_bytes,
            revision=action == "chart_ocr_reconcile",
            case_code=code,
            chart_sources=targets if action == "chart_ocr_reconcile" and targets else None,
        )
        return {
            **common,
            "type": "case_records",
            "method": "source_extraction",
            "content": {"records": records, "gaps": gaps},
            "metrics": {"records": len(records), "source_count": len(inputs)},
        }
    prepared = next((o["content"] for o in previous if o["type"] == "case_records"), None)
    if prepared is None:
        raise ValueError("冻结计划缺少来源解析结果")
    if stage == "analyze":
        if code.startswith("G"):
            from .geo_cases import analyze
        else:
            from .document_cases import analyze
        if action:
            result = analyze(code, prepared["records"], repair_action=action, repair_targets=targets)
        else:
            result = analyze(code, prepared["records"], feedback=feedback)
        result["gaps"] = list(dict.fromkeys([*prepared["gaps"], *result["gaps"]]))
        return {
            **common,
            "type": "case_candidate",
            "method": "specialist_analysis",
            "content": result,
            "metrics": {"products": len(result["products"]), "evidence_count": len(result["evidence"])},
        }
    if stage != "review":
        raise ValueError("未知用例节点")
    candidate = next(o["content"] for o in previous if o["type"] == "case_candidate")
    checks = audit(code, candidate, prepared["records"])
    from .coverage import inventory

    scope = profile.get("scope", "reference")
    gaps = list(candidate["gaps"])
    if scope == "reference":
        from .reference_batches import review_checks

        batch_checks = review_checks(
            profile,
            inputs,
            candidate["products"],
            prepared["records"],
            inputs[0].get("project_id") if inputs else None,
        )
        batch_checks.extend(inventory(code, candidate["products"], prepared["records"]))
        checks.extend(batch_checks)
        gaps.extend(c["message"] for c in batch_checks if not c["passed"])
    from .translation import localize

    readings, notes = localize(candidate["evidence"]) if code[0] != "G" else ([], [])
    from .narrative_checks import monthly_delivery_summary

    # Scope-specific language requirements cannot be silently waived for a reference run.
    if (
        notes
        and (scope == "reference" or profile.get("translation_requested"))
        and code in {"B04", "B06", "T01", "T07"}
    ):
        checks.append(
            {"name": "reading_coverage", "passed": False, "message": "；".join(notes), "hard": False}
        )
    return {
        **common,
        "type": "case_report",
        "method": "independent_evidence_review",
        "content": {
            "case_id": code,
            "scope": scope,
            "title": profile["profile"]["title"],
            **candidate,
            "gaps": list(dict.fromkeys(gaps)),
            **(
                {
                    "reference_batch": {
                        "version": (profile.get("reference_batch", {}).get("entry") or {}).get("version"),
                        "checksum": profile.get("reference_batch", {}).get("checksum"),
                        "registered": profile.get("reference_batch", {}).get("state") == "registered",
                    }
                }
                if scope == "reference"
                else {}
            ),
            "review": checks,
            "reading_aids": readings,
            "reading_notes": notes,
            **({"summary": monthly_delivery_summary(candidate["products"])} if code == "B05" else {}),
        },
        "metrics": {"evidence_count": len(candidate["evidence"]), "gap_count": len(set(gaps))},
    }
