"""Offline reading audits use synthetic, publishable examples and keep the original evidence."""

import pytest

from backend.app.casework import translation as mt


def quote(text, key="A1"):
    return {"kind": "verbatim", "id": key, "quote": text}


def test_typed_comparison_binds_baseline_measurement_and_condition_without_an_installed_model(monkeypatch):
    monkeypatch.delenv("HANGZHI_MT_EN_ZH", raising=False)
    original = "A 13 % increase in sample density was observed for 70 % MIX-7 compared to Batch Q under dry conditions."
    e = quote(original)
    readings, issues = mt.localize([e])
    assert not issues and len(readings) == 1 and e["quote"] == original
    reading = readings[0]
    assert not reading["authoritative"] and reading["evidence_id"] == "A1"
    assert reading["source_fields"] == {
        "amount": "13 %",
        "direction": "increase",
        "metric": "sample density",
        "basis": "observed",
        "trial": "70 % MIX-7",
        "control": "Batch Q",
        "condition": "dry conditions",
    }
    assert "观察到升高 13 %" in reading["text_zh"]
    for adversative in ["not observed", "observed only if stable", "observed without calibration"]:
        assert mt._controlled_reading(original.replace("observed", adversative)) is None


def test_projection_preserves_factor_result_and_conditional_citations():
    src = (
        "Annual sampling distance is projected to increase by a factor of 6 until 2048 compared to "
        "2014 (Brown, 2021), leading to an expected doubling of processing cost (Gold, 2022) "
        "when assuming equal processing time."
    )
    text, _, fields = mt._controlled_reading(src)
    assert fields["baseline"] == "2014" and fields["factor"] == "6" and fields["effect"] == "doubling"
    assert "预计为2014年的6倍" in text and "变为原来的两倍" in text and "为前提" in text
    assert "(Brown, 2021)" in text and "(Gold, 2022)" in text
    assert (
        mt._controlled_reading(src.replace("equal processing time", "equal processing time unless postponed"))
        is None
    )


def test_observer_and_tracer_roles_remain_bound_to_symbol_and_source():
    source = (
        "During Study Q, the Survey Mk 8 aircraft performed particle measurements following a "
        "Target B-7 source aircraft powered by Trial Engine T-84 engines in dry air."
    )
    t, _, g = mt._controlled_reading(source)
    assert g["observer"] == "Survey Mk 8 aircraft" and g["engine"] == "Trial Engine T-84"
    assert g["target"] == "Target B-7" and g["condition"] == "dry air" and "T-84" in t
    assert mt._controlled_reading(source.replace("Study Q,", "Study Q. A separate observation,")) is None
    src = "In order to reduce variance due to CO2 drift, NOy was chosen as a dilution tracer."
    txt, _, fields = mt._controlled_reading(src)
    assert fields["cause"] == "CO2 drift" and fields["tracer"] == "NOy" and "「NOy」" in txt
    assert mt._controlled_reading(src.replace("was chosen", "was never chosen")) is None


def test_affiliation_ids_require_sequential_distinct_institutions():
    src = "1Alpha Research Laboratory, North\n2Beta University, South"
    text, _, data = mt._controlled_reading(src)
    assert [v[0] for v in data["entries"]] == ["1", "2"] and "2 Beta University" in text
    for malformed in [src.replace("\n2", "\n3"), src.replace("Beta University", "ordinary prose")]:
        assert mt._controlled_reading(malformed) is None


@pytest.mark.parametrize(
    ("source", "rendered", "issue"),
    [
        ("The sensor recorded 10 kg.", "传感器记录10公里。", "单位"),
        ("The sensor recorded -0.56 kg.", "传感器记录0.56千克。", "符号"),
        ("The sensor did not detect a change.", "传感器检测出变化。", "否定"),
        ("The sensor detected a change.", "传感器未发现变化。", "否定"),
        ("Fewer samples were found compared to last year.", "与去年相比发现了更多样本。", "降低"),
        ("If stable, the expected increase is 12 %.", "预计升高12%。", "假设"),
        ("The upper troposphere is sampled.", "对流层被采样。", "垂直位置"),
        ("The mean PSD is reported.", "报告平均私营部门。", "缩略语"),
        ("The NOy signal was observed.", "观测到了诺伊信号。", "技术标识"),
        ("10 kg was compared to 20 kg.", "20千克与10千克比较。", "角色顺序"),
    ],
)
def test_reader_rejects_equal_number_bags_when_units_flags_symbols_or_roles_change(source, rendered, issue):
    assert issue in mt._check(source, rendered)


def test_retry_works_only_on_complete_sentences_and_preserves_each_pair(monkeypatch):
    monkeypatch.setenv("HANGZHI_MT_EN_ZH", "fixture")
    first = "A probe measured 17 kg."
    second = "A probe measured 23 kg."
    seen = []

    def translate(texts, folder, src, dst):
        seen.append(texts)
        return ["样本为17千克。", "样本为23千克。"] if len(texts) == 2 else ["样本为17千克。"]

    monkeypatch.setattr(mt, "_translate", translate)
    readings, notes = mt.localize([quote(first + " " + second)])
    assert not notes and seen == [[first + " " + second], [first, second]]
    assert [s["source"] for s in readings[0]["source_segments"]] == [first, second]
    assert not readings[0]["authoritative"]
    assert mt._sentence_retry("If adequate, the specimen was measured; 17 kg is its mass.", "fixture") is None


def test_bad_sentence_or_pivot_is_never_published_as_a_valid_reading(monkeypatch):
    monkeypatch.setenv("HANGZHI_MT_EN_ZH", "fixture")
    monkeypatch.setenv("HANGZHI_MT_RU_EN", "fixture")
    monkeypatch.setattr(
        mt,
        "_translate",
        lambda texts, folder, src, dst: [
            "The temperature rose 7." if src == "ru" else "温度升高7。" for _ in texts
        ],
    )
    readings, notes = mt.localize([quote("Температура не увеличилась на 7.")])
    assert not readings and any("俄英中间译文" in v for v in notes)
    readings, notes = mt.localize([quote("The temperature rose 7. The temperature rose 9.")])
    assert not readings and any("A1" in v for v in notes)


def test_model_estimate_with_unrelated_scientific_input_preserves_quantity_term_and_qualifier(monkeypatch):
    monkeypatch.delenv("HANGZHI_MT_EN_ZH", raising=False)
    src = (
        "Ocean model simulations for the 2041 survey estimate an 11 % increase in aerosol radiative "
        "forcing and weaker effects for smaller emissions."
    )
    rows, notes = mt.localize([quote(src)])
    assert not notes and len(rows) == 1
    r = rows[0]
    assert not r["authoritative"] and r["source_fields"]["metric"] == "aerosol radiative forcing"
    assert r["source_fields"]["qualification"] == "weaker effects for smaller emissions"
    assert "aerosol 辐射强迫（radiative forcing）" in r["text_zh"] and "上升 11 %" in r["text_zh"]
    assert "保守估计" not in r["text_zh"]


@pytest.mark.parametrize(
    ("source", "bad", "correct"),
    [
        (
            "A field study compared contrail radiative forcing.",
            "现场研究比较了反辐射力。",
            "现场研究比较了凝结尾迹辐射强迫。",
        ),
        ("The contrail anomaly persisted.", "反冲力异常持续。", "凝结尾迹异常持续。"),
        ("A project sampled SAF blends.", "研究采集苏丹武装部队的混合物。", "研究采集可持续航空燃料混合物。"),
        (
            "The filter power spectral density (PSD) was recorded.",
            "记录滤波器的粒径分布(PSD)。",
            "记录滤波器的功率谱密度(PSD)。",
        ),
        (
            "The observed particle size distribution (PSD) was recorded.",
            "记录观测到的功率谱密度(PSD)。",
            "记录观测到的粒径分布(PSD)。",
        ),
        ("The device recorded RF intensity.", "设备记录了射频强度。", "设备记录了RF强度。"),
    ],
)
def test_domain_terms_require_evidence_of_the_source_meaning(source, bad, correct):
    assert mt._check(source, bad)
    assert mt._check(source, correct) is None


def test_letters_alone_are_never_reinterpreted_as_climate_or_aviation_terms():
    assert mt._check("The radio frequency (RF) receiver works.", "射频(RF)接收机工作。") is None
    assert (
        mt._check("The Sudanese Armed Forces (SAF) visited the base.", "苏丹武装部队(SAF)访问该基地。")
        is None
    )
    assert mt._check("The PSD was measured.", "记录了 PSD。") is None
    assert mt._check("The PSD was measured.", "记录了私营部门(PSD)。")
    assert mt._check("The RF was measured.", "记录了辐射强迫(RF)。")
    assert mt._check("Contrail measurements were recorded.", "记录了反冲力(contrail)的测量。")
