"""Independent checks against the originals in the frozen project snapshot."""

from threading import Lock

_decoder_lock = Lock()


def verify_sources(code, sources, products):
    if code.startswith("G"):
        from .edition_geo_checks import checks
    elif code.startswith("T"):
        from .edition_tech_checks import checks
    elif code.startswith("B"):
        from .edition_archive_checks import checks
    else:
        raise ValueError("未知用例系列")
    # PDFium is process-global. Don't overlap native decoders in the worker's thread pool.
    with _decoder_lock:
        results = checks(code, sources, products)
    if not results or any(type(r.get("passed")) is not bool or r.get("hard") is not True for r in results):
        raise ValueError("用例未取得有效的原件核查记录")
    return results
