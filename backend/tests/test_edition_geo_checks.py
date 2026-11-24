import io
import json
import zipfile

from openpyxl import Workbook

from backend.app.casework.edition_geo_checks import checks


def _source(name, data):
    return {"filename": name, "read": lambda: data, "records": []}


def test_baseline_and_ranking_recomputed_from_frozen_sheet():
    wb = Workbook()
    sheet = wb.active
    sheet.title = "September-NH"
    sheet.append(["year", "month", "hemisphere", "extent"])
    years = {year: 3 if year == 2023 else 6 for year in range(1979, 2024)}
    for year, extent in years.items():
        sheet.append([year, 9, "N", extent])
    sheet.append([2024, 9, "N", 1])
    stream = io.BytesIO()
    wb.save(stream)
    sources = [_source("original.xlsx", stream.getvalue())]
    products = {
        "年度范围（百万平方公里）": [
            {"year": year, "extent_mkm2": extent, "excel_row": i}
            for i, (year, extent) in enumerate(years.items(), 2)
        ],
        "异常统计": {"baseline": 6, "observed": 3, "difference": -3, "percent": -50, "rank": 1},
    }
    assert all(item["passed"] for item in checks("G03", sources, products))
    products["异常统计"]["rank"] = 2
    series, anomaly = checks("G03", sources, products)
    assert series["passed"] and not anomaly["passed"] and anomaly["hard"]


def test_gauge_peak_ignores_missing_value_in_original_series():
    sites = []
    result = []
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for i in range(6):
            site = {"id": str(i), "station": f"S{i}", "lat": i, "lon": 150, "member": f"{i}.txt"}
            sites.append(site)
            archive.writestr(
                site["member"],
                "15 2022 1 15 0 0 0 1 1 0.25\n15 2022 1 15 0 1 0 1 1 9999\n15 2022 1 15 0 2 0 1 1 -0.1\n",
            )
            result.append(
                {k: site[k] for k in ("id", "station", "lat", "lon")}
                | {"peak_m": 0.25, "peak_utc": "00:00", "trough_m": -0.1, "trough_utc": "00:02", "records": 2}
            )
        archive.writestr("stations.json", json.dumps({"stations": sites}))
    sources = [_source("tide.zip", stream.getvalue())]
    assert checks("G08", sources, {"六站潮位极值": result})[0]["passed"]
    result[0]["peak_m"] = 9999
    (verdict,) = checks("G08", sources, {"六站潮位极值": result})
    assert not verdict["passed"] and verdict["hard"]


def test_malformed_frozen_workbook_fails_closed():
    items = checks("G03", [_source("broken.xlsx", b"not a workbook")], {})
    assert len(items) == 1 and not items[0]["passed"] and items[0]["hard"]
