"""Offline archive photograph correspondence; not open-world aircraft recognition.

Bindings (within frozen JSON/CSV records):
  archive_target: asset_id, target_id
  archive_reference: asset_id, candidate_id, catalog_name, catalog_locator, optional reference_roi=[x,y,w,h]

Only these frozen images are decoded. We test mutual ORB descriptor matches and a robust
similarity transform, and reject weak or competing matches. A support box encloses measured
inlier pixels, not an inferred aircraft outline. No provider, path lookup or download.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from PIL import Image

METHOD = "ORB-Hamming / mutual ratio / RANSAC similarity v1"
MAX_TARGETS = 12
MAX_REFERENCES = 16
MAX_PAIRS = 160
MIN_INLIERS = 15
MIN_INLIER_RATIO = 0.62
MIN_DISTINCT_RATIO = 1.35
MAX_SECONDS = 60


def _backend():
    try:
        import cv2
        import numpy as np
    except (ImportError, OSError) as exc:
        raise ValueError("未安装可用的离线视觉组件（vision）") from exc
    if any(
        not callable(getattr(cv2, fn, None)) for fn in ("ORB_create", "BFMatcher", "estimateAffinePartial2D")
    ):
        raise ValueError("离线视觉组件缺少特征或几何校验功能")
    return cv2, np


def _label(f, key):
    v = f.get(key)
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= 160:
        raise ValueError(f"视觉绑定缺少有效{key}")
    return v.strip()


def _box(value, w, h):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
    ):
        raise ValueError("参考图区域必须为四个有限像素值")
    x, y, dx, dy = value
    if min(x, y) < 0 or min(dx, dy) <= 0 or x + dx > w or y + dy > h:
        raise ValueError("参考图区域越界")
    return (math.floor(x), math.floor(y), min(w, math.ceil(x + dx)), min(h, math.ceil(y + dy)))


@dataclass
class Pixels:
    points: object
    descriptors: object
    width: int
    height: int
    scale_x: float
    scale_y: float
    roi_area: float
    checksum: str


def _descriptor(asset, read_bytes, roi, cv, np):
    if asset.get("modality") != "image" or not asset.get("content_ref"):
        raise ValueError("资料没有冻结的原始图片")
    raw = read_bytes(asset)
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > 20_000_000:
        raise ValueError("原始图片缺失或超过20MB")
    checksum = hashlib.sha256(raw).hexdigest()
    if asset.get("checksum") and asset["checksum"] != checksum:
        raise ValueError("图片字节与冻结校验值不一致")
    with Image.open(io.BytesIO(raw)) as image:
        w, h = image.size
        if min(w, h) < 48 or w * h > 25_000_000:
            raise ValueError("图像分辨率不足或像素总量超过限制")
        size = min(1.0, 1100 / max(w, h))
        sw = max(1, round(w * size))
        sh = max(1, round(h * size))
        image = image.convert("L").resize((sw, sh), Image.Resampling.LANCZOS)
        mask = None
        roi_area = sw * sh
        if roi is not None:
            x, y, x2, y2 = _box(roi, w, h)
            xa, ya, xb, yb = (
                math.floor(x * sw / w),
                math.floor(y * sh / h),
                math.ceil(x2 * sw / w),
                math.ceil(y2 * sh / h),
            )
            mask = np.zeros((sh, sw), dtype=np.uint8)
            mask[ya:yb, xa:xb] = 255
            roi_area = (xb - xa) * (yb - ya)
        orb = cv.ORB_create(nfeatures=1150, edgeThreshold=24, fastThreshold=14)
        kp, d = orb.detectAndCompute(np.asarray(image, dtype=np.uint8), mask)
    pts = np.asarray([k.pt for k in kp], dtype=np.float32).reshape(-1, 2)
    return Pixels(pts, d, w, h, sw / w, sh / h, roi_area, checksum)


def _neighbors(cv, query, train):
    matcher = cv.BFMatcher(cv.NORM_HAMMING)
    good = {}
    for matches in matcher.knnMatch(query, train, k=2):
        if len(matches) != 2:
            continue
        first, second = matches
        if first.distance < 0.75 * second.distance and first.distance <= 72:
            good[first.queryIdx] = first.trainIdx
    return good


def _support(points, px, np):
    xs = points[:, 0] / px.scale_x
    ys = points[:, 1] / px.scale_y
    left = max(0, math.floor(float(xs.min())))
    top = max(0, math.floor(float(ys.min())))
    right = min(px.width, math.ceil(float(xs.max())) + 1)
    bottom = min(px.height, math.ceil(float(ys.max())) + 1)
    return [left, top, right - left, bottom - top]


def _match(ref, tar, cv, np):
    if ref.descriptors is None or tar.descriptors is None:
        return None
    if min(len(ref.descriptors), len(tar.descriptors)) < MIN_INLIERS:
        return None
    a = _neighbors(cv, ref.descriptors, tar.descriptors)
    b = _neighbors(cv, tar.descriptors, ref.descriptors)
    pairs = sorted((i, j) for i, j in a.items() if b.get(j) == i)
    if len(pairs) < MIN_INLIERS:
        return None
    src = np.asarray([ref.points[i] for i, _ in pairs])
    dst = np.asarray([tar.points[j] for _, j in pairs])
    mat, mask = cv.estimateAffinePartial2D(
        src, dst, method=cv.RANSAC, ransacReprojThreshold=3.0, maxIters=1800, confidence=0.995, refineIters=10
    )
    if mat is None or mask is None or not np.isfinite(mat).all():
        return None
    keep = mask.ravel().astype(bool)
    count = int(keep.sum())
    fraction = count / len(pairs)
    if count < MIN_INLIERS or fraction < MIN_INLIER_RATIO:
        return None
    x, y = src[keep], dst[keep]
    scale = float(math.hypot(mat[0, 0], mat[1, 0]))
    if not 0.15 <= scale <= 7:
        return None
    residual = np.linalg.norm(x @ mat[:, :2].T + mat[:, 2] - y, axis=1)
    error = float(np.median(residual))
    ref_area = float(cv.contourArea(cv.convexHull(x))) / ref.roi_area
    tar_area = float(cv.contourArea(cv.convexHull(y))) / tar.roi_area
    if ref_area < 0.025 or tar_area < 0.007 or error > 2.2:
        return None
    rb = _support(x, ref, np)
    tb = _support(y, tar, np)
    if min(rb[2:] + tb[2:]) < 14:
        return None
    # Quality is an ordering statistic, not a probability or a calibrated class confidence.
    score = count * fraction * min(1.0, ref_area / 0.16) * min(1.0, tar_area / 0.075) / (1 + error / 3)
    if score < 6:
        return None
    pts = [
        {
            "reference_xy": [round(float(r[0]) / ref.scale_x, 1), round(float(r[1]) / ref.scale_y, 1)],
            "target_xy": [round(float(t[0]) / tar.scale_x, 1), round(float(t[1]) / tar.scale_y, 1)],
        }
        for r, t in zip(x, y, strict=True)
    ]
    return {
        "method": METHOD,
        "mutual_matches": len(pairs),
        "reference_sha256": ref.checksum,
        "target_sha256": tar.checksum,
        "inliers": count,
        "inlier_fraction": round(fraction, 4),
        "reference_coverage": round(ref_area, 5),
        "target_coverage": round(tar_area, 5),
        "median_residual_work_px": round(error, 4),
        "score": round(score, 4),
        "reference_support_xywh": rb,
        "target_support_xywh": tb,
        "sample_inlier_pairs": pts[:12],
    }


def _record(asset, loc, fields):
    return {
        "source_id": asset["id"],
        "source_version": str(asset["source_version"]),
        "location": loc,
        "text": "",
        "fields": fields,
    }


def enrich_archive(records, assets, read_bytes):
    """Return additional pixel-derived observations and explicit gaps. Existing manual observations are unchanged."""
    targets = []
    references = []
    for row in records:
        f = row.get("fields", {})
        if f.get("kind") == "archive_target":
            targets.append(row)
        if f.get("kind") == "archive_reference":
            references.append(row)
    if not targets and not references:
        return records, []
    out = list(records)
    gaps = []
    if not targets or not references:
        return out, ["实际像素比对需要目标图绑定和带出处的参考图库"]
    if (
        len(targets) > MAX_TARGETS
        or len(references) > MAX_REFERENCES
        or len(targets) * len(references) > MAX_PAIRS
    ):
        return out, ["候选集合或图像对超过单任务上限，需按批提交"]
    frozen = {a["id"]: a for a in assets}
    if len(frozen) != len(assets):
        return out, ["冻结资料ID不唯一，未启动视觉比对"]
    try:
        cv, np = _backend()
    except ValueError as exc:
        return out, [str(exc)]
    started = time.monotonic()
    seen_target_ids = set()
    seen_target_assets = set()
    candidates = []
    candidate_names = {}
    cache = {}
    pool_valid = True

    def pixels(asset, roi):
        k = (asset["id"], str(asset["source_version"]), tuple(roi or ()))
        if k not in cache:
            cache[k] = _descriptor(asset, read_bytes, roi, cv, np)
        return cache[k]

    # A missing or illegible candidate must not silently narrow the comparison pool.
    for row in references:
        f = row["fields"]
        try:
            aid = _label(f, "asset_id")
            cid = _label(f, "candidate_id")
            name = _label(f, "catalog_name")
            locator = _label(f, "catalog_locator")
            a = frozen.get(aid)
            if a is None:
                raise ValueError("参考图不在冻结资料范围内")
            if cid in candidate_names and candidate_names[cid] != name:
                raise ValueError("同一候选条目名称冲突")
            candidate_names[cid] = name
            roi = f.get("reference_roi")
            p = pixels(a, roi)
            if p.descriptors is None or len(p.descriptors) < MIN_INLIERS:
                raise ValueError("参考图缺少足够的局部纹理")
            candidates.append((cid, name, locator, a, p, row))
        except (KeyError, ValueError, TypeError, OSError, Image.DecompressionBombError, cv.error) as exc:
            pool_valid = False
            gaps.append(f"参考图库不完整：{exc}")
    # A target cannot also act as its own reference.
    ref_ids = {c[3]["id"] for c in candidates}
    target_keys = [(r["fields"].get("target_id"), r["fields"].get("asset_id")) for r in targets]
    valid_keys = set()
    for row in targets:
        f = row["fields"]
        aid = f.get("asset_id")
        key = f.get("target_id")
        try:
            aid = _label(f, "asset_id")
            key = _label(f, "target_id")
            if sum(t == key or i == aid for t, i in target_keys) != 1:
                raise ValueError("重复目标绑定")
            if aid in ref_ids or aid not in frozen:
                raise ValueError("目标不在冻结范围内或同时作为参考图")
            if key in seen_target_ids or aid in seen_target_assets:
                raise ValueError("重复目标绑定")
            # No competing manual observation may be overridden by automation.
            if any(
                v["fields"].get("image_id") == key
                and v["fields"].get("kind")
                not in {"catalog", "reference", "馆藏说明", "archive_target", "archive_reference"}
                for v in records
            ):
                raise ValueError("已有同编号观察，禁止覆盖或合并断言")
            valid_keys.add((aid, key))
            seen_target_ids.add(key)
            seen_target_assets.add(aid)
        except (ValueError, TypeError) as exc:
            gaps.append(f"目标绑定无效：{exc}")
    for aid, key in sorted(valid_keys):
        a = frozen[aid]
        reason = None
        best = None
        try:
            if not pool_valid:
                raise ValueError("参考集合不完整，不能进行可靠区分")
            target = pixels(a, None)
            if target.descriptors is None or len(target.descriptors) < MIN_INLIERS:
                raise ValueError("目标缺少足够可见纹理")
            scores = {}
            for cid, name, locator, reference, ref, bind in candidates:
                if time.monotonic() - started > MAX_SECONDS:
                    raise ValueError("比对达到本次处理时限")
                result = _match(ref, target, cv, np)
                if result and (cid not in scores or result["score"] > scores[cid][0]["score"]):
                    scores[cid] = (result, cid, name, locator, reference, bind)
            ranked = sorted(scores.values(), key=lambda hit: (-hit[0]["score"], hit[1]))
            if not ranked:
                raise ValueError("无候选达到双向匹配、几何内点和区域覆盖门槛")
            if len(ranked) > 1 and ranked[0][0]["score"] < ranked[1][0]["score"] * MIN_DISTINCT_RATIO + 1:
                raise ValueError("多个馆藏条目像素支持接近，拒绝唯一候选")
            best = ranked[0]
        except (KeyError, ValueError, TypeError, OSError, Image.DecompressionBombError, cv.error) as exc:
            reason = str(exc)
        if not best:
            gaps.append(f"{key}像素比对未确认：{reason}")
            out.append(
                _record(
                    a,
                    {"image": 1, "observation": "archive_matching"},
                    {
                        "kind": "archive_observation",
                        "image_id": key,
                        "visible_features": "",
                        "candidates": [],
                        "match_status": "abstain",
                        "reason": reason,
                        "method": METHOD,
                    },
                )
            )
            continue
        result, cid, name, locator, reference, bind = best
        box = result["target_support_xywh"]
        rb = result["reference_support_xywh"]
        notes = (
            f"参考条目{cid}与目标图有{result['inliers']}/{result['mutual_matches']}对满足几何变换的局部像素配对；"
            f"内点率{result['inlier_fraction']:.3f}，排序分{result['score']}（不是概率）。仅支持与参考照片相符的候选；"
            "标注仅为几何内点的支持范围，不是目标轮廓；不能据此确认具体亚型、制造号、拍摄日期或独立视角中的同型号。"
        )
        fields = {
            "kind": "archive_observation",
            "image_id": key,
            "visible_features": notes,
            "candidates": [name],
            "width": target.width,
            "height": target.height,
            "candidate_id": cid,
            "reference_asset_id": reference["id"],
            "pixel_match": result,
            "region_meaning": "局部匹配内点的支持范围，不是物体轮廓",
        }
        # A near-full span is reported in match metrics, never emitted as an object/annotation box.
        if box[2] < 0.93 * target.width or box[3] < 0.93 * target.height:
            fields.update(bbox=box, coordinate_space="pixels")
        out.append(_record(a, {"image": 1, "pixel_region": box}, fields))
        try:
            link = urlsplit(locator)
            url = (
                locator
                if link.scheme in {"http", "https"}
                and link.hostname
                and not link.username
                and not link.password
                else None
            )
        except ValueError:
            url = None
        # Keep the reference image and catalogue record separately citable.
        common = {
            "kind": "reference",
            "image_id": key,
            "model_name": name,
            "candidate_id": cid,
            "catalog_locator": locator,
            "url": url,
        }
        out.append(
            _record(
                reference,
                {"image": 1, "pixel_region": rb},
                {
                    **common,
                    "evidence_role": "matched_reference_pixels",
                    "method": METHOD,
                    "inliers": result["inliers"],
                },
            )
        )
        out.append(
            {
                "source_id": bind["source_id"],
                "source_version": bind["source_version"],
                "location": dict(bind["location"]),
                "text": "",
                "fields": {
                    **common,
                    "evidence_role": "archive_metadata",
                    "original_record": dict(bind["fields"]),
                },
            }
        )
    return out, list(dict.fromkeys(gaps))


def audit_archive(candidate, records):
    """An accepted pixel candidate must cite its target support, reference support and catalogue row."""
    targets = {
        r["fields"].get("target_id"): r["fields"].get("asset_id")
        for r in records
        if r["fields"].get("kind") == "archive_target"
    }
    if not targets:
        return []

    def origin(row):
        return (
            row.get("source_id"),
            str(row.get("source_version")),
            json.dumps(row.get("location"), sort_keys=True),
        )

    known = {e["id"]: origin(e) for e in candidate.get("evidence", []) if isinstance(e, dict) and "id" in e}
    p = candidate.get("products", {})
    cards = {c["image_id"]: c for c in (p.get("image_catalog") or [])}
    layers = p.get("overlay_layers") or []
    observations = {
        r["fields"].get("image_id"): r
        for r in records
        if r["fields"].get("kind") == "archive_observation" and r["location"].get("image") == 1
    }
    valid = True
    try:
        for key, aid in targets.items():
            c = cards.get(key)
            r = observations.get(key)
            layer = [v for v in layers if v["image_id"] == key]
            if r is None:
                valid &= c is None or c.get("status") == "abstain"
                continue
            if not c or r["source_id"] != aid:
                valid = False
                continue
            f = r["fields"]
            if f.get("match_status") == "abstain":
                valid &= c.get("status") == "abstain" and not c.get("candidates") and not layer
                continue
            ref_id = f["reference_asset_id"]
            cid = f["candidate_id"]
            m = f["pixel_match"]
            reference = [
                v
                for v in records
                if v["source_id"] == ref_id
                and v["location"].get("pixel_region") == m["reference_support_xywh"]
                and v["fields"].get("candidate_id") == cid
                and v["fields"].get("image_id") == key
                and v["fields"].get("evidence_role") == "matched_reference_pixels"
            ]
            bindings = [
                v
                for v in records
                if v["fields"].get("kind") == "archive_reference"
                and v["fields"].get("asset_id") == ref_id
                and v["fields"].get("candidate_id") == cid
            ]
            cited = {known.get(i) for i in c["evidence_ids"]}
            valid &= (
                aid != ref_id
                and len(reference) == 1
                and bool(bindings)
                and origin(r) in cited
                and any(origin(v) in cited for v in reference)
                and any(origin(v) in cited for v in bindings)
                and c["status"] == "supported_candidate"
                and c["candidates"] == f["candidates"]
                and c["visible_features"] == f["visible_features"]
                and all(c["candidates"] == [v["fields"]["catalog_name"]] for v in bindings)
                and m["inliers"] >= MIN_INLIERS
                and m["inlier_fraction"] >= MIN_INLIER_RATIO
                and m["score"] >= 6
                and METHOD == m["method"]
                and r["location"]["pixel_region"] == m["target_support_xywh"]
            )
            if "bbox" in f:
                valid &= (
                    len(layer) == 1
                    and layer[0].get("source_id") == aid
                    and layer[0].get("bbox_xywh") == f["bbox"]
                )
            else:
                valid &= not layer
    except (TypeError, ValueError, KeyError, IndexError):
        valid = False
    return [
        {
            "name": "archive_pixel_evidence",
            "passed": bool(valid),
            "hard": True,
            "message": "像素候选、局部几何依据和支持框必须与冻结的目标照片、参考图及馆藏条目证据一致；拒识不得改记为确认。",
        }
    ]
