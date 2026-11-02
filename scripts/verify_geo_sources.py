"""Read-only, offline acceptance probe for an explicitly supplied public reference directory.

Run at repository root: python -m scripts.verify_geo_sources --materials <directory>.
The private report is written beneath .local/. This tool does not start a DB, call a
provider, fetch data, or convert reference PDFs into fictitious geometry.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from backend.app.casework.engine import audit
from backend.app.casework.geo_cases import analyze, audit_products
from backend.app.casework.records import extract

FORMATS = {
    "G03": ("*.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "G04": ("*.pdf", "application/pdf"),
    "G05": ("*.pdf", "application/pdf"),
    "G06": ("*.pdf", "application/pdf"),
    "G07": ("*.csv", "text/csv; charset=windows-1252"),
}


def probe(case, path, mime):
    raw = path.read_bytes()
    if not raw or len(raw) > 10_000_000:
        raise ValueError("文件为空或超过单个原件上限")
    checksum = hashlib.sha256(raw).hexdigest()
    started = time.perf_counter()
    asset = {
        "id": "reference-" + case,
        "source_version": checksum,
        "modality": "structured" if case in {"G03", "G07"} else "text",
        "media_type": mime,
        "content_ref": "local-verified-bytes",
        "content": None,
    }
    records, gaps = extract([asset], lambda _: raw)
    candidate = analyze(case, records)
    candidate["gaps"] = list(dict.fromkeys(gaps + candidate["gaps"]))
    checks = audit(case, candidate, records)
    # Older harnesses do not yet include the independent domain replay gates.
    already = {row["name"] for row in checks}
    checks.extend(x for x in audit_products(case, candidate["products"], records) if x["name"] not in already)
    report = {
        "case": case,
        "sha256": checksum,
        "parsed_records": len(records),
        "evidence_count": len(candidate["evidence"]),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "gaps": candidate["gaps"],
        "checks": checks,
        "state": "failed"
        if any(not x["passed"] and x["hard"] for x in checks)
        else "partial"
        if any(not x["passed"] for x in checks)
        else "succeeded",
    }
    p = candidate["products"]
    if case == "G03":
        report.update(anomaly=p["anomaly"], annual_years=len(p["annual_series"]))
    elif case == "G07":
        report.update(
            record_count=p["quality"]["record_count"],
            unique_samples=p["quality"]["unique_samples"],
            unique_sites=p["quality"]["unique_sites"],
            mapped_points=len(p["sample_points"]["features"]),
            excluded=p["quality"]["excluded"],
        )
    else:
        report["located_context_pages"] = [x["page"] for x in p.get("source_notes", [])]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--materials", type=Path, required=True, help="Local public reference directory (not uploaded)"
    )
    parser.add_argument("--cases", nargs="+", choices=sorted(FORMATS), default=sorted(FORMATS))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / ".local" / "casework-geo"
    output.mkdir(parents=True, exist_ok=True)
    directory = args.materials.resolve(strict=True)
    manifest = json.loads((directory / "download_manifest.json").read_text(encoding="utf-8-sig"))
    hashes = {x.get("sha256") for x in manifest}
    result = []
    for code in args.cases:
        pattern, mime = FORMATS[code]
        # Find via filename prefix where possible. For a generic ancillary PDF choose
        # only an explicitly case-tagged file or the public flood bulletin marker.
        matches = [
            p
            for p in directory.glob(pattern)
            if p.name.startswith(code + "_") or (code == "G04" and p.name.startswith("EFAS_"))
        ]
        if len(matches) != 1:
            raise ValueError(f"{code}没有唯一匹配原件")
        digest = hashlib.sha256(matches[0].read_bytes()).hexdigest()
        if digest not in hashes:
            raise ValueError(f"{code}原件与随附清单摘要不符")
        row = probe(code, matches[0], mime)
        result.append(row)
        print(
            code,
            row["state"],
            "records",
            row["parsed_records"],
            "evidence",
            row["evidence_count"],
            "gaps",
            len(row["gaps"]),
        )
    target = output / "public-source-replay.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Private report: {target}")
    if any(r["state"] == "failed" for r in result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
