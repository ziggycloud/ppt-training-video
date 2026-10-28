from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .errors import TrainerError


CACHE_VERSION = "ppt-trainer-0.1.0-v1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 300) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise TrainerError(f"找不到可执行程序：{Path(command[0]).name}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TrainerError(f"{Path(command[0]).name} 执行超时（{timeout} 秒）。") from exc
    if result.returncode:
        # Tool output may contain local paths, but never API headers/configuration.
        detail = result.stderr.decode("utf-8", errors="replace")[-1800:]
        raise TrainerError(f"{Path(command[0]).name} 执行失败：{detail}")
    return result.stdout.decode("utf-8", errors="replace")
