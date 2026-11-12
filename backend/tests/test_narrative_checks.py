from backend.app.casework.narrative_checks import (
    check_monthly_claims,
    monthly_delivery_summary,
    monthly_facts,
    reconcile_assessment,
)


def example():
    return {
        "products": {
            "monthly": [
                {"month": f"{2025 + (i - 1) // 12}-{(i - 1) % 12 + 1:02}", "complete_month": 1 < i < 13}
                for i in range(1, 14)
            ]
        }
    }


def test_actual_b05_wrong_month_counts_are_rejected_and_targeted_prose_repaired():
    facts = monthly_facts(example())
    assert facts["complete_months"] == 11 and facts["partial_months"] == 2
    for text in ("已经获得14个月度记录", "13个完整月加首尾部分月"):
        result = reconcile_assessment(text, facts)
        assert result["status"] == "corrected"
        assert result["original_assessment"] == text
        assert result["action"] == "render_from_verified_month_counts"
        assert all(c["passed"] for c in result["post_checks"])
        assert "11 个完整月" in result["assessment"]


def test_correct_counts_and_dates_are_not_false_positives():
    facts = monthly_facts(example())
    checks = check_monthly_claims("2023-02-15；13个月度记录、11个完整月、2个部分月", facts)
    assert len(checks) == 3 and all(c["passed"] for c in checks)
    assert not check_monthly_claims("2023年2月15日", facts)
    actual_prose = "首尾为部分月，其余 11 个月为完整月。覆盖核查：13 个月份均有证据位置。"
    assert all(c["passed"] for c in check_monthly_claims(actual_prose, facts))
    assert reconcile_assessment(actual_prose, facts)["status"] == "checked"
    assert not check_monthly_claims("其中3个月新增病例较多", facts)
    assert not check_monthly_claims("研究需要14个月后再验证", facts)
    assert not all(c["passed"] for c in check_monthly_claims("覆盖14个月份", facts))


def test_missing_dates_are_not_counted_as_complete_and_duplicate_months_are_visible():
    content = {
        "records": [
            {"fields": {"month": "2024-02", "start_date": "2024-02-01", "end_date": "2024-02-29"}},
            {"fields": {"month": "2024-02"}},
            {"fields": {"month": "2024-03", "start_date": "2024-03-10", "end_date": "2024-03-31"}},
            {"fields": {"page": 1}},
        ]
    }
    assert monthly_facts(content) == {
        "monthly_records": 3,
        "unique_months": 2,
        "complete_months": 1,
        "partial_months": 1,
        "unclassified_months": 1,
    }


def test_summary_only_declares_reconciliation_when_computed_check_agrees():
    p = example()["products"]
    p["reconciliation"] = [{"computed": [30, 40], "agrees": True}]
    assert "合计 70" in monthly_delivery_summary(p)
    p["reconciliation"][0]["agrees"] = False
    assert "不能据此宣称统计完整" in monthly_delivery_summary(p)
    assert "合计 70" not in monthly_delivery_summary(p)


def test_long_citations_preserve_exact_span_and_disclose_truncation():
    from backend.app.casework.document_common import Run

    text = "A" * 340 + ". " + "B" * 400 + "."
    record = {"source_id": "s", "source_version": "v1", "location": {"page": 1}, "text": text, "fields": {}}
    run = Run([record])
    excerpt = run.excerpt(record)
    assert excerpt["text"] == "A" * 340 + "."
    assert excerpt["text_truncated"] is True
    assert run.evidence[0]["quote_truncated"] is True
    assert run.evidence[0]["quote"] in text
