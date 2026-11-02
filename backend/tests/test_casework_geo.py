"""Synthetic, freely reproducible acceptance inputs for ten geographical workflows."""

from io import BytesIO

import pytest
from PIL import Image, TiffImagePlugin

from backend.app.casework.geo_cases import CATALOG, analyze
from backend.app.casework.geo_raster import image_observations


def rows(*data, asset="test-observations"):
    return [
        {"source_id": asset, "source_version": "1", "location": {"row": i}, "text": "", "fields": f}
        for i, f in enumerate(data, 1)
    ]


def frame(asset):
    im = Image.new("RGB", (100, 80), "#404040")
    im.paste((255, 255, 255), (15, 20, 28, 25))
    return [
        {"source_id": asset, "source_version": "1", "location": loc, "text": t, "fields": f}
        for loc, t, f in image_observations({"id": asset}, im)
    ]


def policy(**kw):
    return dict(kind="policy", **kw)


def rectangle(x, y, w, h):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


def area(kind, x=500000, y=120, w=20, h=20, **kw):
    return dict(kind=kind, crs="EPSG:32631", geometry=rectangle(x, y, w, h), **kw)


def grid(kind, values, **kw):
    return {
        "kind": kind,
        "crs": "EPSG:32631",
        "origin_x": 500000,
        "origin_y": 120,
        "pixel_width": 10,
        "pixel_height": 10,
        "values": values,
        **kw,
    }


def edge(id, a, b, km, **kw):
    return {
        "kind": "edge",
        "edge_id": id,
        "from": a,
        "to": b,
        "distance_km": km,
        "oneway": True,
        "state": "open",
        "baseline_state": "open",
        "baseline_speed_kph": 60,
        "speed_kph": 60,
        "crs": "EPSG:4326",
        "geometry": {"type": "LineString", "coordinates": [[1, 1], [1.01, 1.02]]},
        **kw,
    }


def verify(code, records, **kw):
    r = analyze(code, records, **kw)
    assert set(r["products"]) == set(CATALOG[code]["outputs"])
    assert r["evidence"]
    by_id = {e["id"]: e for e in r["evidence"]}
    for e in by_id.values():
        assert any(
            row["source_id"] == e["source_id"]
            and row["source_version"] == e["source_version"]
            and row["location"] == e["location"]
            for row in records
        )

    def visit(v):
        if isinstance(v, list):
            for child in v:
                visit(child)
        if isinstance(v, dict):
            for k, child in v.items():
                if k == "evidence_ids":
                    assert all(ref in by_id for ref in child)
                else:
                    visit(child)

    visit(r["products"])
    return r


def test_hotspots_use_day_baseline_and_exclude_duplicates_and_invalid_points():
    def pt(d, lon):
        return {
            "latitude": 23.0,
            "longitude": lon,
            "observed_at": d + "T03:00:00Z",
            "available_at": d + "T04:00:00Z",
            "sensor": "demo",
            "confidence": "high",
            "frp": 10,
            "region": "west",
        }

    batch = rows(
        policy(start="2024-05-01", end="2024-05-02", match_radius_m=150, confidence_levels=["high"]),
        {"kind": "batch", "date": "2024-05-01", "complete": True},
        {"kind": "batch", "date": "2024-05-02", "complete": True},
        pt("2024-05-01", 1),
        pt("2024-05-02", 1.0001),
        pt("2024-05-02", 3),
        pt("2024-05-02", 3),
        pt("2024-05-02", 300),
    )
    result = verify("G01", batch)
    p = result["products"]
    assert [r["match"] for r in p["hotspots"]] == ["unknown", "matched", "new"]
    assert {x["properties"]["id"] for x in p["points"]["features"]} == {x["id"] for x in p["hotspots"]}
    assert len(p["exceptions"]["invalid"]) == len(p["exceptions"]["duplicates"]) == 1
    assert any("前日" in gap for gap in result["gaps"])


def test_image_panels_overlay_real_image_avoid_invented_coordinates():
    allrows = rows(policy(dates=["2024-02-01", "2024-02-02", "2024-02-03"]))
    for i in range(1, 4):
        key = f"demo-panel-{i}"
        date = f"2024-02-0{i}"
        allrows += frame(key)
        allrows += rows(
            {
                "kind": "raster_binding",
                "target_source_id": key,
                "role": "panel",
                "date": date,
                "image_id": key,
                "brightness_threshold": 230,
            },
            {
                "date": date,
                "image_id": key,
                "width": 100,
                "height": 80,
                "bbox": [15, 20, 13, 5],
                "observation": "可见高亮区",
                "interpretation": "物体类别待核",
                "confidence": "upstream",
            },
            asset=f"annotation-{i}",
        )
    result = verify("G02", allrows)
    p = result["products"]
    assert not result["gaps"]
    assert len(p["changes"]) == len(p["panels"]) == 3
    assert all(
        "data:image/png;base64," in panel["pixel_overlay_svg"] and panel["brightness_candidates"]
        for panel in p["panels"]
    )
    assert all("coords" not in r and r["coordinate_space"] == "image_pixels" for r in p["changes"])
    assert analyze("G02", frame("uninterpreted"))["gaps"]


def test_series_baseline_rank_ties_freeze_cutoff_and_ignore_wrong_month_and_hemisphere():
    vals = {2000: 4, 2001: 5, 2002: 7, 2003: 4, 2004: 1}
    base = rows(
        policy(start_year=2000, target_year=2003, baseline_years=[2001, 2002], extent_unit="km²"),
        *[{"year": y, "month": 9, "hemisphere": "N", "extent": v, "rank": 99} for y, v in vals.items()],
        {"year": 2001, "month": 8, "hemisphere": "N", "extent": 100},
        {"year": 2001, "month": 9, "hemisphere": "S", "extent": 200},
    )
    r = verify("G03", base)
    s = r["products"]["anomaly"]
    assert not r["gaps"] and s["baseline_mean"] == 6 and s["difference"] == -2 and s["ascending_rank"] == 1
    assert s["percent"] == pytest.approx(-100 / 3)
    assert "<svg" in r["products"]["trend_svg"] and len(r["products"]["annual_series"]) == 4
    conflict = base + rows(
        {"year": 2001, "month": 9, "hemisphere": "N", "extent": 2}, asset="conflicting-series"
    )
    bad = verify("G03", conflict)
    assert bad["gaps"] and bad["products"]["anomaly"]["ascending_rank"] is None
    assert bad["products"]["anomaly"]["baseline_mean"] is None


def test_review_uses_only_frozen_field_dictionary_to_repair_aliases():
    data = rows(
        policy(start_year=2000, target_year=2002, baseline_years=[2000, 2001], extent_unit="km²"),
        *[
            {"year": y, "month": 9, "hemisphere": "N", "measured": v}
            for y, v in ((2000, 4), (2001, 6), (2002, 3))
        ],
    )
    data += rows(
        {
            "kind": "field_dictionary",
            "target_source_id": "test-observations",
            "aliases": {"measured": "extent"},
        },
        asset="approved-field-contract",
    )
    first = analyze("G03", data)
    assert first["gaps"] and first["products"]["anomaly"]["difference"] is None
    fixed = verify("G03", data, feedback=first["gaps"])
    assert not fixed["gaps"] and fixed["products"]["anomaly"]["difference"] == -2
    assert any(e["source_id"] == "approved-field-contract" for e in fixed["evidence"])
    conflict = data + rows(
        {
            "kind": "field_dictionary",
            "target_source_id": "test-observations",
            "aliases": {"measured": "other"},
        },
        asset="other-schema",
    )
    assert analyze("G03", conflict, feedback=first["gaps"])["products"]["anomaly"]["difference"] is None


def test_flood_grid_change_clips_roads_excludes_water_and_masks_unknown():
    data = rows(
        policy(metric_crs="EPSG:32631"),
        area("aoi"),
        area("region", region="test"),
        grid("water_grid", [["dry", "water"], ["dry", "dry"]], phase="before"),
        grid("water_grid", [["dry", "water"], ["dry", "dry"]], phase="permanent"),
        grid(
            "water_grid",
            [["water", "water"], ["trace", "dry"]],
            phase="after",
            date="2024-10-31",
            valid_mask=[[True, True], [True, False]],
        ),
        {
            "kind": "road",
            "road_id": "r1",
            "crs": "EPSG:32631",
            "geometry": {"type": "LineString", "coordinates": [[500000, 125], [500020, 125]]},
        },
    )
    p = verify("G04", data)["products"]
    assert p["coverage"]["observed_m2"] == 300 and p["coverage"]["unknown_m2"] == 100
    assert p["areas"][0]["flood_m2"] == 100 and p["areas"][0]["trace_m2"] == 100
    assert p["road_exposure"]["records"][0]["potential_exposure_m"] == 10
    assert analyze("G04", rows(policy(metric_crs="EPSG:4326")))["gaps"]


def test_building_reconciliation_and_image_evidence_no_unknown_counted_as_new():
    data = (
        frame("before-scene")
        + frame("after-scene")
        + rows(
            *[
                {
                    "building_id": str(i),
                    "block": "一区",
                    "before": before,
                    "after": "damaged",
                    "registered": True,
                    "clear": clear,
                    "before_region": [1, 2, 3, 4],
                    "after_region": [1, 2, 3, 4],
                    "before_image": "before-scene",
                    "after_image": "after-scene",
                    "crs": "EPSG:4326",
                    "geometry": rectangle(i, 1, 0.01, 0.01),
                }
                for i, (before, clear) in enumerate([("intact", True), ("damaged", True), ("unknown", False)])
            ]
        )
    )
    result = verify("G05", data)
    p = result["products"]
    assert p["blocks"]["一区"] == {"new_visible": 1, "preexisting": 1, "unknown": 1}
    assert len(p["buildings"]) == 3 and len(p["comparison_panels"]) == 2 and len(p["uncertain"]) == 1


def test_cover_grid_area_closure_transition_and_reject_misalignment():
    base = rows(
        policy(metric_crs="EPSG:32631"),
        area("aoi"),
        grid("cover_grid", [["forest", "farm"], ["water", "farm"]], date="2010-09-18"),
        grid("cover_grid", [["built", "farm"], ["water", "farm"]], date="2017-07-24"),
        grid("cover_grid", [["built", "built"], ["water", None]], date="2023-07-23"),
        *[
            {"kind": "validation_pixel", "date": d, "row": 1, "column": 0, "class": "water"}
            for d in ["2010-09-18", "2017-07-24", "2023-07-23"]
        ],
    )
    r = verify("G06", base)
    p = r["products"]
    assert not r["gaps"]
    assert [x["total_m2"] for x in p["areas"]] == [400, 400, 400]
    assert (
        sum(x["area_m2"] for x in p["transitions"] if x["to_class"] == "built" and x["from_class"] != "built")
        == 200
    )
    assert len(p["new_built"]["features"]) == 2
    base[4]["fields"]["origin_x"] += 5
    assert "未配准" in analyze("G06", base)["gaps"][0]


def test_lab_censored_results_excluded_dates_missing_coords_and_unsampled_cells():
    data = rows(
        policy(start="2024-01-01", end="2024-01-31", qualifier_rules={"U": "nondetect", "R": "rejected"}),
        {"kind": "site", "site_id": "A", "longitude": 3, "latitude": 1},
        {"kind": "grid", "grid_id": "E", "bbox": [2, 0, 4, 3]},
        {"kind": "grid", "grid_id": "W", "bbox": [0, 0, 2, 3]},
        {
            "sample_id": "x",
            "site_id": "A",
            "date": "2024-01-02",
            "value": "<0.3",
            "qualifier": "U",
            "validation": "Final",
            "analyte": "metal",
            "unit": "mg/kg",
        },
        {
            "sample_id": "x",
            "site_id": "A",
            "date": "2024-01-02",
            "value": "5",
            "validation": "Final",
            "analyte": "other",
            "unit": "mg/kg",
        },
        {
            "sample_id": "y",
            "site_id": "B",
            "date": "2024-01-20",
            "value": "9",
            "qualifier": "R",
            "validation": "Final",
            "analyte": "metal",
            "unit": "µg/kg",
        },
        {"sample_id": "z", "site_id": "C", "date": "2025-01-02", "value": "7"},
    )
    r = verify("G07", data)
    p = r["products"]
    assert p["quality"]["record_count"] == 3 and p["quality"]["unique_samples"] == 2
    assert [x["numeric_value"] for x in p["results"]] == [None, 5, None]
    assert p["quality"]["unit_conflicts"] and len(p["sample_points"]["features"]) == 1
    assert {c["grid_id"]: c["sample_count"] for c in p["coverage"]} == {"E": 1, "W": 0}


def test_timeline_keeps_surveys_separate_and_timezone_orders_observations():
    data = rows(
        policy(date="2024-03-02", islands=["a", "b", "missing"]),
        {
            "kind": "tide",
            "island": "b",
            "observed_at": "2024-03-02T12:00:00+02:00",
            "value": 5,
            "unit": "m",
            "datum": "chart",
            "baseline": 3,
        },
        {
            "kind": "frame",
            "island": "a",
            "observed_at": "2024-03-02T08:00:00Z",
            "frame": 1,
            "description": "cloud",
        },
        {
            "kind": "survey",
            "island": "a",
            "observed_at": "2024-03-11T12:00:00Z",
            "event_at": "2024-03-02T12:00:00Z",
            "description": "retrospective",
        },
        {
            "kind": "report",
            "island": "b",
            "observed_at": "2024-03-02T10:00:00",
            "description": "missing zone",
        },
    )
    r = verify("G08", data)
    p = r["products"]
    assert (
        len(p["timeline"]) == 2 and p["timeline"][0]["type"] == "frame" and p["timeline"][1]["anomaly"] == 2
    )
    assert len(p["retrospective"]) == 1 and p["island_matrix"]["missing"] == {
        "observations": [],
        "surveys": [],
    }
    assert r["gaps"]


def test_routes_compare_baseline_disallow_closed_restricted_and_unverified_edges():
    data = rows(
        policy(snapshot="2024-01-31T12:00:00+08:00", expected_od=2),
        edge("direct", "a", "b", 1),
        edge("t1", "a", "c", 2),
        edge("t2", "c", "b", 2),
        edge("a1", "a", "d", 3),
        edge("a2", "d", "b", 4),
        edge("limit", "a", "b", 2, max_height_m=2),
        edge("maybe", "b", "z", 1, state="unknown"),
        edge("dead", "b", "c", 1, state="closed"),
        {
            "kind": "notice",
            "edge_id": "direct",
            "state": "closed",
            "priority": 2,
            "effective_at": "2024-01-31T03:00:00Z",
        },
        {
            "kind": "notice",
            "edge_id": "direct",
            "state": "open",
            "priority": 9,
            "effective_at": "2024-02-01T03:00:00Z",
        },
        {"kind": "od", "od_id": "A", "from": "a", "to": "b", "vehicle": {"height_m": 3}},
        {"kind": "od", "od_id": "B", "from": "b", "to": "z", "vehicle": {"height_m": 3}},
    )
    p = verify("G09", data)["products"]
    assert [r["status"] for r in p["od_comparison"]] == ["reachable", "unconfirmed"]
    routes = p["od_comparison"][0]["candidates"]
    assert [r["distance_km"] for r in routes] == [4, 7] and routes[0]["extra_km"] == 3
    assert all(not {"direct", "limit"} & set(r["edge_ids"]) for r in routes)
    assert len(p["route_layer"]["features"]) == 2


def test_agriculture_union_and_reported_area_reconciliation_no_repeated_area():
    dates = ["2022-08-01", "2022-08-20", "2022-09-10"]
    data = rows(
        policy(
            metric_crs="EPSG:32631",
            expected_districts=1,
            priority_thresholds=[
                {"label": "higher", "min_exposed_fraction": 0.5},
                {"label": "lower", "min_exposed_fraction": 0},
            ],
        ),
        area("district", district="a", official_crop_area_m2=410),
        area("crop", crop="demo", year=2021, accuracy="validated"),
        area("grid", grid_id="x"),
        *[area("coverage", date=dt) for dt in dates],
        *[area("flood", x=500000 + i * 4, y=120, w=8, h=20, date=dt) for i, dt in enumerate(dates)],
    )
    r = verify("G10", data)
    p = r["products"]
    assert not r["gaps"]
    assert sum(x["exposed_m2"] for x in p["exposure"]) == 480
    assert p["priority"][0]["max_union_exposed_m2"] == 320 and p["priority"][0]["candidate_tier"] == "higher"
    assert p["method"]["statistical_differences"][0]["classified_minus_reported_m2"] == -10


def test_small_real_geotiff_and_band_lookup_drive_water_change():
    buf = BytesIO()
    im = Image.new("L", (2, 2))
    im.putdata([1, 0, 0, 0])
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (10.0, 10.0, 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, 500000.0, 140.0, 0.0)
    ifd[34735] = (1, 1, 0, 2, 1025, 0, 1, 1, 3072, 0, 1, 32631)
    im.save(buf, format="TIFF", tiffinfo=ifd)
    buf.seek(0)
    with Image.open(buf) as source:
        obs = image_observations({"id": "test-tiff"}, source)
    data = [
        {"source_id": "test-tiff", "source_version": "2", "location": loc, "text": text, "fields": f}
        for loc, text, f in obs
    ]
    data += rows(
        policy(metric_crs="EPSG:32631"),
        area("aoi"),
        area("region", region="zone"),
        grid("water_grid", [["dry", "dry"], ["dry", "dry"]], phase="before", origin_y=140, pixel_height=-10),
        grid(
            "water_grid", [["dry", "dry"], ["dry", "dry"]], phase="permanent", origin_y=140, pixel_height=-10
        ),
        {
            "kind": "raster_binding",
            "target_source_id": "test-tiff",
            "role": "water_grid",
            "phase": "after",
            "date": "2024-10-31",
            "bands": [0],
            "class_values": {"0": "dry", "1": "water"},
        },
        {
            "kind": "road",
            "road_id": "r1",
            "crs": "EPSG:32631",
            "geometry": {"type": "LineString", "coordinates": [[500000, 135], [500020, 135]]},
        },
    )
    result = verify("G04", data)
    assert not result["gaps"]
    assert result["products"]["road_exposure"]["records"][0]["potential_exposure_m"] == 10


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_irrelevant_input_never_becomes_a_verified_geographical_conclusion(code):
    result = analyze(code, rows({"note": "No qualifying observation."}))
    assert result["gaps"] and set(result["products"]) == set(CATALOG[code]["outputs"])
