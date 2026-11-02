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
    tool, out, checked = results[0]
    assert tool == "table.audit_keys" and checked[0]["passed"] and out["total_rows"] == 2
    assert out["source_rows"]["locations"] == [{"row": 2}, {"row": 3}]
    bad = deepcopy(candidate)
    bad["products"]["portfolio"].pop()
    assert not role_analysis.compare("T04", tool, out, bad)[0]["passed"]
    assert not checks("T04", source([*rows, rows[-1]]))[1][0][2]


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
