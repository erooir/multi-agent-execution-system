"""Optional offline reading aids for audited verbatim evidence; never replace source quotations."""

import functools
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .provider_versions import model_revision


@functools.lru_cache(maxsize=2)
def _loaded_translation(folder, setting, revision):
    import ctranslate2
    import sentencepiece

    folder = Path(folder)
    model = ctranslate2.Translator(str(folder / "model"), device="cpu", compute_type="int8", intra_threads=2)
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    if model_revision(setting, folder) != revision:
        raise ValueError("本地翻译模型加载期间文件发生改变")
    return model, tokenizer, meta


def _load(folder, src):
    setting = {"ru": "HANGZHI_MT_RU_EN", "en": "HANGZHI_MT_EN_ZH"}[src]
    folder = str(Path(folder).resolve())
    return _loaded_translation(folder, setting, model_revision(setting, folder))


def _translate(texts, folder, src, dst):
    model, sp, meta = _load(folder, src)
    if meta["from_code"] != src or meta["to_code"] != dst:
        raise ValueError("翻译模型方向不符合配置")
    tokens = [sp.encode(text, out_type=str) for text in texts]
    if any(len(t) > 510 for t in tokens):
        raise ValueError("引文超过翻译长度限制")
    result = model.translate_batch(tokens, beam_size=3, max_decoding_length=512, max_batch_size=8)
    return [sp.decode(r.hypotheses[0]) for r in result]


def _normal(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    # Remove typesetting-only word breaks, never digit/identifier hyphens.
    text = re.sub(r"(?<=[a-z])-[ \t]*\r?\n\s*(?!und\b|and\b)(?=[a-z])", "", text)
    return " ".join(text.replace("▁", " ").split())


def _numeric_tokens(text):
    # Keep sign, multiplicity and decimals; never silently accept 0.56 as 56%.
    return re.findall(r"[+-]?\d+(?:[.,]\d+)*", _normal(text))


_UNITS = (
    (r"%", r"%"),
    (r"ppb\b", r"(?:ppb|十亿分之)"),
    (r"ppm\b", r"(?:ppm|百万分之)"),
    (r"kg\b", r"(?:kg|千克|公斤)"),
    (r"min(?:ute)?s?\b", r"(?:min|分钟)"),
    (r"km\b", r"(?:km|公里|千米)"),
    (r"Mt\b", r"(?:Mt|百万吨)"),
)
_CUES = (
    (
        r"\b(?:not|never|cannot|without|didn\'t|wasn\'t|no (?:evidence|difference|change|increase|decrease|effect))\b",
        r"不|未|无|非|没有|缺乏",
        "否定",
    ),
    (
        r"\b(?:assuming|assumed|assumptions?|conditional|if)\b",
        r"假设|假定|设想|如果|如若|条件|前提|若",
        "假设条件",
    ),
    (
        r"\b(?:projected|expected|simulated|simulation|modelled|modeled|predicted)\b",
        r"预测|预计|预期|期望|模拟|模型|估计|推算",
        "预测或模拟属性",
    ),
    (
        r"\b(?:reduction|reductions|reduced|decrease|decreased|decreases|lower|fewer)\b",
        r"减|降|低|更少|较少|小于",
        "降低方向",
    ),
    (r"\b(?:increase|increased|increases|higher|greater)\b", r"增|升|高|较大|更大|大于|更多", "升高方向"),
    (
        r"\b(?:relative to|compared (?:to|with)|in comparison with|than)\b",
        r"较|比|对照|相对|高于|低于|多于|少于",
        "比较关系",
    ),
)

# Auditable small terminology set. These are fixed physical locations, not increasing/decreasing trends.
_POSITION = {
    "upper troposphere": "对流层上部",
    "lower troposphere": "对流层下部",
    "upper stratosphere": "平流层上部",
    "lower stratosphere": "平流层下部",
    "upper fuselage": "机身上部",
    "lower fuselage": "机身下部",
}

# IPCC's bilingual climate glossary and ICAO's aviation terminology. Match a whole
# source term; a bare RF or PSD does not identify an expansion across disciplines.
_SCIENCE_TERMS = {
    "contrail cirrus radiative forcing": "凝结尾卷云辐射强迫",
    "contrail radiative forcing": "凝结尾迹辐射强迫",
    "radiative forcing": "辐射强迫",
    "contrail cirrus": "凝结尾卷云",
    "contrails": "凝结尾迹",
    "contrail": "凝结尾迹",
    "sustainable aviation fuel": "可持续航空燃料",
    "particle size distribution": "粒径分布",
    "power spectral density": "功率谱密度",
}
_SCIENCE_PATTERN = re.compile(r"\b(?:" + "|".join(map(re.escape, _SCIENCE_TERMS)) + r")\b", re.IGNORECASE)
_SYMBOL_MEANINGS = {
    "RF": {"radiative forcing": "辐射强迫", "radio frequency": "射频"},
    "PSD": {
        "particle size distribution": "粒径分布",
        "power spectral density": "功率谱密度",
        "private sector": "私营部门",
    },
}


def _source_terms(src):
    found = {m[0]: _SCIENCE_TERMS[m[0].casefold()] for m in _SCIENCE_PATTERN.finditer(src)}
    if (
        "SAF" in src
        and "armed forces" not in src.casefold()
        and (
            "sustainable aviation fuel" in src.casefold()
            or re.search(r"\bSAF (?:blends?|combustion|fuel)\b", src)
        )
    ):
        found["SAF"] = "可持续航空燃料"
    return found


def _bilingual_term(field):
    return _SCIENCE_PATTERN.sub(lambda m: f"{_SCIENCE_TERMS[m[0].casefold()]}（{m[0]}）", field)


def _check(original, zh):
    src, tgt = _normal(original), _normal(zh)
    if not tgt or not re.search(r"[\u4e00-\u9fff]", tgt):
        return "缺少中文结果"
    if Counter(_numeric_tokens(src)) != Counter(_numeric_tokens(tgt)):
        return "数值、符号或标识编号不一致"
    for raw, translated in _UNITS:
        for n in re.findall(r"([+-]?\d+(?:[.,]\d+)*)\s*" + raw, src, flags=re.IGNORECASE):
            if not re.search(re.escape(n) + r"\s*" + translated, tgt, re.IGNORECASE) and not (
                raw == "%" and f"百分之{n}" in tgt
            ):
                return "数字与单位绑定不一致"
    # Never expand an acronym from its letters. The source may refer to radio frequency,
    # radiative forcing, power spectral density, a particle size distribution, etc.
    for symbol in re.findall(r"\b(?:RF|PSD)\b", src):
        if not re.search(r"(?<![A-Za-z])" + symbol + r"(?![A-Za-z])", tgt):
            return "测量变量缩略语未保留：" + symbol
        if any(cn in tgt and en not in src.casefold() for en, cn in _SYMBOL_MEANINGS[symbol].items()):
            return "缩略语译义缺少原句依据：" + symbol
    for term, cn in _source_terms(src).items():
        if cn not in tgt and term not in tgt:
            return "科学术语未保留：" + term
        if ("contrail" in term.casefold() and re.search("反辐射力|反冲[量力]", tgt)) or (
            term == "SAF" and "苏丹武装部队" in tgt
        ):
            return "科学术语出现不相容的译义：" + term
    semantic_src = src
    for orig, cn in _POSITION.items():
        if orig not in semantic_src.casefold():
            continue
        equivalent = cn.replace("上部", "(?:上部|上层)").replace("下部", "(?:下部|下层)")
        reversed_form = cn[-2:] + cn[:-2]
        if not (re.search(equivalent, tgt) or reversed_form in tgt):
            return "垂直位置限定未保留"
        semantic_src = re.sub(re.escape(orig), "", semantic_src, flags=re.IGNORECASE)
    for pattern, expected, name in _CUES:
        if re.search(pattern, semantic_src, re.IGNORECASE) and not re.search(expected, tgt):
            return name + "未保留"
    if re.search(r"没有|从未|不可|无法|不会|并非|不能|未(?:能|测|见|发现|增加|降低)", tgt) and not re.search(
        _CUES[0][0], src, re.IGNORECASE
    ):
        return "译文引入了原句未表达的否定"
    # In scientific measurements an untranslated variable is preferable to an unrelated expansion.
    # For example, the same acronym can have unrelated expansions in different disciplines.
    for symbol in re.findall(r"\b(?:mean|measured|median|average)\s+([A-Z]{2,}(?:[-/][A-Z]+)*)\b", src):
        if symbol not in tgt:
            return "测量变量缩略语未保留：" + symbol
    for token in re.findall(
        r"(?<![A-Za-z0-9])(?:[A-Z]+[A-Za-z]*\d+(?:[-/]\d+)*|[A-Z]{2,}-\d+|NO[xy])\b", src
    ):
        if token not in tgt:
            return "技术标识未保留：" + token
    # A bag of numbers can match after swapping a baseline with a treatment.
    if (
        re.search(r"\b(?:compared (?:to|with)|relative to|than)\b", src, re.IGNORECASE)
        and len(_numeric_tokens(src)) > 1
        and _numeric_tokens(src) != _numeric_tokens(tgt)
    ):
        return "多数量比较的角色顺序无法核对"
    return None


def _controlled_reading(original):
    """Only accept full, unambiguous grammars. Preserve every operand; do not patch a wrong model output."""
    q = _normal(original)
    if re.search(
        r"\b(?:not|never|cannot|without|except|unless|instead of|but|however|whereas|if)\b", q, re.IGNORECASE
    ):
        return None
    # Preserve multiplier vs *increment* semantics: a factor of four means four times the baseline,
    # not a fourfold increase added to the baseline. Keep original citations and assumptions bound.
    mt = re.fullmatch(
        r"(?:Furthermore,\s*)?(?P<variable>[^,;.!?]+?)\s+is projected to increase by a factor of\s+(?P<factor>\d+(?:\.\d+)?)\s+until\s+(?P<year>\d{4})\s+compared to\s+(?P<baseline>\d{4})(?P<cite1>\s*\([^()]+\)),\s*leading to an expected\s+(?P<effect>doubling|tripling)\s+of\s+(?P<metric>[^;.!?()]+?)\s*(?P<cite2>\([^()]+\))\s+when assuming\s+(?P<assumption>.+?)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        multiple = "两" if g["effect"].lower() == "doubling" else "三"
        txt = f"预测对象「{g['variable']}」到{g['year']}年预计为{g['baseline']}年的{g['factor']}倍{g['cite1']}。预计「{_bilingual_term(g['metric'])}」变为原来的{multiple}倍{g['cite2']}；此推断以「{g['assumption']}」为前提。"
        return txt, "带基线的预测句字段对照（倍数不同于增量）", g
    if re.search(r"\b(?:assuming|assumed)\b", q, re.IGNORECASE):
        return None
    # Keep model estimates distinct from measurements. Preserve the exact qualifier
    # when a model relates a further change to another experimental variable.
    mt = re.fullmatch(
        r"(?P<basis>[^;.!?]+?\bsimulations?\b[^;.!?]*?)\s+(?P<caution>conservatively\s+)?estimates?\s+an?\s+"
        r"(?P<amount>\d+(?:\.\d+)?\s*%)\s+(?P<direction>decrease|reduction|increase) in "
        r"(?P<metric>[^;.!?]+?)(?: and (?P<qualification>[^;.!?]+))?\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        direction = "上升" if g["direction"].casefold() == "increase" else "下降"
        estimate = "保守估计" if g["caution"] else "估计"
        txt = f"模拟依据为「{g['basis']}」。模型{estimate}「{_bilingual_term(g['metric'])}」{direction} {g['amount']}。"
        if g["qualification"]:
            txt += f"相关限定保留原文「{g['qualification']}」。"
        return txt, "模拟估计句字段对照（指标术语中英并列，保留原文限定）", g
    mt = re.fullmatch(
        r"(?P<metric>[^;.!?]+?\bmeasurements)\s+were conducted with\s+(?P<instrument>.+?),\s*also receiving\s+(?P<material>[^,;.!?]+?)\s+through\s+(?:an?\s+)?(?P<device>[^,;.!?]+?)\s+mounted on\s+(?:the\s+)?(?P<mount>[^,;.!?]+?)\s+and designed for\s+(?P<purpose>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"测量内容：{g['metric']}；使用仪器：{g['instrument']}；通过安装在「{g['mount']}」的「{g['device']}」接收「{g['material']}」；设计用途（原文）：{g['purpose']}。"
        return txt, "仪器测量句字段对照（位置与用途保留原文）", g
    quantity = r"\d+(?:\.\d+)?\s*(?:%|K\b|kg\b|ppm\b|ppb\b|km\b)"
    bounded = rf"{quantity}(?:\s*[–—-]\s*{quantity})?"
    mt = re.fullmatch(
        rf"The uncertainties of\s+(?P<x>{bounded})\s+in (?:the )?(?P<variable>[^;.!?]+?)\s+and\s+(?P<y>{bounded})\s+in (?:the )?(?P<other>[^;.!?]+?)\s+combine to an uncertainty of\s+(?P<approx>around|approximately|about)\s+(?P<combined>{bounded})\s+for (?:the )?(?P<result>[^();.!?]+?)\s*\((?P<conditions>[^()]+)\)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"「{g['variable']}」的不确定度为{g['x']}，「{g['other']}」的不确定度为{g['y']}；两项合并后，「{g['result']}」的不确定度约为{g['combined']}。原文附加条件：{g['conditions']}。"
        return txt, "不确定度句字段对照（区间、单位、限定与约数原样保留）", g
    mt = re.fullmatch(
        r"(?P<context>(?:\d+(?:\.\d+)+\s+)?[^;.!?]{2,160}?)\s*\((?P<symbol>[A-Za-z][A-Za-z0-9+-]{0,18})\),\s*(?P<definition>[^,;.!?]{1,220}),\s*was measured using\s+(?P<method>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"原文条目「{g['context']}」使用符号「{g['symbol']}」，其定义为「{g['definition']}」；使用「{g['method']}」测量。"
        return txt, "定义与测量方法字段对照（符号及定义保留原文）", g
    # Explicit measurement comparison. Preserve differing numerator/denominator and trial/control verbatim.
    mt = re.fullmatch(
        r"(?:A|An)\s+(?P<amount>\d+(?:[.,]\d+)?\s*%)\s+(?P<direction>reduction|decrease|increase)\s+in\s+(?P<metric>[^;.!?]+?)\s+(?:was|were)\s+(?P<basis>measured|observed)\s+(?:for|with)\s+(?P<trial>[^;.!?]+?)\s+compared\s+(?:to|with)\s+(?P<control>[^;.!?]+?)\s+under\s+(?P<condition>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        direction = "升高" if g["direction"].lower() == "increase" else "降低"
        basis = "实测" if g["basis"].lower() == "measured" else "观察到"
        txt = f"在「{g['condition']}」条件下，以「{g['control']}」为对照，「{g['trial']}」的指标「{g['metric']}」{basis}{direction} {g['amount']}。"
        return txt, "比较句字段对照（数值、对象和条件保留原文）", g
    mt = re.fullmatch(
        r"(?P<metric>[^;.!?]{3,100}?)\s+of\s+(?P<trial>[^;.!?]+?)\s+were measured and compared to\s+(?P<control>[^;.!?]+?)\s+at\s+(?P<condition>[^;.!?]+?)\s+within\s+(?P<scope>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"在「{g['scope']}」范围与「{g['condition']}」条件下，研究测量「{g['trial']}」的「{g['metric']}」，并与「{g['control']}」比较。"
        return txt, "测量对象与参照条件字段对照", g
    # Active agent, measured platform, powerplant and sampling condition are independent source slots.
    mt = re.fullmatch(
        r"(?P<context>During [^;.!?]+?,)\s+(?:the\s+)?(?P<observer>[^,;.!?]{2,110}?\s+aircraft)\s+performed\s+(?P<method>[^,;.!?]+?\bmeasurements)\s+following\s+(?:an?\s+)?(?P<target>[^,;.!?]+?)\s+source aircraft\s+powered by\s+(?P<engine>[^,;.!?]+?)\s+engines\s+in\s+(?P<condition>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"研究背景（原文）：{g['context']} 观测平台：{g['observer']}；测量方式：{g['method']}；跟测的源飞机：{g['target']}；其发动机：{g['engine']}；采样条件：{g['condition']}。"
        return txt, "观测句字段对照（专名、设备与条件保留原文）", g
    mt = re.fullmatch(
        r"(?:Therefore,\s*)?in order to\s+(?P<action>minimi[sz]e|reduce)\s+(?P<effect>[^,;.!?]+?)\s+due to\s+(?P<cause>[^,;.!?]+),\s*(?P<tracer>[A-Za-z][A-Za-z0-9+-]{0,18})\s+was chosen as\s+(?:an?\s+)?(?P<role>[^;.!?]+)\.?",
        q,
        re.IGNORECASE,
    )
    if mt:
        g = mt.groupdict()
        txt = f"为减小「{g['cause']}」引起的「{g['effect']}」，研究选用「{g['tracer']}」作为「{g['role']}」。"
        return txt, "方法句字段对照（因果方向和化学标识保留原文）", g
    # An enumerated organization list is identity metadata, not a claim about an experiment.
    lines = [x.strip() for x in original.splitlines() if x.strip()]
    orgs = []
    for line in lines:
        item = re.fullmatch(r"(\d{1,2})\s*([^\d].+)", line)
        if (
            not item
            or int(item[1]) != len(orgs) + 1
            or not re.search(
                r"Institut|Universit|Corporat|Research|Laborator|\b(?:SAS|Ltd|plc|Inc)\b",
                item[2],
                re.IGNORECASE,
            )
        ):
            break
        orgs.append((item[1], item[2]))
    if len(orgs) == len(lines) and len(orgs) >= 2:
        return (
            "署名机构（专名与地址保留原文）：" + "；".join(i + " " + o for i, o in orgs),
            "机构名录原文对照",
            {"entries": orgs},
        )
    # Name transliteration can lose attached affiliation indices. Leave bylines verbatim.
    names = re.split(r",\s+(?=(?:and\s+)?[A-ZÀ-Ž])", q)
    if 3 <= len(names) <= 80 and all(
        re.fullmatch(r"(?:and\s+)?[A-ZÀ-Ž][\wÀ-ž\s.\'-]{1,65}?\s*\d{1,2}(?:,\d{1,2})*", n) for n in names
    ):
        return "作者与机构编号（保留原始署名）：" + q, "作者名录原文对照", {"byline": q}
    return None


def _bridge_ok(original, en):
    if Counter(_numeric_tokens(original)) != Counter(_numeric_tokens(en)):
        return False
    source_negation = r"\b(?:не|нет|без)\b"
    en_negation = r"\b(?:not|without|no|never)\b"
    if bool(re.search(source_negation, original, re.IGNORECASE)) != bool(
        re.search(en_negation, en, re.IGNORECASE)
    ):
        return False
    for source, target in [
        (r"\bесли\b", r"\bif|condition"),
        (r"увелич", r"increas|greater|higher"),
        (r"сниж|уменьш", r"decreas|reduc|lower"),
    ]:
        if re.search(source, original, re.IGNORECASE) and not re.search(target, en, re.IGNORECASE):
            return False
    return True


def _sentence_retry(original, folder):
    """Retry complete source sentences independently. Never stitch isolated operators or identifiers."""
    q = _normal(original)
    pieces = re.split(r"(?<=[.!?])\s+(?=(?:[A-Z]|\d+\.\d+))", q)
    if not 2 <= len(pieces) <= 12 or any(len(part) < 12 for part in pieces):
        return None
    parts = []
    free = []
    for i, part in enumerate(pieces):
        controlled = _controlled_reading(part)
        if controlled and Counter(_numeric_tokens(part)) == Counter(_numeric_tokens(controlled[0])):
            text, method, fields = controlled
            parts.append({"source": part, "reading": text, "method": method, "source_fields": fields})
        else:
            parts.append({"source": part, "method": "整句机译"})
            free.append(i)
    if free:
        translations = _translate([pieces[i] for i in free], folder, "en", "zh")
        for i, text in zip(free, translations, strict=True):
            if _check(pieces[i], text):
                return None
            parts[i]["reading"] = _normal(text)
    text = " ".join(v["reading"] for v in parts)
    if Counter(_numeric_tokens(q)) != Counter(_numeric_tokens(text)):
        return None
    return {"text_zh": text, "method": "逐句阅读对照（分别校验数值与限定）", "source_segments": parts}


def localize(evidence):
    groups = {"en": [], "ru": []}
    readings = []
    issues = []
    for e in evidence:
        if e.get("kind") != "verbatim":
            continue
        q = e.get("quote", "")
        if re.search(r"[\u4e00-\u9fff]", q):
            continue
        language = (
            "ru"
            if re.search(r"[А-Яа-я]{3}", q)
            else "en"
            if len(re.findall(r"[A-Za-z]{2,}", q)) >= 3
            else None
        )
        if language:
            groups[language].append((e["id"], q))
    paths = {"en": os.environ.get("HANGZHI_MT_EN_ZH"), "ru": os.environ.get("HANGZHI_MT_RU_EN")}
    for lang, pairs in groups.items():
        if not pairs:
            continue
        if len(pairs) > 80:
            issues.append("机译索引超过80段，仅提供首批译文")
        pairs = pairs[:80]
        free = []
        for key, original in pairs:
            known = _controlled_reading(original) if lang == "en" else None
            if known and Counter(_numeric_tokens(original)) == Counter(_numeric_tokens(known[0])):
                txt, method, fields = known
                readings.append(
                    {
                        "evidence_id": key,
                        "text_zh": txt,
                        "method": method,
                        "source_fields": fields,
                        "authoritative": False,
                    }
                )
            else:
                free.append((key, original))
        if not free:
            continue
        if not paths["en"] or (lang == "ru" and not paths["ru"]):
            issues.append("未配置所需离线机译模型，保留原文")
            continue
        try:
            texts = [_normal(q) for _, q in free]
            if lang == "ru":
                texts = _translate(texts, paths["ru"], "ru", "en")
            translated = _translate(texts, paths["en"], "en", "zh")
            for (key, original), bridge, translated_text in zip(free, texts, translated, strict=True):
                problem = (
                    "俄英中间译文的数值或限定不一致"
                    if lang == "ru" and not _bridge_ok(original, bridge)
                    else _check(bridge, translated_text)
                )
                if problem:
                    revised = _sentence_retry(original, paths["en"]) if lang == "en" else None
                    if revised:
                        readings.append({"evidence_id": key, **revised, "authoritative": False})
                        continue
                    issues.append(f"{key} {problem}，译文未采用")
                    continue
                readings.append(
                    {
                        "evidence_id": key,
                        "text_zh": _normal(translated_text),
                        "method": "离线机器翻译" if lang == "en" else "俄英中离线转译",
                        "authoritative": False,
                    }
                )
        except (OSError, RuntimeError, ValueError, ImportError):
            issues.append("离线机译未完成，保留原文")
    return readings, list(dict.fromkeys(issues))
