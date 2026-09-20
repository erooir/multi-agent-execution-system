"""Pre-commit safety check: never print secret values or document contents."""

import re
import subprocess
import sys
from pathlib import PurePosixPath

paths = (
    subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"])
    .decode("utf-8")
    .split("\0")
)
denied_suffixes = {".docx", ".doc", ".pptx", ".ppt", ".pdf", ".xlsx", ".db", ".sqlite3", ".pem", ".key"}
patterns = [
    rb"sk-[A-Za-z0-9_-]{20,}",
    rb"gh[pousr]_[A-Za-z0-9]{30,}",
    rb"github_pat_[A-Za-z0-9_]{30,}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
]
failures = []
for name in filter(None, paths):
    path = PurePosixPath(name)
    if (
        path.suffix.lower() in denied_suffixes
        or any(p in {".local", "uploads", "data", "node_modules", ".venv"} for p in path.parts)
        or path.name.startswith(".env")
        and path.name != ".env.example"
    ):
        failures.append((name, "private/runtime file"))
        continue
    content = subprocess.check_output(["git", "show", f":{name}"])
    if any(re.search(pattern, content) for pattern in patterns):
        failures.append((name, "possible credential"))
if failures:
    for name, reason in failures:
        print(f"BLOCKED {name}: {reason}")
    sys.exit(1)
print(
    f"Safety check passed: {len(list(filter(None, paths)))} staged paths; no office reference files or obvious credentials."
)
