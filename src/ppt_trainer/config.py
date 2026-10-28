from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .errors import TrainerError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Bailian(StrictModel):
    api_key: SecretStr = SecretStr("")
    chat_base_url: str = ""
    native_base_url: str = ""
    timeout_seconds: int = Field(180, ge=5, le=1800)
    retries: int = Field(2, ge=0, le=6)
    model: str = "qwen3.7-plus-2026-05-26"
    temperature: float = Field(0.2, ge=0, le=2)
    max_output_tokens: int = Field(8000, ge=512, le=32768)


class Course(StrictModel):
    target_minutes: float = Field(20, gt=0, le=240)
    characters_per_minute: int = Field(220, ge=80, le=400)
    audience: str = "规培学员"
    language: str = "中文普通话"
    max_slides: int = Field(100, ge=1, le=500)
    max_file_mb: int = Field(100, ge=1, le=500)
    comparison_candidates: int = Field(5, ge=1, le=20)
    focus: str = Field("", max_length=8000)
    extra_instructions: str = "不得补造资料中不存在的事实。"


class TTS(StrictModel):
    model: str = "cosyvoice-v3-flash"
    voice: str = "longanyang"
    rate: float = Field(1, ge=0.5, le=2)
    sample_rate: Literal[24000, 48000] = 24000
    max_chars_per_request: int = Field(42, ge=5, le=300)
    pause_after_slide_seconds: float = Field(0.5, ge=0, le=10)


class Video(StrictModel):
    width: int = Field(1920, ge=640, le=3840)
    height: int = Field(1080, ge=360, le=2160)
    fps: Literal[24, 25, 30] = 25
    crf: int = Field(23, ge=14, le=35)
    preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"] = "veryfast"
    threads: int = Field(2, ge=1, le=32)
    background: str = "#eef2f8"

    @field_validator("width", "height")
    @classmethod
    def even_dimensions(cls, value: int) -> int:
        if value % 2:
            raise ValueError("视频宽高必须为偶数")
        return value


class Avatar(StrictModel):
    mode: Literal["cartoon", "loop", "none"] = "cartoon"
    video_path: str = ""
    side: Literal["left", "right"] = "right"
    width_fraction: float = Field(0.22, ge=0.12, le=0.35)
    coat_color: str = "#516bd4"
    skin_color: str = "#f4ccb1"
    silence_threshold: float = Field(0.012, ge=0, le=1)


class Subtitles(StrictModel):
    enabled: bool = True
    font_path: str = ""
    font_size: int = Field(38, ge=12, le=100)
    max_chars_per_line: int = Field(22, ge=8, le=60)
    max_lines: int = Field(2, ge=1, le=3)
    color: str = "#ffffff"


class Tools(StrictModel):
    libreoffice: str = ""
    ffmpeg: str = ""
    conversion_timeout_seconds: int = Field(180, ge=10, le=1800)


class Settings(StrictModel):
    bailian: Bailian = Field(default_factory=Bailian)
    course: Course = Field(default_factory=Course)
    tts: TTS = Field(default_factory=TTS)
    video: Video = Field(default_factory=Video)
    avatar: Avatar = Field(default_factory=Avatar)
    subtitles: Subtitles = Field(default_factory=Subtitles)
    tools: Tools = Field(default_factory=Tools)
    cache_dir: str = ".ppt-trainer"

    def require_api(self, *, chat: bool = True) -> None:
        from urllib.parse import urlsplit

        if not self.bailian.api_key.get_secret_value().strip():
            raise TrainerError("请在本地 YAML 填写 bailian.api_key，或设置 DASHSCOPE_API_KEY。")
        names = ["native_base_url"] + (["chat_base_url"] if chat else [])
        for name in names:
            value = getattr(self.bailian, name)
            url = urlsplit(value)
            if (
                not url.hostname
                or "YOUR_" in value
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (
                    url.scheme != "https"
                    and not (
                        url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
                    )
                )
            ):
                raise TrainerError(f"请填写有效的 bailian.{name}；生产地址必须为 HTTPS。")
            suffix = "/compatible-mode/v1" if name == "chat_base_url" else "/api/v1"
            if not url.path.rstrip("/").endswith(suffix):
                raise TrainerError(f"bailian.{name} 应以 {suffix} 结尾，不要填写完整接口路径。")


def load_config(path: Path) -> Settings:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = Settings.model_validate(raw)
    except OSError as exc:
        raise TrainerError(f"无法读取配置文件：{path.name}；先执行 ppt-trainer init。") from exc
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        # Do not print validation input: it could contain an API key.
        if isinstance(exc, ValidationError):
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise TrainerError(f"配置字段不合法：{fields}，请对照 config.example.yaml。") from exc
        raise TrainerError("YAML 配置格式不合法，请检查缩进与字段。") from exc
    if os.environ.get("DASHSCOPE_API_KEY"):
        cfg.bailian.api_key = SecretStr(os.environ["DASHSCOPE_API_KEY"])
    root = path.resolve().parent
    for obj, name in [(cfg, "cache_dir"), (cfg.avatar, "video_path"), (cfg.subtitles, "font_path")]:
        value = getattr(obj, name)
        if value:
            p = Path(value).expanduser()
            setattr(obj, name, str(p if p.is_absolute() else root / p))
    for value in [
        cfg.video.background,
        cfg.avatar.coat_color,
        cfg.avatar.skin_color,
        cfg.subtitles.color,
    ]:
        import re

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise TrainerError("颜色必须采用 #RRGGBB 格式。")
    return cfg
