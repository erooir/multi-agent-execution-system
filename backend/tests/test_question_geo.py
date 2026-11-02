import pytest

from backend.app.platform.contracts import AnswerTable
from backend.app.platform.question_geo import project_geo


def test_damage_counts_each_phase_for_the_requested_block_including_unconfirmed_buildings():
    rows = [
        {"block": "北区", "before": "intact", "after": "damaged", "status": "new_visible"},
        {"block": "北区", "before": "damaged", "after": "destroyed", "status": "preexisting"},
        {"block": "北区", "before": "intact", "after": "intact", "status": "unknown"},
        {"block": "南区", "before": "intact", "after": "intact", "status": "no_new_visible"},
    ]
    out = project_geo("G05", {"buildings": rows}, "北区灾前灾后各有几栋？")
    assert out["tables"][0]["rows"] == [["北区", "灾前", 3, 1, 1, 0, 1], ["北区", "灾后", 3, 0, 1, 1, 1]]
    assert "南区" not in out["answer"] and "倒塌或毁坏 1 栋" in out["answer"]
    assert project_geo("G05", {}, "数一下房屋")["missing"]


@pytest.mark.parametrize(
    ("code", "products", "contains"),
    [
        (
            "G01",
            {
                "hotspots": [{"coords": [2, 3], "match": "matched"}, {"coords": [3, 4], "match": "unknown"}],
                "exceptions": {"duplicates": ["obs"]},
            },
            ["1 个与前一日观测匹配", "重复记录另有 1 条", "1 个无法判定"],
        ),
        (
            "G02",
            {
                "changes": [
                    {
                        "date": "2024-01-01",
                        "image_id": "图甲",
                        "bbox": [3, 8, 7, 9],
                        "observation": "轮廓可见。",
                    }
                ]
            },
            ["(3, 8)", "宽 7、高 9 像素"],
        ),
        (
            "G04",
            {
                "road_exposure": {"records": [{"road_id": "道路甲", "potential_exposure_m": 7.5}]},
                "areas": [{"region": "区一", "flood_m2": 25, "unobserved_m2": 4}],
            },
            ["7.5 米", "25 平方米", "无法据此确认道路封闭"],
        ),
        (
            "G06",
            {
                "areas": [{"date": "2000", "by_class_m2": {"farm": 30}}],
                "transitions": [
                    {
                        "from_date": "2000",
                        "to_date": "2001",
                        "from_class": "farm",
                        "to_class": "建设用地",
                        "area_m2": 12,
                    }
                ],
            },
            ["农地转为建设用地 12 平方米", "农地 30 平方米"],
        ),
        (
            "G07",
            {
                "results": [
                    {
                        "site_id": "站甲",
                        "analyte": "铅",
                        "date": "周二",
                        "numeric_value": 3,
                        "unit": "mg/kg",
                        "status": "detected",
                        "grid_ids": ["甲"],
                    },
                    {"site_id": "站乙", "status": "pending_qc", "grid_ids": ["乙"]},
                ],
                "coverage": [
                    {"grid_id": "甲", "status": "sampled"},
                    {"grid_id": "乙", "status": "sampled"},
                    {"grid_id": "丙", "status": "no_sampling_evidence"},
                ],
            },
            ["检出 铅", "3 mg/kg", "没有采样证据的网格：丙", "没有可核对的最终检测结果的网格：乙"],
        ),
        (
            "G08",
            {
                "timeline": [
                    {"observed_at": "当日", "type": "frame", "island": "岛甲", "description": "云层"}
                ],
                "retrospective": [
                    {
                        "observed_at": "月末",
                        "event_at": "次日",
                        "island": "岛乙",
                        "type": "survey",
                        "description": "访谈",
                    }
                ],
            },
            ["岛甲 的影像", "月末 在 岛乙", "事件时间 次日"],
        ),
        (
            "G09",
            {
                "od_comparison": [
                    {
                        "from": "甲",
                        "to": "乙",
                        "status": "reachable",
                        "candidates": [
                            {
                                "rank": 1,
                                "edge_ids": ["桥西", "桥东"],
                                "distance_km": 8,
                                "extra_km": 3,
                                "estimated_time_min": 12,
                            }
                        ],
                    },
                    {"from": "乙", "to": "丙", "status": "unconfirmed"},
                ]
            },
            ["桥西 → 桥东", "全长 8 公里", "较原路线多 3 公里", "尚不能确认"],
        ),
        (
            "G10",
            {
                "priority": [
                    {
                        "district": "南区",
                        "max_union_exposed_m2": 70,
                        "repeated_exposure_m2": 20,
                        "coverage_complete": False,
                    }
                ]
            },
            ["南区", "70 平方米", "20 平方米", "观测未覆盖全部农田"],
        ),
    ],
)
def test_geo_answers_return_measured_business_results(code, products, contains):
    out = project_geo(code, products, "请结合原始资料回答")
    assert all(s in out["answer"] for s in contains)
    assert out["tables"] and all(AnswerTable.model_validate(t) for t in out["tables"])


def test_absent_data_preserves_the_gap_instead_of_reporting_zero_or_success():
    for code in ("G01", "G02", "G04", "G05", "G06", "G07", "G08", "G09", "G10"):
        out = project_geo(code, {}, "请问结果是什么？")
        assert out["missing"] and not out["tables"]
    assert project_geo("G03", {}, "海冰基线") is None
