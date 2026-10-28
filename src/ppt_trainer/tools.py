from __future__ import annotations

import os
import shutil
from pathlib import Path

from PIL import ImageFont

from .config import Settings
from .errors import TrainerError
from .util import run


def libreoffice(configured: str = "") -> str:
    candidates = [configured, shutil.which("soffice"), shutil.which("libreoffice")]
    for root in [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]:
        if root:
            candidates.append(str(Path(root) / "LibreOffice/program/soffice.exe"))
    candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise TrainerError(
        "找不到 LibreOffice。请安装后将 tools.libreoffice 指向 soffice.exe / soffice。"
    )


def ffmpeg(configured: str = "") -> str:
    if configured:
        p = shutil.which(configured) or configured
        if Path(p).is_file():
            return str(Path(p).resolve())
        raise TrainerError("tools.ffmpeg 指定的文件不存在。")
    try:
        import imageio_ffmpeg

        p = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(p).is_file():
            return p
    except (ImportError, RuntimeError):
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    raise TrainerError("找不到 FFmpeg，请执行 uv sync 或设置 tools.ffmpeg。")


def font_path(configured: str = "") -> Path:
    if configured:
        candidates = [Path(configured)]
    else:
        windows = Path(os.environ.get("WINDIR", "C:/Windows"))
        candidates = [
            windows / "Fonts/msyh.ttc",
            windows / "Fonts/simhei.ttf",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/STHeiti Light.ttc"),
        ]
    for p in candidates:
        if p.is_file():
            try:
                ImageFont.truetype(str(p), 24)
                return p.resolve()
            except OSError:
                pass
    raise TrainerError(
        "缺少中文字体。Windows 设置微软雅黑路径；Linux 安装 fonts-noto-cjk，或设置 subtitles.font_path。"
    )


def doctor(cfg: Settings) -> list[str]:
    ff = ffmpeg(cfg.tools.ffmpeg)
    lo = libreoffice(cfg.tools.libreoffice)
    font = font_path(cfg.subtitles.font_path)
    filters = run([ff, "-hide_banner", "-filters"])
    if cfg.subtitles.enabled and "subtitles" not in filters:
        raise TrainerError("该 FFmpeg 未启用 libass/subtitles，请安装支持 libass 的 FFmpeg。")
    if cfg.avatar.mode == "loop" and not Path(cfg.avatar.video_path).is_file():
        raise TrainerError("avatar.mode=loop 时必须指定存在的 avatar.video_path。")
    return [
        f"FFmpeg: {ff}",
        f"LibreOffice: {lo}",
        f"中文字体: {font}",
        "API 连通性：未检测（此命令不会收费调用模型）",
    ]
