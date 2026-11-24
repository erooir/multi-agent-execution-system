"""Read a bounded NASA MATLAB v5 reference-discharge sample independently of the preparation pipeline."""

import io
import math
import struct
import sys
import zlib
from itertools import chain, pairwise
from pathlib import PurePosixPath
from zipfile import ZipFile

from .control import pulse


def _elements(buf):
    """Iterate a little-endian MAT-v5 element stream, including its small-data encoding."""
    offset = 0
    while offset < len(buf):
        kind, count = struct.unpack_from("<II", buf, offset)
        small = kind >> 16
        if small:
            if small > 4:
                raise ValueError("无效 MAT 短元素")
            yield kind & 0xFFFF, buf[offset + 4 : offset + 4 + small]
            offset += 8
        else:
            end = offset + 8 + count
            if end > len(buf):
                raise ValueError("MAT 元素超出边界")
            yield kind, buf[offset + 8 : end]
            offset = (end + 7) & ~7


def _struct(matrix):
    elements = iter(_elements(matrix))

    def field():
        item = next(elements, None)
        if item is None:
            raise ValueError("MAT 结构体字段截断")
        return item

    _, flags = field()
    if struct.unpack_from("<I", flags)[0] & 0xFF != 2:
        raise ValueError("期望 MATLAB struct")
    _, dimensions = field()
    count = math.prod(i for (i,) in struct.iter_unpack("<i", dimensions))
    _, _name = field()
    _, stride_data = field()
    stride = struct.unpack("<i", stride_data)[0]
    _, keys = field()
    if not (1 <= stride <= 255 and 0 < count <= 100_000 and len(keys) % stride == 0):
        raise ValueError("MAT 结构体超出支持范围")
    fields = [bytes(keys[i : i + stride]).rstrip(b"\0").decode("ascii") for i in range(0, len(keys), stride)]
    if not 1 <= len(fields) <= 64 or len(set(fields)) != len(fields):
        raise ValueError("MAT 字段清单无效")
    for _ in range(count):
        record = {}
        for name in fields:
            kind, data = field()
            if kind != 14:
                raise ValueError("MAT 字段缺少矩阵")
            record[name] = data
        yield record


def _value(matrix):
    items = iter(_elements(matrix))
    for _ in range(3):  # Array flags, dimensions, name.
        next(items)
    return next(items)


def _numeric(matrix):
    kind, raw = _value(matrix)
    formats = {1: "b", 2: "B", 3: "h", 4: "H", 5: "i", 6: "I", 7: "f", 9: "d", 12: "q", 13: "Q"}
    fmt = formats.get(kind)
    if fmt is None:
        raise ValueError("不支持的 MAT 数值字段")
    return raw.cast(fmt)


def _capacity(row):
    time, amps = (_numeric(row[k]) for k in ("relativeTime", "current"))
    if len(time) != len(amps) or len(time) < 2:
        raise ValueError("参考放电时间或电流长度不符")
    intervals = [b - a for a, b in pairwise(time)]
    if min(intervals) < 0 or not all(math.isfinite(x) for x in chain(intervals, amps)):
        raise ValueError("参考放电时间序列无效")
    return math.fsum(dt * (a + b) / 2 for dt, (a, b) in zip(intervals, pairwise(amps), strict=True)) / 3600


def battery_sample(data):
    """Verify full MAT inventory, then independently integrate the first and last reference test of one cell."""
    with ZipFile(io.BytesIO(data)) as archive:
        mats = sorted(
            (x for x in archive.infolist() if x.filename.lower().endswith(".mat")), key=lambda x: x.filename
        )
        if not mats or len(mats) > 20 or mats[0].file_size > 40_000_000:
            raise ValueError("电池样本清单超出允许范围")
        names = [PurePosixPath(x.filename).stem for x in mats]
        sample = archive.read(mats[0])
    if (
        sys.byteorder != "little"
        or sample[126:128] != b"IM"
        or struct.unpack_from("<I", sample, 128)[0] != 15
    ):
        raise ValueError("需要 little-endian 压缩 MAT v5 数据")
    n = struct.unpack_from("<I", sample, 132)[0]
    if n + 136 != len(sample):
        raise ValueError("MAT 顶层压缩段长度无效")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(sample[136:], 185_000_000)
    if not decoder.eof or decoder.unused_data:
        raise ValueError("MAT 数据截断或超出体积限制")
    kind, main = next(_elements(memoryview(raw)))
    if kind != 14:
        raise ValueError("MAT 缺少 data 结构")
    root = next(_struct(main))
    count = 0
    first = last = None
    for index, row in enumerate(_struct(root["step"])):
        if index % 500 == 0:
            pulse()
        char_type, chars = _value(row["comment"])
        if char_type in (1, 2, 16) and bytes(chars) == b"reference discharge":
            if count == 0:
                first = row
            last = row
            count += 1
    if not count:
        raise ValueError("没有找到可积分的参考放电")
    return names, count, _capacity(first), _capacity(last)
