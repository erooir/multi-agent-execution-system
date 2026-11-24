"""Publish a project case catalog and immutable extracts of operator-approved public originals."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.casework import editions
from backend.app.casework.edition_files import SAFE_TYPES, validate_attachment
from backend.app.platform import service as s
from backend.app.platform.asset_originals import original_path, valid_filename
from backend.app.platform.db import now


def fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def store(project_id, path, expected=None):
    checksum = fingerprint(path)
    if expected is not None and checksum != expected:
        raise ValueError(f"原件已变化 {path.name}")
    target = original_path(project_id, checksum)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                temporary = Path(tmp.name)
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, tmp)
            if fingerprint(temporary) != checksum:
                raise ValueError(f"复制时文件发生变化 {path.name}")
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    elif fingerprint(target) != checksum:
        raise ValueError("原件库指纹不符")
    return {"filename": path.name, "bytes": target.stat().st_size, "sha256": checksum}


def outline(folder):
    (spec,) = folder.glob("*.md")
    result = {}
    for code, title in re.findall(
        r"^### ([BGT]\d{2})｜(.+)$", spec.read_text(encoding="utf-8-sig"), re.MULTILINE
    ):
        files = sorted(p.name for p in (folder / "材料" / code).iterdir() if p.is_file())
        if not files or code in result:
            raise ValueError(f"清单编号或材料错误 {code}")
        result[code] = {
            "id": code,
            "title": title.strip(),
            "files": files,
            "scope": "",
            "deliverables": "",
            "goal": "",
        }
    if not result:
        raise ValueError("材料清单为空")
    return result


def prepare(pid, case, folder, pack_file, attachments_dir):
    pack = json.loads(pack_file.read_text(encoding="utf-8-sig"))
    if case["title"].replace(" ", "") != pack["title"].replace(" ", "") or case["id"] != pack["case_id"]:
        raise ValueError("新包编号或标题与清单不符 " + case["id"])
    by_file = {v["path"]: v["sha256"] for v in pack["sources"]}
    if set(by_file) != set(case["files"]):
        raise ValueError("证据包没有覆盖当前用例的全部原件 " + case["id"])
    rows = pack["records"]
    if (
        not rows
        or len({r["id"] for r in rows}) != len(rows)
        or any(
            r["source"] not in by_file
            or not all(isinstance(r[k], str) and r[k].strip() for k in ("id", "text", "locator"))
            for r in rows
        )
    ):
        raise ValueError("证据编号或原件位置无效 " + case["id"])
    attachments = []
    for name in pack.get("attachments", []):
        if (
            not valid_filename(name)
            or Path(name).suffix.lower() not in SAFE_TYPES
            or name in [a["name"] for a in attachments]
        ):
            raise ValueError(f"不支持的交付附件 {name}")
        validate_attachment(attachments_dir / name)
        meta = store(pid, attachments_dir / name)
        attachments.append({"name": name, "sha256": meta["sha256"], "bytes": meta["bytes"]})
    if sum(a["bytes"] for a in attachments) > 80_000_000:
        raise ValueError("交付附件总量过大")
    assets = []
    for i, name in enumerate(case["files"]):
        if not valid_filename(name):
            raise ValueError("原始文件名无效")
        original = store(pid, folder / "材料" / case["id"] / name, by_file[name])
        content = {
            "case_id": case["id"],
            "records": [{k: r[k] for k in ("id", "locator", "text")} for r in rows if r["source"] == name],
        }
        if i == 0:
            for k, default in (("products", {}), ("limitations", []), ("unmet", [])):
                content[k] = pack.get(k, default)
            content["attachments"] = attachments
        if len(json.dumps(content, ensure_ascii=False).encode()) > 2_000_000:
            raise ValueError("请将大型逐行结果保存为附件，证据目录上限 2 MB")
        checksum = s.digest(content)
        asset_id = s.digest([pid, case["id"], name, original["sha256"], checksum])[:32]
        assets.append(
            {
                "id": asset_id,
                "project_id": pid,
                "name": name,
                "modality": "structured",
                "media_type": "application/json",
                "content": content,
                "source_id": "published:" + case["id"] + ":" + name,
                "source_version": original["sha256"],
                "provenance": "新版案例公开原件；处理摘录保留页、行或媒体定位。",
                "classification": "public",
                "status": "active",
                "case_library_role": "primary",
                "received_at": now(),
                "retention_days": 365,
                "expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
                "checksum": checksum,
                "bytes": len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()),
                "original_file": original,
            }
        )
    profile = {
        **case,
        **{k: pack[k] for k in ("goal", "scope", "deliverables", "completion")},
        "input_refs": [a["id"] for a in assets],
        "checksums": {a["id"]: a["checksum"] for a in assets},
    }
    return profile, assets


def archive_replaced_assets(tx, pid, catalog):
    current = {ref for case in catalog.values() for ref in case.get("input_refs", [])}
    for asset in tx.list("data_assets", pid):
        name = asset["name"]
        source = (asset.get("source_id") or "").split(":", 2)
        if len(source) != 3 or source[0] != "published" or source[2] != name:
            continue
        published_id = s.digest([pid, source[1], name, asset.get("source_version"), asset.get("checksum")])[
            :32
        ]
        if asset["id"] != published_id:
            continue
        role = "primary" if published_id in current else "supplementary"
        if asset.get("case_library_role") != role:
            tx.put("data_assets", {**asset, "case_library_role": role})


def publish(pid, user, directory, updates):
    if user.get("role") != "admin":
        raise ValueError("仅管理员可以发布原件目录")
    with s.repo.transaction() as tx:
        s.project_access(pid, user, True, tx)
        project = tx.get("projects", pid, for_update=True)
        previous = (project.get("scenario_edition") or {}).get("cases", {})
        catalog = {
            k: previous[k]
            if k in previous
            and previous[k]["title"] == case["title"]
            and previous[k]["files"] == case["files"]
            else case
            for k, case in directory.items()
        }
        for profile, assets in updates:
            for asset in assets:
                stored = tx.get("data_assets", asset["id"])
                if stored:
                    s.verify_frozen_asset(asset, stored)
                    if stored.get("original_file") != asset["original_file"]:
                        raise ValueError("原件登记冲突")
                else:
                    tx.put("data_assets", asset)
            editions.provision(tx, pid, profile)
            catalog[profile["id"]] = profile
        archive_replaced_assets(tx, pid, catalog)
        edition = {"format": editions.EDITION, "cases": catalog}
        if project.get("scenario_edition") != edition:
            tx.put("projects", {**project, "scenario_edition": edition})
            s.audit(
                tx,
                pid,
                "casework.edition_published",
                pid,
                user,
                {"cases": len(catalog), "updated": [p["id"] for p, _ in updates]},
            )


if __name__ == "__main__":
    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--project", required=True, action="append")
    parser.add_argument("--api", required=True)
    parser.add_argument("--login-file", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}", args.api):
        raise ValueError("仅支持本机部署")
    cases = outline(args.package)
    with httpx.Client(base_url=args.api, trust_env=False, timeout=120) as client:
        login = json.loads(args.login_file.read_text(encoding="utf-8-sig"))
        while True:
            response = client.post("/api/auth/login", json={k: login[k] for k in ("username", "password")})
            if response.status_code != 429:
                response.raise_for_status()
                break
            time.sleep(max(1, int(response.headers.get("retry-after", 60))))
        try:
            user = client.get("/api/auth/me").raise_for_status().json()
            if user.get("role") != "admin":
                raise ValueError("需要项目目录发布权限")
            for pid in args.project:
                s.project_access(pid, user, True)
                updates = []
                for code in args.case:
                    group = {"B": "archive/artifacts", "T": "tech", "G": "geo"}[code[0]]
                    updates.append(
                        prepare(
                            pid,
                            cases[code],
                            args.package,
                            args.prepared / (code + ".json"),
                            args.prepared.parent / group / code,
                        )
                    )
                publish(pid, user, cases, updates)
        finally:
            client.post("/api/auth/logout")
    print("Published", args.case, "to", args.project)
