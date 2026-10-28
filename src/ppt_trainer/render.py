from __future__ import annotations

import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .audio import Cue, SlideAudio
from .config import Settings
from .errors import TrainerError
from .tools import ffmpeg, font_path
from .util import run


def srt_time(value: float) -> str:
    ms = round(value * 1000)
    seconds, ms = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def ass_time(value: float) -> str:
    centis = round(value * 100)
    seconds, centis = divmod(centis, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}.{centis:02}"


def wrap_caption(text: str, limit: int) -> str:
    # Fullwidth replacements prevent ASS override-tag injection from source text.
    text = (
        text.replace("\\", "＼")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\r", "")
        .replace("\n", " ")
    )
    return "\\N".join(text[i : i + limit] for i in range(0, len(text), limit))


def write_ass(cues: list[Cue], path: Path, cfg: Settings, family: str) -> None:
    r, g, b = (int(cfg.subtitles.color[i : i + 2], 16) for i in (1, 3, 5))
    color = f"&H00{b:02X}{g:02X}{r:02X}"
    family = family.replace(",", " ").replace("\n", " ")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.video.width}
PlayResY: {cfg.video.height}
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{family},{cfg.subtitles.font_size},{color},&H000000FF,&H001A263B,&H901A263B,0,0,0,0,100,100,0,0,3,2,0,2,30,30,25,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},Default,,0,0,0,,{wrap_caption(c.text, cfg.subtitles.max_chars_per_line)}"
        for c in cues
    ]
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def cartoon_sprite(width: int, height: int, mouth: int, blink: bool, cfg: Settings) -> Image.Image:
    # Original, code-drawn 2D teaching mascot. No external image or model license required.
    scale = 2
    img = Image.new("RGB", (width * scale, height * scale), cfg.video.background)
    d = ImageDraw.Draw(img)
    w, h = img.size
    x, y = w / 2, h * 0.37
    face_w, face_h = w * 0.39, h * 0.28
    hair = "#293854"
    d.ellipse((w * 0.12, h * 0.89, w * 0.88, h * 0.96), fill="#dce3ef")
    d.rounded_rectangle(
        (w * 0.14, h * 0.57, w * 0.86, h * 0.91), radius=w * 0.18, fill=cfg.avatar.coat_color
    )
    d.rounded_rectangle(
        (w * 0.43, h * 0.48, w * 0.57, h * 0.64), radius=w * 0.06, fill=cfg.avatar.skin_color
    )
    d.ellipse(
        (x - face_w * 0.58, y - face_h * 0.65, x + face_w * 0.58, y + face_h * 0.55), fill=hair
    )
    d.rounded_rectangle(
        (x - face_w / 2, y - face_h / 2, x + face_w / 2, y + face_h / 2),
        radius=face_w * 0.36,
        fill=cfg.avatar.skin_color,
    )
    d.ellipse(
        (x - face_w * 0.55, y - face_h * 0.57, x + face_w * 0.2, y - face_h * 0.17), fill=hair
    )
    for eye in [x - face_w * 0.22, x + face_w * 0.22]:
        if blink:
            d.line(
                (eye - 8 * scale, y - 2 * scale, eye + 8 * scale, y - 2 * scale),
                fill=hair,
                width=3 * scale,
            )
        else:
            d.ellipse((eye - 4 * scale, y - 6 * scale, eye + 4 * scale, y + 2 * scale), fill=hair)
    opening = (1 + mouth * 1.8) * scale
    d.ellipse(
        (
            x - 9 * scale,
            y + face_h * 0.22 - opening / 2,
            x + 9 * scale,
            y + face_h * 0.22 + opening / 2,
        ),
        fill="#a35f5b",
    )
    d.polygon([(w * 0.38, h * 0.60), (w * 0.50, h * 0.70), (w * 0.62, h * 0.60)], fill="white")
    d.line(
        (w * 0.24, h * 0.68, w * 0.09, h * 0.77), fill=cfg.avatar.skin_color, width=int(w * 0.075)
    )
    d.line(
        (w * 0.76, h * 0.68, w * 0.91, h * 0.77), fill=cfg.avatar.skin_color, width=int(w * 0.075)
    )
    return img.resize((width, height), Image.Resampling.LANCZOS)


def make_cartoon(audio: SlideAudio, folder: Path, cfg: Settings, width: int, height: int) -> Path:
    output = folder / "avatar.mp4"
    with wave.open(str(audio.path), "rb") as stream:
        sr = stream.getframerate()
        pcm = (
            np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32)
            / 32768
        )
    step = sr // cfg.video.fps
    energies = np.sqrt(np.mean(pcm.reshape(-1, step) ** 2, axis=1))
    reference = max(float(np.percentile(energies, 90)), cfg.avatar.silence_threshold * 2, 0.02)
    sprites = {
        (mouth, blink): np.asarray(cartoon_sprite(width, height, mouth, blink, cfg)).tobytes()
        for mouth in range(6)
        for blink in (False, True)
    }
    ff = ffmpeg(cfg.tools.ffmpeg)
    command = [
        ff,
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(cfg.video.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        cfg.video.preset,
        "-crf",
        str(cfg.video.crf),
        "-threads",
        str(cfg.video.threads),
        str(output),
    ]
    log = folder / "avatar-encoder.log"
    with log.open("wb") as stderr:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr
        )
        try:
            assert process.stdin is not None
            for index, rms in enumerate(energies):
                mouth = (
                    0
                    if rms < cfg.avatar.silence_threshold
                    else max(1, min(5, round(float(rms) / reference * 5)))
                )
                blink = (index + 13) % (cfg.video.fps * 4) < max(2, cfg.video.fps // 8)
                process.stdin.write(sprites[(mouth, blink)])
            process.stdin.close()
            process.wait(timeout=max(60, int(audio.duration * 4)))
            if process.returncode:
                raise TrainerError("2D 动画编码失败，请检查 FFmpeg 与输出目录。")
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.kill()
            process.wait()
            raise
    return output


def render_slide(image: Path, audio: SlideAudio, folder: Path, cfg: Settings) -> Path:
    ff = ffmpeg(cfg.tools.ffmpeg)
    v = cfg.video
    padding = max(12, round(v.width * 0.015))
    subtitle_height = (
        (cfg.subtitles.font_size * cfg.subtitles.max_lines + 65)
        if cfg.subtitles.enabled
        else padding
    )
    area_h = v.height - subtitle_height - padding * 2
    avatar_w = (round(v.width * cfg.avatar.width_fraction) // 2) * 2
    avatar_h = min((round(avatar_w * 1.6) // 2) * 2, (area_h // 2) * 2)
    slide_w = v.width - padding * 2
    slide_x = padding
    if cfg.avatar.mode != "none":
        slide_w -= avatar_w + padding
        if cfg.avatar.side == "left":
            slide_x += avatar_w + padding
    slide_h = area_h
    # Pre-fit original slides with Pillow: no shell/filter escaping of user-supplied image paths.
    background = Image.new("RGB", (v.width, v.height), v.background)
    with Image.open(image) as source:
        page = source.convert("RGB")
        page.thumbnail((slide_w, slide_h), Image.Resampling.LANCZOS)
        background.paste(
            page, (slide_x + (slide_w - page.width) // 2, padding + (slide_h - page.height) // 2)
        )
    background.save(folder / "background.png")
    command = [
        ff,
        "-y",
        "-v",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(v.fps),
        "-i",
        "background.png",
    ]
    filters = []
    last = "0:v"
    if cfg.avatar.mode != "none":
        if cfg.avatar.mode == "cartoon":
            avatar_path = make_cartoon(audio, folder, cfg, avatar_w, avatar_h)
            command += ["-i", str(avatar_path)]
        else:
            if not cfg.avatar.video_path or not Path(cfg.avatar.video_path).is_file():
                raise TrainerError("循环视频不存在，请设置 avatar.video_path。")
            command += ["-stream_loop", "-1", "-i", str(Path(cfg.avatar.video_path).resolve())]
        x = padding if cfg.avatar.side == "left" else v.width - avatar_w - padding
        y = padding + (area_h - avatar_h) // 2
        filters.append(
            f"[1:v]fps={v.fps},scale={avatar_w}:{avatar_h}:force_original_aspect_ratio=decrease,"
            f"pad={avatar_w}:{avatar_h}:(ow-iw)/2:(oh-ih)/2:color={v.background.replace('#', '0x')},setsar=1[avatar]"
        )
        filters.append(f"[{last}][avatar]overlay={x}:{y}:shortest=1[combined]")
        last = "combined"
    if cfg.subtitles.enabled:
        font = font_path(cfg.subtitles.font_path)
        fonts = folder / "fonts"
        fonts.mkdir(exist_ok=True)
        shutil.copyfile(font, fonts / ("caption" + font.suffix))
        family = ImageFont.truetype(str(font), 20).getname()[0]
        write_ass(audio.cues, folder / "captions.ass", cfg, family)
        filters.append(f"[{last}]subtitles=filename=captions.ass:fontsdir=fonts[captioned]")
        last = "captioned"
    output = folder / "video.mp4"
    if filters:
        command += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    else:
        command += ["-map", "0:v"]
    command += [
        "-an",
        "-frames:v",
        str(audio.frames),
        "-r",
        str(v.fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        v.preset,
        "-crf",
        str(v.crf),
        "-threads",
        str(v.threads),
        "-video_track_timescale",
        str(v.fps * 1000),
        str(output),
    ]
    run(command, cwd=folder, timeout=max(300, int(audio.duration * 10)))
    return output


def join_video(
    videos: list[Path], master_audio: Path, output: Path, folder: Path, cfg: Settings
) -> None:
    # Copy/link to controlled ASCII filenames so concat lists are portable and cannot be injected.
    concat = folder / "concat"
    concat.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, video in enumerate(videos):
        dest = concat / f"segment_{i:05}.mp4"
        dest.unlink(missing_ok=True)
        try:
            import os

            os.link(video, dest)
        except OSError:
            shutil.copyfile(video, dest)
        rows.append(f"file '{dest.name}'")
    listing = concat / "list.txt"
    listing.write_text("\n".join(rows) + "\n", encoding="utf-8")
    joined = folder / "joined.mp4"
    ff = ffmpeg(cfg.tools.ffmpeg)
    run(
        [
            ff,
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            "list.txt",
            "-map",
            "0:v",
            "-c:v",
            "copy",
            "-an",
            str(joined),
        ],
        cwd=concat,
        timeout=600,
    )
    temporary = output.with_name(output.stem + ".partial.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ff,
            "-y",
            "-v",
            "error",
            "-i",
            str(joined),
            "-i",
            str(master_audio),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        timeout=1800,
    )
    temporary.replace(output)
