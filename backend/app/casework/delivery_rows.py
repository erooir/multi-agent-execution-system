"""Select human-readable product records with their own, resolvable evidence links."""

from itertools import islice

# The brief accompanies the complete JSON and media attachments. Do not expand graphics,
# geometry, raw cells or duplicated chart series into text.
SKIP = {
    "evidence_ids",
    "refs",
    "source_id",
    "source_version",
    "location",
    "text_truncated",
    "geometry",
    "coordinates",
    "coords",
    "features",
    "png",
    "svg",
    "trend_svg",
    "pixel_overlay_svg",
    "map_view",
    "comparison_panels",
    "brightness_candidates",
}
IDENTITY = (
    "project",
    "report",
    "topic",
    "question",
    "indicator",
    "image_id",
    "lake_id",
    "figure",
    "cell",
    "metric",
    "region",
    "theme",
    "unit",
    "date",
)
SUBJECTS = {
    "milestones": ("project", "event_type"),
    "timed_index": ("topic", "speaker"),
    "comparison": ("indicator", "region"),
    "observations": ("region",),
    "timeline": ("event",),
    "results": ("site_id", "analyte"),
    "monthly": ("scope", "month"),
}
PRIORITY = (
    "event_date",
    "report_dates",
    "event_time",
    "formed_at",
    "published_at",
    "declassified_at",
    "start_ms",
    "end_ms",
    "turn_type",
    "text",
    "translation_zh",
    "alignment",
    "script",
    "observed_at",
    "cutoff",
    "published_date",
    "data_as_of",
    "effective_date",
    "date",
    "year",
    "commitment",
    "statement",
    "observation",
    "interpretation",
    "supplied_value",
    "feature",
    "value_text",
    "value",
    "unit",
    "status",
    "scope",
    "definition",
    "coverage",
    "method",
    "count",
    "previous",
    "change",
    "deaths",
    "injured",
    "total",
    "complete_month",
)


def _simple(value):
    return isinstance(value, (str, int, float, bool)) and value != ""


def _fields(record):
    for key in dict.fromkeys((*PRIORITY, *record)):
        if key not in record or key in SKIP or key.endswith("_svg"):
            continue
        val = record[key]
        if _simple(val) or (isinstance(val, list) and val and all(_simple(v) for v in val)):
            if key == "script" and val == record.get("text"):
                continue
            yield key, val


def _linked_rows(value, product, evidence, context=()):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                yield from _linked_rows(item, product, evidence, context)
        return
    if not isinstance(value, dict) or value.get("type") in ("Feature", "FeatureCollection"):
        return

    fields = dict(_fields(value))
    ids = value.get("evidence_ids")
    if fields and isinstance(ids, list) and ids and all(isinstance(i, str) and i in evidence for i in ids):
        subjects = [key for key in SUBJECTS.get(product, ()) if key in fields]
        yield (
            context,
            [(k, fields[k]) for k in subjects],
            [(k, v) for k, v in fields.items() if k not in subjects],
            ids,
        )

    inherited = tuple((k, fields[k]) for k in IDENTITY if k in fields)
    for key, child in value.items():
        if key in SKIP or key.endswith("_svg") or not isinstance(child, (list, dict)):
            continue
        if isinstance(child, list) and not any(isinstance(r, (list, dict)) for r in child):
            continue
        # Keep the metric, parent event or topic attached to its nested data points.
        # Dictionary keys (e.g. terms or the two versions of a clause) can also carry meaning.
        yield from _linked_rows(child, product, evidence, (*context, *inherited, ("", key)))


def product_sections(products, evidence, *, group_limit=6, row_limit=8, total_limit=28):
    """Yield bounded groups; the extra record records genuine overflow without scanning a long table."""
    remaining = total_limit
    shown = 0
    # A figure card can copy its labelled measurements from the comparison table; a
    # panel annotation can also be repeated verbatim in changes. Prefer a single
    # display only when all visible fields, parent identifiers and evidence match.
    names = list(products)
    if "figure_cards" in products and isinstance(products.get("comparison"), list):
        names.remove("comparison")
        names.insert(0, "comparison")
    seen = {"comparison": set(), "panels": set()}
    figure_titles = set()
    duplicates_of = {"figure_cards": "comparison", "changes": "panels"}
    for name in names:
        value = products[name]
        if name in SKIP or name in {"trend", "map"}:
            continue
        cap = min(row_limit, remaining)
        rows = _linked_rows(value, name, evidence)
        if name in duplicates_of:
            originals = seen[duplicates_of[name]]
            rows = (row for row in rows if _record_key(row) not in originals)
        if name == "figure_cards" and figure_titles:
            rows = (row for row in rows if _figure_title(row, caption=True) not in figure_titles)
        records = list(islice(rows, cap + 1))
        if records:
            if name in seen:
                seen[name].update(_record_key(row) for row in records[:cap])
            if name == "comparison":
                figure_titles.update(k for row in records[:cap] if (k := _figure_title(row)) is not None)
            yield name, records[:cap], len(records) > cap
            shown += 1
            remaining -= min(len(records), cap)
            if shown >= group_limit or not remaining:
                return


def _record_key(row):
    context, subjects, fields, ids = row
    values = {k: v for k, v in context if k}
    values.update(subjects)
    values.update(fields)
    return (
        frozenset((k, tuple(v) if isinstance(v, list) else v) for k, v in values.items()),
        frozenset(ids),
    )


def _figure_title(row, *, caption=False):
    context, subjects, fields, ids = row
    values = dict(subjects + fields)
    if caption and (("", "captions") not in context or values.keys() - {"title", "page"}):
        return None
    title = values.get("title" if caption else "图题")
    figure = values.get("figure", dict(context).get("figure"))
    if isinstance(title, str) and isinstance(figure, (str, int, float)):
        return figure, title, values.get("page"), frozenset(ids)
    return None
