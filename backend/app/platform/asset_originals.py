"""Verified, download-only attachments registered by the deployment's offline importer."""

import hashlib
import os
import re
import stat
import unicodedata
from pathlib import PureWindowsPath

from . import service as s
from .db import ROOT


def original_path(project_id, sha256):
    if (
        not isinstance(project_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}", project_id)
        or project_id.endswith(".")
        or PureWindowsPath(project_id).is_reserved()
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", sha256)
    ):
        s.fail("invalid_storage_ref", "原件存储标识无效", 409)
    local = ROOT / ".local"
    base = local / "knowledge-originals"
    project = base / project_id
    path = project / sha256
    if any(part.is_symlink() or part.is_junction() for part in (local, base, project, path)):
        s.fail("invalid_storage_ref", "原件存储路径不能包含链接", 409)
    return path


def _validate_metadata(metadata):
    if not isinstance(metadata, dict) or set(metadata) != {"filename", "sha256", "bytes"}:
        s.fail("invalid_original", "原件信息不完整", 409)
    name = metadata["filename"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 255
        or name.endswith((" ", "."))
        or PureWindowsPath(name).is_reserved()
        or any(c in '<>:"/\\|?*' or unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in name)
        or type(metadata["bytes"]) is not int
        or metadata["bytes"] < 0
    ):
        s.fail("invalid_original", "原件文件名或大小无效", 409)


def _verified_file(project_id, metadata):
    _validate_metadata(metadata)
    path = original_path(project_id, metadata["sha256"])
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            s.fail("invalid_storage_ref", "原件必须是普通文件", 409)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        stream = os.fdopen(os.open(path, flags | getattr(os, "O_NONBLOCK", 0)), "rb")
        try:
            opened = os.fstat(stream.fileno())
            if (
                not os.path.samestat(info, opened)
                or opened.st_size != metadata["bytes"]
                or hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]
            ):
                s.fail("original_changed", "原件完整性校验失败", 409)
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise
    except FileNotFoundError:
        s.fail("original_not_found", "原件文件不存在", 404)
    except OSError:
        s.fail("original_unreadable", "原件文件无法读取", 409)


def _catalog_asset(asset_id, user, repository, *, write=False):
    asset = s.required("data_assets", asset_id, user, write=write, repository=repository)
    if asset.get("task_case_id"):
        s.fail("task_scoped_source", "任务专用资料须通过所属任务访问", 403)
    s.usable_asset(asset)
    return asset


def attach_original(asset_id, metadata, user, repository=None):
    """Bind one immutable attachment to a catalog asset after verifying the staged bytes."""
    repository = repository or s.repo
    asset = _catalog_asset(asset_id, user, repository, write=True)
    with _verified_file(asset["project_id"], metadata):
        pass
    with repository.transaction() as tx:
        asset = _catalog_asset(asset_id, user, tx, write=True)
        existing = asset.get("original_file")
        if existing is not None:
            if existing != metadata:
                s.fail("original_conflict", "此资料已登记其他原件", 409)
            return s.safe_asset(asset)
        updated = tx.put("data_assets", {**asset, "original_file": dict(metadata)})
        s.audit(tx, asset["project_id"], "data.original_attached", asset_id, user)
        return s.safe_asset(updated)


def open_original(asset_id, user, repository):
    asset = _catalog_asset(asset_id, user, repository)
    metadata = asset.get("original_file")
    if metadata is None:
        s.fail("original_not_found", "此资料未登记可下载的原件", 404)
    stream = _verified_file(asset["project_id"], metadata)
    try:
        # Hashing a large original can overlap with revocation or a membership change.
        current = _catalog_asset(asset_id, user, repository)
        if current.get("original_file") != metadata:
            s.fail("original_changed", "原件登记已变化", 409)
        return stream, metadata
    except BaseException:
        stream.close()
        raise
