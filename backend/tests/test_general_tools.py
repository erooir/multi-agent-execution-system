"""General tools operate on supplied sources, not predefined case answers."""

import hashlib
import io
import json

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image

from backend.app.platform.domain_tool import dispatch
from backend.app.platform.tool_registry import catalog


class Reader:
    def __init__(self):
        self.assets, self.raw = {}, {}
        self.checks = 0

    def add(self, source_id, value, mime="application/json", modality="structured"):
        raw = (
            value
            if isinstance(value, bytes)
            else (value.encode() if isinstance(value, str) else json.dumps(value).encode())
        )
        self.assets[source_id] = {
            "id": source_id,
            "source_version": "v1",
            "media_type": mime,
            "modality": modality,
            "content": None if isinstance(value, bytes) else value,
            "checksum": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "name": source_id,
        }
        self.raw[source_id] = raw
        return source_id

    def __call__(self, source_id):
        self.checkpoint()
        if source_id not in self.assets:
            raise ValueError("source_denied")
        return self.assets[source_id]

    def read_bytes(self, source_id):
        self(source_id)
        return self.raw[source_id]

    def checkpoint(self):
        self.checks += 1


def test_text_pagination_preserves_offsets_and_reports_partial_coverage():
    reader = Reader()
    reader.add("text", "First paragraph.\nSecond paragraph.\nThird.", "text/plain", "text")
    result = dispatch("document.extract", {"source_id": "text", "start": 1, "limit": 1}, reader)
    assert result["total_records"] == 3 and result["truncated"] and result["next_start"] == 2
    assert result["records"][0]["text"] == "Second paragraph."
    assert result["records"][0]["location"] == {"paragraph": 2, "start": 17, "end": 34}
    with pytest.raises(ValueError, match="source_denied"):
        dispatch("document.extract", {"source_id": "ungranted"}, reader)


def test_word_and_xlsx_extract_actual_document_locations():
    reader = Reader()
    doc = Document()
    doc.add_paragraph("Synthetic task reference")
    buffer = io.BytesIO()
    doc.save(buffer)
    reader.add(
        "word",
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text",
    )
    result = dispatch("document.extract", {"source_id": "word"}, reader)
    assert result["records"][0]["location"] == {"paragraph": 1}
    assert result["records"][0]["text"] == "Synthetic task reference"
    workbook = Workbook()
    workbook.active.append(["city", "count"])
    workbook.active.append(["A", 3])
    workbook.active.append(["B", 7])
    buffer = io.BytesIO()
    workbook.save(buffer)
    reader.add(
        "sheet", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    result = dispatch("table.describe", {"source_id": "sheet", "fields": ["count"]}, reader)
    assert result["fields"]["count"]["sum"] == "10"
    assert result["total_rows"] == 2


def test_table_statistics_do_not_impute_null_invalid_or_booleans():
    reader = Reader()
    reader.add("data", [{"n": "0.1"}, {"n": "0.2"}, {"n": None}, {"n": "bad"}, {"n": True}])
    result = dispatch("table.describe", {"source_id": "data", "fields": ["n"]}, reader)
    assert result["fields"]["n"] == {
        "count": 5,
        "missing": 1,
        "numeric_count": 2,
        "invalid_numeric": 2,
        "distinct": 4,
        "sum": "0.3",
        "mean": "0.15",
        "minimum": "0.1",
        "maximum": "0.2",
        "median": "0.15",
    }
    invalid = dispatch(
        "table.validate", {"source_id": "data", "required_fields": ["n"], "numeric_fields": ["n"]}, reader
    )
    assert not invalid["passed"] and invalid["invalid_rows"] == 3


def test_join_requires_unique_explicit_key_and_preserves_both_sources():
    reader = Reader()
    reader.add("left", [{"city": "A", "n": 2}, {"city": "B", "n": 4}])
    reader.add("right", [{"id": "A", "label": "Alpha"}])
    result = dispatch(
        "table.join",
        {"source_id": "left", "other_source_id": "right", "left_key": "city", "right_key": "id"},
        reader,
    )
    assert result["total_rows"] == 2 and result["unmatched_left_rows"] == 1
    assert result["records"][0]["locations"] == [
        {"source_id": "left", "source_version": "v1", "row": 1},
        {"source_id": "right", "source_version": "v1", "row": 1},
    ]
    assert result["records"][1]["right"] is None
    reader.add("right", [{"id": "A"}, {"id": "A"}])
    with pytest.raises(ValueError, match="join_right_key_not_unique"):
        dispatch(
            "table.join",
            {"source_id": "left", "other_source_id": "right", "left_key": "city", "right_key": "id"},
            reader,
        )


def test_chart_refuses_missing_values_and_keeps_unit_and_row_positions():
    reader = Reader()
    reader.add("data", [{"month": "2026-01", "n": 4}, {"month": "2026-02", "n": 6}])
    result = dispatch(
        "table.chart",
        {"source_id": "data", "category_field": "month", "value_field": "n", "unit": "items"},
        reader,
    )
    assert result["values"][1] == {"category": "2026-02", "value": "6", "location": {"row": 2}}
    reader.add("data", [{"month": "2026-01", "n": None}])
    with pytest.raises(ValueError, match="chart_fields_missing"):
        dispatch(
            "table.chart",
            {"source_id": "data", "category_field": "month", "value_field": "n", "unit": "items"},
            reader,
        )


def test_image_probe_reports_real_decoded_dimensions():
    reader = Reader()
    buffer = io.BytesIO()
    Image.new("RGB", (31, 19), "white").save(buffer, format="PNG")
    reader.add("image", buffer.getvalue(), "image/png", "image")
    result = dispatch("media.probe", {"source_id": "image"}, reader)
    assert (result["width"], result["height"], result["format"]) == (31, 19, "PNG")


def test_every_document_skill_dependency_exists_and_readiness_is_separate(monkeypatch):
    monkeypatch.setenv("HANGZHI_SANDBOX_ENABLED", "false")
    available = catalog()
    names = {tool["name"] for tool in available["tools"]}
    assert len(available["skills"]) == 12 and len(available["tools"]) == 30
    assert all(set(skill["required_tools"]).issubset(names) for skill in available["skills"])
    assert all("readiness" not in tool for tool in available["tools"])
    assert not available["readiness"]["tools"]["script.execute"]["available"]
    assert not available["readiness"]["skills"]["scripted-data-processing"]["available"]
    assert available["readiness"]["skills"]["document-reading"]["available"]
