import csv
import io
import json
import struct
import zlib
from copy import deepcopy
from zipfile import ZipFile

from backend.app.casework.edition_tech_checks import checks


def test_certification_dates_objects_and_lanes_are_bound_to_the_source_csv():
    headers = ["program", "milestone_date", "subject", "event"]
    table = io.StringIO()
    writer = csv.writer(table)
    writer.writerow(headers)
    writer.writerow(["演示项目", "2022-04", "演示生产方", "Production Certificate"])
    data = table.getvalue().encode()
    sources = [{"filename": "T06-AN01.csv", "read": lambda: data, "records": [{"id": "E1"}]}]
    row = {
        "项目": "演示项目",
        "日期": "2022-04",
        "主体": "演示生产方",
        "节点": "Production Certificate",
        "泳道": "单机及生产",
        "evidence_ids": ["E1"],
    }
    assert all(c["passed"] for c in checks("T06", sources, {"timeline": [row]}))
    for change in ({"泳道": "运营主体"}, {"日期": "2022-04-01"}, {"evidence_ids": ["unknown"]}):
        assert not all(c["passed"] for c in checks("T06", sources, {"timeline": [{**row, **change}]}))


def test_cordis_recomputes_roles_and_both_funding_measures_separately():
    raw = [
        {
            "project_id": "42",
            "coordinator": "示例机构A",
            "participants": ["示例机构B"],
            "source": "example:42",
            "total_cost_display": "€ 10,00",
            "eu_contribution_display": "€ 6,00",
        }
    ]
    source = {
        "filename": "T04-F01.json",
        "read": lambda: json.dumps(raw).encode(),
        "records": [
            {"id": "E1", "locator": "$[0]", "text": "示例项目 42：总成本 € 10,00；EU 贡献 € 6,00。"},
            {
                "id": "E2",
                "locator": "分组",
                "text": "主题：总成本已披露 1 项合计 €10.00；EU 贡献已披露 1 项合计 €6.00。",
            },
        ],
    }
    products = {
        "协调与参与关系": [
            {
                "ID": "42",
                "主题": "主题",
                "协调机构": "示例机构A",
                "参与机构": ["示例机构B"],
                "出处": "example:42",
                "evidence_ids": ["E1"],
            }
        ]
    }
    assert all(c["passed"] for c in checks("T04", [source], products))
    data = deepcopy(products)
    data["协调与参与关系"][0]["参与机构"] = []
    assert not checks("T04", [source], data)[0]["passed"]
    raw[0]["eu_contribution_display"] = "€ 10,00"
    assert not checks("T04", [source], products)[1]["passed"]


def test_integrates_irregular_matlab_discharge_intervals_and_rejects_tampering():
    def element(kind, payload):
        return struct.pack("<II", kind, len(payload)) + payload + b"\0" * ((-len(payload)) % 8)

    def header(cls, count, name=b""):
        return (
            element(6, struct.pack("<II", cls, 0))
            + element(5, struct.pack("<ii", 1, count))
            + element(1, name)
        )

    def numeric(values):
        return element(14, header(6, len(values)) + element(9, struct.pack("<" + "d" * len(values), *values)))

    def string(value):
        return element(14, header(4, len(value)) + element(16, value.encode()))

    def structure(name, keys, rows):
        width = max(len(key) for key in keys) + 1
        fields = b"".join(key.encode().ljust(width, b"\0") for key in keys)
        return element(
            14,
            header(2, len(rows), name)
            + element(5, struct.pack("<i", width))
            + element(1, fields)
            + b"".join(v for r in rows for v in r),
        )

    rows = [
        [string("rest"), numeric([0, 1]), numeric([0, 0])],
        [string("reference discharge"), numeric([0, 300, 1800]), numeric([2, 2, 2])],
        [string("reference discharge"), numeric([0, 900, 1800]), numeric([1, 1, 1])],
    ]
    root = structure(b"data", ["step"], [[structure(b"", ["comment", "relativeTime", "current"], rows)]])
    compressed = zlib.compress(root)
    mat = (
        b"MATLAB 5.0".ljust(124, b" ") + b"\x00\x01IM" + struct.pack("<II", 15, len(compressed)) + compressed
    )
    packed = io.BytesIO()
    with ZipFile(packed, "w") as archive:
        archive.writestr("group/S1.mat", mat)
    sources = [{"filename": "T09-F01.zip", "read": packed.getvalue, "records": [{"id": "A"}]}]
    summary = {"电池": "S1", "参考放电次数": 2, "首容量Ah": 1.0, "末容量Ah": 0.5, "evidence_ids": ["A"]}
    assert all(c["passed"] for c in checks("T09", sources, {"汇总": [summary]}))
    assert not checks("T09", sources, {"汇总": [{**summary, "末容量Ah": 1.0}]})[1]["passed"]
