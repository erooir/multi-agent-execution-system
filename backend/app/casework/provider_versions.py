"""Fingerprints for locally provisioned extraction/inference tools; no paths leave the server."""

import functools
import hashlib
import importlib.metadata
import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

MT_FILES = (
    "metadata.json",
    "sentencepiece.model",
    "model/model.bin",
    "model/config.json",
    "model/shared_vocabulary.json",
    "model/source_vocabulary.json",
    "model/target_vocabulary.json",
    "model/shared_vocabulary.txt",
    "model/source_vocabulary.txt",
    "model/target_vocabulary.txt",
)
MODEL_FILES = {
    "HANGZHI_ASR_MODEL_DIR": (
        "model.bin",
        "config.json",
        "tokenizer.json",
        "vocabulary.txt",
        "vocabulary.json",
        "preprocessor_config.json",
    ),
    "HANGZHI_MT_EN_ZH": MT_FILES,
    "HANGZHI_MT_RU_EN": MT_FILES,
}
_expected = ContextVar("case_frozen_providers", default=None)


@contextmanager
def bind(providers):
    token = _expected.set(providers)
    try:
        yield
        if profile() != providers:
            raise ValueError("本地工具或模型在节点执行期间变化，需重新受理")
    finally:
        _expected.reset(token)


def binary(name):
    configured = os.getenv("HANGZHI_" + name.upper() + "_PATH")
    if configured:
        path = Path(configured)
        return str(path) if path.is_absolute() and path.is_file() else None
    return shutil.which(name)


@functools.lru_cache(maxsize=96)
def _hash_file(path, size, modified, changed, inode):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fingerprint(path):
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    stat = file.stat()
    changed = stat.st_ctime_ns
    if os.name == "nt":
        # Windows stat.ctime is the creation time. An in-place update can retain creation time,
        # inode, size and packaged mtime. Ask the filesystem for its actual change time.
        import ctypes
        import msvcrt

        with file.open("rb") as handle:
            info = (ctypes.c_int64 * 5)()  # FILE_BASIC_INFO: 4 FILETIMEs, attributes, padding
            valid = ctypes.windll.kernel32.GetFileInformationByHandleEx(
                ctypes.c_void_p(msvcrt.get_osfhandle(handle.fileno())),
                0,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not valid or not info[3]:
                return hashlib.file_digest(handle, "sha256").hexdigest()
            changed = info[3]
    return _hash_file(str(file.resolve()), stat.st_size, stat.st_mtime_ns, changed, stat.st_ino)


def _verify(key, sha):
    frozen = _expected.get()
    if frozen is not None and frozen["tool_files"].get(key) != sha:
        raise ValueError("本地工具或模型与冻结指纹不一致")


def model_revision(setting, folder):
    root = Path(folder)
    revision = tuple((name, _fingerprint(root / name)) for name in MODEL_FILES[setting])
    for name, sha in revision:
        _verify(setting + ":" + name, sha)
    needed = (
        {"model.bin", "tokenizer.json"}
        if setting == "HANGZHI_ASR_MODEL_DIR"
        else {"model/model.bin", "sentencepiece.model", "metadata.json"}
    )
    if any(not sha for name, sha in revision if name in needed):
        raise ValueError("缺少已预配的模型或分词器文件，禁止运行时下载或回退")
    return revision


def bound_binary(name):
    path = binary(name)
    _verify(name, _fingerprint(path))
    return path


def _ocr_root():
    folder = os.environ.get("TESSDATA_PREFIX")
    return Path(folder) if folder and Path(folder).is_absolute() and Path(folder).is_dir() else None


def _languages(value):
    return value.split("+") if re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", value) else []


def ocr_args(languages=None):
    """Always name the exact language data directory; never let Tesseract discover unknown weights."""
    exe = bound_binary("tesseract")
    root = _ocr_root()
    langs = languages or os.getenv("HANGZHI_OCR_LANG", "eng")
    keys = _languages(langs)
    if not exe or not root or not keys:
        return None
    for key in keys:
        sha = _fingerprint(root / (key + ".traineddata"))
        _verify("ocr:" + key, sha)
        if not sha:
            return None
    return [exe, "stdin", "stdout", "--tessdata-dir", str(root), "-l", langs]


def profile():
    packages = {}
    for name in (
        "pypdf",
        "pypdfium2",
        "python-docx",
        "openpyxl",
        "pillow",
        "shapely",
        "pyproj",
        "opencv-python-headless",
        "ctranslate2",
        "faster-whisper",
        "sentencepiece",
        "tokenizers",
        "av",
        "numpy",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    assets = {name: _fingerprint(binary(name)) for name in ("tesseract", "ffmpeg", "ffprobe")}
    tess = _ocr_root()
    general = os.getenv("HANGZHI_OCR_LANG", "eng")
    languages = _languages(general)
    # The printed-chart reader explicitly uses English even if general OCR is configured differently.
    for language in sorted(set(languages) | {"eng"}):
        assets["ocr:" + language] = _fingerprint(tess / (language + ".traineddata")) if tess else None
    for setting, files in MODEL_FILES.items():
        root = os.getenv(setting)
        if root:
            for name in files:
                assets[setting + ":" + name] = _fingerprint(Path(root) / name)
    return {
        "packages": packages,
        "tool_files": assets,
        "ocr": {
            "requested_languages": general,
            "general_available": bool(
                assets["tesseract"] and languages and all(assets.get("ocr:" + k) for k in languages)
            ),
            "chart_english_available": bool(assets["tesseract"] and assets["ocr:eng"]),
        },
    }
