"""Independent G07 verification: compare with frozen rows, not only with the analyst's totals."""

from copy import deepcopy

import pytest

from backend.app.casework.geo_cases import analyze, audit_products
from backend.tests.test_casework_geo import policy, rows


@pytest.fixture
def batch():
    # Both source schemas and censored/confirmed/rejected measurements in the same batch.
    sources = rows(
        policy(start="2024-03-01", end="2024-03-31", qualifier_rules={"U": "nondetect", "R": "rejected"}),
        {"kind": "site", "site_id": "S1", "longitude": 12, "latitude": 31},
        {"kind": "grid", "grid_id": "G1", "bbox": [11, 30, 13, 32]},
        {
            "Samp_No": "P1",
            "Location": "S1",
            "SampleDate_txt": "3/6/2024",
            "SampleMedia": "soil",
            "Analyte": "metal-A",
            "Result_Units": "mg/kg",
            "Result_Final_Txt": "<0.3",
            "Result_Qualifier_Final": "U",
            "Reporting_Limit": "0.3",
            "Validation_Level": "Final",
        },
        {
            "sample_id": "P2",
            "site_id": "S1",
            "date": "2024-03-07",
            "medium": "water",
            "analyte": "metal-B",
            "unit": "mg/L",
            "value": "5.125",
            "reporting_limit": 0.01,
            "qualifier": "",
            "validation": "validated",
        },
        {
            "sample_id": "P3",
            "site_id": "S1",
            "date": "2024-03-07",
            "medium": "soil",
            "analyte": "metal-C",
            "unit": "mg/kg",
            "value": "3.5",
            "qualifier": "R",
            "validation": "Final",
        },
        {"sample_id": "old", "site_id": "S1", "date": "2023-03-07", "value": "41"},
    )
    product = analyze("G07", sources)["products"]
    assert all(c["passed"] for c in audit_products("G07", product, sources))
    return sources, product


@pytest.mark.parametrize(
    ("index", "field", "forged"),
    [
        (0, "sample_id", "P2"),
        (0, "site_id", "S9"),
        (0, "medium", "gas"),
        (0, "date", "2024-03-07"),
        (0, "analyte", "metal-B"),
        (0, "unit", "ppm"),
        (0, "qualifier", ""),
        (0, "validation", "Preliminary"),
        (0, "detection_limit", 0.8),
        (0, "value_text", "0"),
        (1, "numeric_value", 5.126),
        (1, "value_text", "4.2"),
        (2, "status", "nondetect"),
        (1, "evidence_ids", ["g4", "g2"]),
        (1, "evidence_ids", ["g5", "g3"]),
        (1, "evidence_ids", ["g7", "g2"]),
        (1, "coords", [12, 32]),
    ],
)
def test_reported_measurement_must_equal_its_own_source(batch, index, field, forged):
    sources, product = batch
    product["results"][index][field] = forged
    if field == "status":
        product["quality"]["status_counts"] = {"nondetect": 2, "detected": 1}
    failed = [c for c in audit_products("G07", product, sources) if not c["passed"]]
    assert any(c["hard"] and c["name"].startswith("geo_lab_source_") for c in failed)


def test_omission_and_duplicated_citation_do_not_pass_self_consistent_totals(batch):
    sources, product = batch
    product["results"].pop(2)
    product["results"].append(deepcopy(product["results"][1]))
    product["quality"].update(unique_samples=2, status_counts={"detected": 2, "nondetect": 1})
    checks = {c["name"]: c["passed"] for c in audit_products("G07", product, sources)}
    assert checks["geo_sampling_counts"] and not checks["geo_lab_source_rows"]


def test_empty_observations_do_not_pass_on_background_notes():
    for products in (
        {"results": None, "source_notes": [{"page": 3}], "map_view": "<svg/>"},
        {"results": [], "quality": {}, "sample_points": {"type": "FeatureCollection", "features": []}},
    ):
        checks = audit_products("G07", products, [])
        assert any(
            c["name"] == "geo_products_unavailable" and not c["passed"] and not c["hard"] for c in checks
        )


def test_candidate_cannot_rebind_a_lab_evidence_id_to_another_existing_source_location(batch):
    from backend.app.casework.engine import audit

    sources, _ = batch
    candidate = analyze("G07", sources)
    evidence = {e["id"]: e for e in candidate["evidence"]}
    evidence["g4"]["location"] = evidence["g5"]["location"]
    assert any(
        c["name"] == "evidence_locations" and not c["passed"] and c["hard"]
        for c in audit("G07", candidate, sources)
    )


def test_all_null_candidate_without_declared_gaps_is_not_deliverable(batch):
    from backend.app.casework.engine import audit

    sources, _ = batch
    candidate = analyze("G07", sources)
    candidate.update(products=dict.fromkeys(candidate["products"]), gaps=[])
    checks = audit("G07", candidate, sources)
    assert any(c["name"] == "geo_products_unavailable" and not c["passed"] for c in checks)
    assert any(not c["passed"] and c["hard"] for c in checks)
