"""Real local OCR adapter tests using only generated images."""

import io
import math

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app.platform.ocr import normalize_confidence, polygon_bbox, run_ocr, xywh_bbox


def _png(text="INVOICE 12345"):
    image = Image.new("RGB", (1000, 240), "white")
    if text:
        draw = ImageDraw.Draw(image)
        draw.text((50, 65), text, fill="black", font=ImageFont.load_default(size=72))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image(payload, source_id="synthetic:ocr"):
    return {
        "source_id": source_id,
        "source_version": "1",
        "payload": payload,
        "media_type": "image/png",
        "name": "synthetic.png",
    }


def test_real_ocr_returns_recognized_text_boxes_and_confidences():
    result = run_ocr([_image(_png())], {"min_confidence": 0.5})

    assert result["warnings"] == []
    image = result["content"]["images"][0]
    assert image["source_id"] == "synthetic:ocr"
    assert image["source_version"] == "1"
    assert image["no_text"] is False
    assert image["blocks"]
    assert "12345" in image["text"].replace(" ", "")
    for block in image["blocks"]:
        assert block["text"]
        assert type(block["confidence"]) is float
        assert math.isfinite(block["confidence"])
        assert 0.0 <= block["confidence"] <= 1.0
        assert len(block["bbox"]) >= 4
        assert all(len(point) == 2 for point in block["bbox"])
        assert all(math.isfinite(coordinate) for point in block["bbox"] for coordinate in point)


def test_blank_image_has_no_fabricated_text():
    image = run_ocr([_image(_png(""))])["content"]["images"][0]

    assert image["text"] == ""
    assert image["blocks"] == []
    assert image["no_text"] is True


def test_animated_image_uses_only_first_frame_and_warns():
    first = Image.new("RGB", (1000, 240), "white")
    second = Image.new("RGB", (1000, 240), "white")
    ImageDraw.Draw(second).text((50, 65), "INVOICE 12345", fill="black", font=ImageFont.load_default(size=72))
    buffer = io.BytesIO()
    first.save(buffer, format="GIF", save_all=True, append_images=[second])

    result = run_ocr([_image(buffer.getvalue())])

    assert result["content"]["images"][0]["no_text"] is True
    assert len(result["warnings"]) == 1
    assert "first frame" in result["warnings"][0]


@pytest.mark.parametrize("payload", [b"", b"not an image", _png()[:60]])
def test_invalid_or_incomplete_images_raise_invalid_media(payload):
    with pytest.raises(ValueError, match="^invalid_media$"):
        run_ocr([_image(payload)])


@pytest.mark.parametrize("threshold", [-0.01, 1.01, True, "0.5", None, math.nan, math.inf])
def test_invalid_confidence_thresholds_raise_invalid_config(threshold):
    with pytest.raises(ValueError, match="^invalid_config$"):
        run_ocr([], {"min_confidence": threshold})


def test_non_dict_config_raises_invalid_config():
    with pytest.raises(ValueError, match="^invalid_config$"):
        run_ocr([], [])


def test_image_over_forty_megapixels_is_rejected_before_decoding():
    buffer = io.BytesIO()
    Image.new("1", (6401, 6400), 1).save(buffer, format="PNG")

    with pytest.raises(ValueError, match="^input_too_large$"):
        run_ocr([_image(buffer.getvalue())])


def test_shared_ocr_metadata_normalization():
    assert normalize_confidence(0.75) == 0.75
    assert xywh_bbox(1, 2, 30, 40) == (1.0, 2.0, 30.0, 40.0)
    assert xywh_bbox("1", "2", "30", "40") == (1.0, 2.0, 30.0, 40.0)
    with pytest.raises(ValueError, match="^invalid_media$"):
        xywh_bbox("0", "0", "0", "40")
    assert polygon_bbox([(1, 2), (31, 2), (31, 42), (1, 42)]) == [
        [1.0, 2.0],
        [31.0, 2.0],
        [31.0, 42.0],
        [1.0, 42.0],
    ]
    with pytest.raises(ValueError, match="^invalid_media$"):
        normalize_confidence(1.1)
    with pytest.raises(ValueError, match="^invalid_media$"):
        xywh_bbox(0, 0, 0, 1)
    with pytest.raises(ValueError, match="^invalid_media$"):
        polygon_bbox([(0, 0), (1, 1)])
