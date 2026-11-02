"""Deployment-owned reference inventories. No HTTP route registers reference data.

Registry contents belong to the deployment, not the algorithm version. One
applicable inventory is copied into a task; reviewing/replaying a task never
re-opens the registry or trusts request-supplied claims about coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]
HEX = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
KEY = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
VERSION = Annotated[str, Field(min_length=1, max_length=100)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Source(Strict):
    sha256: HEX
    source_version: VERSION
    role: Literal["source", "before", "after"] = "source"


class RecordCount(Strict):
    kind: Literal["record_count"]
    sha256: HEX
    count: int = Field(ge=1, le=1_000_000)


class ProductCount(Strict):
    kind: Literal["product_count"]
    product: KEY
    count: int = Field(ge=1, le=1_000_000)


class ProductValues(Strict):
    kind: Literal["product_values"]
    product: KEY
    field: KEY
    values: list[str | int] = Field(min_length=1, max_length=2000)


class Batch(Strict):
    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    case_id: str = Field(pattern=r"^[BTG](0[1-9]|10)$")
    version: VERSION
    sources: list[Source] = Field(min_length=1, max_length=50)
    conditions: list[Annotated[RecordCount | ProductCount | ProductValues, Field(discriminator="kind")]] = (
        Field(default_factory=list, max_length=30)
    )
    supplement: Literal["none", "before_after_binding"] = "none"

    @model_validator(mode="after")
    def unambiguous(self):
        hashes = {x.sha256 for x in self.sources}
        if len(hashes) != len(self.sources):
            raise ValueError("Reference source fingerprints must be unique")
        if self.supplement == "before_after_binding":
            roles = Counter(s.role for s in self.sources)
            if self.case_id != "B06" or roles["before"] != 1 or roles["after"] != 1:
                raise ValueError("A B06 binding requires exactly one before and one after document")
            if len(self.sources) >= 50:
                raise ValueError("Reserve one of the 50 task input slots for the B06 version binding")
        for item in self.conditions:
            if isinstance(item, RecordCount) and item.sha256 not in hashes:
                raise ValueError("Record count points to unregistered source")
            if isinstance(item, ProductValues) and (
                len({str(v) for v in item.values}) != len(item.values)
                or any(isinstance(v, str) and (not v or len(v) > 160) for v in item.values)
            ):
                raise ValueError("Invalid expected product values")
        return self


def digest(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def signed_entry(spec):
    payload = Batch.model_validate(spec).model_dump()
    return {**payload, "digest": digest(payload)}


def registry_path():
    return Path(
        os.environ.get("HANGZHI_REFERENCE_BATCHES_FILE", ROOT / ".local/casework/reference-batches.json")
    )


def read_entries(path):
    if path.is_symlink():
        raise ValueError("Reference registry cannot be a symbolic link")
    if not path.exists():
        return []
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Invalid reference registry")
    registry = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "batches"}
        or type(registry["schema_version"]) is not int
        or registry["schema_version"] != 1
        or not isinstance(registry["batches"], list)
        or len(registry["batches"]) > 1000
    ):
        raise ValueError("Invalid reference registry schema")
    entries = []
    keys = set()
    for record in registry["batches"]:
        if not isinstance(record, dict):
            raise TypeError("Invalid registry entry")
        entry = signed_entry({k: v for k, v in record.items() if k != "digest"})
        key = (entry["project_id"], entry["case_id"])
        if entry["digest"] != record.get("digest") or key in keys:
            raise ValueError("Corrupt or ambiguous reference registry")
        keys.add(key)
        entries.append(entry)
    return entries


def freeze(project_id, code):
    state = "unregistered"
    entry = None
    try:
        entries = read_entries(registry_path())
        matching = [e for e in entries if e["case_id"] == code and e["project_id"] in (project_id, None)]
        if matching:
            entry = deepcopy(max(matching, key=lambda e: e["project_id"] is not None))
            state = "registered"
    except (OSError, ValueError, TypeError):
        # Never reveal paths, parse details or data belonging to another project.
        state = "registry_invalid"
    snapshot = {
        "schema_version": 1,
        "project_id": project_id,
        "case_id": code,
        "state": state,
        "entry": entry,
    }
    return {**snapshot, "checksum": digest(snapshot)}


def _check(name, passed, message, hard=False):
    return {"name": name, "passed": bool(passed), "message": message, "hard": hard}


def source_checks(profile, inputs, project_id):
    ref = profile.get("reference_batch")
    if not ref:
        return [
            _check(
                "trusted_reference_batch", False, "该参考任务没有冻结可信来源清单；只能按样本或部分结果解释。"
            )
        ]
    if (
        not isinstance(ref, dict)
        or ref.get("project_id") != project_id
        or ref.get("case_id") != profile.get("case_id")
        or ref.get("schema_version") != 1
        or ref.get("checksum") != digest({k: v for k, v in ref.items() if k != "checksum"})
    ):
        return [
            _check("trusted_reference_integrity", False, "冻结参考清单的项目、用例或校验摘要不匹配。", True)
        ]
    if ref.get("state") != "registered":
        msg = (
            "本项目和共享范围均未登记该用例的参考批次。"
            if ref.get("state") == "unregistered"
            else "部署参考清单无效，请管理员修复后重新受理。"
        )
        return [_check("trusted_reference_batch", False, msg)]
    entry = ref.get("entry")
    try:
        canonical = signed_entry({k: v for k, v in entry.items() if k != "digest"})
    except (AttributeError, ValueError, TypeError):
        canonical = None
    if (
        not canonical
        or canonical != entry
        or entry["project_id"] not in (project_id, None)
        or entry["case_id"] != profile["case_id"]
    ):
        return [_check("trusted_reference_integrity", False, "冻结参考批次条目校验失败。", True)]
    allowed = {(src["sha256"], src["source_version"]): src for src in entry["sources"]}
    consumed = {}
    extras = []
    for asset in inputs:
        if asset.get("project_id") != project_id:
            return [_check("trusted_reference_integrity", False, "参考输入存在其他项目资料。", True)]
        key = (asset["checksum"], asset["source_version"])
        if key in allowed and key not in consumed:
            consumed[key] = asset
        else:
            extras.append(asset)
    missing = [k for k in allowed if k not in consumed]
    valid_extra = False
    if entry["supplement"] == "before_after_binding" and not missing and len(extras) == 1:
        expected = {
            src["role"]: consumed[key]["id"]
            for key, src in allowed.items()
            if src["role"] in {"before", "after"}
        }
        binding = extras[0]
        rows = binding.get("content")
        valid_extra = (
            binding.get("modality") == "structured"
            and binding["checksum"] not in {sha for sha, _ in allowed}
            and isinstance(rows, list)
            and len(rows) == 2
            and all(
                isinstance(r, dict)
                and set(r) == {"kind", "target_source_id", "side"}
                and r["kind"] == "document_binding"
                and isinstance(r["side"], str)
                and r["side"] in expected
                and expected[r["side"]] == r["target_source_id"]
                for r in rows
            )
            and {r["side"] for r in rows} == {"before", "after"}
        )
    mismatches = []
    if missing:
        mismatches.append("缺少规定版本/摘要：" + ", ".join(h[:12] + "@" + v for h, v in missing[:4]))
    if entry["supplement"] == "before_after_binding" and not valid_extra:
        mismatches.append("缺少或不符合已登记前后原件顺序的唯一版本绑定")
    elif extras and not valid_extra:
        mismatches.append(f"存在{len(extras)}份未登记或重复的额外资料")
    return [
        _check(
            "trusted_reference_sources",
            not mismatches,
            "；".join(mismatches)
            or f"已核对参考批次 {entry['version']} 的全部{len(consumed)}份原件摘要和版本。",
        )
    ]


def review_checks(profile, inputs, products, records, project_id):
    checks = source_checks(profile, inputs, project_id)
    if not all(c["passed"] for c in checks):
        return checks
    entry = profile["reference_batch"]["entry"]
    assets = {a["checksum"]: a["id"] for a in inputs}
    nrecords = Counter(r["source_id"] for r in records)
    for cond in entry["conditions"]:
        kind = cond["kind"]
        if kind == "record_count":
            actual = nrecords[assets[cond["sha256"]]]
            expected = cond["count"]
            ok = actual == expected
            note = f"规定原件应解析{expected}条，实际{actual}条。"
        elif kind == "product_count":
            rows = products.get(cond["product"])
            actual = len(rows) if isinstance(rows, (list, dict)) else 0
            expected = cond["count"]
            ok = actual == expected
            note = f"规定产品{cond['product']}应有{expected}项，实际{actual}项。"
        else:
            rows = products.get(cond["product"])
            expected = {str(v) for v in cond["values"]}
            values = (
                [r.get(cond["field"]) if isinstance(r, dict) else None for r in rows]
                if isinstance(rows, list)
                else []
            )
            seen = {str(v) for v in values if type(v) in (int, str)}
            ok = bool(values) and seen == expected and all(type(v) in (int, str) for v in values)
            note = f"规定产品{cond['product']}.{cond['field']}缺少{sorted(expected - seen)[:8]}，额外{sorted(seen - expected)[:8]}。"
        checks.append(_check("trusted_reference_" + kind, ok, note))
    return checks
