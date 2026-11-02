"""Mixed collections use business evidence and frozen image links when selecting inputs."""

from backend.app.casework.intake_sources import linked_sources, resolve_candidates
from backend.app.platform.tool_registry import digest


def view(id, rows, *, source=None, provenance="馆藏", modality="structured"):
    checksum = digest(rows)
    return {
        "asset": {
            "id": id,
            "project_id": "p",
            "source_id": source or id,
            "source_version": checksum,
            "checksum": checksum,
            "provenance": provenance,
            "classification": "local",
            "media_type": "image/png" if modality == "image" else "application/json",
            "modality": modality,
            "content": {"records": rows},
        }
    }


def scored(*views):
    return [(v, 4, [], "字段吻合") for v in views]


def ids(items):
    return {item[0]["asset"]["id"] for item in items}


def test_matrix_preserves_origins_rejects_unrelated_roles_and_obeys_question_ids():
    q = view(
        "q",
        [{"kind": "question", "question_id": "a", "question": "船坞试验结果如何？"}],
        source="archive/questions",
    )
    q_copy = view("qcopy", q["asset"]["content"]["records"], source="archive/questions")
    evidence = [{"question_id": "a", "role": "judicial", "statement": "原件记录通过。"}]
    first = view("j1", evidence, source="court")
    independent = view("j2", evidence, source="court", provenance="另一馆藏")
    partner = view("i", [{"question_id": "a", "role": "investigation", "statement": "独立调查结果待定。"}])
    other_role = view("audit", [{"question_id": "a", "role": "audit", "statement": "审计结论。"}])
    unrelated = view("other", [{"question_id": "b", "role": "investigation", "statement": "另一试验。"}])
    inputs = [q, q_copy, first, independent, partner, other_role, unrelated]
    picked, why = resolve_candidates("B08", "", inputs, scored(q, q_copy, first, independent, other_role))
    assert not why and ids(picked) == {"q", "j1", "j2", "i"}
    conflict = view("conflict", [{"kind": "question", "question_id": "a", "question": "未关联的另一问题"}])
    picked, why = resolve_candidates("B08", "", inputs + [conflict], scored(q, first))
    assert picked is None and why


def test_speech_adds_exact_transcript_text_not_an_unrelated_lookalike():
    timed = view(
        "time", [{"kind": "timed_asr", "utterance": "轴承已完成检测", "start_ms": 0, "end_ms": 1000}]
    )
    transcript = view("original", [{"kind": "transcript", "text": "轴承已完成检测"}], source="transcript")
    copy = view("reimport", transcript["asset"]["content"]["records"], source="transcript")
    other = view("other", [{"kind": "transcript", "text": "叶轮尚未完成检测"}])
    picked, why = resolve_candidates("T07", "", [timed, transcript, copy, other], scored(timed, other))
    assert not why and ids(picked) == {"time", "original"}


def test_split_clause_editions_cannot_silently_merge_different_before_text():
    old = {"side": "before", "clause_id": "A", "topic": "复核", "clause_text": "按年核对。"}
    first = view("a", [old])
    incompatible = view("b", [{**old, "clause_text": "按月核对。"}])
    after = view("c", [{**old, "side": "after", "clause_text": "按季核对。"}])
    picked, why = resolve_candidates(
        "T06", "", [first, incompatible, after], scored(first, incompatible, after)
    )
    assert picked is None and why


def test_explicit_authorized_image_links_only_and_missing_target_is_honest():
    observation = view("measure", [{"kind": "raster_binding", "role": "panel", "target_source_id": "frame"}])
    image = view("frame", [], modality="image")
    other = view("unrelated", [], modality="image")
    picked, gap = linked_sources("G02", [observation, image, other], {"measure"})
    assert not gap and [r["id"] for r in picked] == ["frame"]
    picked, gap = linked_sources("G02", [observation, other], {"measure"})
    assert not picked and gap
    before = view("damage", [{"building_id": "W7", "before_image": "frame", "after_image": "unrelated"}])
    picked, gap = linked_sources("G05", [before, image, other], {"damage", "frame"})
    assert not gap and [r["id"] for r in picked] == ["unrelated"]
