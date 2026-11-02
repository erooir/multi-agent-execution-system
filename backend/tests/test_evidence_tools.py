"""Document operations must preserve verbatim offsets in frozen source versions."""

import io

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.platform.evidence_tools import TOOLS

BY_ID = {op.id: op for op in TOOLS}


class Reader:
    def __init__(self):
        self.sources = {}
        self.files = {}
        self.read = []

    def add(self, key, content, mime="text/plain"):
        self.sources[key] = {
            "id": key,
            "source_version": "frozen-" + key,
            "content": content,
            "media_type": mime,
        }
        if isinstance(content, bytes):
            self.files[key] = content
            self.sources[key]["content"] = None

    def __call__(self, key):
        if key not in self.sources:
            raise PermissionError("unapproved source")
        self.read.append(key)
        return self.sources[key]

    def read_bytes(self, key):
        self(key)
        return self.files[key]

    def run(self, tool, **arguments):
        op = BY_ID["evidence." + tool]
        return op.execute(self, op.schema.model_validate(arguments))


def paper(text, *, password=None):
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    body = DecodedStreamObject()
    literal = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body.set_data(f"BT /F1 12 Tf 10 200 Td ({literal}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = body
    if password:
        writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def located(row, source, text, page=None):
    assert row["source_id"] == source and row["source_version"] == "frozen-" + source
    assert row.get("page") == page
    assert text[row["start"] : row["end"]] == row["quote"]


def test_pair_comparison_tracks_each_side_and_never_guesses_semantics():
    reader = Reader()
    a = "前文。甲条款：三日提交。\r\n其它范围。"
    b = "新前文。\r\n甲条款：五日提交签章。"
    reader.add("before", a)
    reader.add("after", b)
    result = reader.run(
        "compare_passages",
        source_id="before",
        other_source_id="after",
        start=a.index("甲"),
        end=a.index("其它") - 2,
        other_start=b.index("甲"),
        other_end=len(b),
    )
    assert not result["identical"] and result["change_count"] == 2
    assert [(r["operation"], r["before"]["quote"], r["after"]["quote"]) for r in result["items"]] == [
        ("replace", "三", "五"),
        ("insert", "", "签章"),
    ]
    for r in result["items"]:
        located(r["before"], "before", a)
        located(r["after"], "after", b)
    assert set(reader.read) == {"before", "after"}
    with pytest.raises(PermissionError):
        reader.run(
            "compare_passages",
            source_id="before",
            other_source_id="outside",
            start=0,
            end=1,
            other_start=0,
            other_end=1,
        )
    with pytest.raises(ValidationError):
        reader.run(
            "compare_passages",
            source_id="before",
            other_source_id="after",
            start=0,
            end=2049,
            other_start=0,
            other_end=1,
        )


def test_quantities_preserve_measurement_range_uncertainty_qualifier_and_unit_boundaries():
    reader = Reader()
    txt = "条件A为≤1.7E-3 mg/kg；条件B为−4.5 ± 0.2 K。比较值7–9 km。独立数字345与CO2标识均未给单位。"
    reader.add("p", txt)
    first = reader.run("extract_quantities", source_id="p", limit=2)
    assert first["truncated"] and first["next_start"] == txt.index("7–9")
    assert [(v["relation"], v["value"], v["second_value"], v["unit"]) for v in first["items"]] == [
        ("≤", "1.7E-3", None, "mg/kg"),
        ("±", "−4.5", "0.2", "K"),
    ]
    for row in first["items"]:
        located(row, "p", txt)
        located(row["context"], "p", txt)
    next_page = reader.run("extract_quantities", source_id="p", start=first["next_start"])
    assert [v["quote"] for v in next_page["items"]] == ["7–9 km"] and next_page["next_start"] is None
    reader.add("codes", "AB12kg 15 msecs 20 kg/unknown; bare 19")
    assert reader.run("extract_quantities", source_id="codes")["items"] == []


def test_exact_definitions_in_both_orders_and_no_acronym_guessing():
    reader = Reader()
    txt = "粒径。particle size distributions (PSDs) differ. AOD (aerosol optical depth) observed. BAD (unrelated phrase)."
    reader.add("x", txt)
    result = reader.run("list_definitions", source_id="x")
    assert [r["acronym"] for r in result["items"]] == ["PSD", "AOD"]
    for r in result["items"]:
        located(r, "x", txt)
    assert reader.run("list_definitions", source_id="x", acronym="BAD")["items"] == []
    plural = reader.run("list_definitions", source_id="x", acronym="PSDs")["items"]
    assert len(plural) == 1 and plural[0]["acronym"] == "PSD"
    located(plural[0], "x", txt)
    assert (
        reader.run("list_definitions", source_id="x", acronym="PSD")["items"][0]["expansion"]
        == "particle size distributions"
    )


def test_timeline_orders_full_dates_and_does_not_use_invalid_or_ambiguous_dates():
    reader = Reader()
    txt = "发布于2024年3月2日，记录于2024-02-29；签名 January 12, 2024。坏日期2023-02-29。数字03/04/2024意义不明。"
    reader.add("x", txt)
    result = reader.run("timeline_dates", source_id="x")
    assert [r["date"] for r in result["items"]] == ["2024-01-12", "2024-02-29", "2024-03-02"]
    for row in result["items"]:
        located(row, "x", txt)
        located(row["context"], "x", txt)


def test_subtitle_intersection_uses_true_time_bounds_and_preserves_crlf_offsets():
    r = Reader()
    txt = (
        "WEBVTT\r\n\r\nNOTE explanation\r\n00:00:00.000 --> 00:00:05.000\r\nnot spoken\r\n\r\n"
        "cue-A\r\n00:00:01.000 --> 00:00:02.000 align:start\r\n第一句\r\n\r\n"
        "cue-B\r\n00:00:02.000 --> 00:00:03.500\r\n第二句\r\n跨行。\r\n\r\n"
        "cue-bad\r\n00:00:06.000 --> 00:00:05.010\r\n错误"
    )
    r.add("captions", txt, "text/vtt")
    results = r.run("select_cues", source_id="captions", start_ms=2000, end_ms=3500)
    assert len(results["items"]) == 1 and results["invalid_cues_seen"] == 1
    item = results["items"][0]
    assert (item["start_ms"], item["end_ms"], item["quote"]) == (2000, 3500, "第二句\r\n跨行。")
    located(item, "captions", txt)
    assert r.run("select_cues", source_id="captions", start_ms=3600, end_ms=4000)["items"] == []


def test_pdf_operations_use_verified_native_page_and_its_local_offsets():
    r = Reader()
    raw = paper("Measured 14 ppm, particle size distributions (PSDs). Issued 18 May 2023.")
    r.add("paper", raw, "application/pdf")
    page = r.run("pdf_page", source_id="paper", page=2)
    assert page["status"] == "ok" and page["page_count"] == 2 and page["total_chars"] == len(page["quote"])
    for name, needle in (
        ("extract_quantities", "14 ppm"),
        ("list_definitions", "particle size distributions (PSDs)"),
        ("timeline_dates", "18 May 2023"),
    ):
        result = r.run(name, source_id="paper", page=2)
        assert result["status"] == "ok" and result["items"][0]["quote"] == needle
        located(result["items"][0], "paper", page["quote"], 2)
        assert r.run(name, source_id="paper", page=1)["status"] == "native_text_unavailable"
        with pytest.raises(ValueError, match="页码"):
            r.run(name, source_id="paper")
    assert r.run("pdf_page", source_id="paper", page=1)["status"] == "native_text_unavailable"
    with pytest.raises(ValueError, match="页数或页码"):
        r.run("pdf_page", source_id="paper", page=3)
    r.add("plain", "14 ppm", "text/plain")
    with pytest.raises(ValueError, match="非PDF"):
        r.run("extract_quantities", source_id="plain", page=1)
    cmp = r.run(
        "compare_passages",
        source_id="paper",
        page=2,
        other_source_id="plain",
        start=9,
        end=15,
        other_start=0,
        other_end=6,
    )
    assert cmp["identical"] and cmp["before_scope"]["page"] == 2 and "page" not in cmp["after_scope"]


def test_encrypted_pdf_and_nonauthorized_sources_fail_closed():
    r = Reader()
    r.add("p", paper("secret", password="for-test"), "application/pdf")
    with pytest.raises(ValueError, match="加密"):
        r.run("pdf_page", source_id="p", page=1)
    with pytest.raises(PermissionError):
        r.run("list_definitions", source_id="other")
    with pytest.raises(ValidationError):
        r.run("pdf_page", source_id="p", page=1, path="example.pdf")
