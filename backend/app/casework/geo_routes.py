"""Snapshot-constrained civil road routing, including unknown/closed edges."""

import heapq
from collections import defaultdict
from dataclasses import dataclass
from itertools import count

from .geo_common import collection, feature, number, stamp


@dataclass(frozen=True)
class Edge:
    id: str
    start: str
    end: str
    km: float
    speed: float | None
    refs: tuple[str, ...]
    geometry: dict | None


def shortest(graph, start, end, ban_edges=frozenset(), ban_nodes=frozenset()):
    if start in ban_nodes or end in ban_nodes:
        return None
    seq = count()
    queue = [(0, next(seq), start)]
    distance = {start: 0}
    pred = {}
    while queue:
        cost, _, node = heapq.heappop(queue)
        if cost != distance[node]:
            continue
        if node == end:
            route = []
            while node != start:
                edge = pred[node]
                route.append(edge)
                node = edge.start
            return route[::-1]
        for e in graph.get(node, []):
            if e.end in ban_nodes or (e.start, e.id, e.end) in ban_edges:
                continue
            value = cost + e.km
            if value < distance.get(e.end, float("inf")):
                distance[e.end] = value
                pred[e.end] = e
                heapq.heappush(queue, (value, next(seq), e.end))
    return None


def two_paths(graph, start, end):
    first = shortest(graph, start, end)
    if first is None:
        return []
    # Yen K=2: each spur diverges once from path #1; prefix vertices are forbidden.
    best = None
    for i, e in enumerate(first):
        spur = shortest(graph, e.start, end, {(e.start, e.id, e.end)}, {p.start for p in first[:i]})
        if spur is not None:
            path = first[:i] + spur
            score = (sum(v.km for v in path), tuple(v.id for v in path))
            if best is None or score < best[0]:
                best = (score, path)
    return [first] + ([best[1]] if best else [])


def _time(path):
    if path is None or any(e.speed is None or e.speed <= 0 for e in path):
        return None
    return sum(60 * e.km / e.speed for e in path)


def routes(ctx):
    moment = stamp(ctx.policy.get("snapshot"))
    if not moment:
        raise ValueError("路网需要带时区的快照时刻")
    notices = defaultdict(list)
    conflicts = []
    ambiguous = defaultdict(list)
    for r in ctx.select("notice"):
        f = r["fields"]
        begin = stamp(f.get("effective_at"))
        until = stamp(f.get("expires_at"))
        if not begin or (f.get("expires_at") and not until):
            ctx.gap("公告缺少有效起止时间，不能安全判断生效")
            ambiguous[str(f.get("edge_id", ""))].append(ctx.ref(r))
            continue
        if begin <= moment and (not until or until > moment):
            notices[str(f.get("edge_id", ""))].append(
                (number(f.get("priority")) or 0, begin, str(f.get("state", "unknown")), ctx.ref(r))
            )
    chosen = {}
    for key, history in notices.items():
        level = max((p, t) for p, t, _, _ in history)
        top = [(s, ref) for p, t, s, ref in history if (p, t) == level]
        states = {s for s, _ in top}
        chosen[key] = (next(iter(states)) if len(states) == 1 else "unknown", [ref for _, ref in top])
        if len(states) > 1:
            conflicts.append({"edge_id": key, "states": sorted(states), "refs": chosen[key][1]})
    for key, ids in ambiguous.items():
        chosen[key] = ("unknown", chosen.get(key, ("unknown", []))[1] + ids)
    edges = []
    seen = set()
    for r in ctx.select("edge"):
        f = r["fields"]
        ref = ctx.ref(r)
        eid = str(f.get("edge_id", ""))
        km = number(f.get("distance_km"))
        a = str(f.get("from", ""))
        b = str(f.get("to", ""))
        if not eid or not a or not b or km is None or km <= 0 or eid in seen:
            raise ValueError("路网边须有唯一ID、端点和正距离")
        seen.add(eid)
        edges.append((eid, a, b, km, f, ref))
    if not edges:
        ctx.gap("缺少可路由路网")
    orders = ctx.select("od")
    if len(orders) != int(ctx.policy.get("expected_od", 5)):
        ctx.gap("起终点对数量与分析参数不一致")
    if len(orders) > 50 or len(edges) > 100000:
        raise ValueError("单任务OD或边数量超过范围，请拆分批次")
    out = []
    features = []
    excluded = []
    for r in orders:
        f = r["fields"]
        order = str(f.get("od_id", ""))
        start = str(f.get("from", ""))
        end = str(f.get("to", ""))
        if not start or not end or not order or start == end:
            raise ValueError("OD缺少唯一标识或起终点无效")
        oref = ctx.ref(r)
        profile = f.get("vehicle", {})
        if not isinstance(profile, dict):
            raise TypeError("车辆规格必须是对象")
        ok = defaultdict(list)
        possible = defaultdict(list)
        baseline = defaultdict(list)
        unknown_used = False
        for eid, a, b, km, raw, ref in edges:
            flag, extra = chosen.get(eid, (raw.get("state", "unknown"), []))
            refs = (ref, *extra)
            snap = stamp(raw.get("snapshot"))
            if raw.get("snapshot") and snap is None:
                flag = "unknown"
            max_age = number(ctx.policy.get("max_age_seconds"))
            if snap and (
                snap > moment or (max_age is not None and (moment - snap).total_seconds() > max_age)
            ):
                flag = "unknown"
            allow = True
            uncertain = False
            if raw.get("allowed_classes"):
                if not profile.get("class"):
                    uncertain = True
                elif profile["class"] not in raw["allowed_classes"]:
                    allow = False
            for limit, key in (("max_height_m", "height_m"), ("max_weight_t", "weight_t")):
                if raw.get(limit) is not None:
                    limit_val = number(raw.get(limit))
                    actual = number(profile.get(key))
                    if actual is None or limit_val is None:
                        uncertain = True
                    elif actual > limit_val:
                        allow = False
            if not allow or flag != "open" or uncertain:
                excluded.append(
                    {
                        "od_id": order,
                        "edge_id": eid,
                        "reason": "vehicle_restricted"
                        if not allow
                        else "restriction_unknown"
                        if uncertain
                        else flag,
                        "refs": list(refs),
                    }
                )
            geom = raw.get("geometry") if raw.get("crs") in {"EPSG:4326", "OGC:CRS84"} else None
            if geom is not None and (geom.get("type") != "LineString" or not geom.get("coordinates")):
                geom = None
            directions = [(a, b)] + ([] if raw.get("oneway", True) else [(b, a)])
            for src, dst in directions:
                e = Edge(eid, src, dst, km, number(raw.get("speed_kph")), refs, geom)
                if allow and flag not in {"closed", "restricted"}:
                    possible[src].append(e)
                    if flag == "open" and not uncertain:
                        ok[src].append(e)
                    else:
                        unknown_used = True
                if allow and not uncertain and raw.get("baseline_state") == "open":
                    baseline[src].append(
                        Edge(eid, src, dst, km, number(raw.get("baseline_speed_kph")), refs, geom)
                    )
        paths = two_paths(ok, start, end)
        base = shortest(baseline, start, end)
        dist_before = sum(v.km for v in base) if base is not None else number(f.get("baseline_distance_km"))
        time_before = _time(base) if base is not None else number(f.get("baseline_time_min"))
        candidates = []
        for index, path in enumerate(paths, 1):
            km = sum(v.km for v in path)
            minutes = _time(path)
            ids = [e.id for e in path]
            item = {
                "rank": index,
                "edge_ids": ids,
                "distance_km": km,
                "estimated_time_min": minutes,
                "extra_km": km - dist_before if dist_before is not None else None,
                "extra_min": minutes - time_before
                if minutes is not None and time_before is not None
                else None,
                "refs": list(dict.fromkeys([oref, *(ref for e in path for ref in e.refs)])),
            }
            candidates.append(item)
            if path and all(e.geometry for e in path):
                features.append(
                    feature(
                        {"type": "MultiLineString", "coordinates": [e.geometry["coordinates"] for e in path]},
                        {"od_id": order, "candidate": index, "edge_ids": ids, "refs": item["refs"]},
                    )
                )
            elif path:
                ctx.gap("候选路径缺少边几何，未绘制虚构路线")
            if minutes is None:
                ctx.gap("候选路线存在缺失速度，未给确定行程时间")
            if dist_before is None or time_before is None:
                ctx.gap("缺少灾前同口径距离或时间基线")
        uncertain_path = bool(not paths and unknown_used and shortest(possible, start, end) is not None)
        status = "reachable" if paths else "unconfirmed" if uncertain_path else "unreachable_in_snapshot"
        if uncertain_path:
            ctx.gap("存在须经未知状态边的OD，不能确认可达")
        out.append(
            {
                "od_id": order,
                "from": start,
                "to": end,
                "status": status,
                "vehicle": profile,
                "baseline_km": dist_before,
                "baseline_min": time_before,
                "candidates": candidates,
                "refs": [oref],
            }
        )
    if conflicts:
        ctx.gap("有同优先级、同时间相互矛盾的公告，相关边标记未知")
    return {
        "od_comparison": out,
        "route_layer": collection(features),
        "exclusions": excluded,
        "notice_audit": {
            "conflicts": conflicts,
            "snapshot": moment.isoformat(),
            "rule": "在快照内选择最高优先级再取最新生效版本；最短距离无环路径，未知状态不进入确定候选。",
        },
    }
