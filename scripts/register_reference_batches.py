"""Register verified original files on the deployment host, outside the upload/API boundary."""

import argparse
import csv
import hashlib
import io
import json
import os
import uuid
from datetime import date
from pathlib import Path

from backend.app.casework.geo_common import day
from backend.app.casework.reference_batches import ROOT, digest, read_entries, registry_path, signed_entry
from scripts.verify_case_batch import sources


def _allowed_directory(value):
    if any(p.is_symlink() or p.is_junction() for p in (value, *value.parents)):
        raise ValueError("可信清单输入输出目录不能为符号链接或目录联接")
    path = value.resolve()
    uploads = (ROOT / ".local/platform-assets").resolve()
    if path.is_relative_to(uploads):
        raise ValueError("不能把平台上传资产目录用作管理员的可信来源或注册表")
    return path


def registrations(materials, project_id, version):
    rows, mapping = sources(_allowed_directory(materials))
    entries = []
    for case, names in sorted(mapping.items()):
        required = []
        conditions = []
        for index, name in enumerate(names):
            path, sha, _, _ = rows[name]
            required.append(
                {
                    "sha256": sha,
                    "source_version": "sha256:" + sha,
                    "role": ("before", "after")[index] if case == "B06" else "source",
                }
            )
            if case == "G07":
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError("登记前原件发生变化")
                records = list(csv.DictReader(io.StringIO(raw.decode("cp1252"))))
                # Derive the coverage requirements from the *verified original*, not from an agent's product.
                periods = []
                for record in records:
                    if "Samp_No" not in record or "SampleDate_txt" not in record:
                        raise ValueError("G07参考原件缺少采样编号或日期列")
                    dt = day(record["SampleDate_txt"])
                    if dt and date(2023, 2, 3) <= dt <= date(2023, 5, 31):
                        periods.append(dt.isoformat())
                if not periods:
                    raise ValueError("G07参考原件没有规定窗口内的日期")
                conditions.extend(
                    [
                        {"kind": "record_count", "sha256": sha, "count": len(records)},
                        {"kind": "product_count", "product": "results", "count": len(periods)},
                        {
                            "kind": "product_values",
                            "product": "results",
                            "field": "date",
                            "values": sorted(set(periods)),
                        },
                    ]
                )
        if case == "G03":
            conditions.append(
                {
                    "kind": "product_values",
                    "product": "annual_series",
                    "field": "year",
                    "values": list(range(1979, 2024)),
                }
            )
        entries.append(
            signed_entry(
                {
                    "project_id": project_id,
                    "case_id": case,
                    "version": version,
                    "sources": required,
                    "conditions": conditions,
                    "supplement": "before_after_binding" if case == "B06" else "none",
                }
            )
        )
    return entries


def custom_registration(manifest, project_id, version):
    manifest = _allowed_directory(manifest)
    if not manifest.is_file() or manifest.stat().st_size > 2_000_000:
        raise ValueError("管理员批次说明缺失或过大")
    declaration = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(declaration, dict)
        or set(declaration) - {"schema_version", "case_id", "sources", "conditions", "supplement"}
        or type(declaration.get("schema_version")) is not int
        or declaration["schema_version"] != 1
        or not isinstance(declaration.get("sources"), list)
        or not 1 <= len(declaration["sources"]) <= 50
    ):
        raise ValueError("无效的管理员批次说明")
    verified = []
    for source in declaration["sources"]:
        if (
            not isinstance(source, dict)
            or set(source) - {"path", "sha256", "source_version", "role"}
            or not isinstance(source.get("path"), str)
        ):
            raise ValueError("原件说明缺少本机相对路径或含未知字段")
        relative = Path(source["path"])
        if relative.anchor or ".." in relative.parts:
            raise ValueError("原件必须位于管理员说明所在目录内")
        original = _allowed_directory(manifest.parent / relative)
        if not original.is_relative_to(manifest.parent) or not original.is_file() or original == manifest:
            raise ValueError("管理员指定的独立原件不存在")
        with original.open("rb") as fh:
            actual = hashlib.file_digest(fh, "sha256").hexdigest()
        if actual != source.get("sha256"):
            raise ValueError("原件与管理员声明的SHA-256不一致；未登记任何资料")
        verified.append({key: value for key, value in source.items() if key != "path"})
    return [
        signed_entry(
            {
                **{k: v for k, v in declaration.items() if k not in {"schema_version", "sources"}},
                "project_id": project_id,
                "version": version,
                "sources": verified,
            }
        )
    ]


def install(path, entries, replace):
    path = _allowed_directory(path)
    if path.is_relative_to(ROOT) and not path.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("仓库内清单只能写入忽略的.local目录")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    # O_EXCL: two operator invocations cannot silently overwrite one another.
    lock.open("x", encoding="utf-8").close()
    try:
        old = read_entries(path)
        known = {(e["case_id"], e["project_id"]): e for e in old}
        history = _allowed_directory(path.parent / "history")
        pending = {(e["project_id"], e["case_id"], e["version"]): e for e in entries}
        if history.exists():
            for archived in history.glob("*.json"):
                archival = _allowed_directory(archived)
                if hashlib.sha256(archival.read_bytes()).hexdigest() != archival.stem:
                    raise ValueError("历史清单摘要不一致")
                for record in read_entries(archival):
                    key = record["project_id"], record["case_id"], record["version"]
                    if key in pending and pending[key] != record:
                        raise ValueError("历史同版本内容不可更换")
        for entry in entries:
            key = (entry["case_id"], entry["project_id"])
            previous = known.get(key)
            if previous and previous != entry and (previous["version"] == entry["version"] or not replace):
                raise ValueError("已登记的同版本内容不可更换；启用不同版本须显式--replace")
            known[key] = entry
        if sorted(old, key=digest) == sorted(known.values(), key=digest):
            return
        if path.exists():
            previous_bytes = path.read_bytes()
            history.mkdir(exist_ok=True)
            archival = _allowed_directory(history / (hashlib.sha256(previous_bytes).hexdigest() + ".json"))
            if not archival.exists():
                archival.write_bytes(previous_bytes)
        staged = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            staged.write_text(
                json.dumps(
                    {"schema_version": 1, "batches": list(known.values())}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            read_entries(staged)
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--materials", type=Path, help="含11份已供原件与供方下载清单的本机目录")
    source.add_argument(
        "--manifest", type=Path, help="管理员编制的单用例说明；重新读取本机原件校验摘要后登记"
    )
    parser.add_argument("--scope", required=True, choices=["public", "project"])
    parser.add_argument("--project-id")
    parser.add_argument("--version", required=True)
    parser.add_argument("--replace", action="store_true", help="以新版本替换同范围活跃清单，保留原清单历史")
    parser.add_argument("--output", type=Path, default=registry_path())
    args = parser.parse_args()
    if (args.scope == "project") != bool(args.project_id):
        parser.error("project范围必须有project-id；public范围不能指定project-id")
    entries = (
        registrations(args.materials, args.project_id, args.version)
        if args.materials
        else custom_registration(args.manifest, args.project_id, args.version)
    )
    install(args.output, entries, args.replace)
    print(
        json.dumps(
            {
                "registered_cases": len(entries),
                "verified_originals": sum(len(e["sources"]) for e in entries),
                "version": args.version,
                "scope": args.scope,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
