"""Safe previews and georeferencing read from the original local pixel image."""

import base64
import io
from math import isfinite

from PIL import Image, ImageOps


def image_observations(asset, im):
    preview = im.convert("RGB")
    preview.thumbnail((192, 192), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    rows = [
        (
            {"image": 1},
            "",
            {
                "kind": "raster_preview",
                "width": im.width,
                "height": im.height,
                "png": base64.b64encode(buf.getvalue()).decode("ascii"),
            },
        )
    ]
    if im.format != "TIFF" or im.width * im.height > 65536:
        return rows
    tags = getattr(im, "tag_v2", {})
    keys = tags.get(34735)
    if not keys or len(keys) < 4 or len(keys) != 4 + 4 * keys[3]:
        return rows
    codes = {keys[i]: keys[i + 3] for i in range(4, len(keys), 4) if keys[i + 1] == 0 and keys[i + 2] == 1}
    epsg = codes.get(3072) or codes.get(2048)
    # Pixel-is-point and rotated/sheared matrices require resampling, never silent alignment.
    if not epsg or epsg in (0, 32767) or codes.get(1025, 1) != 1:
        return rows
    matrix = tags.get(34264)
    if matrix:
        if len(matrix) != 16 or matrix[1] != 0 or matrix[4] != 0:
            return rows
        x, y, dx, dy = map(float, (matrix[3], matrix[7], matrix[0], matrix[5]))
    else:
        tie = tags.get(33922)
        scale = tags.get(33550)
        if not tie or len(tie) != 6 or not scale or len(scale) < 2:
            return rows
        dx, dy = float(scale[0]), -float(scale[1])
        x = float(tie[3]) - float(tie[0]) * dx
        y = float(tie[4]) - float(tie[1]) * dy
    if not all(isfinite(n) for n in (x, y, dx, dy)) or dx == 0 or dy == 0:
        return rows
    nodata = tags.get(42113)
    try:
        nodata = float(str(nodata).strip("\x00")) if nodata is not None else None
    except ValueError:
        nodata = None
    pixels = list(im.get_flattened_data())
    values = []
    for i in range(im.height):
        line = []
        for v in pixels[i * im.width : (i + 1) * im.width]:
            channels = v if isinstance(v, tuple) else (v,)
            line.append(None if any(not isfinite(x) or x == nodata for x in channels) else list(channels))
        values.append(line)
    rows.append(
        (
            {"image": 1, "pixel_window": [0, 0, im.width, im.height]},
            "",
            {
                "kind": "geotiff_grid",
                "crs": "EPSG:" + str(epsg),
                "origin_x": x,
                "origin_y": y,
                "pixel_width": dx,
                "pixel_height": dy,
                "values": values,
            },
        )
    )
    return rows


def preview_png(encoded):
    """Return a re-encoded thumbnail. Never insert untrusted SVG/URLs into an overlay."""
    if not isinstance(encoded, str) or len(encoded) > 180000:
        return None
    try:
        buf = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(buf)) as im:
            if im.format != "PNG" or im.width > 192 or im.height > 192:
                return None
            dest = io.BytesIO()
            im.convert("RGB").save(dest, format="PNG")
        return base64.b64encode(dest.getvalue()).decode("ascii")
    except (ValueError, OSError):
        return None


def bright_regions(encoded, threshold, min_pixels=1):
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as im:
        grey = ImageOps.grayscale(im)
        w, h = grey.size
        pixels = list(grey.get_flattened_data())
    unseen = {i for i, v in enumerate(pixels) if v >= threshold}
    boxes = []
    while unseen:
        initial = unseen.pop()
        stack = [initial]
        xs = []
        ys = []
        while stack:
            p = stack.pop()
            y, x = divmod(p, w)
            xs.append(x)
            ys.append(y)
            for xx, yy in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                idx = yy * w + xx
                if 0 <= xx < w and 0 <= yy < h and idx in unseen:
                    unseen.remove(idx)
                    stack.append(idx)
        if len(xs) >= min_pixels:
            boxes.append((min(xs) / w, min(ys) / h, (max(xs) - min(xs) + 1) / w, (max(ys) - min(ys) + 1) / h))
    return sorted(boxes)


def resolve_grids(ctx):
    """Bindings supply role/bands, not coordinates or pixels: those must come from the file."""
    original = {r["source_id"]: r for r in ctx.select("geotiff_grid") if r["location"].get("pixel_window")}
    for binding in ctx.select("raster_binding"):
        f = binding["fields"]
        role = f.get("role")
        target = f.get("target_source_id")
        if role not in {"water_grid", "cover_grid"}:
            continue
        if target not in original:
            ctx.gap("格网绑定未匹配有地理定位的受控原件")
            continue
        r = original[target]
        data = r["fields"].copy()
        info = {
            k: f[k]
            for k in ("date", "phase", "ndwi_threshold", "max_spectral_distance", "valid_mask")
            if k in f
        }
        bands = f.get("bands")
        lut = f.get("class_values")
        converted = []
        for line in data["values"]:
            values = []
            for v in line:
                if v is None:
                    values.append(None)
                    continue
                if (
                    not isinstance(bands, list)
                    or not bands
                    or any(type(n) is not int or n < 0 or n >= len(v) for n in bands)
                ):
                    raise ValueError("GeoTIFF绑定缺少有效零基波段选择")
                selected = [v[n] for n in bands]
                if isinstance(lut, dict) and len(selected) == 1:
                    values.append(lut.get(str(selected[0])))
                elif role == "water_grid" and len(selected) != 2:
                    raise ValueError("水域影像需要两波段NDWI规则或给定类别编码")
                else:
                    values.append(selected)
            converted.append(values)
        # Preserve the file's source/location; retain the explicit rule as separate evidence.
        new = {**r, "fields": {**data, **info, "kind": role, "values": converted}}
        ctx.add_derived(r, new)
        ctx.ref(binding)
