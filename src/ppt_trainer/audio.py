from __future__ import annotations

import json
import math
import re
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from .api import BailianClient
from .config import Settings
from .errors import TrainerError
from .tools import ffmpeg
from .util import CACHE_VERSION, digest, file_hash, run, write_json


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class SlideAudio:
    path: Path
    duration: float
    frames: int
    cues: list[Cue]


def split_utterances(text: str, limit: int) -> list[str]:
    """Bounded utterances keep measured sentence captions within the screen area."""
    text = text.strip()
    if not text:
        raise TrainerError("讲稿为空，不能合成。")
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    result: list[str] = []
    for part in parts:
        part = part.strip()
        while part:
            if len(part) <= limit:
                result.append(part)
                break
            prefix = part[:limit]
            cut = max(prefix.rfind("，"), prefix.rfind(","), prefix.rfind(" "), prefix.rfind("、"))
            cut = cut + 1 if cut >= limit // 3 else limit
            # Avoid splitting decimal numbers and short product/model identifiers.
            if cut == limit and part[cut - 1].isascii() and part[cut].isascii():
                boundary = re.search(r"[A-Za-z0-9_.%-]+$", prefix)
                if boundary and boundary.start() > 0:
                    cut = boundary.start()
            result.append(part[:cut].strip())
            part = part[cut:].strip()
    return [s for s in result if s]


def wav_info(path: Path) -> tuple[int, int]:
    try:
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getnframes() == 0:
                raise TrainerError("配音不是有效的16位单声道PCM音频。")
            return stream.getnframes(), stream.getframerate()
    except (wave.Error, EOFError) as exc:
        raise TrainerError("配音文件无法解码。") from exc


def write_pcm(path: Path, content: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(content)


def synthesize_slide(text: str, cfg: Settings, client: BailianClient, folder: Path) -> SlideAudio:
    folder.mkdir(parents=True, exist_ok=True)
    limit = min(
        cfg.tts.max_chars_per_request, cfg.subtitles.max_chars_per_line * cfg.subtitles.max_lines
    )
    utterances = split_utterances(text, limit)
    samples = bytearray()
    cues: list[Cue] = []
    sr = cfg.tts.sample_rate
    ff = ffmpeg(cfg.tools.ffmpeg)
    for utterance in utterances:
        key = digest([CACHE_VERSION, client.namespace, cfg.tts.model_dump(), utterance])
        audio_dir = Path(cfg.cache_dir) / "tts"
        audio_dir.mkdir(parents=True, exist_ok=True)
        normalized = audio_dir / f"{key}.wav"
        metadata = audio_dir / f"{key}.json"
        valid = False
        if normalized.is_file() and metadata.is_file() and not client.refresh:
            try:
                raw = json.loads(metadata.read_text(encoding="utf-8"))
                valid = raw.get("sha256") == file_hash(normalized) and wav_info(normalized)[1] == sr
            except (TrainerError, ValueError):
                pass
        if not valid:
            content, info = client.synthesize(utterance)
            raw_path = audio_dir / f"{key}.download"
            temporary = audio_dir / f"{key}.tmp.wav"
            raw_path.write_bytes(content)
            try:
                run(
                    [
                        ff,
                        "-y",
                        "-v",
                        "error",
                        "-i",
                        str(raw_path),
                        "-ac",
                        "1",
                        "-ar",
                        str(sr),
                        "-c:a",
                        "pcm_s16le",
                        str(temporary),
                    ]
                )
                wav_info(temporary)
                temporary.replace(normalized)
                write_json(metadata, {**info, "sha256": file_hash(normalized)})
            finally:
                raw_path.unlink(missing_ok=True)
        frames, rate = wav_info(normalized)
        if rate != sr:
            raise TrainerError("缓存音频采样率不匹配。")
        start = len(samples) / 2 / sr
        with wave.open(str(normalized), "rb") as stream:
            samples.extend(stream.readframes(frames))
        cues.append(Cue(start, start + frames / sr, utterance))
    audio_seconds = len(samples) / 2 / sr
    # Align page boundaries to whole video frames, then pad PCM to those exact boundaries.
    video_frames = math.ceil((audio_seconds + cfg.tts.pause_after_slide_seconds) * cfg.video.fps)
    target_samples = video_frames * sr // cfg.video.fps
    samples.extend(b"\x00\x00" * (target_samples - len(samples) // 2))
    output = folder / "audio.wav"
    write_pcm(output, samples, sr)
    write_json(folder / "cues.json", [asdict(c) for c in cues])
    return SlideAudio(output, target_samples / sr, video_frames, cues)


def concatenate_audio(paths: list[Path], output: Path, sr: int) -> None:
    with wave.open(str(output), "wb") as dest:
        dest.setnchannels(1)
        dest.setsampwidth(2)
        dest.setframerate(sr)
        for path in paths:
            with wave.open(str(path), "rb") as source:
                if source.getframerate() != sr:
                    raise TrainerError("待拼接音轨的采样率不一致。")
                while frames := source.readframes(65536):
                    dest.writeframesraw(frames)
