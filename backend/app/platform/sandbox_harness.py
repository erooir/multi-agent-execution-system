"""Container-only entrypoint. Never import or run this module on the application host."""

import base64
import json
import resource
import stat
import subprocess
import sys
import time
from pathlib import Path

OUTPUT_LIMIT = 4_000_000
LOG_LIMIT = 12_000
SUFFIXES = {".json", ".csv", ".txt", ".md", ".png"}


def run():
    if not Path("/inputs/manifest.json").is_file() or not Path("/outputs").is_dir():
        raise RuntimeError("sandbox_container_required")
    cgroup = Path("/sys/fs/cgroup")
    limits = {name: (cgroup / name).read_text().strip() for name in ("memory.max", "pids.max", "cpu.max")}
    quota, period = map(int, limits["cpu.max"].split())
    if int(limits["memory.max"]) != 268435456 or int(limits["pids.max"]) != 64 or not 0 < quota / period <= 1:
        raise RuntimeError("sandbox_kernel_limits_unavailable")
    resource.setrlimit(resource.RLIMIT_FSIZE, (8_000_000, 8_000_000))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Output is a bounded tmpfs. Logs cannot consume host disk or unbounded memory.
    with open("/tmp/stdout", "wb") as out, open("/tmp/stderr", "wb") as err:
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "/inputs/script.py"],
            cwd="/outputs",
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"},
        )
        returncode = process.wait()
    files, size = [], 0
    error = None
    for path in sorted(Path("/outputs").rglob("*")):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in SUFFIXES or info.st_nlink != 1:
            error = "sandbox_output_type_denied"
            break
        size += info.st_size
        if len(files) >= 16 or size > OUTPUT_LIMIT:
            error = "sandbox_output_limit"
            break
        files.append(
            {
                "name": path.relative_to("/outputs").as_posix(),
                "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    logs = {}
    for name in ("stdout", "stderr"):
        path = Path("/tmp") / name
        with path.open("rb") as stream:
            logs[name] = stream.read(LOG_LIMIT).decode("utf-8", errors="replace")
        logs[name + "_truncated"] = path.stat().st_size > LOG_LIMIT
    return {
        "protocol": "hangzhi-sandbox-v1",
        "returncode": returncode,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "kernel_limits": limits,
        **logs,
        "error_code": error,
        "files": [] if error else files,
    }


if __name__ == "__main__":
    try:
        result = run()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {
            "protocol": "hangzhi-sandbox-v1",
            "error_code": "sandbox_harness_failed",
            "error_type": type(exc).__name__,
            "files": [],
        }
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    # Host checks the container process status as well as the untrusted wire document.
    sys.exit(0 if result.get("returncode") == 0 and not result.get("error_code") else 42)
