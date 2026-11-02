"""Numerical and source-location oracles for independently authorized analysis operations."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.platform.analysis_tools import TOOLS
from backend.tests.test_casework_geo_samples import samples


def source(rows, sid="source", **extras):
    return {
        "id": sid,
        "source_version": "reference-2",
        "media_type": "application/json",
        "modality": "structured",
        "content": rows,
        **extras,
    }


def call(tool_id, assets, **args):
    op = next(tool for tool in TOOLS if tool.id == tool_id)
    return op.execute(
        {a["id"]: a for a in assets}.__getitem__,
        op.schema.model_validate({"source_id": assets[0]["id"], **args}),
    )


def test_duplicate_identical_conflicting_missing_keys_and_csv_locations():
    a = source("id,value\nx,2\nx,2\ny,3\ny,4\n,5\n", media_type="text/csv")
    out = call("table.audit_keys", [a], key_fields=["id"])
    assert out["duplicate_groups"] == 1 and out["conflict_groups"] == 1 and out["missing_key_count"] == 1
    assert out["missing_locations"] == [{"row": 6}]
    assert out["source_rows"]["source_version"] == "reference-2"
    assert out["examples"][0]["locations"] == [{"row": 2}, {"row": 3}]
    with pytest.raises(ValueError):
        call("table.audit_keys", [a], key_fields=["nonexistent"])


def test_exact_reconciliation_preserves_mismatch_and_scope():
    rows = [
        {"kind": "detail", "item": "1", "value": "0.1"},
        {"kind": "detail", "item": "2", "value": "0.2"},
        {"kind": "total", "value": "0.31"},
    ]
    rows = [dict(r, scope="Zone-A", unit="kg") for r in rows]
    rows += [dict(rows[0], scope="Zone-B", unit="g", value="98")]
    args = {"scope": "Zone-A", "unit": "kg", "value_field": "value", "detail_id_field": "item"}
    out = call("stats.reconcile_total", [source(rows)], **args)
    assert out["computed_total"] == "0.3" and out["reported_total"] == "0.31"
    assert out["difference"] == "-0.01" and not out["agrees"] and out["detail_count"] == 2
    assert out["source_rows"]["locations"] == [{"row": 1}, {"row": 2}, {"row": 3}]
    for extra in (rows[0], rows[2], dict(rows[0], item="3", unit="g"), dict(rows[0], item="4", value=None)):
        with pytest.raises(ValueError):
            call("stats.reconcile_total", [source([*rows, extra])], **args)


def test_baseline_anomaly_compares_only_selected_observations_and_tied_rank():
    args = {
        "where": {"month": 9, "hemisphere": "N"},
        "value_field": "extent",
        "unit": "Mkm²",
        "baseline_start": 2000,
        "baseline_end": 2001,
        "target_year": 2003,
        "rank_start": 2000,
    }
    rows = [
        {"kind": "policy", "extent_unit": "Mkm²"},
        *[
            {"year": y, "extent": v, "month": 9, "hemisphere": "N"}
            for y, v in [(2000, 6), (2001, 4), (2002, 3), (2003, 3)]
        ],
        {"year": 2000, "extent": 999, "month": 8, "hemisphere": "N"},
    ]
    out = call("series.baseline_anomaly", [source(rows)], **args)
    assert (
        out["target"],
        out["baseline_mean"],
        out["difference"],
        out["percent"],
        out["ascending_rank"],
    ) == (3, 5, -2, -40, 1)
    assert out["gaps"] == [] and out["source_rows"]["locations"][-1] == {"row": 5}
    missing = call("series.baseline_anomaly", [source([r for r in rows if r.get("year") != 2000])], **args)
    assert (
        missing["gaps"] == [{"year": 2000, "reason": "missing"}]
        and missing["difference"] is None
        and missing["ascending_rank"] is None
    )
    conflict = call("series.baseline_anomaly", [source([*rows, dict(rows[4], extent=20)])], **args)
    assert conflict["gaps"] == [{"year": 2003, "reason": "conflict"}] and conflict["target"] is None
    with pytest.raises(ValueError):
        call("series.baseline_anomaly", [source([*rows, dict(rows[4], unit="km²")])], **args)


def polygon(a, b):
    return {
        "type": "Feature",
        "properties": {"crs": "EPSG:3857"},
        "geometry": {"type": "Polygon", "coordinates": [[[a, 0], [b, 0], [b, 10], [a, 10], [a, 0]]]},
    }


def test_overlap_dissolves_duplicated_footprints_and_requires_metric_projection():
    left = source(
        {"type": "FeatureCollection", "features": [polygon(0, 10), polygon(5, 15)]},
        media_type="application/geo+json",
    )
    right = source(
        {"type": "FeatureCollection", "features": [polygon(10, 20)]},
        sid="mask",
        media_type="application/geo+json",
    )
    args = {"other_source_id": "mask", "metric_crs": "EPSG:3857"}
    out = call("geo.overlay_area", [left, right], **args)
    assert (out["left_area"], out["right_area"], out["intersection"], out["union"]) == (150, 100, 50, 200)
    assert out["sources"][0]["locations"] == [{"row": 1}, {"row": 2}]
    with pytest.raises(ValueError):
        call("geo.overlay_area", [left, right], **{**args, "metric_crs": "EPSG:4326"})
    bad = deepcopy(right)
    bad["content"]["features"][0]["properties"].pop("crs")
    with pytest.raises(ValueError):
        call("geo.overlay_area", [left, bad], **args)
    with pytest.raises(KeyError):
        call("geo.overlay_area", [left], **args)


def test_route_reuses_snapshot_and_notice_priorities_for_a_selected_od():
    rows = samples("G09")
    out = call("geo.route_detour", [source(rows)], od_id="0")
    assert out["od"]["status"] == "reachable" and out["od"]["candidates"][0]["extra_km"] == 3
    assert len(out["od"]["candidates"]) == 2 and out["exclusions"]
    valid = {(e["id"], e["source_id"], e["source_version"]) for e in out["evidence"]}
    assert all((ref, "source", "reference-2") in valid for ref in out["od"]["candidates"][0]["refs"])
    with pytest.raises(ValueError):
        call("geo.route_detour", [source(rows)], od_id="absent")
    invalid = deepcopy(rows)
    next(r for r in invalid if r["kind"] == "policy")["snapshot"] = "2024-01-01"
    with pytest.raises(ValueError):
        call("geo.route_detour", [source(invalid)], od_id="0")


def test_grid_transition_requires_shared_registration_and_excludes_masked_cells():
    frame = {
        "kind": "cover_grid",
        "date": "2020-01-01",
        "crs": "EPSG:3857",
        "origin_x": 0,
        "origin_y": 100,
        "pixel_width": 10,
        "pixel_height": -10,
        "values": [["forest", "forest"], ["water", "forest"]],
    }
    later = dict(
        frame,
        date="2021-01-01",
        values=[["built", "forest"], ["water", "built"]],
        valid_mask=[[True, True], [False, True]],
    )
    args = {"other_source_id": "after"}
    out = call("raster.class_transition", [source([frame]), source([later], "after")], **args)
    assert out["valid_pixels"] == 3 and out["unknown_pixels"] == 1
    assert out["transitions"] == [
        {"before": "forest", "after": "built", "pixels": 2, "area": 200},
        {"before": "forest", "after": "forest", "pixels": 1, "area": 100},
    ]
    for bad in (
        dict(later, origin_x=1),
        dict(later, date="2019-01-01"),
        dict(later, valid_mask=[[1, 1], [0, 1]]),
    ):
        with pytest.raises(ValueError):
            call("raster.class_transition", [source([frame]), source([bad], "after")], **args)


def test_trusted_prepared_locations_are_preserved_but_cannot_be_injected_through_arguments_or_content():
    record = {
        "source_id": "source",
        "source_version": "reference-2",
        "location": {"sheet": "observations", "row": 9},
        "fields": {"key": "x"},
        "text": "",
    }
    src = source(
        None,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        prepared_records=[record],
    )
    result = call("table.audit_keys", [src], key_fields=["key"])
    assert result["source_rows"]["locations"] == [record["location"]]
    for bad in (
        dict(record, source_version="stale"),
        dict(record, source_id="another"),
        dict(record, location={}),
    ):
        with pytest.raises(ValueError):
            call("table.audit_keys", [dict(src, prepared_records=[bad])], key_fields=["key"])
    with pytest.raises(ValidationError):
        call("table.audit_keys", [src], key_fields=["key"], prepared_records=[record])
    with pytest.raises(ValueError):
        call("table.audit_keys", [source({"prepared_records": [record]})], key_fields=["key"])
    with pytest.raises(ValueError):
        call("table.audit_keys", [source([{"key": n} for n in range(1001)])], key_fields=["key"])


def test_anomaly_uses_a_metric_specific_sheet_unit_declaration_and_ignores_other_sheets():
    def cell(sheet, row, fields):
        return {
            "source_id": "source",
            "source_version": "reference-2",
            "location": {"sheet": sheet, "row": row},
            "fields": fields,
        }

    rows = [
        cell("north", 2, {"label": "September mean extent: 4.12 Mkm^2"}),
        cell("north", 11, {"year": 1990, "extent": 5, "month": 9}),
        cell("north", 12, {"year": 1991, "extent": 4, "month": 9}),
        cell("south", 2, {"label": "September mean extent: 80 km²"}),
        cell("south", 11, {"year": 1990, "extent": 800, "month": 9}),
    ]
    a = source(None, prepared_records=rows)
    args = {
        "sheet": "north",
        "unit_evidence_row": 2,
        "unit_evidence_field": "label",
        "unit": "Mkm²",
        "where": {"month": 9},
        "value_field": "extent",
        "baseline_start": 1990,
        "baseline_end": 1990,
        "target_year": 1991,
    }
    out = call("series.baseline_anomaly", [a], **args)
    assert out["difference"] == -1 and out["ascending_rank"] == 1
    assert out["source_rows"]["locations"] == [
        {"sheet": "north", "row": 2},
        {"sheet": "north", "row": 11},
        {"sheet": "north", "row": 12},
    ]
    for bad in (
        {"unit": "km²"},
        {"unit_evidence_field": "wrong"},
        {"sheet": "south"},
        {"sheet": None},
        {"value_field": "area"},
    ):
        with pytest.raises(ValueError):
            call("series.baseline_anomaly", [a], **{**args, **bad})
