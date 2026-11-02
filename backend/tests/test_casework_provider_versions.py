import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.casework import media, translation
from backend.app.casework import provider_versions as providers


@pytest.fixture(autouse=True)
def controlled(monkeypatch):
    for key in [*providers.MODEL_FILES, "TESSDATA_PREFIX", "HANGZHI_OCR_LANG"]:
        monkeypatch.delenv(key, raising=False)
    media._loaded_speech_model.cache_clear()
    translation._loaded_translation.cache_clear()
    yield
    media._loaded_speech_model.cache_clear()
    translation._loaded_translation.cache_clear()


def replace(path, data):
    # Deployment by atomic replacement, even with the same size and packaged mtime.
    next_path = path.with_suffix(".replacement")
    next_path.write_bytes(data)
    stat = path.stat()
    os.utime(next_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    next_path.replace(path)


def test_in_place_weight_update_with_identical_mtime_and_length_has_new_hash(tmp_path):
    file = tmp_path / "weights"
    file.write_bytes(b"v1")
    stat = file.stat()
    old = providers._fingerprint(file)
    file.write_bytes(b"v2")
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert providers._fingerprint(file) != old


def test_replaced_asr_weights_cannot_use_a_stale_session_or_old_freeze(tmp_path, monkeypatch):
    monkeypatch.setenv("HANGZHI_ASR_MODEL_DIR", str(tmp_path))
    file = tmp_path / "model.bin"
    file.write_bytes(b"v1")
    (tmp_path / "tokenizer.json").write_text("{}")
    loads = []

    def fake(folder, **settings):
        assert settings["local_files_only"] is True
        version = (Path(folder) / "model.bin").read_bytes()
        loads.append(version)
        return version

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=fake))
    first = providers.profile()
    with providers.bind(first):
        assert media._speech_model(tmp_path) == media._speech_model(tmp_path) == b"v1"
    replace(file, b"v2")
    with pytest.raises(ValueError, match="冻结指纹"), providers.bind(first):
        media._speech_model(tmp_path)
    current = providers.profile()
    assert current != first
    with providers.bind(current):
        assert media._speech_model(tmp_path) == b"v2"
    assert loads == [b"v1", b"v2"]
    (tmp_path / "tokenizer.json").unlink()
    with pytest.raises(ValueError, match="禁止运行时下载"):
        media._speech_model(tmp_path)


def test_mt_vocabulary_and_tokenizer_used_by_the_session_are_both_frozen(tmp_path, monkeypatch):
    folder = tmp_path / "translation"
    model_dir = folder / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"weights")
    vocab = model_dir / "shared_vocabulary.json"
    vocab.write_bytes(b"v1")
    (folder / "metadata.json").write_text('{"from_code":"en","to_code":"zh"}')
    tokenizer = folder / "sentencepiece.model"
    tokenizer.write_bytes(b"t1")
    monkeypatch.setenv("HANGZHI_MT_EN_ZH", str(folder))
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            Translator=lambda path, **options: (Path(path) / "shared_vocabulary.json").read_bytes(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentencepiece",
        SimpleNamespace(
            SentencePieceProcessor=lambda model_file: Path(model_file).read_bytes(),
        ),
    )
    old = providers.profile()
    with providers.bind(old):
        assert translation._load(folder, "en")[:2] == (b"v1", b"t1")
    replace(vocab, b"v2")
    with pytest.raises(ValueError, match="冻结指纹"), providers.bind(old):
        translation._load(folder, "en")
    current = providers.profile()
    with providers.bind(current):
        assert translation._load(folder, "en")[:2] == (b"v2", b"t1")
    with pytest.raises(ValueError, match="执行期间"), providers.bind(current):
        replace(tokenizer, b"t2")
    with providers.bind(providers.profile()):
        assert translation._load(folder, "en")[:2] == (b"v2", b"t2")


def test_ocr_cannot_fall_back_to_unknown_language_data(tmp_path, monkeypatch):
    exe = tmp_path / "test-tesseract"
    exe.write_bytes(b"test executable marker")
    monkeypatch.setenv("HANGZHI_TESSERACT_PATH", str(exe))
    monkeypatch.setenv("HANGZHI_OCR_LANG", "rus")
    assert not providers.profile()["ocr"]["general_available"] and providers.ocr_args() is None
    root = tmp_path / "tessdata"
    root.mkdir()
    monkeypatch.setenv("TESSDATA_PREFIX", str(root))
    (root / "rus.traineddata").write_bytes(b"r1")
    assert providers.profile()["ocr"]["general_available"]
    assert not providers.profile()["ocr"]["chart_english_available"] and providers.ocr_args("eng") is None
    (root / "eng.traineddata").write_bytes(b"e1")
    frozen = providers.profile()
    with providers.bind(frozen):
        assert providers.ocr_args()[-4:] == ["--tessdata-dir", str(root), "-l", "rus"]
        assert providers.ocr_args("eng")[-1] == "eng"
    replace(root / "eng.traineddata", b"e2")
    with pytest.raises(ValueError, match="冻结指纹"), providers.bind(frozen):
        providers.ocr_args("eng")
