"""Prepare a local source/static bundle without runtime data or implicit downloads."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".gitattributes",
}
ROOT_DIRECTORIES = {"backend", "frontend", "scripts", "docs", "skills", "tools", ".github", ".githooks"}
DENIED_PARTS = {
    ".local",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "uploads",
    "reports",
    "data",
}
DENIED_SUFFIXES = {
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".pdf",
    ".xlsx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".bundle",
    ".log",
    ".pyc",
}
SECRET_PATTERNS = (
    rb"\bsk-[A-Za-z0-9_-]{20,}\b",
    rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    rb"\bgithub_pat_[A-Za-z0-9_]{30,}\b",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
)


def validate_public_path(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or any(part in DENIED_PARTS for part in path.parts)
        or path.suffix.lower() in DENIED_SUFFIXES
        or path.name.startswith(".env")
        or (len(path.parts) == 1 and path.name not in ROOT_FILES)
        or (len(path.parts) > 1 and path.parts[0] not in ROOT_DIRECTORIES)
    ):
        raise ValueError(f"源文件不在可发布清单：{name}")


def dependency_inventory() -> list[dict]:
    result = []
    for dist in importlib.metadata.distributions():
        license_value = (
            dist.metadata.get("License-Expression") or dist.metadata.get("License") or "UNVERIFIED"
        )
        if len(license_value) > 300:
            license_value = "SEE_DISTRIBUTION_LICENSE"
        result.append(
            {
                "name": dist.metadata.get("Name", "unknown"),
                "version": dist.version,
                "declared_license": license_value,
                "license_files": [
                    str(file)
                    for file in dist.files or []
                    if "license" in str(file).lower() or "notice" in str(file).lower()
                ],
            }
        )
    return sorted(result, key=lambda item: item["name"].lower())


def create_package(
    root: Path, output: Path, *, wheelhouse: Path | None = None, target: str = "unconfirmed"
) -> dict:
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to((root / ".local" / "release").resolve()):
        raise ValueError("交付包只写入 .local/release")
    if output.exists():
        raise ValueError("交付包已存在，不覆盖")
    raw = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    ).decode("utf-8")
    files = {}
    for name in sorted(set(filter(None, raw.split("\0")))):
        path = root / name
        if not path.is_file():
            continue
        validate_public_path(name)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("源文件不能是符号链接")
        content = path.read_bytes()
        if any(re.search(pattern, content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"可能含凭据，拒绝打包：{name}")
        files["source/" + name] = content
    dist = root / "frontend" / "dist"
    has_frontend = (dist / "index.html").is_file()
    if has_frontend:
        for path in sorted(dist.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(dist.resolve()):
                raise ValueError("前端构建目录不能含外部文件引用")
            if path.is_file():
                content = path.read_bytes()
                if any(re.search(pattern, content) for pattern in SECRET_PATTERNS):
                    raise ValueError("前端构建可能含凭据")
                if path.suffix in {".html", ".css"} and re.search(
                    rb"(?:src\s*=\s*[\"']https?://|@import\s+[\"']https?://|url\s*\(\s*[\"']?https?://)",
                    content,
                    re.IGNORECASE,
                ):
                    raise ValueError("前端构建依赖外部资源")
                files["source/frontend/dist/" + path.relative_to(dist).as_posix()] = content
    wheels = []
    if wheelhouse:
        if wheelhouse.resolve().is_relative_to((root / ".local" / "new-platform-handoff-20261017").resolve()):
            raise ValueError("交接材料不能作为依赖来源")
        for wheel in sorted(wheelhouse.glob("*.whl")):
            if wheel.is_symlink():
                raise ValueError("wheel 不能是符号链接")
            files["wheelhouse/" + wheel.name] = wheel.read_bytes()
            wheels.append(wheel.name)
    inventory = dependency_inventory()
    files["dependency-inventory.json"] = json.dumps(inventory, ensure_ascii=False, indent=2).encode()
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()
        ),
        "target": target,
        "build_host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "frontend_built": has_frontend,
        "wheels": wheels,
        "offline_acceptance": "NOT_RUN",
        "redistribution_review": "REQUIRED",
        "limitations": [
            "源码/静态文件包不是完成的离线验收；wheel 闭包及目标平台兼容性须在目标环境验证。",
            "包中不含运行数据、历史预算、密钥、上游模型权重或私有原文；历史预算必须私下完整迁移。",
            "许可证字段来自安装分发元数据，不能替代固定版本、权重、编解码器和再分发审查。",
        ],
        "files": [
            {"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "file_count": len(files),
        "offline_acceptance": "NOT_RUN",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--target", default="unconfirmed")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print(
        json.dumps(
            create_package(
                root,
                args.output or root / ".local" / "release" / f"hangzhi-v2-{stamp}.zip",
                wheelhouse=args.wheelhouse,
                target=args.target,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
