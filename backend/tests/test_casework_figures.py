"""T05 checks on synthetic, differently valued chart records; no bundled report answers."""

from copy import deepcopy

from PIL import Image, ImageDraw

from backend.app.casework import document_chart_ocr, document_figures
from backend.app.casework.document_cases import analyze
from backend.app.casework.engine import audit


def page(n, text, fields=None, **loc):
    return {
        "source_id": "synthetic-report",
        "source_version": "edition-2",
        "location": {"page": n, **loc},
        "text": text,
        "fields": fields or {},
    }


def specimen():
    records = [
        page(
            1,
            "FIGURE 4: Fuel Burn from International Aviation, 2000 to 2080. "
            "For 2080 Fuel Scenario 9 (upper bound) aircraft technology accounts for a reduction of 91 Mt "
            "and operations accounts for an additional 17 Mt for a total reduction of 108 Mt as compared with Fuel Scenario 2. "
            "Fuel Scenario 9 results in 8% and 11% reduction in fuel burn as compared with Fuel Scenario 2 in 2060 and 2080, respectively.",
        ),
        page(
            2,
            "Figure 6 presents full-flight CO2 emissions from 2000 to 2080; assuming that 2 kg of jet fuel burned generates 6.4 kg of CO2. "
            "The delta between the CAEP/18 and CAEP/21 CO2 trends baselines in 2060 is approximately 18%. "
            "FIGURE 6: CO2 Emissions from International Aviation, 2000 to 2080. "
            "FIGURE 7: Combustion CO2 Emissions Including Alternative Jet Fuels. "
            "As shown in Figure 7, for the year 2060 and 2080, with alternative fuels replacement, there is a 37% reduction in residual CO2 emissions.",
        ),
    ]
    for n, p in [(4, 1), (6, 2), (7, 2)]:
        records.append(page(p, "", {"figure": n, "image_review": "inspected"}, figure=n, object=f"chart-{n}"))
    records.append(
        page(
            2,
            "312 MtCO₂",
            {"figure": 7, "image_label": True, "year": 2060, "value": 312, "unit": "Mt CO₂"},
            figure=7,
            object="chart-7",
            bbox_pixels=[50, 80, 95, 95],
        )
    )
    return records


def test_publication_charts_preserve_semantic_roles_pages_and_source_type():
    records = specimen()
    candidate = analyze("T05", records)
    assert not candidate["gaps"] and all(x["passed"] for x in audit("T05", candidate, records))
    rows = candidate["products"]["comparison"]
    assert {(r["year"], r["value"]) for r in rows if r["metric_role"] == "燃油消耗相对减少"} == {
        (2060, 8),
        (2080, 11),
    }
    coefficient = next(r for r in rows if r["metric_role"] == "燃烧换算系数")
    assert (coefficient["value"], coefficient["year"], coefficient["时间属性"]) == (
        3.2,
        "2000–2080",
        "核算前提",
    )
    printed = next(r for r in rows if r["basis"] == "label")
    assert (printed["figure"], printed["year"], printed["page"], printed["value"]) == (7, 2060, 2, 312)
    assert printed["取值方式"] == "图上标注" and printed["location"]["bbox_pixels"] == [50, 80, 95, 95]
    missing = deepcopy(candidate)
    for group in [
        missing["products"]["comparison"],
        missing["products"]["figure_cards"][0]["labelled_values"],
    ]:
        group[:] = [v for v in group if v["metric_role"] != "合计减少量"]
    assert any(
        c["name"] == "chart_discussion_inventory" and not c["passed"] for c in audit("T05", missing, records)
    )
    for key, fake in [("value", 713), ("year", 2070), ("unit", "%"), ("metric_role", "减少比例")]:
        changed = deepcopy(candidate)
        for group in [
            changed["products"]["comparison"],
            changed["products"]["figure_cards"][-1]["labelled_values"],
        ]:
            next(r for r in group if r["basis"] == "label")[key] = fake
        assert any(not c["passed"] and c["hard"] for c in audit("T05", changed, records))
    for group in [
        candidate["products"]["comparison"],
        candidate["products"]["figure_cards"][-1]["labelled_values"],
    ]:
        group[:] = [row for row in group if row["basis"] != "label"]
    assert any(
        c["name"] == "chart_printed_inventory" and not c["passed"] for c in audit("T05", candidate, records)
    )


def test_ocr_requires_an_explicit_label_a_unique_year_and_two_readings(monkeypatch):
    im = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im).polygon([(133, 169), (136, 172), (133, 175), (130, 172)], fill="black")
    ticks = [(str(year), x, 770, 40, 25, 95) for year, x in [(2020, 180), (2030, 380), (2040, 580)]]
    callout = [("731", 404, 530, 35, 20, 94), ("MtCO₂", 445, 529, 65, 22, 91)]
    monkeypatch.setattr(document_figures, "_words", lambda image, psm=11: ticks + callout)
    labels, clear = document_figures.printed_labels(im)
    assert clear and len(labels) == 1 and labels[0][:2] == (2030, 731)
    assert document_figures.printed_labels(Image.new("RGB", im.size, "white")) == ([], False)
    monkeypatch.setattr(document_figures, "_words", lambda image, psm=11: ticks)
    assert document_figures.printed_labels(im) == ([], True)
    monkeypatch.setattr(
        document_figures, "_words", lambda image, psm=11: ticks + (callout if psm == 11 else [])
    )
    assert document_figures.printed_labels(im) == ([], False)
    shifted = [(t, x + 90, y, w, h, c) for t, x, y, w, h, c in callout]
    monkeypatch.setattr(document_figures, "_words", lambda image, psm=11: ticks + shifted)
    assert document_figures.printed_labels(im) == ([], False)


def test_unreadable_figure_is_a_gap_even_when_body_has_projections():
    records = [r for r in specimen() if r["location"].get("figure") != 7]
    out = analyze("T05", records)
    assert any("图7未检查原图" in g for g in out["gaps"])


def test_tesseract_tsv_coordinates_are_pixels_and_confidence_is_normalized(monkeypatch):
    monkeypatch.setattr(document_chart_ocr, "ocr_args", lambda language: ["tesseract"])
    monkeypatch.setattr(
        document_chart_ocr,
        "command",
        lambda *args, **kwargs: b"level\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t2\t30\t40\t96\t456\n",
    )
    assert document_chart_ocr._words(Image.new("RGB", (10, 10), "white")) == [("456", 1, 2, 30, 40, 0.96)]
