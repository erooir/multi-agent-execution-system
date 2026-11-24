import io
import zipfile

import pytest
from docx import Document

from backend.app.casework import edition_files, editions
from backend.app.casework.delivery import bundle
from backend.app.platform.service import DomainError


def test_project_edition_replaces_reused_codes_and_scopes_sources():
    case = {
        "id": "B01",
        "title": "民用项目统计",
        "files": ["公告.csv"],
        "goal": "对比两月数据",
        "scope": "两月",
        "deliverables": "带证据的月报",
        "completion": "可按原件复算",
        "input_refs": ["new"],
        "checksums": {"new": "hash"},
    }
    project = {"id": "p", "scenario_edition": {"cases": {"B01": case}}}
    assert editions.directory({"id": "other"}) is None
    (row,) = editions.directory(project)
    assert row["title"] == case["title"] and row["case_request"]["input_refs"] == ["new"]
    assert editions.entry(project, "B01") == case
    with pytest.raises(DomainError):
        editions.entry(project, "B11")
    req = {"case_scope": "project", "mode": "cloud"}
    asset = {"id": "new", "checksum": "hash", "classification": "public"}
    assert editions.freeze(case, [asset], req)["edition"] == editions.EDITION
    for changed in ({"id": "old"}, {"classification": "local"}, {"checksum": "replaced"}):
        with pytest.raises(DomainError):
            editions.freeze(case, [{**asset, **changed}], req)
    with pytest.raises(DomainError):
        editions.freeze(case, [asset], {**req, "case_scope": "sample"})


def test_download_uses_frozen_attachments_and_new_report_layout(tmp_path):
    with pytest.raises(ValueError, match="冻结"):
        edition_files.read_attachments(
            {"attachments": [{"name": "someone.csv", "sha256": "x"}]},
            {"snapshot": {"inputs": []}, "project_id": "p"},
        )
    row = {"heading": "逐月对比", "text": "公开记录含两个可比月份。", "evidence_ids": ["E1"]}
    content = {
        "case_id": "B01",
        "title": "公开月报",
        "edition": editions.EDITION,
        "scope": "project",
        "gaps": [],
        "products": {"专题分析": [row], "资料核算与明细": {}, "资料适用范围": ["只涵盖两个整月。"]},
        "review": [{"name": "sum", "passed": True, "message": "复算通过", "hard": True}],
        "evidence": [
            {
                "id": "E1",
                "source_id": "asset",
                "source_version": "v1",
                "location": {"文件": "公告.csv", "原件位置": "第2行"},
                "quote": "二月",
            }
        ],
    }
    with zipfile.ZipFile(io.BytesIO(bundle(content, edition_files={"原始比较.csv": b"a,b"}))) as z:
        assert z.read("研究附件/原始比较.csv") == b"a,b" and "bulletin.docx" not in z.namelist()
        assert row["text"] in "".join(p.text for p in Document(io.BytesIO(z.read("brief.docx"))).paragraphs)
    html = tmp_path / "地图.html"
    html.write_text("<style>svg{width:99%}</style><svg></svg>", encoding="utf8")
    edition_files.validate_attachment(html)
    html.write_text("<script>alert(1)</script>", encoding="utf8")
    with pytest.raises(ValueError):
        edition_files.validate_attachment(html)
