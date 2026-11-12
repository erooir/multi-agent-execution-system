"""Fail-closed Podman runner. Only per-job inputs are mounted; no host execution fallback."""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath

MAX_WIRE_BYTES = 6_000_000
_lock = threading.Lock()
_SUFFIXES = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
}


def configuration():
    image = os.getenv("HANGZHI_SANDBOX_IMAGE", "")
    distro = os.getenv("HANGZHI_SANDBOX_WSL_DISTRO", "")
    machine = os.getenv("HANGZHI_SANDBOX_MACHINE", "")
    executable = os.getenv("HANGZHI_SANDBOX_PODMAN", "podman")
    enabled = os.getenv("HANGZHI_SANDBOX_ENABLED", "false").lower() == "true"
    image_pinned = bool(re.fullmatch(r"(?:[A-Za-z0-9_./:-]+@)?sha256:[0-9a-f]{64}", image))
    return {
        "enabled": enabled,
        "image": image,
        "image_pinned": image_pinned,
        "distro": distro,
        "machine": machine,
        "executable": executable,
        "ready": enabled
        and image_pinned
        and (bool(shutil.which("wsl")) if distro and not machine else bool(shutil.which(executable))),
        "readiness_basis": "configuration only; actual image, rootless mode and cgroup controllers checked at execution",
        "isolation": "rootless-podman",
        "network": "none",
        "packages": ["Python 3.12 standard library"],
        "limits": {"cpu": 1, "memory_mb": 256, "pids": 64, "timeout_seconds": 60, "output_bytes": 4_000_000},
    }


def _prefix(config):
    if config.get("machine"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", config["machine"]):
            raise ValueError("sandbox_invalid_configuration")
        return [config["executable"], "machine", "ssh", config["machine"], "podman --cgroup-manager=systemd"]
    if config["distro"]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", config["distro"]):
            raise ValueError("sandbox_invalid_configuration")
        return [
            "wsl.exe",
            "-d",
            config["distro"],
            "-u",
            "user",
            "--",
            "env",
            "XDG_RUNTIME_DIR=/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            "podman",
            "--cgroup-manager=systemd",
        ]
    return [config["executable"], "--cgroup-manager=systemd"]


def _engine(prefix, arguments):
    if len(prefix) == 5 and tuple(prefix[1:3]) == ("machine", "ssh"):
        # Machine SSH receives one shell command. Quote every individual argument; no source code,
        # credential or user-selected path is interpolated into shell syntax.
        return [*prefix[:-1], prefix[-1] + " " + shlex.join(arguments)]
    return [*prefix, *arguments]


def _guest_path(path, config):
    value = path.resolve().as_posix()
    if config["distro"] or config.get("machine"):
        if not re.match(r"^[A-Za-z]:/", value):
            raise ValueError("sandbox_path_not_supported")
        return "/mnt/" + value[0].lower() + value[2:]
    return value


@lru_cache(maxsize=8)
def _verify_engine(prefix, image, interval):
    """Fail closed if requested limits cannot be enforced; short-lived cache avoids per-tool overhead."""
    del interval
    try:
        info = subprocess.run(
            _engine(prefix, ["info", "--format=json"]), capture_output=True, timeout=20, check=True
        )
        payload = json.loads(info.stdout)
        host = payload.get("host", {})
        if (
            not host.get("security", {}).get("rootless")
            or host.get("cgroupVersion") != "v2"
            or host.get("cgroupManager") != "systemd"
        ):
            raise ValueError("sandbox_rootless_cgroup_required")
        if not {"cpu", "memory", "pids"}.issubset(set(host.get("cgroupControllers", []))):
            raise ValueError("sandbox_resource_limits_unavailable")
        inspect = subprocess.run(
            _engine(prefix, ["image", "inspect", image]), capture_output=True, timeout=15, check=True
        )
        images = json.loads(inspect.stdout)
        if len(images) != 1 or images[0].get("Os") != "linux":
            raise ValueError("sandbox_image_unavailable")
        probe = subprocess.run(
            _engine(
                prefix,
                [
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--user=65534:65534",
                    "--cpus=1",
                    "--memory=256m",
                    "--memory-swap=256m",
                    "--pids-limit=64",
                    "--cgroupns=private",
                    "--timeout=5",
                    "--http-proxy=false",
                    image,
                    "python",
                    "-I",
                    "-c",
                    "import json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); print(json.dumps({n:(p/n).read_text().strip() for n in ['memory.max','pids.max','cpu.max']}))",
                ],
            ),
            capture_output=True,
            timeout=15,
            check=True,
        )
        limits = json.loads(probe.stdout)
        quota, period = map(int, limits["cpu.max"].split())
        if (
            int(limits["memory.max"]) != 268435456
            or int(limits["pids.max"]) != 64
            or not 0 < quota / period <= 1
        ):
            raise ValueError("sandbox_resource_limits_unavailable")
        return {
            "rootless": True,
            "cgroup_version": "v2",
            "image_id": images[0]["Id"],
            "limits_verified": True,
            "kernel_limits": limits,
        }
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("sandbox_engine_unavailable") from exc


def validate_code(code):
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > 20_000:
        raise ValueError("sandbox_code_limit")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError("sandbox_syntax_invalid") from exc
    forbidden = {
        "subprocess",
        "socket",
        "ctypes",
        "multiprocessing",
        "pty",
        "resource",
        "http",
        "urllib",
        "requests",
        "pip",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(name.split(".")[0] in forbidden for name in names):
                raise ValueError("sandbox_import_denied")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile", "__import__"}
        ):
            raise ValueError("sandbox_dynamic_code_denied")
    # Static checks are usability checks, not the isolation boundary. Podman is mandatory.
    return hashlib.sha256(code.encode()).hexdigest()


def _command(config, name, input_dir, timeout):
    return _engine(
        _prefix(config),
        [
            "run",
            "--name",
            name,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--read-only-tmpfs=false",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            "--cpus=1",
            "--memory=256m",
            "--memory-swap=256m",
            "--pids-limit=64",
            "--timeout=" + str(timeout + 5),
            "--ulimit=nofile=64:64",
            "--ulimit=core=0:0",
            "--ulimit=fsize=8000000:8000000",
            "--ipc=private",
            "--pid=private",
            "--cgroupns=private",
            "--log-driver=none",
            "--http-proxy=false",
            "--mount",
            "type=bind,src=" + _guest_path(input_dir, config) + ",target=/inputs,ro=true",
            "--tmpfs=/outputs:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=24m,mode=1777",
            "--workdir=/outputs",
            "--entrypoint=python",
            config["image"],
            "-I",
            "-B",
            "/inputs/_runner.py",
        ],
    )


def _decode_files(result):
    files, total = [], 0
    if not isinstance(result.get("files"), list) or len(result["files"]) > 16:
        raise ValueError("sandbox_output_invalid")
    seen = set()
    for entry in result["files"]:
        name = entry.get("name", "")
        path = PurePosixPath(name)
        if (
            not name
            or len(name) > 160
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or path.suffix.lower() not in _SUFFIXES
            or name in seen
        ):
            raise ValueError("sandbox_output_path_denied")
        seen.add(name)
        data = base64.b64decode(entry["base64"], validate=True)
        total += len(data)
        if total > 4_000_000:
            raise ValueError("sandbox_output_limit")
        preview = None
        preview_truncated = False
        if path.suffix.lower() == ".png":
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.width * image.height > 4_000_000:
                    raise ValueError("sandbox_image_invalid")
                image.verify()
        else:
            text = data.decode("utf-8")
            if path.suffix.lower() == ".json":
                json.loads(
                    text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_json_number"))
                )
            preview = text[:4000]
            preview_truncated = len(text) > 4000
        files.append(
            {
                "name": name,
                "media_type": _SUFFIXES[path.suffix.lower()],
                "bytes": len(data),
                "checksum": hashlib.sha256(data).hexdigest(),
                "data": data,
                "preview": preview,
                "preview_truncated": preview_truncated,
            }
        )
    return files


def run_script(code, sources, *, timeout_seconds=30, checkpoint=lambda: None, work_root=None):
    checksum = validate_code(code)
    config = configuration()
    if not config["ready"]:
        raise ValueError("sandbox_unavailable")
    verification = _verify_engine(tuple(_prefix(config)), config["image"], int(time.monotonic() / 30))
    if not 1 <= timeout_seconds <= 60 or len(sources) > 8:
        raise ValueError("sandbox_input_limit")
    root = Path(
        work_root
        or os.getenv("HANGZHI_SANDBOX_WORK_DIR")
        or Path(__file__).resolve().parents[3] / ".local" / "sandbox"
    )
    root.mkdir(parents=True, exist_ok=True)
    with _lock, tempfile.TemporaryDirectory(prefix="job-", dir=root) as temp:
        input_dir = Path(temp) / "inputs"
        input_dir.mkdir()
        manifest, total = [], 0
        for index, source in enumerate(sources):
            raw = source["data"]
            total += len(raw)
            if len(raw) > 10_000_000 or total > 20_000_000:
                raise ValueError("sandbox_input_limit")
            suffix = {
                "application/json": ".json",
                "text/csv": ".csv",
                "text/plain": ".txt",
                "text/markdown": ".md",
            }.get(source["media_type"].partition(";")[0], ".bin")
            filename = f"source-{index + 1}{suffix}"
            (input_dir / filename).write_bytes(raw)
            manifest.append({key: source[key] for key in ("id", "source_version", "media_type", "checksum")})
            manifest[-1].update(path="/inputs/" + filename, bytes=len(raw))
        (input_dir / "manifest.json").write_text(
            json.dumps({"sources": manifest}, ensure_ascii=False), encoding="utf-8"
        )
        (input_dir / "script.py").write_text(code, encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("sandbox_harness.py"), input_dir / "_runner.py")
        name = "hangzhi-script-" + uuid.uuid4().hex
        command = _command(config, name, input_dir, timeout_seconds)
        result = None
        stdout_path, stderr_path = Path(temp) / "wire.json", Path(temp) / "engine.log"
        started = time.monotonic()
        try:
            checkpoint()
            # A file-backed pipe has a polled cap; --log-driver=none prevents duplicate engine logs.
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    while process.poll() is None:
                        checkpoint()
                        if time.monotonic() - started > timeout_seconds + 10:
                            raise ValueError("sandbox_timeout")
                        if stdout_path.stat().st_size + stderr_path.stat().st_size > MAX_WIRE_BYTES:
                            raise ValueError("sandbox_output_limit")
                        time.sleep(0.1)
                except BaseException:
                    subprocess.run(
                        _engine(_prefix(config), ["rm", "--force", name]),
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                    process.kill()
                    process.wait(timeout=10)
                    raise
            if stdout_path.stat().st_size > MAX_WIRE_BYTES:
                raise ValueError("sandbox_output_limit")
            if process.returncode not in {0, 42}:
                if time.monotonic() - started >= timeout_seconds:
                    raise ValueError("sandbox_timeout")
                raise ValueError("sandbox_container_failed")
            result = json.loads(stdout_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or result.get("protocol") != "hangzhi-sandbox-v1":
                raise ValueError("sandbox_protocol_invalid")
            if result.get("error_code"):
                raise ValueError(result["error_code"])
            if (process.returncode == 0) != (result.get("returncode") == 0):
                raise ValueError("sandbox_exit_status_mismatch")
            files = _decode_files(result)
            checkpoint()
            return {
                "script_checksum": checksum,
                "image": config["image"],
                "status": "succeeded" if result.get("returncode") == 0 else "failed",
                "returncode": result.get("returncode"),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "stdout": str(result.get("stdout", ""))[:12000],
                "stderr": str(result.get("stderr", ""))[:12000],
                "stdout_truncated": result.get("stdout_truncated", False),
                "stderr_truncated": result.get("stderr_truncated", False),
                "outputs": files,
                "isolation": config["isolation"],
                "limits": config["limits"],
                "engine_verification": verification,
                "trust": "untrusted-derived; requires independent checks",
            }
        finally:
            # Always remove the entire process group/container, including failed or cancelled scripts.
            subprocess.run(
                _engine(_prefix(config), ["rm", "--force", name]),
                capture_output=True,
                timeout=15,
                check=False,
            )
