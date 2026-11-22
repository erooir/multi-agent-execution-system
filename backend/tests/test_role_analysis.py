"""Independent registered-tool comparisons against real case products on synthetic materials."""

from copy import deepcopy

import pytest

from backend.app.casework import document_cases, geo_cases, role_analysis
from backend.app.casework.records import extract
from backend.app.platform.analysis_tools import TOOLS
from backend.tests.test_casework_documents import samples as document_samples
from backend.tests.test_casework_geo_samples import samples


def source(rows, sid="source"):
    return {
        "id": sid,
        "source_version": "v1",
        "modality": "structured",
        "media_type": "application/json",
        "content": {"records": rows},
    }


def checks(case, src, candidate=None):
    sources = [src]
    records, _ = extract(sources, lambda _: None)
    candidate = (
        candidate
        if candidate is not None
        else (geo_cases if case[0] == "G" else document_cases).analyze(case, records)
    )
    plan = role_analysis.requests(case, sources, records, candidate)
    output = []
    for tool, args in plan:
        op = next(o for o in TOOLS if o.id == tool)
        result = op.execute(
            lambda key: {
                **next(s for s in sources if s["id"] == key),
                "prepared_records": [r for r in records if r["source_id"] == key],
            },
            op.schema.model_validate(args),
        )
        output.append((tool, result, role_analysis.compare(case, tool, result, candidate)))
    return candidate, output


def test_hotspot_key_audit_reads_actual_observations_without_inventing_a_kind():
    rows = samples("G01")
    src = source(rows)
    records, _ = extract([src], lambda _: None)
    ((tool, args),) = role_analysis.requests("G01", [src], records)
    assert tool == "table.audit_keys" and args["exclude_kinds"] == ["batch"] and "where" not in args
    _, results = checks("G01", src)
    assert results[0][1]["total_rows"] == 8
    for row in rows:
        if "observed_at" in row:
            row["kind"] = "hotspot"
    records, _ = extract([src], lambda _: None)
    assert role_analysis.requests("G01", [src], records)[0][1]["where"] == {"kind": "hotspot"}


@pytest.mark.parametrize(
    ("code", "id", "count"),
    [
        ("G03", "series.baseline_anomaly", 1),
        ("G06", "raster.class_transition", 2),
        ("G09", "geo.route_detour", 3),
    ],
)
def test_registered_computation_and_semantic_tamper_rejection(code, id, count):
    src = source(samples(code))
    candidate, results = checks(code, src)
    assert len(results) == count and all(
        tool_id == id and all(c["passed"] for c in checked) for tool_id, _, checked in results
    )
    forged = deepcopy(candidate)
    if code == "G03":
        forged["products"]["anomaly"]["difference"] = 123.456
    elif code == "G06":
        forged["products"]["transitions"][0]["area_m2"] += 1
    else:
        forged["products"]["od_comparison"][0]["candidates"][0]["extra_km"] += 1
    assert any(
        not c["passed"] and c["hard"]
        for _, out, _ in results
        for c in role_analysis.compare(code, id, out, forged)
    )


def test_soil_sample_key_audit_does_not_invent_sample_kind():
    _, results = checks("G07", source(samples("G07")))
    assert len(results) == 1 and results[0][1]["total_rows"] == 2


def test_monthly_comparison_requires_source_scope_unit_roles_and_exact_value_field():
    common = {"unit": "人", "scope": "测试地区"}
    rows = [
        dict(
            common,
            kind="detail",
            month="2023-01",
            start_date="2023-01-01",
            end_date="2023-01-31",
            deaths=2,
            injured=4,
        ),
        dict(
            common,
            kind="detail",
            month="2023-02",
            start_date="2023-02-01",
            end_date="2023-02-28",
            deaths=3,
            injured=5,
        ),
        dict(common, kind="total", deaths=5, injured=9),
    ]
    candidate, results = checks("B05", source(rows))
    assert len(results) == 2 and all(
        tool == "stats.reconcile_total" and tested[0]["passed"] for tool, _, tested in results
    )
    tamper = deepcopy(candidate)
    tamper["products"]["reconciliation"][0]["computed"][0] = 22
    assert not role_analysis.compare("B05", results[0][0], results[0][1], tamper)[0]["passed"]
    for row in rows:
        row.pop("unit")
    assert not checks("B05", source(rows))[1]


@pytest.mark.parametrize("bad_month", [None, "", "2024-01"])
def test_structured_month_key_audit_excludes_only_marked_totals_and_reports_key_issues(bad_month):
    months = [
        {"month": "2024-01", "deaths": 0, "injured": 2, "scope": "workplace"},
        {"month": "2024-02", "deaths": 1, "injured": 3, "scope": "workplace"},
        {"kind": "reported_total", "deaths": 1, "injured": 5, "scope": "workplace"},
    ]
    _, [(tool, out, checks_good)] = checks("B05", source(months))
    assert tool == "table.audit_keys" and checks_good[0]["passed"]
    assert out["source_rows"]["locations"] == [{"row": 1}, {"row": 2}]
    months[1]["month"] = bad_month
    _, [(tool, out, checks_bad)] = checks("B05", source(months))
    assert out["missing_key_count"] or out["conflict_groups"]
    assert tool == "table.audit_keys" and checks_bad == []
    assert not checks("B05", source([*months, {"kind": "metadata", "deaths": 10}]))[1]


def test_printed_chart_key_audit_and_duplicate_clues():
    rows = [
        {
            "kind": "chart_label_ocr",
            "month": "2023-02",
            "deaths": 3,
            "injured": 2,
            "start_date": "2023-02-01",
            "end_date": "2023-02-28",
        }
    ]
    candidate, results = checks("B05", source(rows))
    tool, out, compared = results[0]
    assert tool == "table.audit_keys" and compared[0]["passed"]
    tamper = deepcopy(candidate)
    tamper["products"]["monthly"] *= 2
    assert not role_analysis.compare("B05", tool, out, tamper)[0]["passed"]
    assert not checks("B05", source([*rows, rows[0]]))[1][0][2]


def test_project_ledger_excludes_frozen_taxonomy_and_detects_portfolio_count_tampering():
    rows = [r["fields"] for r in document_samples("T04")]
    candidate, results = checks("T04", source(rows))
    assert [tool for tool, _, _ in results] == ["table.audit_keys"]
    tool, out, checked = results[0]
    assert tool == "table.audit_keys" and checked[0]["passed"] and out["total_rows"] == 2
    assert out["source_rows"]["locations"] == [{"row": 2}, {"row": 3}]
    bad = deepcopy(candidate)
    bad["products"]["portfolio"].pop()
    assert not role_analysis.compare("T04", tool, out, bad)[0]["passed"]
    assert not checks("T04", source([*rows, rows[-1]]))[1][0][2]


def test_project_funding_reconciliation_requires_typed_scoped_details_and_reported_amount():
    rows = [dict(r["fields"]) for r in document_samples("T04")]
    for row in rows[1:]:
        row.update(kind="detail", scope="community")
    rows.append(
        {
            "kind": "reported_total",
            "scope": "community",
            "currency": "EUR",
            "total_cost": 102,
            "contribution": 71,
        }
    )
    _, results = checks("T04", source(rows))
    assert [tool for tool, _, _ in results] == [
        "table.audit_keys",
        "stats.reconcile_total",
        "stats.reconcile_total",
    ]
    assert results[0][1]["total_rows"] == 2
    _, first, first_checks = results[1]
    _, second, second_checks = results[2]
    assert first["computed_total"] == first["reported_total"] == "102" and first_checks[0]["passed"]
    assert second["computed_total"] == "73" and second["reported_total"] == "71"
    assert not second_checks[0]["passed"] and second_checks[0]["hard"]
    rows[-1]["currency"] = "CNY"
    assert [tool for tool, _, _ in checks("T04", source(rows))[1]] == ["table.audit_keys"]


def test_unregistered_geometry_and_foreign_records_are_not_silently_used():
    raw = samples("G06")
    src = source(raw)
    records, _ = extract([src], lambda _: None)
    missing = deepcopy(records)
    target = next(r for r in missing if r["fields"].get("kind") == "cover_grid")
    target["fields"]["origin_x"] += 10
    assert not role_analysis.requests("G06", [src], missing, {})
    wrong = deepcopy(records)
    wrong[0]["source_version"] = "other"
    assert not role_analysis.requests("G06", [src], wrong, {})


def test_annual_raw_column_cannot_disprove_a_separately_scoped_dictionary_repair():
    rows = samples("G03")
    for f in rows:
        if "extent" in f:
            f["measured"] = f.pop("extent")
    src = source(rows)
    mapping = source(
        [{"kind": "field_dictionary", "target_source_id": src["id"], "aliases": {"measured": "extent"}}],
        "dictionary",
    )
    records, _ = extract([src, mapping], lambda _: None)
    candidate = geo_cases.analyze(
        "G03", records, repair_action="apply_field_dictionary", repair_targets=[src["id"]]
    )
    assert candidate["products"]["anomaly"]["difference"] == -3 and not candidate["gaps"]
    assert not role_analysis.requests("G03", [src, mapping], records, candidate)
