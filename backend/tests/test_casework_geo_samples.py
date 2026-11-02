"""Public synthetic integration fixtures. They are input observations, not stored answers.

For G02/G05 register each PNG from media_samples first, then pass the returned key→asset ID
mapping to samples(). All other fields can be uploaded together as a JSON records array.
"""

import io
from copy import deepcopy

import pytest
from PIL import Image

from backend.app.casework.geo_cases import CATALOG, audit_products
from backend.app.casework.records import extract
from backend.tests.test_casework_geo import area, edge, grid, policy, verify


def media_samples(code):
    names = {"G02": ["p1", "p2", "p3"], "G05": ["earlier", "later"]}.get(code, [])
    images = {}
    for i, key in enumerate(names):
        im = Image.new("RGB", (100, 80), (50, 65, 70))
        im.paste((240, 240, 240), (10 + i * 3, 10, 20 + i * 3, 20))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        images[key] = buf.getvalue()
    return images


def samples(code, media_ids=None):
    ids = media_ids or {k: k for k in media_samples(code)}
    if code == "G01":
        dates = [f"2024-07-{day:02d}" for day in range(1, 9)]
        return [
            policy(start=dates[1], end=dates[-1], match_radius_m=80, confidence_levels=["high"]),
            *[{"kind": "batch", "date": dt, "complete": True} for dt in dates],
            *[
                {
                    "observed_at": dt + "T10:00:00Z",
                    "available_at": dt + "T10:30:00Z",
                    "longitude": 10 + i * 0.1,
                    "latitude": 30,
                    "sensor": "synthetic",
                    "confidence": "high",
                    "region": "zone-a",
                    "frp": 20 + i,
                }
                for i, dt in enumerate(dates)
            ],
        ]
    if code == "G02":
        dates = [f"2024-03-{day:02d}" for day in range(1, 4)]
        return [
            policy(dates=dates),
            *[
                f
                for i, dt in enumerate(dates, 1)
                for f in [
                    {
                        "kind": "raster_binding",
                        "role": "panel",
                        "target_source_id": ids[f"p{i}"],
                        "image_id": f"scene-{i}",
                        "date": dt,
                        "brightness_threshold": 220,
                        "sensor": "synthetic",
                    },
                    {
                        "image_id": f"scene-{i}",
                        "date": dt,
                        "width": 100,
                        "height": 80,
                        "bbox": [10 + (i - 1) * 3, 10, 10, 10],
                        "observation": "测试图中可见高亮方形",
                        "interpretation": "语义未从亮度自动推断",
                        "confidence": "given",
                    },
                ]
            ],
        ]
    if code == "G03":
        return [
            policy(start_year=2000, target_year=2004, baseline_years=[2000, 2002], extent_unit="km²"),
            *[
                {"year": y, "month": 9, "hemisphere": "N", "extent": v}
                for y, v in enumerate([10, 12, 14, 11, 9], 2000)
            ],
        ]
    if code == "G04":
        return [
            policy(metric_crs="EPSG:32631"),
            area("aoi"),
            area("region", region="zone"),
            grid("water_grid", [["dry", "water"], ["dry", "dry"]], phase="before"),
            grid("water_grid", [["dry", "water"], ["dry", "dry"]], phase="permanent"),
            grid("water_grid", [["water", "water"], ["trace", "dry"]], phase="after", date="2024-01-02"),
            {
                "kind": "road",
                "road_id": "demo-road",
                "crs": "EPSG:32631",
                "geometry": {"type": "LineString", "coordinates": [[500000, 125], [500020, 125]]},
            },
        ]
    if code == "G05":
        return [
            {
                "building_id": "B" + str(i),
                "block": "block-1",
                "before": before,
                "after": after,
                "registered": True,
                "clear": True,
                "before_region": [10, 10, 10, 10],
                "after_region": [13, 10, 10, 10],
                "before_image": ids["earlier"],
                "after_image": ids["later"],
                "crs": "EPSG:4326",
                "geometry": {"type": "Point", "coordinates": [2 + i * 0.001, 15]},
                "confidence": "upstream_annotation",
            }
            for i, (before, after) in enumerate(
                [("intact", "damaged"), ("damaged", "damaged"), ("intact", "intact")]
            )
        ]
    if code == "G06":
        return [
            policy(metric_crs="EPSG:32631"),
            area("aoi"),
            grid("cover_grid", [["forest", "farm"], ["water", "farm"]], date="2010-09-18"),
            grid("cover_grid", [["built", "farm"], ["water", "farm"]], date="2017-07-24"),
            grid("cover_grid", [["built", "built"], ["water", "farm"]], date="2023-07-23"),
            *[
                {"kind": "validation_pixel", "date": d, "row": 1, "column": 0, "class": "water"}
                for d in ["2010-09-18", "2017-07-24", "2023-07-23"]
            ],
        ]
    if code == "G07":
        return [
            policy(start="2024-01-01", end="2024-01-31", qualifier_rules={"U": "nondetect"}),
            {"kind": "site", "site_id": "s", "longitude": 8, "latitude": 30},
            {"kind": "grid", "grid_id": "g", "bbox": [7, 29, 9, 31]},
            *[
                {
                    "sample_id": "sample",
                    "site_id": "s",
                    "date": "2024-01-02",
                    "medium": "soil",
                    "analyte": name,
                    "value": v,
                    "qualifier": q,
                    "unit": "mg/kg",
                    "validation": "Final",
                }
                for name, v, q in [("A", "<1", "U"), ("B", "5", "")]
            ],
        ]
    if code == "G08":
        islands = ["A", "B", "C", "D", "E"]
        return [
            policy(date="2024-01-02", islands=islands),
            *[
                {
                    "kind": "tide",
                    "island": v,
                    "observed_at": "2024-01-02T03:30:00Z",
                    "latitude": 20 + i * 0.1,
                    "longitude": 30,
                    "value": i + 1,
                    "baseline": 1,
                    "datum": "test",
                    "unit": "m",
                }
                for i, v in enumerate(islands)
            ],
            {
                "kind": "frame",
                "island": "A",
                "observed_at": "2024-01-02T01:00:00Z",
                "frame": 5,
                "description": "已定位云团观测",
            },
            {
                "kind": "survey",
                "island": "B",
                "observed_at": "2024-02-01T01:00:00Z",
                "event_at": "2024-01-02T02:00:00Z",
                "description": "回溯调查",
            },
        ]
    if code == "G09":
        return [
            policy(snapshot="2024-01-01T08:00:00Z", expected_od=5),
            *[
                edge(e, a, b, k, oneway=False)
                for e, a, b, k in [
                    ("direct", "a", "b", 1),
                    ("via1", "a", "c", 2),
                    ("via2", "c", "b", 2),
                    ("via3", "a", "d", 3),
                    ("via4", "d", "b", 4),
                ]
            ],
            {
                "kind": "notice",
                "edge_id": "direct",
                "state": "closed",
                "priority": 1,
                "effective_at": "2024-01-01T07:30:00Z",
            },
            *[
                {"kind": "od", "od_id": str(i), "from": a, "to": b, "vehicle": {"class": "light"}}
                for i, (a, b) in enumerate([("a", "b"), ("a", "c"), ("a", "d"), ("c", "d"), ("d", "b")])
            ],
        ]
    if code == "G10":
        dates = ["2022-08-01", "2022-08-20", "2022-09-10"]
        return [
            policy(
                metric_crs="EPSG:32631",
                priority_thresholds=[
                    {"label": "priority", "min_exposed_fraction": 0.15},
                    {"label": "other", "min_exposed_fraction": 0},
                ],
            ),
            *[
                area("district", x=500000 + i * 20, district=f"d{i}", official_crop_area_m2=400)
                for i in range(3)
            ],
            *[area("grid", x=500000 + i * 20, grid_id=f"g{i}") for i in range(3)],
            area("crop", w=60, crop="demo", year=2021, accuracy="given"),
            *[area("coverage", w=60, date=dt) for dt in dates],
            *[area("flood", x=500000 + i * 4, w=8, date=dt) for i, dt in enumerate(dates)],
        ]
    raise KeyError(code)


def assert_expected(code, candidate):
    """Use after the real Worker or direct specialist runs; synthetic products only."""
    p = candidate["products"]
    assert not candidate["gaps"], candidate["gaps"]
    if code == "G01":
        assert sum(x["match"] == "new" for x in p["hotspots"]) == 7
    elif code == "G02":
        assert len(p["changes"]) == 3 and all("data:image/png" in x["pixel_overlay_svg"] for x in p["panels"])
    elif code == "G03":
        assert p["anomaly"]["difference"] == -3 and p["anomaly"]["ascending_rank"] == 1
    elif code == "G04":
        assert p["road_exposure"]["records"][0]["potential_exposure_m"] == 10
    elif code == "G05":
        assert p["blocks"]["block-1"] == {"new_visible": 1, "preexisting": 1, "no_new_visible": 1}
    elif code == "G06":
        assert [x["total_m2"] for x in p["areas"]] == [400, 400, 400]
    elif code == "G07":
        assert (
            p["quality"]["unique_samples"] == 1
            and p["quality"]["record_count"] == 2
            and p["results"][0]["numeric_value"] is None
        )
    elif code == "G08":
        assert len(p["timeline"]) == 6 and len(p["retrospective"]) == 1 and len(p["island_matrix"]) == 5
    elif code == "G09":
        assert len(p["od_comparison"]) == 5 and p["od_comparison"][0]["candidates"][0]["extra_km"] == 3
    elif code == "G10":
        assert len(p["priority"]) == 3 and p["priority"][0]["max_union_exposed_m2"] == 320


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_complete_supplied_geo_contracts(code):
    media = media_samples(code)
    assets = [
        {
            "id": key,
            "source_version": "1",
            "content_ref": "test",
            "modality": "image",
            "media_type": "image/png",
        }
        for key in media
    ]
    assets.append(
        {
            "id": "json-observations",
            "source_version": "1",
            "modality": "structured",
            "media_type": "application/json",
            "content": {"records": deepcopy(samples(code))},
        }
    )
    normalized, gaps = extract(assets, lambda a: media[a["id"]])
    assert not gaps
    candidate = verify(code, normalized)
    assert_expected(code, candidate)
    assert all(c["passed"] for c in audit_products(code, candidate["products"], normalized))
    # A structurally valid but false result must not pass the independent reviewer.
    p = candidate["products"]
    if code == "G01":
        p["points"]["features"].pop()
    elif code == "G02":
        p["changes"][0]["bbox"] = [0, 0, 999, 999]
    elif code == "G03":
        p["anomaly"]["difference"] += 4
    elif code == "G04":
        p["road_exposure"]["records"][0]["potential_exposure_m"] = 999
    elif code == "G05":
        p["blocks"]["block-1"]["new_visible"] += 3
    elif code == "G06":
        p["areas"][0]["total_m2"] += 25
    elif code == "G07":
        p["results"][0]["numeric_value"] = 0
    elif code == "G08":
        p["timeline"].reverse()
    elif code == "G09":
        p["od_comparison"][0]["candidates"][0]["edge_ids"] = ["direct"]
    elif code == "G10":
        p["priority"][0]["max_union_exposed_m2"] = 99999
    assert any(not c["passed"] and c["hard"] for c in audit_products(code, p, normalized))
