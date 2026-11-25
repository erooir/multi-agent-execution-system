"""Download builder tests; all observations are deliberately synthetic."""

import base64
import csv
import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from backend.app.casework import delivery
from backend.app.casework.delivery import bundle, stage_bundle
from backend.app.casework.delivery_files import csv_cell, safe_svg
from backend.app.casework.delivery_html import brief_html


def report(code="B01", products=None, review=None, gaps=None):
    return {
        "case_id": code,
        "title": "合成证据分析",
        "products": products or {},
        "evidence": [
            {
                "id": "e1",
                "source_id": "sample-a",
                "source_version": "v2",
                "location": {"page": 3},
                "quote": "示例观察",
            },
            {
                "id": "e2",
                "source_id": "sample-b",
                "source_version": "v4",
                "location": {"row": 7},
                "quote": "数据范围",
            },
        ],
        "review": review or [{"name": "invariant", "passed": True, "message": "样例一致", "hard": True}],
        "gaps": gaps or [],
    }


def unpack(body):
    return zipfile.ZipFile(io.BytesIO(body))


def word(archive, name="brief.docx"):
    doc = Document(io.BytesIO(archive.read(name)))
    return "\n".join(
        [p.text for p in doc.paragraphs] + [c.text for t in doc.tables for row in t.rows for c in row.cells]
    )


@pytest.mark.parametrize("stage", ["case_candidate", "case_records"])
def test_stage_reading_copy_is_passive_and_preserves_the_complete_source(stage):
    quote = '<script src="https://invalid.test">材料标记</script>'
    content = (
        {"findings": [{"heading": "资料比对", "text": quote, "evidence_ids": ["E1", "E2"]}]}
        if stage == "case_candidate"
        else {
            "approach": "核对附录行号",
            "priority_ids": ["E1"],
            "source_checks": [{"name": "页数核查", "passed": True, "message": "共两页"}],
            "records": [{"id": "E1", "原件位置": "表五第九行", "依据": quote}] * 105,
            "file_context": {"对照表.csv": "检查摘录"},
        }
    )
    content["gaps"] = ["原件第二列缺失"]
    with unpack(stage_bundle(stage, content, "合成案例")) as z:
        html = z.read("过程记录.html").decode()
        assert json.loads(z.read("original.json")) == content
        assert "材料标记" in html and "&lt;script" in html and "<script" not in html
        assert "原件第二列缺失" in html and "合成案例" in html
        if stage == "case_candidate":
            assert "<h2>资料比对</h2>" in html and "E1、E2" in html
        else:
            assert "共 105 条，摘列 100 条" in html and "对照表.csv" in html and "表五第九行" in html


def test_html_uses_the_embedded_word_image_at_its_caption_and_respects_package_budget():
    doc = Document()
    doc.add_heading("示例图", 2)
    image = io.BytesIO()
    Image.new("RGB", (24, 16), "navy").save(image, format="PNG")
    doc.add_picture(image)
    doc.add_paragraph("图后说明")
    raw = base64.b64encode(image.getvalue()).decode()
    page = brief_html(doc, 10_000).decode()
    assert (
        page.index("<h3>示例图</h3>")
        < page.index(f'src="data:image/png;base64,{raw}"')
        < page.index("图后说明")
    )
    reduced = brief_html(doc, len(page.encode()) - 1).decode()
    assert "图件见同包 brief.docx" in reduced and "data:image" not in reduced


def test_full_legacy_package_survives_when_optional_html_or_its_readme_note_will_not_fit(monkeypatch):
    result = report(products={"表一": [{"站点": "南区", "读数": 2}]})
    with unpack(bundle(result)) as normal:
        old_files = set(normal.namelist()) - {"研究简报.html"}
        readme = normal.read("README.txt").decode()
        legacy_readme = "".join(
            line for line in readme.splitlines(keepends=True) if "研究简报.html" not in line
        )
        base_size = len(legacy_readme.encode()) + sum(
            normal.getinfo(name).file_size for name in old_files - {"README.txt", "manifest.json"}
        )
        full_size = sum(info.file_size for info in normal.infolist() if info.filename != "manifest.json")
        originals = {
            name: normal.read(name)
            for name in old_files
            if name.endswith((".json", ".csv")) and name != "manifest.json"
        }
        original_text = {name: word(normal, name) for name in old_files if name.endswith(".docx")}
    for limit in (base_size, full_size - 1):
        monkeypatch.setattr(delivery, "MAX_BUNDLE_BYTES", limit)
        with unpack(bundle(result)) as archive:
            assert set(archive.namelist()) == old_files
            assert archive.read("README.txt").decode() == legacy_readme
            assert all(archive.read(name) == data for name, data in originals.items())
            assert all(word(archive, name) == value for name, value in original_text.items())
            manifest = json.loads(archive.read("manifest.json"))
            assert {f["name"] for f in manifest["files"]} == old_files - {"manifest.json"}
            assert any(
                "研究简报.html" in entry["reason"] and "brief.docx" in entry["reason"]
                for entry in manifest["omitted"]
            )
    monkeypatch.setattr(delivery, "MAX_BUNDLE_BYTES", base_size - 1)
    with pytest.raises(ValueError, match="文件包超过导出上限"):
        bundle(result)


def test_historical_mixed_stage_values_keep_a_passive_preview_and_complete_json():
    content = {
        "findings": ["旧版摘要", {"无固定列": "多列结论"}],
        "products": {"观测": [{"日期": "十月"}, {"增量": 3}, "备注"], "汇总": {"行数": 2}},
        "gaps": [{"缺字段": "精度"}],
    }
    with unpack(stage_bundle("case_candidate", content, "历史材料")) as archive:
        page = archive.read("过程记录.html").decode()
        assert "旧版摘要" in page and "多列结论" in page and "精度" in page
        assert json.loads(archive.read("original.json")) == content


def test_brief_uses_simplified_chinese_fonts_without_losing_finding_emphasis():
    title = "苏北人口安置压力分析"
    finding = {"heading": "样本观察", "text": "本期登记人数保持稳定。", "evidence_ids": ["e1"]}
    r = report("B02", {"专题分析": [finding]})
    r.update(title=title, edition="public-casework-v1", scope="project")
    r["evidence"][0]["location"] = {"原件位置": "第3页"}
    r["evidence"][1]["location"] = {"原件位置": "第7行"}
    with unpack(bundle(r)) as archive:
        doc = Document(io.BytesIO(archive.read("brief.docx")))
        html = archive.read("研究简报.html").decode()
        assert f"<h1>{title}</h1>" in html and "<strong>样本观察" in html and "<table>" in html
        assert finding["text"] in html and "第3页" in html

    assert doc.paragraphs[0].text == title
    assert doc.paragraphs[0].style.name == "Title"
    default_lang = doc.styles.element.xpath("./w:docDefaults/w:rPrDefault/w:rPr/w:lang")[0]
    assert default_lang.get(qn("w:eastAsia")) == "zh-CN"
    assert doc.settings.element.find(qn("w:themeFontLang")).get(qn("w:eastAsia")) == "zh-CN"
    for name in ("Normal", "Title", "Subtitle", "Heading 1", "Heading 2"):
        style = doc.styles[name]
        fonts = style.element.rPr.rFonts
        assert all(
            fonts.get(qn(f"w:{slot}")) == "Microsoft YaHei" for slot in ("ascii", "hAnsi", "eastAsia", "cs")
        )
        assert not any(key.lower().endswith("theme") for key in fonts.attrib)
        assert bool(style.font.bold) == (name in {"Title", "Heading 1", "Heading 2"})

    paragraph = next(p for p in doc.paragraphs if p.text.startswith(finding["heading"]))
    label, body = paragraph.runs
    assert label.bold and label.text.strip() == finding["heading"]
    assert not body.bold and finding["text"] in body.text


def test_report_reuses_actual_located_values_and_indexes_every_generated_file():
    r = report(
        products={
            "observations": [
                {"region": "合成区域", "count": 13, "previous": 17, "change": -4, "evidence_ids": ["e1"]}
            ],
            "report_window": {"published_date": {"value": "2000-03-02", "evidence_ids": ["e2"]}},
        }
    )
    metadata = {"task_id": "t1", "status": "succeeded", "secret": "do-not-persist"}
    source_names = {"sample-a": "社区观察记录", "unreferenced": "其他来源不应出现在简报中"}
    with unpack(bundle(r, metadata, source_names=source_names)) as z:
        manifest = json.loads(z.read("manifest.json"))
        for f in manifest["files"]:
            data = z.read(f["name"])
            assert len(data) == f["bytes"] and hashlib.sha256(data).hexdigest() == f["sha256"]
        payload = json.loads(z.read("report.json"))
        assert payload["products"] == r["products"] and payload["evidence"] == r["evidence"]
        assert "secret" not in payload["task"] and payload["task"]["task_id"] == "t1"
        text = word(z)
        assert "社区观察记录" in text and source_names["unreferenced"] not in text
        assert "source_names" not in payload["task"] and payload["evidence"][0]["source_id"] == "sample-a"
        assert "本期数量为13，上期为17，差额为-4" in text and "〔e1〕" in text and "2000-03-02" in text
        assert "13起" not in text
        evidence = list(csv.DictReader(io.StringIO(z.read("evidence.csv").decode("utf-8-sig"))))
        assert json.loads(evidence[1]["location"]) == {"row": 7}


def test_milestone_brief_includes_the_last_event_with_its_distinct_publication_dates_and_evidence():
    milestones = [
        {
            "project": "合成民航项目",
            "event_type": "milestone",
            "event_date": f"2001-03-0{i}",
            "status": "new_event",
            "report_dates": [f"2001-04-0{i}", f"2001-05-0{i}"],
            "evidence_ids": [f"E{i}"],
        }
        for i in range(1, 4)
    ]
    r = report("T11", {"milestones": milestones})
    r["evidence"] = [
        {"id": f"E{i}", "source_id": "civil-timeline", "source_version": "v3", "location": {"row": i}}
        for i in range(1, 4)
    ]
    with unpack(bundle(r)) as z:
        doc = Document(io.BytesIO(z.read("brief.docx")))
        rows = [" ".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows]
        for row in milestones:
            fields = [row["event_date"], *row["report_dates"], *row["evidence_ids"]]
            assert any(all(field in line for field in fields) for line in rows)
        text = word(z)
        assert "civil-timeline" in text and "v3" in text
        assert json.loads(z.read("report.json"))["products"]["milestones"] == milestones


def test_meeting_brief_preserves_timed_speech_and_transcript_only_material():
    cue = {
        "topic": "开放时间",
        "speaker": "馆长",
        "turn_type": "answer",
        "text": "The library opens on Monday.",
        "translation_zh": "图书馆周一开放。",
        "start_ms": 2410,
        "end_ms": 6930,
        "evidence_ids": ["e1"],
    }
    additions = [
        {"speaker": "记录员", "text": "借阅规则将于下周送审。", "evidence_ids": ["e2"]},
        {"speaker": "记录员", "text": "周三发放首批借书证。", "evidence_ids": ["e2"]},
        {"speaker": "记录员", "text": "儿童阅览区将在六月单独开放。", "evidence_ids": ["e2"]},
    ]
    products = {"timed_index": [cue], "transcript_only": additions}
    with unpack(bundle(report("B04", products))) as z:
        text = word(z)
        assert all(value in text for value in (cue["text"], cue["translation_zh"], "2410", "6930"))
        assert all(row["text"] in text for row in additions)
        assert "sample-a" in text and "sample-b" in text
        assert json.loads(z.read("report.json"))["products"] == products


def test_chart_brief_preserves_figure_coverage_values_time_kinds_and_source_rows():
    def point(figure, year, value, kind, row):
        return {
            "figure": figure,
            "year": year,
            "value": value,
            "kind": kind,
            "unit": "t",
            "scenario": "纸张回收",
            "location": {"row": row},
            "evidence_ids": [f"E{row}"],
        }

    historical = [point(4, 2020 + i, i, "historical", i + 1) for i in range(8)]
    forecasts = [point(6, 2030, 12, "forecast", 9), point(7, 2040, 25, "forecast", 10)]
    rows = historical + forecasts
    r = report("T05", {"comparison": rows})
    r["evidence"] = [
        {
            "id": row["evidence_ids"][0],
            "source_id": "labels",
            "source_version": "1",
            "location": row["location"],
        }
        for row in rows
    ]
    with unpack(bundle(r)) as z:
        text = word(z)
        assert "图4，2020年，情景“纸张回收”，历史，0 t，第1行〔E1〕" in text
        assert "图6，2030年，情景“纸张回收”，预测，12 t，第9行〔E9〕" in text
        assert "图7，2040年，情景“纸张回收”，预测，25 t，第10行〔E10〕" in text
        assert "简报摘列7条，共10条" in text and "report.json 的 products.comparison" in text
        assert json.loads(z.read("report.json"))["products"]["comparison"] == rows


@pytest.mark.parametrize("code", ["B08", "B10", "T08"])
def test_matrix_brief_samples_each_source_role_and_preserves_disagreements(code):
    claims = [
        {
            "role": role,
            "published_at": date,
            "statement": text,
            "scope": "同批",
            "comparable_value": count,
            "evidence_ids": [eid],
        }
        for role, date, text, count, eid in [
            *(("project", "2024-01-01", "登记12箱", 12, "e1") for _ in range(8)),
            ("audit", "2024-01-02", "清点11箱", 11, "e2"),
            ("study", "2024-01-03", "观察11箱", 11, "e3"),
        ]
    ]
    issues = [
        {"question": "收货数量", "status": "conflicting_values", "claims": claims},
        {"question": "次日数量", "status": "missing", "claims": []},
    ]
    r = report(code, {"matrix": issues, "version_timeline": claims})
    r["evidence"].append({"id": "e3", "source_id": "c", "source_version": "1", "location": {"row": 1}})
    with unpack(bundle(r)) as z:
        text = word(z)
        for role, date, value, evidence in [
            ("项目方", "2024-01-01", "登记12箱", "e1"),
            ("审计", "2024-01-02", "清点11箱", "e2"),
            ("研究", "2024-01-03", "观察11箱", "e3"),
        ]:
            assert any(
                f"{role}，{date}发布，{value}" in line and f"同口径数值有分歧〔{evidence}〕" in line
                for line in text.splitlines()
            )
        assert "简报摘列7条来源陈述，共10条" in text and "1个问题缺少来源陈述" in text
        assert "report.json 的 products.matrix" in text


def test_battery_brief_reports_endpoint_differences_with_units_sources_and_scope():
    series = []
    for metric, unit, first, last in [("capacity", "Ah", 12, 11), ("resistance", "mOhm", 2, 2.4)]:
        series.append(
            {
                "cell": "lamp",
                "metric": metric,
                "unit": unit,
                "observed_difference": last - first,
                "points": [
                    {"cycle": 1, "value": first, "evidence_ids": ["e1"]},
                    {"cycle": 3, "value": last, "evidence_ids": ["e2"]},
                ],
            }
        )
    scope = {"literature": [{"population": "单个灯具", "conditions": "22℃恒定负载", "evidence_ids": ["e1"]}]}
    r = report("T09", {"trend_series": series, "applicability": scope})
    with unpack(bundle(r)) as z:
        text = word(z)
        assert "容量：第1循环为12 Ah，第3循环为11 Ah，首末差为-1 Ah〔e1, e2〕" in text
        assert "内阻：第1循环为2 mOhm，第3循环为2.4 mOhm，首末差为0.4 mOhm〔e1, e2〕" in text
        assert "样本为单个灯具，实验条件为22℃恒定负载〔e1〕" in text
        assert "热点图层" not in text


def test_portfolio_brief_separates_currency_roles_unknown_fields_and_larger_lists():
    def project(code, currency, cost, contribution, evidence):
        return {
            "project_id": code,
            "theme": "储能",
            "currency": currency,
            "total_cost": cost,
            "contribution": contribution,
            "evidence_ids": [evidence],
        }

    projects = [
        project("A", "EUR", 10, None, "e1"),
        project("B", "EUR", 20, 5, "e2"),
        project("C", "USD", 7, None, "e1"),
        project("D", None, 70, None, "e2"),
        project("F", "GBP", None, None, "e2"),
    ]
    sums = [
        {
            "theme": "储能",
            "currency": "EUR",
            "total_cost": {"known_amount": 30, "records": 2, "evidence_ids": ["e1", "e2"]},
            "contribution": {"known_amount": 5, "records": 1, "evidence_ids": ["e2"]},
        },
        {
            "theme": "储能",
            "currency": "USD",
            "total_cost": {"known_amount": 7, "records": 1, "evidence_ids": ["e1"]},
        },
    ]
    edges = [
        {"from": code, "to": "社区图书馆", "role": role, "evidence_ids": [eid]}
        for code, role, eid in [
            ("A", "coordinator", "e1"),
            ("A", "participant", "e1"),
            ("B", "participant", "e2"),
            ("B", "coordinator", "e2"),
            ("C", "participant", "e1"),
        ]
    ]
    r = report(
        "T04",
        {
            "portfolio": projects,
            "theme_summary": {"known_funding": sums},
            "organization_graph": {"edges": edges},
        },
    )
    with unpack(bundle(r)) as z:
        text = word(z)
        for expected in (
            "项目清单共5项",
            "已知总成本 30 EUR〔e1, e2〕",
            "已知贡献额 5 EUR〔e2〕（1项未计入该数额〔e1〕）",
            "已知总成本 7 USD〔e1〕，已知贡献额未提供〔e1〕",
            "币种未提供，未计入资金汇总〔e2〕",
            "项目 A 的协调机构为社区图书馆〔e1〕",
            "项目 B 的参与机构为社区图书馆〔e2〕",
            "完整4组资金与5条角色关系见 report.json",
        ):
            assert expected in text
        assert "37 EUR" not in text and "0 USD" not in text


def test_citation_brief_distinguishes_claim_actions_and_identifies_omitted_claims():
    claims = [
        {
            "claim_id": f"C{i}",
            "action": action,
            "reason": reason,
            "original": "合成主张",
            "evidence_ids": [eid],
        }
        for i, (action, reason, eid) in enumerate(
            [
                ("withdraw", "唯一依据均已撤稿", "e1"),
                ("withdraw", "唯一依据均已撤稿", "e1"),
                ("withdraw", "唯一依据均已撤稿", "e1"),
                ("withdraw", "唯一依据均已撤稿", "e1"),
                ("unaffected", "有独立支持", "e2"),
            ],
            1,
        )
    ]
    with unpack(bundle(report("T10", {"citation_impact": claims}))) as z:
        text = word(z)
        assert "主张 C1 建议撤回。合成主张。依据：唯一依据均已撤稿〔e1〕" in text
        assert "主张 C5 建议保留。合成主张。依据：有独立支持〔e2〕" in text
        assert "简报摘列4条主张，共5条" in text and "products.citation_impact" in text


def test_unreconciled_months_and_missing_baseline_are_not_rendered_as_success_or_zero():
    r = report(
        "B05",
        {
            "monthly": [{"month": "2001-02", "complete_month": False, "evidence_ids": ["e1"]}],
            "reconciliation": [
                {
                    "scope": "演示范围",
                    "computed": None,
                    "reported": [[3, 8]],
                    "agrees": False,
                    "evidence_ids": ["e2"],
                }
            ],
        },
        review=[{"name": "coverage", "passed": False, "message": "只含部分月份", "hard": False}],
        gaps=["缺少两个完整月份"],
    )
    with unpack(bundle(r, {"status": "partial"})) as z:
        text = word(z)
        assert "仅支持部分结果" in text and "不足以独立复算" in text and "死亡3人、受伤8人" in text
        assert "月度记录加总为死亡0" not in text and "缺少两个完整月份" in text


def test_geo_features_svg_and_baseline_summary_match_payload():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><path d="M0,0 L20,15" stroke="#27597a" fill="none"/></svg>'
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [8, 30]},
                "properties": {"id": "obs"},
            }
        ],
    }
    r = report(
        "G03",
        {
            "annual_series": [{"year": 2002, "extent": 8, "evidence_ids": ["e1"]}],
            "anomaly": {
                "target_year": 2002,
                "unit": "km²",
                "target_extent": 8,
                "baseline_years": [2000, 2001],
                "baseline_n": 2,
                "baseline_mean": 10,
                "difference": -2,
                "percent": -20,
                "ascending_rank": 1,
            },
            "trend_svg": svg,
            "test_layer": geo,
        },
    )
    with unpack(bundle(r)) as z:
        assert json.loads(z.read("layers/layer-001.geojson")) == geo
        assert ET.fromstring(z.read("figures/figure-001.svg")).tag.endswith("}svg")
        doc = word(z)
        assert "平均范围为8 km²" in doc and "均值为10 km²" in doc and "低于基线2 km²（相差20%）" in doc
        assert "排名第1" in doc and "〔e1〕" in doc


def test_untrusted_keys_formulas_translations_and_unsafe_figures_do_not_become_active_content():
    rows = [
        {
            "=标题": '=HYPERLINK("https://invalid.test")',
            "bin": "\t @SUM(1,2)",
            "number": -8,
            "translation_zh": "示例译文",
            "translation_method": "机器翻译；尚未由来源确认",
            "evidence_ids": ["e1"],
        }
    ]
    bads = [
        "<svg><script>alert(1)</script></svg>",
        '<svg><image href="https://invalid.test"/></svg>',
        '<svg><path style="fill:url(javascript:alert(1))"/></svg>',
        '<svg onload="alert(1)"/>',
        "<!DOCTYPE svg><svg/>",
    ]
    products = {"../../bad/evil.csv": rows, **{f"unsafe{i}": s for i, s in enumerate(bads)}}
    with unpack(bundle(report("T07", products))) as z:
        for name in z.namelist():
            assert (
                not PurePosixPath(name).is_absolute()
                and ".." not in PurePosixPath(name).parts
                and "\\" not in name
            )
        assert not any(n.endswith(".svg") for n in z.namelist())
        cells = list(csv.reader(io.StringIO(z.read("tables/table-001.csv").decode("utf-8-sig"))))
        assert (
            cells[0][0] == "'=标题"
            and cells[1][0].startswith("'=HYPERLINK")
            and cells[1][1].startswith("'\t")
            and cells[1][2] == "-8"
        )
        assert json.loads(z.read("report.json"))["products"] == products
        assert len(json.loads(z.read("manifest.json"))["omitted"]) >= 4
        assert "机器翻译；尚未由来源确认" in word(z)
    assert csv_cell("  ＋公式").startswith("'")
    assert csv_cell("\x00\ufeff=implicit").startswith("'")


@pytest.mark.parametrize(
    "bad",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        '<!DOCTYPE svg [<!ENTITY x "boom">]><svg><text>&x;</text></svg>',
        '<svg><image href="data:image/svg+xml;base64,AAA"/></svg>',
        '<svg><path fill="URL(https://invalid.test)"/></svg>',
    ],
)
def test_svg_allowlist_refuses_external_or_active_documents(bad):
    with pytest.raises(ValueError):
        safe_svg(bad)


def test_withdrawn_content_and_unaudited_candidates_cannot_be_exported():
    with pytest.raises(PermissionError):
        bundle({**report(), "content_withheld": True})
    with pytest.raises(PermissionError):
        bundle(report(), {"unavailable_source_refs": ["asset-1"]})
    with pytest.raises(ValueError):
        bundle({**report(), "review": []})
    with pytest.raises(ValueError):
        bundle({**report(), "case_id": "../../escape"})


def test_hard_review_failure_preserves_diagnostics_without_promoting_candidate_to_a_finding():
    r = report(
        products={"observations": [{"region": "虚构", "count": 900, "previous": 2, "change": 898}]},
        review=[{"name": "source_replay", "passed": False, "hard": True, "message": "数值与原件不符"}],
    )
    with unpack(bundle(r, {"status": "failed"})) as z:
        text = word(z)
        assert "强制核验" in text and "数值与原件不符" in text and "900" not in text
        html = z.read("研究简报.html").decode()
        assert "数值与原件不符" in html and "虚构" not in html
        assert json.loads(z.read("report.json"))["products"] == r["products"]


def test_export_preserves_acceptance_scope_and_located_machine_reading():
    r = report("T01", {"summary": "Synthetic observation"})
    r.update(
        scope="sample",
        reading_aids=[{"evidence_id": "e1", "text_zh": "合成阅读线索", "method": "本地机译"}],
        reading_notes=["数值原文未核实"],
        reference_batch=None,
    )
    with unpack(bundle(r)) as archive:
        result = json.loads(archive.read("report.json"))
        assert result["scope"] == "sample" and result["reading_aids"] == r["reading_aids"]
        assert result["reading_notes"] == r["reading_notes"] and result["reference_batch"] is None
        rendered = word(archive)
        assert "合成阅读线索" in rendered and "数值原文未核实" in rendered


@pytest.mark.parametrize(("code", "extra"), [("B01", "bulletin.docx"), ("T01", "reading-card.docx")])
def test_sample_scope_boilerplate_is_removed_from_all_word_exports(code, extra):
    r = {**report(code), "scope": "sample"}
    with unpack(bundle(r)) as archive:
        for name in ("brief.docx", extra):
            text = word(archive, name)
            assert "样本" in text
            assert "验收范围：缩小样本验证" not in text and "不代表完整参考用例验收" not in text
        assert json.loads(archive.read("report.json"))["scope"] == "sample"


def test_bulletin_distinguishes_field_observations_hearsay_assessments_and_absent_entries():
    r = report(
        products={
            "observations": [
                {"region": "区域甲", "count": 9, "previous": 12, "change": -3, "evidence_ids": ["e1"]}
            ],
            "report_window": {"report_number": {"value": "S-42", "evidence_ids": ["e2"]}},
            "context": {
                "observed": [{"text": "示例观察", "evidence_ids": ["e1"]}],
                "reported": [{"text": "受访者称维修延迟", "evidence_ids": ["e2"]}],
            },
        },
        gaps=["未记录时区"],
    )
    r["scope"] = "reference"
    r["reference_batch"] = {"registered": False}
    with unpack(bundle(r, {"status": "partial"})) as z:
        t = word(z, "bulletin.docx")
        assert all(
            v in t
            for v in (
                "S-42",
                "受访者称维修延迟",
                "报告评估 · assessed　未提取",
                "未记录时区",
                "未登记",
                "-3",
                "S1/e2·行7",
                "S2/e1·第3页",
            )
        )
        assert "reading-card.docx" not in z.namelist() and "brief.docx" in z.namelist()


def test_bulletin_labels_follow_the_subject_of_the_cited_statistics():
    r = report(
        products={
            "observations": [
                {"region": "公园东片区", "count": 18, "previous": 12, "change": 6, "evidence_ids": ["e1"]}
            ]
        }
    )
    r["evidence"][0]["quote"] = "本期已完成18件公园座椅维护工单。"
    with unpack(bundle(r)) as z:
        text = word(z, "bulletin.docx")
        assert "区域观察统计快报" in text and "分区统计" in text and "公园东片区" in text
        assert "停火" not in text and "本期（起）" not in text
    r["evidence"][0]["quote"] = "The report recorded 18 ceasefire violations."
    with unpack(bundle(r)) as z:
        text = word(z, "bulletin.docx")
        assert "停火监测日报快报" in text and "分区停火观察" in text


def test_paper_card_separates_actual_measurement_model_limits_and_successful_translation():
    def item(text, ref):
        return [{"text": text, "evidence_ids": [ref]}]

    r = report(
        "T01",
        {
            "bibliography": {"title": {"value": "Synthetic study", "evidence_ids": ["e2"]}},
            "literature_card": {
                "measurements": item("A had seven particles.", "e1"),
                "model_results": item("模型估计九个颗粒", "e2"),
            },
        },
    )
    r["scope"] = "sample"
    r["evidence"][0]["quote"] = "A had seven particles."
    r["reading_aids"] = [
        {
            "evidence_id": "e1",
            "text_zh": "试验 A 实测七个颗粒",
            "method": "离线机器翻译",
            "authoritative": False,
        }
    ]
    r["reading_notes"] = ["另一段否定词未保留"]
    with unpack(bundle(r)) as z:
        t = word(z, "reading-card.docx")
        assert all(
            v in t
            for v in (
                "实测结果　译摘：试验 A 实测七个颗粒",
                "模型结果　模型估计九个颗粒",
                "适用限制　未提取",
                "另一段否定词未保留",
                "S2/e1·第3页",
                "以原文为准",
            )
        )
        complete = word(z)
        assert "离线机器翻译" in complete and "试验 A 实测七个颗粒" in complete
        assert json.loads(z.read("report.json"))["reading_aids"] == r["reading_aids"]


def test_one_page_cards_withhold_failed_or_unlocatable_claims():
    r = report(
        "T01",
        {"literature_card": {"model_results": [{"text": "unsupported claim", "evidence_ids": ["unknown"]}]}},
    )
    with unpack(bundle(r)) as z:
        t = word(z, "reading-card.docx")
        assert "未取得可定位证据" in t and "unsupported claim" not in t
    r["review"] = [{"name": "source", "passed": False, "hard": True, "message": "证据不符"}]
    with unpack(bundle(r)) as z:
        assert "证据不符" in word(z, "reading-card.docx") and "模型结果" not in word(z, "reading-card.docx")


def test_paper_card_keeps_model_original_beside_machine_reading():
    original = "The model estimates a 17% decrease in forcing."
    r = report("T01", {"literature_card": {"model_results": [{"text": original, "evidence_ids": ["e1"]}]}})
    r["evidence"][0]["quote"] = original
    r["reading_aids"] = [
        {
            "evidence_id": "e1",
            "text_zh": "模型估计强迫下降17%",
            "method": "离线机器翻译",
            "authoritative": False,
        }
    ]
    with unpack(bundle(r)) as z:
        text = word(z, "reading-card.docx")
        assert "模型结果　译摘：模型估计强迫下降17%" in text
        assert "模型原文　" + original in text


def test_b05_svg_csv_separate_sources_preserve_units_exact_values_and_break_at_missing_month():
    rows = [
        {
            "source_id": "alpha",
            "source_version": "v",
            "scope": "综合",
            "month": month,
            "deaths": death,
            "injured": injured,
            "complete_month": complete,
            "evidence_ids": ["e1"],
        }
        for month, death, injured, complete in (
            ("2000-01", 5, 7, False),
            ("2000-02", 8, 19, True),
            ("2000-03", 8, 10, True),
            ("2000-05", 4, 11, True),
            ("2000-06", 2, 5, None),
        )
    ]
    rows.append({**rows[1], "source_id": "beta", "deaths": 57, "evidence_ids": ["e2"]})
    r = report(
        "B05",
        {
            "monthly": rows,
            "reconciliation": [{"source_id": "alpha", "version": "v", "scope": "综合", "agrees": True}],
        },
    )
    r["evidence"][0].update(source_id="alpha", source_version="v")
    r["evidence"][1].update(source_id="beta", source_version="v")
    with unpack(bundle(r)) as z:
        m = json.loads(z.read("manifest.json"))
        f = next(f for f in m["files"] if f["type"] == "table" and f["json_pointer"] == "/products/monthly")
        table = list(csv.DictReader(io.StringIO(z.read(f["name"]).decode("utf-8-sig"))))
        assert table[-1]["deaths"] == "57" and table[0]["complete_month"] == "False"
        charts = [f for f in m["files"] if f["type"] == "figure" and f["json_pointer"] == "/products/monthly"]
        assert len(charts) == 2
        data = z.read(charts[0]["name"])
        root = ET.fromstring(data)

        def elements(tag):
            return root.findall("{*}" + tag)

        assert (
            len([line for line in elements("line") if line.get("stroke-width") == "2"]) == 2
        )  # only Feb–Mar
        assert len([dot for dot in elements("circle") if dot.get("fill") == "#ffffff"]) == 4
        s = " ".join(root.itertext())
        assert all(
            v in s
            for v in (
                "人数（人）",
                "不完整月份：2000-01",
                "覆盖未核实：2000-06",
                "2000-02 受伤 19 人",
                "已核对一致",
                "资料 alpha",
            )
        )
        assert "2000-02 死亡 57 人" in z.read(charts[1]["name"]).decode() and "57 人" not in s
    with unpack(bundle({**r, "evidence": [{**e, "source_version": "other"} for e in r["evidence"]]})) as z:
        assert not any(n.endswith(".svg") for n in z.namelist())
    with unpack(bundle(report("B05", {"monthly": [{**rows[0], "injured": None}]}))) as z:
        assert not any(n.endswith(".svg") for n in z.namelist())
        assert any("人数或证据不齐" in o["reason"] for o in json.loads(z.read("manifest.json"))["omitted"])
