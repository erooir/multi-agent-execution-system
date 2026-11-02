"""Index known observations and timed speech; never infer timestamps from transcript length."""

import difflib
import re
from collections import defaultdict

from .document_common import items, norm, number, string, tokens, value


def archive_images(run):
    photos = {}
    refs = defaultdict(list)
    layers = []
    for r in run.records:
        key = string(r, "image_id", "catalog_id", "图片编号")
        kind = string(r, "kind", "role")
        if not key:
            continue
        if kind in {"catalog", "reference", "馆藏说明"}:
            refs[key].append(r)
            continue
        if key in photos:
            run.gap(f"{key} 图片编号不唯一")
            continue
        photos[key] = r
    cards = []
    for key, r in photos.items():
        visible = string(r, "visible_features", "observed", "可见依据")
        reference = refs.get(key, [])
        box = value(r, "bbox", "box")
        valid_box = (
            isinstance(box, list)
            and len(box) == 4
            and all(
                isinstance(n, (int, float)) and not isinstance(n, bool) and number(n) is not None and n >= 0
                for n in box
            )
            and box[2] > 0
            and box[3] > 0
        )
        if valid_box and string(r, "coordinate_space") not in {"pixels", "pixel"}:
            valid_box = False
        if valid_box:
            width = number(value(r, "width"))
            height = number(value(r, "height"))
            valid_box = bool(width and height and box[0] + box[2] <= width and box[1] + box[3] <= height)
        candidates = items(value(r, "candidates", "candidate")) if visible else []
        ref_names = [
            {
                "archival_name": string(q, "model_name", "name"),
                "url": value(q, "url"),
                "evidence_ids": [run.cite(q)],
            }
            for q in reference
        ]
        cards.append(
            {
                "image_id": key,
                "visible_features": visible or None,
                "candidates": candidates,
                "reference_records": ref_names,
                "status": "supported_candidate" if visible and candidates and reference else "abstain",
                "reason": None
                if visible and candidates and reference
                else "缺可见依据、候选或对应馆藏说明；不从标题反推机型",
                "evidence_ids": [run.cite(r)] + [run.cite(q) for q in reference],
            }
        )
        if valid_box:
            layers.append(
                {
                    "image_id": key,
                    "source_id": r["source_id"],
                    "bbox_xywh": box,
                    "coordinate_space": "pixels",
                    "evidence_ids": [run.cite(r)],
                }
            )
        elif box is not None:
            run.gap(f"{key} 标注框坐标或图像尺寸无效")
    if not photos:
        run.gap("没有可编号的图像或已知观察记录，未进行图像识别")
    return {"image_catalog": cards, "overlay_layers": layers}


def _milliseconds(raw):
    parts = raw.replace(",", ".").split(":")
    seconds = float(parts[-1])
    mins = int(parts[-2])
    hours = int(parts[-3]) if len(parts) > 2 else 0
    if mins >= 60 or seconds >= 60:
        return None
    return round((hours * 3600 + mins * 60 + seconds) * 1000)


def speech(run, interview=False):
    timed = []
    scripts = []
    ambiguous = []
    terms = {}
    for r in run.records:
        kind = string(r, "kind", "role")
        raw = r.get("text", "")
        speaker = string(r, "speaker", "发言人") or None
        if kind == "glossary":
            word = string(r, "term")
            if word:
                terms[word] = {"meaning": value(r, "meaning", "definition"), "evidence_ids": [run.cite(r)]}
            continue
        a = number(value(r, "start_ms", "开始毫秒"))
        b = number(value(r, "end_ms", "结束毫秒"))
        turn = string(r, "turn_type", "发言类型") or None
        utterance = string(r, "utterance", "transcript", "原话") or raw
        if a is not None or b is not None:
            if a is None or b is None or a < 0 or b <= a:
                run.gap("媒体区间无效，无法生成回听定位")
                continue
            timed.append(
                {
                    "text": utterance,
                    "start_ms": a,
                    "end_ms": b,
                    "speaker": speaker,
                    "turn_type": turn,
                    "topic": value(r, "topic"),
                    "translation_zh": value(r, "translation_zh"),
                    "hearing": value(r, "hearing", "audibility"),
                    "r": r,
                    "media_id": value(r, "media_id", default=r["source_id"]),
                }
            )
        elif "-->" in raw:
            pattern = r"(?m)^\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
            matched = False
            for m in re.finditer(pattern, raw.replace("\r\n", "\n"), re.DOTALL):
                a, b = _milliseconds(m[1]), _milliseconds(m[2])
                if a is None or b is None or b <= a:
                    run.gap("字幕时间码无效")
                    continue
                matched = True
                timed.append(
                    {
                        "text": m[3].strip(),
                        "start_ms": a,
                        "end_ms": b,
                        "speaker": speaker,
                        "turn_type": turn,
                        "topic": None,
                        "translation_zh": None,
                        "hearing": None,
                        "r": r,
                        "media_id": value(r, "media_id", default=r["source_id"]),
                    }
                )
            if not matched:
                run.gap("未找到合法VTT/SRT字幕区间")
        elif utterance:
            scripts.append(
                {
                    "text": utterance,
                    "speaker": speaker,
                    "turn_type": turn,
                    "r": r,
                    "topic": value(r, "topic"),
                    "translation_zh": value(r, "translation_zh"),
                }
            )
    timed.sort(key=lambda t: (str(t["media_id"]), t["start_ms"]))
    # Candidate retrieval first, then an ordered monotone match; the similarity is not ASR confidence.
    posting = defaultdict(set)
    script_tokens = []
    for i, s in enumerate(scripts):
        ts = tokens(s["text"])
        script_tokens.append(ts)
        for t in ts:
            posting[t].add(i)
    index = []
    topics = defaultdict(list)
    used = set()
    last = defaultdict(lambda: -1)
    for cue in timed:
        ts = tokens(cue["text"])
        candidates = set().union(*(posting[t] for t in ts)) if ts else set()
        candidates = [i for i in candidates if i not in used and i >= last[cue["media_id"]]]
        candidates.sort(key=lambda i: (-len(ts & script_tokens[i]) / max(1, len(ts | script_tokens[i])), i))
        scores = [
            (
                difflib.SequenceMatcher(
                    None, norm(cue["text"])[:2500], norm(scripts[i]["text"])[:2500]
                ).ratio(),
                i,
            )
            for i in candidates[: (12 if run.retry else 4)]
        ]
        scores.sort(key=lambda p: (-p[0], p[1]))
        best = scores[0] if scores else None
        match = (
            scripts[best[1]]
            if best and best[0] >= 0.55 and (len(scores) == 1 or best[0] > scores[1][0] + 0.02)
            else None
        )
        if match:
            used.add(best[1])
            last[cue["media_id"]] = best[1]
        row = {k: v for k, v in cue.items() if k != "r"}
        row.update(
            script=match["text"][:800] if match else None,
            alignment="aligned" if match else "unmatched",
            similarity=round(best[0], 4) if best else None,
            evidence_ids=[run.cite(cue["r"])] + ([run.cite(match["r"])] if match else []),
        )
        for k in ("speaker", "turn_type", "topic", "translation_zh"):
            if row[k] is None and match:
                row[k] = match.get(k)
        if row["topic"] is None:
            row["topic"] = "未分类"
        if row["turn_type"] is None:
            row["turn_type"] = "question" if row["text"].endswith(("?", "？")) else "unknown"
        if match and norm(row["text"]) != norm(row["script"]) or not match:
            ambiguous.append(
                {"index": len(index), "reason": "逐字稿与字幕措辞不同" if match else "无唯一可信逐字稿对应项"}
            )
        if row["hearing"] in {"unclear", "inaudible", "不清"}:
            ambiguous.append({"index": len(index), "reason": "上游标注听辨不清"})
        topics[row["topic"]].append(len(index))
        index.append(row)
    if not timed:
        run.gap("无上游对齐时间码；仅能引用逐字稿，不能生成音轨时间")
    if not scripts and not interview:
        run.gap("缺独立逐字稿，不能核对字幕差异")
    if timed and not any(x["translation_zh"] or re.search(r"[\u4e00-\u9fff]", x["text"]) for x in index):
        run.gap("缺核验过的中文译文；保留原话，不伪造翻译")
    if any(x["topic"] == "未分类" for x in index):
        run.gap("部分发言未获可核专题标签")
    if ambiguous:
        run.gap("存在待核对的听辨或逐字稿差异")
    unmatched = [
        {"text": s["text"][:600], "speaker": s["speaker"], "evidence_ids": [run.cite(s["r"])]}
        for i, s in enumerate(scripts)
        if i not in used
    ]
    return {
        "timed_index": index,
        "topics": dict(topics),
        "differences": ambiguous,
        "transcript_only": unmatched,
        "glossary": terms,
    }
