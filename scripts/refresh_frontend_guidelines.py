"""Record public design resources and licenses; downloaded Markdown is never executed."""

import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tools" / "frontend-skills"


def download(url):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "hangzhi-design-resources"}), timeout=25
    ) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError("Resource exceeds size limit")
    return content


def main():
    records = []
    for repository, prefix in (
        ("vercel-labs/agent-skills", "agent-skills"),
        ("vercel-labs/web-interface-guidelines", "web-interface-guidelines"),
    ):
        revision = json.loads(download(f"https://api.github.com/repos/{repository}/commits/main"))["sha"]
        record = {"repository": f"https://github.com/{repository}", "revision": revision}
        if prefix == "web-interface-guidelines":
            license_text = download(f"https://raw.githubusercontent.com/{repository}/{revision}/LICENSE")
            (ROOT / f"LICENSE-{prefix}").write_bytes(license_text)
            record["license_sha256"] = hashlib.sha256(license_text).hexdigest()
        else:
            record.update(
                license="MIT",
                license_evidence="skills/react-best-practices/SKILL.md frontmatter; upstream has no root LICENSE",
            )
        records.append(record)
        if prefix == "agent-skills":
            for name in ("react-best-practices", "web-design-guidelines"):
                expected = download(
                    f"https://raw.githubusercontent.com/{repository}/{revision}/skills/{name}/SKILL.md"
                )
                if expected != (ROOT / name / "SKILL.md").read_bytes():
                    raise ValueError("Installed skill changed upstream; reinstall the pinned revision")
        else:
            rules = download(f"https://raw.githubusercontent.com/{repository}/{revision}/command.md")
            (ROOT / "web-design-guidelines" / "command.md").write_bytes(rules)
    (ROOT / "PROVENANCE.json").write_text(
        json.dumps(
            {
                "retrieved_at": datetime.now(UTC).isoformat(),
                "installer": "Codex skill-installer",
                "resources": records,
                "files": {
                    str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in ROOT.rglob("*")
                    if path.is_file() and path.name != "PROVENANCE.json"
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("Design resources, pinned revisions, checksums and licenses recorded.")


if __name__ == "__main__":
    main()
