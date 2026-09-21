from __future__ import annotations

import asyncio
import importlib
import io
import json
import zipfile
from copy import deepcopy

import pytest

knowledge_module = importlib.import_module("backend.app.knowledge")
reports = importlib.import_module("backend.app.reports")
seeds = importlib.import_module("backend.app.seeds")
from backend.app.storage import Store


@pytest.fixture
def local(tmp_path, monkeypatch):
    database = Store(tmp_path / "records.sqlite3")
    for module in (knowledge_module, reports, seeds):
        monkeypatch.setattr(module, "store", database)
    knowledge = knowledge_module.Knowledge(tmp_path)
    monkeypatch.setattr(knowledge_module, "knowledge", knowledge)
    database.save("projects", {"id": "test-project", "name": "测试资料", "category": "technology"})
    return knowledge, database


def test_ingest_retrieval_isolation_and_source_locations(local):
    knowledge, database = local
    document = knowledge.ingest(
        "研究.md", "# 材料研究\n\n复合材料回收采用加热重塑。缺少量产验证。".encode(), "test-project", "local"
    )
    evidence = knowledge.search("复合材料回收", "test-project")
    assert evidence and evidence[0]["document_id"] == document["id"]
    assert evidence[0]["visibility"] == "local"
    assert "行" in evidence[0]["location"]
    assert database.get("chunks", evidence[0]["id"])["text"] == evidence[0]["text"]
    assert knowledge.search("复合材料", "another-project") == []
    assert knowledge.search("复合材料", document_ids=[]) == []
    assert knowledge.search("量子纠缠") == []
    assert "path" not in document and "text" not in document
    assert knowledge.document_path(document["id"]).is_file()
    knowledge.remove(document["id"])
    assert not knowledge.search("复合材料")


@pytest.mark.parametrize(
    "name", ["../escape.txt", "..\\escape.txt", "C:\\escape.txt", "/tmp/a.txt", "x:y.txt", "x\x00.txt"]
)
def test_reject_path_traversal(local, name):
    with pytest.raises(ValueError, match="路径"):
        local[0].ingest(name, b"hello", "test-project", "external")


def test_reject_large_empty_bad_and_wrong_project(local, monkeypatch):
    knowledge = local[0]
    monkeypatch.setattr(knowledge_module, "MAX_UPLOAD_BYTES", 16)
    for content in (b"", b"x" * 17):
        with pytest.raises(ValueError):
            knowledge.ingest("test.txt", content, "test-project", "external")
    with pytest.raises(ValueError, match="不支持"):
        knowledge.ingest("run.exe", b"hello", "test-project", "external")
    with pytest.raises(ValueError, match="存在的项目"):
        knowledge.ingest("test.txt", b"hello", "missing", "external")


def test_csv_json_docx_parser_locations(local):
    knowledge = local[0]
    csv_doc = knowledge.ingest("样例.csv", "主题,年份\n复合材料,2026\n".encode(), "test-project", "external")
    assert "主题：复合材料" in knowledge.chunks(csv_doc["id"])[0]["text"]
    assert "第 2 行" in knowledge.chunks(csv_doc["id"])[0]["location"]
    json_doc = knowledge.ingest(
        "样例.json",
        json.dumps([{"主题": "航空技术"}], ensure_ascii=False).encode(),
        "test-project",
        "external",
    )
    assert "JSON $[0]" in knowledge.chunks(json_doc["id"])[0]["location"]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>航空技术测试</w:t></w:r></w:p></w:body></w:document>',
        )
    docx = knowledge.ingest("测试.docx", output.getvalue(), "test-project", "external")
    assert "第 1 段" in knowledge.chunks(docx["id"])[0]["location"]


def test_semantic_does_not_silently_fallback(local, monkeypatch):
    knowledge = local[0]
    knowledge.ingest("测试.txt", "复合材料研究".encode(), "test-project", "external")

    def unavailable():
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(knowledge, "_get_embedding", unavailable)
    with pytest.raises(RuntimeError, match="模型不可用"):
        knowledge.search("复合材料", semantic=True)
    assert knowledge.search("复合材料", semantic=False)


def test_semantic_ranks_vectors_not_keyword_scores(local, monkeypatch):
    import numpy as np

    knowledge = local[0]
    knowledge.ingest("a.txt", "飞机燃料研究".encode(), "test-project", "external")
    target = knowledge.ingest("b.txt", "低碳发展技术".encode(), "test-project", "external")

    class Embedding:
        def embed(self, texts, batch_size=16):
            return iter(np.array([1.0, 0.0]) if "低碳" in text else np.array([0.0, 1.0]) for text in texts)

        def query_embed(self, query):
            return iter([np.array([1.0, 0.0])])

    monkeypatch.setattr(knowledge, "_get_embedding", lambda: Embedding())
    result = knowledge.search("环境友好", semantic=True)
    assert result[0]["document_id"] == target["id"]
    assert result[0]["score"] == 1.0
    assert knowledge.search("环境友好", semantic=False) == []


def test_local_image_never_reaches_gateway(local, monkeypatch):
    from PIL import Image

    knowledge = local[0]
    image = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(image, format="PNG")
    document = knowledge.ingest("本地.png", image.getvalue(), "test-project", "local")
    with pytest.raises(ValueError, match="禁止发送"):
        asyncio.run(knowledge.execute("multimodal", {"document_ids": [document["id"]], "mode": "live"}))


def test_seed_idempotent_30_runnable_samples_and_graph(local, monkeypatch):
    knowledge, database = local
    workflows = importlib.import_module("backend.app.workflows")
    monkeypatch.setattr(workflows, "store", database)
    seeds.seed_all()
    before = {
        kind: len(database.list(kind))
        for kind in ("projects", "agents", "workflows", "samples", "documents", "chunks")
    }
    seeds.seed_all()
    assert before == {kind: len(database.list(kind)) for kind in before}
    assert before["agents"] == 5 and before["workflows"] == 6 and before["samples"] == 30
    assert any(agent["role"] == "parser" for agent in database.list("agents"))
    assert before["documents"] == 10
    for workflow in database.list("workflows"):
        result = workflows.validate_workflow(workflow)
        assert result["valid"], result
    for sample in database.list("samples"):
        assert knowledge.search(sample["prompt"], sample["project_id"]), sample["id"]
    for edge in knowledge.graph()["edges"]:
        assert database.get("chunks", edge["chunk_id"])
    assert all(doc["synthetic"] for doc in database.list("documents"))


def test_report_versions_restore_and_exports(local):
    knowledge, database = local
    knowledge.ingest("来源.txt", "复合材料研究依据".encode(), "test-project", "external")
    evidence = knowledge.search("复合材料")
    report = reports.create_report(
        {"id": "run-test", "name": "研究报告", "project_id": "test-project", "mode": "rehearsal"},
        "## 发现\n复合材料资料已整理。",
        evidence,
    )
    original = report["content"]
    assert "离线演练" in original
    changed = reports.update_report(report["id"], "<script>alert(1)</script>\n审核补充。", "修订稿")
    assert changed["version"] == 2
    page, _, _ = reports.export_report(report["id"], "html")
    assert "<script>" not in page.decode() and "&lt;script&gt;" in page.decode()
    restored = reports.restore(report["id"], 1)
    assert restored["version"] == 3 and restored["content"] == original
    assert [version["version"] for version in reports.versions(report["id"])] == [3, 2, 1]
    assert database.get("report_versions", report["id"] + ":1")["content"] == original
    binary, _, _ = reports.export_report(report["id"], "docx")
    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "研究报告" in xml and "引用资料索引" in xml
    assert evidence[0]["id"] in reports.export_report(report["id"], "md")[0].decode()
    approved = reports.mark_reviewed(report["id"], feedback="来源已核对")
    assert approved["status"] == "reviewed" and approved["version"] == 4
    assert database.get("report_versions", report["id"] + ":3")["snapshot"]["status"] == "draft"


def test_report_rejects_fabricated_citation(local):
    with pytest.raises(ValueError, match="伪造证据"):
        reports.create_report(
            {"id": "r", "project_id": "test-project"}, "正文", [{"id": "fiction", "document_id": "none"}]
        )


def test_seed_migration_keeps_history_and_user_edits(local):
    _, database = local
    seeds.seed_all()
    original = database.get("workflows", "workflow-tech-trends")
    old = deepcopy(original)
    old["edges"] = [edge for edge in old["edges"] if edge["source"] != "report"]
    old["edges"].append({"id": "old-report-end", "source": "report", "target": "end"})
    database.save("workflows", old)
    database.save(
        "workflow_versions",
        {"id": old["id"] + ":1", "workflow_id": old["id"], "version": 1, "snapshot": deepcopy(old)},
    )
    edited = database.get("workflows", "workflow-tech-comparison")
    edited.update(version=2, name="用户自定义名称")
    database.save("workflows", edited)
    seeds.seed_all()
    migrated = database.get("workflows", old["id"])
    assert migrated["version"] == 2
    assert migrated["edges"] == original["edges"]
    assert database.get("workflow_versions", old["id"] + ":1")["snapshot"]["edges"] == old["edges"]
    assert database.get("workflows", edited["id"])["name"] == "用户自定义名称"
    assert database.get("workflows", edited["id"])["version"] == 2
