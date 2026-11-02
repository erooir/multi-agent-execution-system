"""Known correspondences and abstentions for offline archive photo comparison."""

import hashlib
import io
import random
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from backend.app.casework.document_cases import analyze
from backend.app.casework.engine import audit
from backend.app.casework.vision import audit_archive, enrich_archive


def binding_rows(*fields):
    return [
        {"source_id": "catalog-data", "source_version": "v2", "location": {"row": i}, "text": "", "fields": f}
        for i, f in enumerate(fields, 1)
    ]


def texture(seed):
    """Stationery-like fixtures, not actual aircraft or customer materials."""
    rng = random.Random(seed)
    im = Image.new("RGB", (480, 350), (224, 225, 220))
    d = ImageDraw.Draw(im)
    for i in range(115):
        x = rng.randrange(18, 440)
        y = rng.randrange(15, 310)
        w = rng.randrange(4, 28)
        h = rng.randrange(4, 23)
        color = tuple(rng.randrange(5, 165) for _ in range(3))
        if i % 3 == 0:
            d.ellipse((x, y, x + w, y + h), outline=color, width=2)
        elif i % 3 == 1:
            d.rectangle((x, y, x + w, y + h), fill=color)
        else:
            d.line((x, y, x + w, y + h, x + w * 2, y - 3), fill=color, width=3)
        if i % 8 == 0:
            d.text((x, y), str(rng.randrange(999)), fill="black")
    return im


def rotated_photo(reference):
    fragment = reference.resize((380, 277), Image.Resampling.LANCZOS).rotate(
        18, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="#c5c5c5"
    )
    target = Image.new("RGB", (680, 510), "#acadb1")
    target.paste(fragment, (48, 40))
    return target


def fixture(*, other=None, query=None):
    a = texture(843)
    b = texture(577) if other is None else other
    images = {"query-pixels": rotated_photo(a) if query is None else query, "ref-a": a, "ref-b": b}
    raw = {}
    for k, img in images.items():
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw[k] = buf.getvalue()
    assets = [
        {"id": k, "source_version": "input-1", "content_ref": "permitted-content", "modality": "image"}
        for k in images
    ]
    bindings = binding_rows(
        {"kind": "archive_target", "asset_id": "query-pixels", "target_id": "P01"},
        {
            "kind": "archive_reference",
            "asset_id": "ref-a",
            "candidate_id": "A",
            "catalog_name": "Synthetic entry A",
            "catalog_locator": "catalog:sheet-a",
            "reference_roi": [20, 10, 410, 300],
        },
        {
            "kind": "archive_reference",
            "asset_id": "ref-b",
            "candidate_id": "B",
            "catalog_name": "Synthetic entry B",
            "catalog_locator": "https://example.invalid/catalog/b",
        },
    )
    return assets, bindings, raw


def enriched(assets, rows, raw):
    return enrich_archive(rows, assets, lambda asset: raw[asset["id"]])


def test_no_bindings_keep_existing_known_observations_unchanged_without_loading_backend():
    r = binding_rows({"kind": "observation", "image_id": "abc", "visible_features": "上游可见注记"})
    unchanged, gaps = enrich_archive(r, [], lambda _: pytest.fail("should not read any file"))
    assert unchanged is r and not gaps


def test_missing_offline_component_reports_gap_without_fallback_model(monkeypatch):
    from backend.app.casework import vision

    def unavailable():
        raise ValueError("vision 未安装")

    monkeypatch.setattr(vision, "_backend", unavailable)
    _, bindings, _ = fixture()
    unchanged, gaps = enrich_archive(bindings, [], lambda _: pytest.fail("no provider or file read"))
    assert unchanged == bindings and gaps and "未安装" in gaps[0]


def test_real_rotated_scaled_raster_correspondence_produces_only_supported_local_box():
    pytest.importorskip("cv2")
    assets, bindings, raw = fixture()
    normalized, gaps = enriched(assets, bindings, raw)
    assert not gaps
    row = next(r for r in normalized if r["fields"].get("kind") == "archive_observation")
    f = row["fields"]
    metrics = f["pixel_match"]
    x, y, w, h = f["bbox"]
    assert f["candidates"] == ["Synthetic entry A"] and f["candidate_id"] == "A"
    assert metrics["inliers"] >= 15 and metrics["inlier_fraction"] >= 0.62 and metrics["score"] > 6
    assert metrics["sample_inlier_pairs"] and 0 < x < x + w < 680 and 0 < y < y + h < 510
    assert metrics["reference_sha256"] == hashlib.sha256(raw["ref-a"]).hexdigest()
    assert metrics["target_sha256"] == hashlib.sha256(raw["query-pixels"]).hexdigest()
    assert row["source_id"] == "query-pixels" and row["location"]["pixel_region"] == f["bbox"]
    candidate = analyze("B03", normalized)
    assert not candidate["gaps"]
    card = candidate["products"]["image_catalog"][0]
    assert card["status"] == "supported_candidate" and set(card["candidates"]) == {"Synthetic entry A"}
    assert {e["source_id"] for e in candidate["evidence"]} == {"query-pixels", "ref-a", "catalog-data"}
    assert all(c["passed"] for c in audit("B03", candidate, normalized))
    assert all(c["passed"] for c in audit_archive(candidate, normalized))
    for mutate in ("name", "proof", "box"):
        tampered = deepcopy(candidate)
        card = tampered["products"]["image_catalog"][0]
        if mutate == "name":
            card["candidates"] = ["Unsupported entry B"]
        elif mutate == "proof":
            card["evidence_ids"] = card["evidence_ids"][:1]
        else:
            tampered["products"]["overlay_layers"][0]["bbox_xywh"] = [0, 0, 680, 510]
        assert not audit_archive(tampered, normalized)[0]["passed"]


@pytest.mark.parametrize("target", ["blank", "unrelated"])
def test_abstains_on_blank_and_unrelated_pixels_even_with_convincing_catalogue_name(target):
    pytest.importorskip("cv2")
    query = Image.new("RGB", (480, 350), "white") if target == "blank" else texture(55324)
    assets, bindings, raw = fixture(query=query)
    bindings[1]["fields"]["catalog_name"] = "Misleading matching title"
    normalized, gaps = enriched(assets, bindings, raw)
    assert gaps
    candidate = analyze("B03", normalized)
    assert candidate["products"]["image_catalog"][0]["status"] == "abstain"
    assert not candidate["products"]["overlay_layers"]


def test_duplicate_lookalike_catalogues_have_no_unique_winner():
    pytest.importorskip("cv2")
    assets, bindings, raw = fixture(other=texture(843))
    bindings[1]["fields"].pop("reference_roi")
    result, gaps = enriched(assets, bindings, raw)
    assert any("接近" in g for g in gaps)
    assert analyze("B03", result)["products"]["image_catalog"][0]["status"] == "abstain"


@pytest.mark.parametrize("mistake", ["outside_snapshot", "bad_roi", "same_image", "illegible_reference"])
def test_incomplete_or_invalid_reference_pool_never_uses_best_of_remaining(mistake):
    pytest.importorskip("cv2")
    assets, bindings, raw = fixture(
        other=Image.new("L", (480, 350), 0) if mistake == "illegible_reference" else None
    )
    if mistake == "outside_snapshot":
        bindings[2]["fields"]["asset_id"] = "https://example.invalid/remote.png"
    if mistake == "bad_roi":
        bindings[2]["fields"]["reference_roi"] = [0, -10, 30, 30]
    if mistake == "same_image":
        bindings[0]["fields"]["asset_id"] = "ref-b"
    called = []

    def read(asset):
        called.append(asset["id"])
        assert asset in assets
        return raw[asset["id"]]

    result, gaps = enrich_archive(bindings, assets, read)
    assert gaps and all(x["id"] in raw for x in assets)
    assert not any(
        r["fields"].get("candidates") for r in result if r["fields"].get("kind") == "archive_observation"
    )


def test_offline_extract_of_real_bytes_through_independent_review_without_upstream_target_label():
    pytest.importorskip("cv2")
    from backend.app.casework.records import extract

    assets, bindings, raw = fixture()
    assets = [
        dict(a, media_type="image/png", checksum=hashlib.sha256(raw[a["id"]]).hexdigest()) for a in assets
    ]
    assets.append(
        {
            "id": "bindings",
            "modality": "structured",
            "media_type": "application/json",
            "source_version": "1",
            "content": {"records": [r["fields"] for r in bindings]},
        }
    )
    rows, gaps = extract(assets, lambda a: raw[a["id"]], case_code="B03")
    assert not gaps
    candidate = analyze("B03", rows)
    assert not candidate["gaps"]
    assert candidate["products"]["image_catalog"][0]["candidates"] == ["Synthetic entry A"]
    assert all(c["passed"] for c in audit("B03", candidate, rows))
    # Tampering with one permitted reference must not turn the survivor into a certain winner.
    assets[2]["checksum"] = "not-the-file-checksum"
    _, gaps = enrich_archive(bindings, assets, lambda a: raw[a["id"]])
    assert any("冻结校验值" in g for g in gaps)
