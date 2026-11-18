"""Measured luminance and document legibility cues; all values describe decoded pixels."""

from PIL import Image, ImageOps, ImageStat


def assess(image: Image.Image) -> dict:
    sample = image.convert("L")
    sample.thumbnail((512, 512))
    histogram = sample.histogram()
    n = sum(histogram)

    def percentile(fraction):
        total = 0
        for gray, count in enumerate(histogram):
            total += count
            if total >= fraction * n:
                return gray
        return 255

    span = percentile(0.99) - percentile(0.01)
    return {
        "mean_luminance": round(ImageStat.Stat(sample).mean[0], 1),
        "luminance_span_p01_p99": span,
        "low_tonal_range": span < 40,
        "constant_luminance": sample.getextrema()[0] == sample.getextrema()[1],
    }


def document_contrast(image: Image.Image, quality: dict) -> Image.Image:
    if quality["low_tonal_range"] and not quality["constant_luminance"]:
        return ImageOps.autocontrast(image.convert("L")).convert("RGB")
    return image
