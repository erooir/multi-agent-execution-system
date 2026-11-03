"""Local OCR using Pillow decoding and a cached RapidOCR engine."""

from __future__ import annotations

import io
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 40_000_000


def normalize_confidence(value) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_media")  # noqa: TRY004
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("invalid_media")
    return score


def polygon_bbox(points) -> list[list[float]]:
    if not isinstance(points, (list, tuple)) or len(points) < 4:
        raise ValueError("invalid_media")
    if any(
        not isinstance(point, (list, tuple))
        or len(point) != 2
        or not all(math.isfinite(float(value)) for value in point)
        for point in points
    ):
        raise ValueError("invalid_media")
    return [[float(point[0]), float(point[1])] for point in points]


def xywh_bbox(left, top, width, height) -> tuple[float, float, float, float]:
    values = tuple(float(value) for value in (left, top, width, height))
    if not all(math.isfinite(value) for value in values) or any(value < 0 for value in values):
        raise ValueError("invalid_media")
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError("invalid_media")
    return values


@lru_cache(maxsize=1)
def _get_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _confidence_threshold(config: dict | None) -> float:
    if config is None:
        return 0.0
    if not isinstance(config, dict):
        raise ValueError("invalid_config")  # noqa: TRY004
    value = config.get("min_confidence", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_config")  # noqa: TRY004
    threshold = float(value)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("invalid_config")
    return threshold


def _decode_image(payload: bytes) -> tuple[Image.Image, bool]:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("invalid_media")
    try:
        with Image.open(io.BytesIO(payload)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("input_too_large")
            animated = bool(getattr(source, "is_animated", False))
            source.seek(0)
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode != "RGB":
                image = image.convert("RGB")
            return image, animated
    except ValueError as error:
        if error.args == ("input_too_large",):
            raise
        raise ValueError("invalid_media") from None
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError("invalid_media") from None


def run_ocr(images: list[dict], config: dict | None = None) -> dict:
    min_confidence = _confidence_threshold(config)
    engine = _get_engine()
    results = []
    warnings = []
    for image_input in images:
        image, animated = _decode_image(image_input["payload"])
        raw_blocks, _ = engine(np.asarray(image))
        blocks = []
        for bbox, text, confidence in raw_blocks or []:
            score = normalize_confidence(confidence)
            if score < min_confidence:
                continue
            blocks.append(
                {
                    "text": text,
                    "confidence": score,
                    "bbox": polygon_bbox(bbox),
                }
            )
        source_id = image_input["source_id"]
        results.append(
            {
                "source_id": source_id,
                "source_version": image_input["source_version"],
                "text": "\n".join(block["text"] for block in blocks),
                "blocks": blocks,
                "no_text": not blocks,
            }
        )
        if animated:
            warnings.append(f"{source_id}: animated image; only the first frame was processed")
    return {"content": {"images": results}, "warnings": warnings}
