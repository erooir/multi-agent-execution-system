import io
import zipfile

import pytest
from docx import Document

from backend.app.casework import edition_files, editions
from backend.app.casework.delivery import bundle
from backend.app.platform import api
from backend.app.platform.service import DomainError
from backend.tests import test_v2_platform as fixtures

db = fixtures.db


def test_project_upgrades_only_the_requested_case_and_preserves_older_versions(db):
    project = fixtures.project(db)
    pid = project["id"]
    cases = {}
    for code in ("B02", "G03"):
        case = {
            "id": code,
            "title": code,
            "goal": "核查统计表",
            "files": ["合成.csv"],
            "input_refs": ["fixture"],
            "scope": "合成数据",
            "deliverables": "简报",
        }
        cases[code] = case
        wid = editions.workflow_id(pid, code)
        old = {
            "id": wid,
            "project_id": pid,
            "status": "published",
            "current_version": 1,
            "edition_executor": editions.EDITION,
            "nodes": [],
        }
        db.put("workflows", old)
        db.put("workflow_versions", {**old, "id": f"{wid}:1", "definition_id": wid, "version": 1})
    db.put("projects", {**project, "scenario_edition": {"cases": cases}})
    assert not any(row["project_tools_current"] for row in api.case_catalog(pid, fixtures.ADMIN))
    with pytest.raises(DomainError):
        api.upgrade_case_tools(pid, "B02", fixtures.OTHER)
    with pytest.raises(DomainError):
        api.upgrade_case_tools(pid, "B11", fixtures.ADMIN)
    wid = editions.workflow_id(pid, "B02")
    assert wid in api.upgrade_case_tools(pid, "B02", fixtures.ADMIN)["updated"]
    result = {row["id"]: row for row in api.case_catalog(pid, fixtures.ADMIN)}
    assert result["B02"]["project_tools_current"] and not result["G03"]["project_tools_current"]
    assert result["B02"]["role_contract"]["skills"] == ["statistics-visualization"]
    assert "document.extract" in result["B02"]["role_contract"]["grants"]
    assert "edition_guidance" not in db.get("workflow_versions", f"{wid}:1")
    assert api.upgrade_case_tools(pid, "B02", fixtures.ADMIN) == {"created": [], "updated": []}
    assert db.get("workflows", wid)["current_version"] == 2


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
