"""User-facing descriptions of audited spatial products in their original units."""

import re
from collections import Counter, defaultdict
from math import isfinite


def _rows(products, name):
    value = products.get(name)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _num(value):
    return type(value) in (int, float) and isfinite(value)


def _show(value):
    if value is None:
        return "未核实"
    if _num(value):
        return format(value, ".10g")
    return str(value)


def _table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows[:100], "total_rows": len(rows)}


def _pack(lines, tables, missing=()):
    lines = [s for s in lines if s]
    return {
        "answer": "\n".join(lines),
        "tables": [t for t in tables if t["total_rows"]],
        "missing": list(missing),
    }


def _limited(lines):
    if len(lines) <= 10:
        return lines
    return [*lines[:10], f"另有 {len(lines) - 10} 项，见相应附表。"]


def _points(p):
    rows = _rows(p, "hotspots")
    if not rows:
        return _pack([], [], ["缺少可与前一日对照的有效火点观测。"])
    labels = {"new": "前一日未匹配", "matched": "匹配前一日观测", "unknown": "无法判定"}
    counts = Counter(r.get("match") for r in rows)
    lines = [
        f"共 {len(rows)} 个火点观测，其中 {counts['new']} 个在前一日规定半径内未匹配到观测，{counts['matched']} 个与前一日观测匹配，另有 {len(rows) - counts['new'] - counts['matched']} 个无法判定。"
    ]
    exc = p.get("exceptions") or {}
    if isinstance(exc.get("duplicates"), list):
        lines.append(
            f"同一时刻、坐标和传感器等均相同的重复记录另有 {len(exc['duplicates'])} 条，未重复计数。"
        )
    items = []
    for r in rows:
        xy = r.get("coords") or []
        items.append(
            [
                r.get("observed_at"),
                r.get("region"),
                xy[0] if len(xy) > 1 else None,
                xy[1] if len(xy) > 1 else None,
                labels.get(r.get("match"), "无法判定"),
            ]
        )
    lines.append("每个火点的经纬度、时间和匹配结果见表。未匹配到前一日观测不等于已确认新起火。")
    return _pack(lines, [_table("逐点跨日比对", ["观测时间", "地区", "经度", "纬度", "与前一日比较"], items)])


def _panels(p):
    rows = _rows(p, "changes")
    items = []
    for r in rows:
        box = r.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_num(n) for n in box):
            continue
        items.append([r.get("date"), r.get("image_id"), *box, r.get("observation"), r.get("interpretation")])
    if not items:
        return _pack([], [], ["缺少有像素位置和原始图像对应关系的观察注记。"])
    items.sort(key=lambda r: str(r[0]))
    lines = [
        f"共在 {len({r[0] for r in items if r[0]})} 个观测日定位到 {len(items)} 处像素注记。坐标以原图像左上角为原点。"
    ]
    lines.extend(
        _limited(
            [
                f"{date}，{image}：左上角 ({x}, {y})，宽 {w}、高 {h} 像素。{note or ''}"
                for date, image, x, y, w, h, note, _ in items
            ]
        )
    )
    return _pack(
        lines,
        [
            _table(
                "各期像素定位",
                ["日期", "图像", "左上角 X", "左上角 Y", "宽（像素）", "高（像素）", "观察", "解释边界"],
                items,
            )
        ],
    )


def _flood(p):
    roads = (p.get("road_exposure") or {}).get("records", [])
    areas = _rows(p, "areas")
    hits = [r for r in roads if _num(r.get("potential_exposure_m")) and r["potential_exposure_m"] > 0]
    lines = _limited(
        [
            f"道路 {r.get('road_id')} 的已观测路段中，有 {_show(r['potential_exposure_m'])} 米与淹水范围相交。"
            for r in hits
        ]
    )
    if roads and not hits:
        lines.append("所给道路在有观测的范围内未记录正的暴露长度。")
    lines.extend(
        _limited(
            [
                f"{r.get('region')} 的淹水面积为 {_show(r.get('flood_m2'))} 平方米，未观测面积为 {_show(r.get('unobserved_m2'))} 平方米。"
                for r in areas
            ]
        )
    )
    if roads:
        lines.append("空间相交说明道路可能暴露，无法据此确认道路封闭。")
    gaps = [] if roads else ["缺少道路及其与淹水范围相交的可核验记录。"]
    if not areas:
        gaps.append("缺少分区水域面积。")
    return _pack(
        lines,
        [
            _table(
                "道路暴露",
                ["道路", "观测长度（米）", "潜在暴露（米）"],
                [[r.get(k) for k in ("road_id", "observed_length_m", "potential_exposure_m")] for r in roads],
            ),
            _table(
                "分区水域",
                ["地区", "淹水（平方米）", "水迹（平方米）", "未观测（平方米）"],
                [[r.get(k) for k in ("region", "flood_m2", "trace_m2", "unobserved_m2")] for r in areas],
            ),
        ],
        gaps,
    )


def _buildings(p):
    rows = _rows(p, "buildings")
    if not rows:
        return _pack([], [], ["缺少可逐栋核对的灾前、灾后房屋记录。"])
    counts = defaultdict(Counter)
    for r in rows:
        block = r.get("block") or "街区未注明"
        for field, phase in (("before", "灾前"), ("after", "灾后")):
            state = r.get(field)
            if r.get("status") == "unknown" or state not in {"intact", "damaged", "destroyed"}:
                state = "unknown"
            counts[block, phase][state] += 1
    items = []
    lines = []
    for (block, phase), n in counts.items():
        values = [n[k] for k in ("intact", "damaged", "destroyed", "unknown")]
        items.append([block, phase, sum(values), *values])
        lines.append(
            f"{block} {phase}共 {sum(values)} 栋：完好 {values[0]} 栋、受损 {values[1]} 栋、倒塌或毁坏 {values[2]} 栋、无法判定 {values[3]} 栋。"
        )
    lines = _limited(lines)
    lines.append("数量限于有记录的房屋，可见损伤不能代替结构安全鉴定。")
    return _pack(
        lines,
        [
            _table(
                "各街区房屋状态",
                ["街区", "时期", "记录栋数", "完好", "受损", "倒塌或毁坏", "无法判定"],
                items,
            )
        ],
    )


LAND = {
    "forest": "林地",
    "farm": "农地",
    "water": "水域",
    "built": "建设用地",
    "urban": "城镇用地",
    "road": "道路用地",
    "unknown": "未判定",
}


def _land(p):
    areas = _rows(p, "areas")
    transitions = _rows(p, "transitions")
    values = [
        [r.get("date"), LAND.get(c, c), v] for r in areas for c, v in (r.get("by_class_m2") or {}).items()
    ]
    built = [
        r
        for r in transitions
        if r.get("to_class") in {"built", "建设用地", "road"}
        and r.get("from_class") not in {"built", "建设用地", "road", "unknown", None}
        and _num(r.get("area_m2"))
        and r["area_m2"] > 0
    ]
    lines = _limited(
        [
            f"{r.get('from_date')} 至 {r.get('to_date')}，{LAND.get(r.get('from_class'), r.get('from_class'))}转为建设用地 {_show(r['area_m2'])} 平方米。"
            for r in built
        ]
    )
    if transitions and not built:
        lines.append("已核实的转移记录中没有原类别明确、面积大于零的新增建设用地。")
    for r in areas:
        by = r.get("by_class_m2") or {}
        lines.append(
            f"{r.get('date')} 的已分类面积："
            + "、".join(f"{LAND.get(c, c)} {_show(v)} 平方米" for c, v in by.items())
            + "。"
        )
    gaps = [] if transitions else ["缺少跨期转移记录，无法核算转入建设用地的面积。"]
    return _pack(
        lines,
        [
            _table("各期分类面积", ["日期", "类别", "面积（平方米）"], values),
            _table(
                "相邻时期转移",
                ["前期", "后期", "原类别", "后类别", "面积（平方米）"],
                [
                    [
                        r.get("from_date"),
                        r.get("to_date"),
                        LAND.get(r.get("from_class"), r.get("from_class")),
                        LAND.get(r.get("to_class"), r.get("to_class")),
                        r.get("area_m2"),
                    ]
                    for r in transitions
                ],
            ),
        ],
        gaps,
    )


def _samples(p):
    results = _rows(p, "results")
    coverage = _rows(p, "coverage")
    hits = [r for r in results if r.get("status") == "detected" and not r.get("duplicate_result")]
    lines = _limited(
        [
            f"{r.get('site_id')} 在 {r.get('date')} 检出 {r.get('analyte')}，结果为 {_show(r.get('numeric_value'))} {r.get('unit') or ''}。"
            for r in hits
        ]
    )
    if results and not hits:
        lines.append("现有检测结果没有可确认为检出的非重复记录。")
    valid_grids = {
        str(g)
        for r in results
        if r.get("status") in {"detected", "nondetect"} and not r.get("duplicate_result")
        for g in (r.get("grid_ids") or [])
    }

    def coverage_note(r):
        if r.get("status") == "no_sampling_evidence":
            return "无采样证据"
        return "有最终检测结果" if str(r.get("grid_id")) in valid_grids else "已采样，缺少可核对的最终检测"

    empty = [str(r.get("grid_id")) for r in coverage if r.get("status") == "no_sampling_evidence"]
    pending = [
        str(r.get("grid_id"))
        for r in coverage
        if r.get("status") != "no_sampling_evidence" and str(r.get("grid_id")) not in valid_grids
    ]
    if empty:
        lines.append("没有采样证据的网格：" + "、".join(empty) + "。")
    elif coverage:
        lines.append("登记的覆盖网格均有采样记录；这只说明曾采样，不能证明每项污染物均有有效检测。")
    if pending:
        lines.append("已采样但没有可核对的最终检测结果的网格：" + "、".join(pending) + "。")
    lines.append("低于检出限不能记为零；未采样区域不能据此判为安全。")
    flags = {
        "detected": "检出",
        "nondetect": "未检出",
        "unknown": "未判定",
        "qualified": "限定结果",
        "rejected": "结果无效",
    }
    items = [
        [
            r.get("site_id"),
            r.get("date"),
            r.get("analyte"),
            r.get("value_text"),
            r.get("unit"),
            flags.get(r.get("status"), "需核对"),
            r.get("duplicate_result"),
        ]
        for r in results
    ]
    return _pack(
        lines,
        [
            _table("站点检测记录", ["站点", "日期", "检测项", "报告值", "单位", "判定", "重复记录"], items),
            _table(
                "采样覆盖",
                ["网格", "样品数", "覆盖状态"],
                [
                    [
                        r.get("grid_id"),
                        r.get("sample_count"),
                        coverage_note(r),
                    ]
                    for r in coverage
                ],
            ),
        ],
        [] if results else ["缺少可判断是否检出的实验室记录。"],
    )


def _islands(p):
    current, retro = _rows(p, "timeline"), _rows(p, "retrospective")
    labels = {"frame": "影像", "tide": "潮位", "survey": "调查"}

    def cells(r):
        return [
            r.get("observed_at"),
            r.get("event_at"),
            r.get("island"),
            labels.get(r.get("type"), r.get("type")),
            r.get("description"),
            r.get("value"),
            r.get("unit"),
            r.get("datum"),
        ]

    rows = [cells(r) for r in current]
    lines = _limited(
        [
            f"{r[0]}，{r[2]} 的{r[3]}记录：{r[4] or ''}{(_show(r[5]) + ' ' + str(r[6] or '')) if r[5] is not None else ''}。"
            for r in rows
        ]
    )
    history = [cells(r) for r in retro]
    lines += _limited(
        [
            f"{r[0]} 在 {r[2]} 形成的回溯记录涉及事件时间 {_show(r[1])}；{r[4] or '详情见表'}。"
            for r in history
        ]
    )
    cols = ["记录时间", "事件时间", "岛礁", "类型", "观察说明", "数值", "单位", "基准"]
    return _pack(
        lines,
        [_table("同期观测时间线", cols, rows), _table("回溯性调查", cols, history)],
        [] if rows or history else ["缺少有明确观测或调查时间的资料。"],
    )


def _routes(p):
    od = _rows(p, "od_comparison")
    snapshot = (p.get("notice_audit") or {}).get("snapshot")
    lines = [f"通行判断时间为 {snapshot}。"] if snapshot else []
    items = []
    flags = {"reachable": "可达", "unconfirmed": "尚不能确认", "unreachable_in_snapshot": "在该时点不可达"}
    for r in od:
        pair = f"{r.get('from')} 至 {r.get('to')}"
        paths = r.get("candidates") or []
        if not paths:
            lines.append(f"{pair}：{flags.get(r.get('status'), '未判定')}。")
            items.append([pair, flags.get(r.get("status"), "未判定"), None, None, None, None, None, None])
        for path in paths:
            edges = " → ".join(str(x) for x in path.get("edge_ids", []))
            items.append(
                [
                    pair,
                    flags.get(r.get("status"), "未判定"),
                    path.get("rank"),
                    edges,
                    path.get("distance_km"),
                    path.get("extra_km"),
                    path.get("estimated_time_min"),
                    path.get("extra_min"),
                ]
            )
            if path.get("rank") == 1:
                extra = (
                    f"较原路线多 {_show(path['extra_km'])} 公里"
                    if _num(path.get("extra_km"))
                    else "原路线距离不足以核算增加里程"
                )
                lines.append(
                    f"{pair} 可经 {edges} 通行，全长 {_show(path.get('distance_km'))} 公里，{extra}，预计 {_show(path.get('estimated_time_min'))} 分钟。"
                )
    return _pack(
        _limited(lines),
        [
            _table(
                "各起终点候选路线",
                [
                    "起点至终点",
                    "通行状态",
                    "顺位",
                    "经过路段",
                    "全长（公里）",
                    "增加距离（公里）",
                    "预计用时（分钟）",
                    "增加用时（分钟）",
                ],
                items,
            )
        ],
        [] if od else ["缺少可计算的起终点与指定时点路网。"],
    )


def _farmland(p):
    rows = _rows(p, "priority")
    exposure = _rows(p, "exposure")
    lines = []
    for r in rows:
        text = f"{r.get('district')} 在已观测地块中的多期受淹并集为 {_show(r.get('max_union_exposed_m2'))} 平方米，至少两期重复受淹 {_show(r.get('repeated_exposure_m2'))} 平方米。"
        if r.get("coverage_complete") is False:
            text += "观测未覆盖全部农田。"
        lines.append(text)
    daily = [
        [r.get(k) for k in ("district", "date", "crop", "crop_area_m2", "covered_m2", "exposed_m2")]
        for r in exposure
    ]
    per_region = [
        [
            r.get(k)
            for k in (
                "district",
                "crop_area_m2",
                "max_union_exposed_m2",
                "repeated_exposure_m2",
                "coverage_complete",
            )
        ]
        for r in rows
    ]
    return _pack(
        _limited(lines),
        [
            _table(
                "各地重复受淹面积",
                ["地区", "农田面积（平方米）", "多期并集（平方米）", "重复受淹（平方米）", "覆盖完整"],
                per_region,
            ),
            _table(
                "各期地块暴露",
                ["地区", "日期", "作物", "农田面积（平方米）", "已观测（平方米）", "暴露（平方米）"],
                daily,
            ),
        ],
        [] if rows else ["缺少可用于计算多期并集和重复受淹面积的农田观测。"],
    )


def project_geo(case_id, products, question):
    project = {
        "G01": _points,
        "G02": _panels,
        "G04": _flood,
        "G05": _buildings,
        "G06": _land,
        "G07": _samples,
        "G08": _islands,
        "G09": _routes,
        "G10": _farmland,
    }.get(case_id)
    if not project:
        return None
    # Named blocks and districts can be answered without mixing in another place's numbers.
    if question and case_id in {"G05", "G10"}:
        key, fields = (
            ("block", ("buildings",)) if case_id == "G05" else ("district", ("priority", "exposure"))
        )
        places = {str(row[key]) for field in fields for row in _rows(products, field) if row.get(key)}
        mentioned = {
            place
            for place in places
            if len(place) >= 2
            and re.search(
                r"(?<![A-Za-z0-9_-])" + re.escape(place) + r"(?![A-Za-z0-9_-])", question, re.IGNORECASE
            )
        }
        if mentioned:
            products = {
                **products,
                **{
                    field: [r for r in _rows(products, field) if str(r.get(key)) in mentioned]
                    for field in fields
                },
            }
    return project(products)
