"""Resolve only current project editions, preserving request and source authorization."""

import re
import unicodedata

from . import service as s


def match(project, text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    cases = project["scenario_edition"]["cases"]
    codes = set(re.findall(r"(?<![a-z0-9])([bgt][0-9]{2})(?![a-z0-9-])", normalized))
    unknown = {code.upper() for code in codes if code.upper() not in cases}
    if unknown:
        s.fail("case_archived", "所指用例不在当前项目目录中：" + "、".join(sorted(unknown)), 422)
    matches = {code.upper() for code in codes}
    matches.update(
        key
        for key, case in cases.items()
        if case.get("title") and unicodedata.normalize("NFKC", case["title"]).casefold() in normalized
    )
    if len(matches) > 1:
        s.fail("ambiguous_case", "同时指向多个用例，请选择一个当前目录中的编号或标题。", 422)
    return cases[next(iter(matches))] if matches else None


def sources(tx, project_id, case, *, declared=(), owner_case=None):
    refs = list(case.get("input_refs") or [])
    if not refs:
        s.fail("insufficient_data", "该用例尚未登记可执行的配套原件。", 422)
    source_ids = set()
    for ref in refs:
        asset = tx.get("data_assets", ref)
        if (
            not asset
            or asset.get("project_id") != project_id
            or asset.get("task_case_id") not in {None, owner_case}
        ):
            s.fail("case_source_scope", "当前任务的资料权限不覆盖该用例的全部原件。", 403)
        s.usable_task_input(asset)
        if asset.get("classification") != "public" or asset.get("checksum") != case["checksums"].get(ref):
            s.fail("case_source_scope", "该用例配套原件的版本或公开授权发生了变化。", 409)
        source_ids.add(asset["source_id"])
    if declared and source_ids != set(declared):
        s.fail("case_source_scope", "指定来源与该用例登记的整套原件不一致。", 422)
    return refs


def requirement(case, refs):
    return [
        {
            "id": "edition-originals",
            "title": "用例配套公开原件",
            "description": "、".join(case["files"]),
            "required": True,
            "formats": list(dict.fromkeys(name.rsplit(".", 1)[-1].upper() for name in case["files"])),
            "fields": [],
            "status": "provided",
            "matched_asset_ids": list(refs),
            "provided_asset_ids": list(refs),
            "profiles": [],
            "validation": "使用项目发布的资料清单，执行时复核原件指纹和逐项证据。",
        }
    ]


def current(tx, project, row):
    case = ((project.get("scenario_edition") or {}).get("cases") or {}).get(row["edition_case_id"])
    if not case or set(
        sources(tx, row["project_id"], case, declared=row["source_ids"], owner_case=row["id"])
    ) != set(row["input_refs"]):
        s.fail("case_source_scope", "用例资料清单已经变化，请重新创建任务。", 409)
    return case
