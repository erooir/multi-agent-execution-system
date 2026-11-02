"""Prepare a local source/static bundle without runtime data or implicit downloads."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

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
BUILD_SCRIPTS = {
    "scripts/Containerfile",
    "scripts/dm_database.py",
    "scripts/dm_business_migration.py",
    "scripts/register_reference_batches.py",
    "scripts/verify_case_batch.py",
}
CASE_EXTRAS = {
    "dmpython",
    "dmsqlalchemy",
    "faster-whisper",
    "ctranslate2",
    "sentencepiece",
    "opencv-python-headless",
}


def public_frontend(root):
    """Only reviewed Vite outputs may enter a package; never copy arbitrary dist/ downloads or maps."""
    dist = root / "frontend/dist"
    if not dist.exists():
        return {}
    if dist.is_symlink() or not dist.resolve().is_relative_to(root.resolve()):
        raise ValueError("前端构建不能引用外部目录")
    files = {}
    for path in sorted(dist.rglob("*")):
        name = path.relative_to(dist).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(dist.resolve()):
            raise ValueError("前端构建目录不能含外部文件引用")
        if not path.is_file():
            continue
        if name != "index.html" and not re.fullmatch(
            r"assets/[\w.-]+-[A-Za-z0-9_-]{8,}\.(js|css)", name, re.ASCII
        ):
            raise ValueError("前端构建含未列入发布范围的文件；使用洁净构建目录")
        data = path.read_bytes()
        if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
            raise ValueError("前端构建可能含凭据")
        if path.suffix in {".html", ".css"} and re.search(
            rb"(?:src\s*=\s*[\"']https?://|@import\s+[\"']https?://|url\s*\(\s*[\"']?https?://)",
            data,
            re.IGNORECASE,
        ):
            raise ValueError("前端构建依赖外部资源")
        files["frontend/dist/" + name] = data
    return files


def locked_wheels(root, wheelhouse, target_arch=None):
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ValueError("需要实际 wheelhouse 目录")
    lock = tomllib.loads(subprocess.check_output(["git", "show", ":uv.lock"], cwd=root).decode("utf-8"))
    allowed = {
        Path(unquote(urlsplit(w["url"]).path)).name: w["hash"]
        for pkg in lock["package"]
        for w in pkg.get("wheels", [])
    }
    result = []
    for path in sorted(wheelhouse.glob("*.whl")):
        if path.is_symlink() or path.name not in allowed:
            raise ValueError("wheel 未在已核验的项目锁文件内")
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if allowed[path.name] != "sha256:" + sha:
            raise ValueError("wheel 校验和与项目锁文件不符")
        tags = parse_wheel_filename(path.name)[3]
        if target_arch and not any(
            t.platform == "any" or ("linux" in t.platform and t.platform.endswith("_" + target_arch))
            for t in tags
        ):
            raise ValueError("wheel 平台不匹配所选 Linux 容器架构")
        result.append((path, sha))
    if not result:
        raise ValueError("wheelhouse 为空")
    return result


def clean_requirements(path, wheels):
    """Keep only hashed pins, not machine paths, comments, indexes, credentials or direct URLs."""
    items = []
    selected = {}
    hashed = {}
    current = None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        token = body.removesuffix("\\").strip()
        if match := re.fullmatch(r"--hash=sha256:([a-f0-9]{64})", token):
            if current is None:
                raise ValueError("requirements 含无所属包的 hash")
            hashed[current].add(match[1])
        else:
            try:
                req = Requirement(token)
            except InvalidRequirement:
                raise ValueError("requirements 含非锁定依赖行，拒绝打包") from None
            specs = list(req.specifier)
            if (
                req.url
                or req.extras
                or len(specs) != 1
                or specs[0].operator != "=="
                or "*" in specs[0].version
            ):
                raise ValueError("requirements 仅允许带固定版本及 SHA-256 的包")
            current = (canonicalize_name(req.name), specs[0].version)
            selected[current] = True
            hashed.setdefault(current, set())
        items.append(body)
    if not CASE_EXTRAS <= {p[0] for p in selected} or not all(hashed.values()):
        raise ValueError("容器清单缺少必要的用例/DM依赖或哈希")
    wheel_packages = set()
    for wheel, sha in wheels:
        name, version, _, _ = parse_wheel_filename(wheel.name)
        if sha not in hashed.get((name, str(version)), set()):
            raise ValueError("wheel 与导出的 requirements 不匹配")
        wheel_packages.add(name)
    if not CASE_EXTRAS <= wheel_packages:
        raise ValueError("wheelhouse 缺少 DM/用例的可安装包")
    encoded = ("\n".join(items) + "\n").encode("utf-8")
    if any(re.search(pattern, encoded) for pattern in SECRET_PATTERNS):
        raise ValueError("requirements 含疑似凭据")
    return encoded


def create_container_context(root, output, wheelhouse, requirements, target):
    """Export a clean build context from reviewed index paths and the local frontend build."""
    root, output = root.resolve(), output.resolve()
    release = (root / ".local/release").resolve()
    if output == release or not output.is_relative_to(release) or output.exists():
        raise ValueError("仅可写入 .local/release 下不存在的新容器构建目录")
    targets = {"linux-x86_64-python3.12": "x86_64", "linux-aarch64-python3.12": "aarch64"}
    if target not in targets:
        raise ValueError("容器目标须显式指定 Linux 架构和 Python 3.12")
    wheels = locked_wheels(root, wheelhouse, targets[target])
    reqs = clean_requirements(requirements, wheels)
    names = (
        subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=root).decode("utf8").split("\0")
    )
    source = {
        name
        for name in names
        if name in BUILD_SCRIPTS
        or name == "backend/__init__.py"
        or name.startswith("backend/app/")
        and name.endswith(".py")
        or name.startswith("skills/")
        and name.endswith(".md")
    }
    required = BUILD_SCRIPTS | {
        "backend/app/main.py",
        "backend/app/casework/engine.py",
        "backend/app/casework/reference_batches.py",
        "backend/app/platform/dameng.py",
        "backend/app/casework/provider_versions.py",
        "backend/__init__.py",
    }
    if not required <= source:
        raise ValueError("Git 索引缺少生产程序或工具；先提交/暂存全部已审核代码")
    frontend = public_frontend(root)
    if "frontend/dist/index.html" not in frontend or not any(n.endswith(".js") for n in frontend):
        raise ValueError("请先构建受控的前端静态资源")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / "context"
        staging.mkdir()
        for name in sorted(source):
            validate_public_path(name)
            data = subprocess.check_output(["git", "show", ":" + name], cwd=root)
            if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
                raise ValueError("生产源码含疑似凭据")
            dest = staging / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for name, data in frontend.items():
            dest = staging / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (staging / "requirements-offline.txt").write_bytes(reqs)
        (staging / "wheelhouse").mkdir()
        for path, sha in wheels:
            dest = staging / "wheelhouse" / path.name
            shutil.copyfile(path, dest)
            with dest.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                    raise ValueError("复制期间 wheel 发生变化")
        staging.rename(output)
    return {"context": str(output), "target": target, "wheels": len(wheels), "offline_acceptance": "NOT_RUN"}


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
        ["git", "ls-files", "--cached", "-z"],
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
        content = subprocess.check_output(["git", "show", ":" + name], cwd=root)
        if any(re.search(pattern, content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"可能含凭据，拒绝打包：{name}")
        files["source/" + name] = content
    frontend = public_frontend(root)
    files.update({"source/" + name: data for name, data in frontend.items()})
    has_frontend = "frontend/dist/index.html" in frontend
    wheels = []
    if wheelhouse:
        if wheelhouse.resolve().is_relative_to((root / ".local" / "new-platform-handoff-20261017").resolve()):
            raise ValueError("交接材料不能作为依赖来源")
        for wheel, sha in locked_wheels(root, wheelhouse):
            data = wheel.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError("读取期间 wheel 发生变化")
            files["wheelhouse/" + wheel.name] = data
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
    parser.add_argument(
        "--container-context",
        type=Path,
        help="生成仅含审核过的运行代码、静态资源与哈希锁定 wheel 的 Linux 构建目录",
    )
    parser.add_argument("--requirements", type=Path, help="uv export --frozen --all-extras 输出的带哈希清单")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if args.container_context:
        if args.output or not args.requirements or not args.wheelhouse:
            parser.error("--container-context 需要 --requirements 和 --wheelhouse，不能同时指定 --output")
        print(
            json.dumps(
                create_container_context(
                    root, args.container_context, args.wheelhouse, args.requirements, args.target
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.requirements:
        parser.error("--requirements 仅用于 --container-context")
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
