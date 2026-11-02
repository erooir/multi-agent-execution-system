"""Provisioned, offline decoders. User records cannot select commands or model paths."""

import functools
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .provider_versions import bound_binary, model_revision, ocr_args


def command(args, data=None, timeout=60):
    done = subprocess.run(args, input=data, capture_output=True, timeout=timeout, check=False)
    return done.stdout if done.returncode == 0 else None


def ocr_image(im, *, revision=False):
    args = ocr_args()
    if not args:
        return ""
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    result = command(
        [
            *args,
            "--psm",
            "6" if revision else "3",
        ],
        buf.getvalue(),
    )
    return result.decode("utf-8", errors="replace").strip() if result else ""


def ocr_pdf_page(data, page, *, revision=False):
    if not ocr_args():
        return ""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        p = doc[page - 1]
        try:
            w, h = p.get_size()
            if min(w, h) <= 0 or w * h * 4 > 20_000_000:
                return ""
            bitmap = p.render(scale=2)
            try:
                return ocr_image(bitmap.to_pil(), revision=revision)
            finally:
                bitmap.close()
        finally:
            p.close()
    finally:
        doc.close()


@functools.lru_cache(maxsize=1)
def _loaded_speech_model(path, revision):
    from faster_whisper import WhisperModel

    model = WhisperModel(path, device="cpu", compute_type="int8", local_files_only=True)
    if model_revision("HANGZHI_ASR_MODEL_DIR", path) != revision:
        raise ValueError("本地转录模型加载期间文件发生改变")
    return model


def _speech_model(path):
    path = str(Path(path).resolve())
    return _loaded_speech_model(path, model_revision("HANGZHI_ASR_MODEL_DIR", path))


def read_medium(asset, data, *, revision=False):
    if not data:
        return [], [f"{asset['id']} 缺少原始媒体"]
    if asset["modality"] == "image":
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 25_000_000:
                raise ValueError("图像像素超过限制")
            im.load()
            text = ocr_image(im, revision=revision)
            observations = [
                (
                    {"image": 1},
                    text,
                    {
                        "kind": "image_metadata",
                        "width": im.width,
                        "height": im.height,
                        "format": im.format,
                        "ocr": bool(text),
                    },
                )
            ]
            from .geo_raster import image_observations

            observations.extend(image_observations(asset, im))
            return observations, []
    # No extension, playlist or URL from the user is passed to a decoder.
    probe = bound_binary("ffprobe")
    if not probe:
        return [], [f"{asset['id']} 离线媒体解码器未安装"]
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as temp:
        path = P(temp) / "media.bin"
        path.write_bytes(data)
        raw = command(
            [
                probe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "wav,mp3,flac,ogg,mov,matroska,asf,avi",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,sample_rate",
                "-of",
                "json",
                str(path),
            ]
        )
        if not raw:
            return [], [f"{asset['id']} 媒体无法解码"]
        meta = json.loads(raw)
        streams = meta.get("streams", [])
        if not any(s.get("codec_type") == asset["modality"] for s in streams):
            return [], [f"{asset['id']} 没有声明的媒体流"]
        duration = float(meta.get("format", {}).get("duration") or 0)
        records = [
            ({"media": 1}, "", {"kind": "media_metadata", "duration_seconds": duration, "streams": streams})
        ]
        gaps = []
        if asset["modality"] == "video":
            try:
                import av

                from .geo_raster import image_observations

                with av.open(
                    io.BytesIO(data),
                    options={"protocol_whitelist": "file,pipe", "format_whitelist": "mov,matroska,asf,avi"},
                ) as container:
                    video = container.streams.video[0]
                    seen = set()
                    for t in [0, duration / 3, 2 * duration / 3, max(0, duration - 1)]:
                        container.seek(max(0, int(t / float(video.time_base))), stream=video, backward=True)
                        for frame in container.decode(video):
                            if frame.time is None or frame.time + 0.05 < t:
                                continue
                            ms = round(frame.time * 1000)
                            if ms not in seen:
                                seen.add(ms)
                                im = frame.to_image()
                                obs = image_observations(asset, im)[0][2]
                                records.append(
                                    ({"frame_ms": ms}, "", {**obs, "kind": "video_frame", "frame_ms": ms})
                                )
                            break
                    if not seen:
                        gaps.append(f"{asset['id']} 未能解码可定位视频帧")
            except (ImportError, ValueError, RuntimeError, OSError, IndexError):
                gaps.append(f"{asset['id']} 视频帧解码未完成")
        if any(s.get("codec_type") == "audio" for s in streams):
            modeldir = os.getenv("HANGZHI_ASR_MODEL_DIR")
            if not modeldir or not Path(modeldir).is_dir():
                gaps.append(f"{asset['id']} 没有配置本地转录模型")
            elif duration <= 0 or duration > 3600:
                gaps.append(f"{asset['id']} 无有效时长或超过单件一小时处理上限")
            else:
                try:
                    # Decode only the verified audio stream to PCM. The model never opens untrusted URLs.
                    ffmpeg = bound_binary("ffmpeg")
                    if not ffmpeg:
                        raise ValueError("未配置ffmpeg")
                    pcm = command(
                        [
                            ffmpeg,
                            "-v",
                            "error",
                            "-protocol_whitelist",
                            "file,pipe",
                            "-format_whitelist",
                            "wav,mp3,flac,ogg,mov,matroska,asf,avi",
                            "-i",
                            str(path),
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "-f",
                            "s16le",
                            "pipe:1",
                        ],
                        timeout=120,
                    )
                    if not pcm:
                        raise ValueError("音轨解码失败")
                    import numpy as np

                    audio = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768
                    segments, info = _speech_model(modeldir).transcribe(audio, beam_size=3, vad_filter=False)
                    for s in segments:
                        from .control import pulse

                        pulse()
                        text = s.text.strip()
                        if text:
                            loc = {"start_ms": round(s.start * 1000), "end_ms": round(s.end * 1000)}
                            records.append(
                                (
                                    loc,
                                    text,
                                    {"kind": "asr_segment", "text": text, "language": info.language, **loc},
                                )
                            )
                except (ImportError, RuntimeError, ValueError, OSError):
                    gaps.append(f"{asset['id']} 本地语音推理未完成")
        return records, gaps
