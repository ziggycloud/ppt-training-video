from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .api import BailianClient
from .audio import concatenate_audio, synthesize_slide
from .config import Settings
from .errors import TrainerError
from .models import Plan
from .render import join_video, render_slide, srt_time
from .util import CACHE_VERSION, digest, file_hash, write_json


def load_plan(path: Path) -> Plan:
    try:
        plan = Plan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise TrainerError(
            "计划文件无法读取或结构不合法。仅编辑 narration/title，其他字段需保持有效。"
        ) from exc
    if len({s.scene_id for s in plan.scenes}) != len(plan.scenes):
        raise TrainerError("计划中的 scene_id 重复。")
    for scene in plan.scenes:
        image = Path(scene.image_path)
        if not image.is_absolute():
            image = path.resolve().parent / image
            scene.image_path = str(image.resolve())
        if not image.is_file() or file_hash(image) != scene.image_sha256:
            raise TrainerError(f"第{scene.slide_number}页画面缺失或已改变，请重新生成计划。")
        if not set(scene.fact_refs) <= set(plan.evidence):
            raise TrainerError("计划引用了不存在的事实。")
    return plan


def produce(
    plan: Plan, cfg: Settings, client: BailianClient, output: Path, progress: Callable[[str], None]
) -> dict:
    output = output.resolve()
    if output.suffix.lower() != ".mp4":
        raise TrainerError("输出文件必须以 .mp4 结尾。")
    render_key = digest(
        [
            CACHE_VERSION,
            plan.model_dump(),
            cfg.video.model_dump(),
            cfg.avatar.model_dump(),
            cfg.subtitles.model_dump(),
            cfg.tts.model_dump(),
        ]
    )
    root = Path(cfg.cache_dir) / "renders" / render_key
    root.mkdir(parents=True, exist_ok=True)
    videos, audios, timeline, subtitles = [], [], [], []
    offset = 0.0
    loop_hash = file_hash(Path(cfg.avatar.video_path)) if cfg.avatar.mode == "loop" else None
    from .tools import font_path

    font_hash = file_hash(font_path(cfg.subtitles.font_path)) if cfg.subtitles.enabled else None
    for number, scene in enumerate(plan.scenes, 1):
        if file_hash(Path(scene.image_path)) != scene.image_sha256:
            raise TrainerError("页面图像在生成过程中发生变化，请重新运行。")
        progress(f"配音 {number}/{len(plan.scenes)}：{scene.title}")
        # Scene hash includes all media inputs, not API key or configuration serialization.
        scene_key = digest(
            [
                CACHE_VERSION,
                scene.model_dump(),
                cfg.tts.model_dump(),
                cfg.video.model_dump(),
                cfg.avatar.model_dump(),
                cfg.subtitles.model_dump(),
                loop_hash,
                font_hash,
                client.namespace,
            ]
        )
        folder = root / scene_key
        audio = synthesize_slide(scene.narration, cfg, client, folder)
        cached = folder / "render.json"
        video = folder / "video.mp4"
        reuse = False
        if cached.is_file() and video.is_file() and not client.refresh:
            try:
                reuse = json.loads(cached.read_text(encoding="utf-8")).get("sha256") == file_hash(
                    video
                )
            except ValueError:
                pass
        if not reuse:
            progress(f"渲染 {number}/{len(plan.scenes)}：{cfg.avatar.mode}")
            video = render_slide(Path(scene.image_path), audio, folder, cfg)
            write_json(cached, {"sha256": file_hash(video)})
        videos.append(video)
        audios.append(audio.path)
        timeline.append(
            {
                "scene_id": scene.scene_id,
                "slide_number": scene.slide_number,
                "title": scene.title,
                "start": offset,
                "end": offset + audio.duration,
                "frames": audio.frames,
                "audio_sha256": file_hash(audio.path),
                "fact_refs": scene.fact_refs,
                "warnings": scene.warnings,
            }
        )
        for cue in audio.cues:
            subtitles.append(
                f"{len(subtitles) + 1}\n{srt_time(offset + cue.start)} --> {srt_time(offset + cue.end)}\n{cue.text}\n"
            )
        offset += audio.duration
    progress("拼接画面与唯一主音轨")
    master = root / "master.wav"
    concatenate_audio(audios, master, cfg.tts.sample_rate)
    join_video(videos, master, output, root, cfg)
    output.with_suffix(".srt").write_text("\n".join(subtitles), encoding="utf-8-sig")
    manifest = {
        "version": 1,
        "duration_seconds": offset,
        "target_minutes": plan.target_minutes,
        "subtitle_timing": "measured_per_utterance_audio_not_word_alignment",
        "avatar_mode": cfg.avatar.mode,
        "lip_sync": "audio_amplitude"
        if cfg.avatar.mode == "cartoon"
        else ("loop_unrelated_to_speech" if cfg.avatar.mode == "loop" else "none"),
        "source_names": plan.source_names,
        "source_hashes": plan.source_hashes,
        "models": {
            "vision_and_script": cfg.bailian.model,
            "tts": cfg.tts.model,
            "voice": cfg.tts.voice,
        },
        "timeline": timeline,
        "calls_this_run": client.usage,
        "output_sha256": file_hash(output),
    }
    write_json(output.with_suffix(".manifest.json"), manifest)
    write_json(output.with_suffix(".plan.json"), plan.model_dump())
    return manifest
