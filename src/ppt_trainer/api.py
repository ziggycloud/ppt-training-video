from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings
from .errors import TrainerError
from .util import CACHE_VERSION, digest, write_json

T = TypeVar("T", bound=BaseModel)


def strict_schema(schema: dict) -> dict:
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    schema.pop("default", None)
    for value in schema.values():
        if isinstance(value, dict):
            strict_schema(value)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    strict_schema(child)
    return schema


class BailianClient:
    def __init__(
        self, cfg: Settings, *, refresh: bool = False, transport: httpx.BaseTransport | None = None
    ):
        self.cfg = cfg
        self.refresh = refresh
        self.http = httpx.Client(timeout=cfg.bailian.timeout_seconds, transport=transport)
        self.auth = {"Authorization": "Bearer " + cfg.bailian.api_key.get_secret_value()}
        # A digest isolates caches across accounts/endpoints without persisting a key.
        self.namespace = digest(
            [
                cfg.bailian.chat_base_url,
                cfg.bailian.native_base_url,
                cfg.bailian.api_key.get_secret_value(),
            ]
        )
        self.usage: list[dict] = []

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.cfg.bailian.retries + 1):
            try:
                response = self.http.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                # POST timeout may already have been billed. Never blindly retry it.
                raise TrainerError(
                    "百炼网络请求失败。若为配音提交超时，请核对用量后重试；不会打印密钥或原始响应。"
                ) from exc
            if response.status_code in {429, 502, 503, 504} and attempt < self.cfg.bailian.retries:
                time.sleep(min(2**attempt, 8))
                continue
            if response.is_error:
                request_id = response.headers.get("x-request-id", "未提供")
                raise TrainerError(
                    f"百炼 HTTP {response.status_code}，request_id={request_id}。检查地域、Base URL、模型权限、音色和余额。"
                )
            return response
        raise TrainerError("百炼重试次数已用尽。")

    def json_model(
        self, stage: str, system: str, text: str, result_type: type[T], *, image: Path | None = None
    ) -> T:
        cfg = self.cfg.bailian
        content: list[dict] = [{"type": "text", "text": text}]
        if image:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(image.read_bytes()).decode("ascii")
                    },
                }
            )
        body = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": result_type.__name__,
                    "strict": True,
                    "schema": strict_schema(result_type.model_json_schema()),
                },
            },
        }
        key = digest([CACHE_VERSION, self.namespace, body])
        path = Path(self.cfg.cache_dir) / "llm" / f"{key}.json"
        if path.is_file() and not self.refresh:
            try:
                return result_type.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, ValueError):
                pass
        for repair in range(2):
            response = self.request(
                "POST",
                cfg.chat_base_url.rstrip("/") + "/chat/completions",
                headers=self.auth,
                json=body,
            )
            try:
                raw = response.json()
                answer = raw["choices"][0]["message"]["content"]
                if raw["choices"][0].get("finish_reason") == "length":
                    raise TrainerError("模型输出被截断，请提高 max_output_tokens 或减少单页内容。")
                parsed = result_type.model_validate_json(answer)
            except TrainerError:
                raise
            except (KeyError, ValueError, TypeError, ValidationError):
                if repair == 0:
                    body["messages"].append(
                        {
                            "role": "user",
                            "content": "上次输出不符合指定JSON Schema。请仅返回完整且合法的JSON，不要省略必填字段。",
                        }
                    )
                    continue
                raise TrainerError(
                    f"{stage} 两次返回均未通过结构校验，已停止以避免错配。"
                ) from None
            self.usage.append(
                {"stage": stage, "request_id": raw.get("id"), "usage": raw.get("usage", {})}
            )
            write_json(path, parsed.model_dump())
            return parsed
        raise TrainerError("结构化模型调用失败。")

    def synthesize(self, text: str) -> tuple[bytes, dict]:
        cfg = self.cfg.tts
        url = self.cfg.bailian.native_base_url.rstrip("/") + "/services/audio/tts/SpeechSynthesizer"
        response = self.request(
            "POST",
            url,
            headers=self.auth,
            json={
                "model": cfg.model,
                "input": {
                    "text": text,
                    "voice": cfg.voice,
                    "format": "wav",
                    "sample_rate": cfg.sample_rate,
                    "rate": cfg.rate,
                },
            },
        )
        try:
            data = response.json()
            audio = data["output"]["audio"]
        except (ValueError, KeyError, TypeError):
            raise TrainerError(
                "TTS 响应缺少 output.audio，请确认 native_base_url 和模型协议。"
            ) from None
        if audio.get("data"):
            try:
                content = base64.b64decode(audio["data"], validate=True)
            except (ValueError, TypeError):
                raise TrainerError("TTS 返回的音频编码不合法。") from None
        elif audio.get("url"):
            audio_url = audio["url"]
            parts = urlsplit(audio_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
                raise TrainerError("TTS 返回了不支持的音频下载地址。")
            # Do not send API authorization to object-storage download URLs.
            downloaded = self.request("GET", audio_url)
            content = downloaded.content
        else:
            raise TrainerError("TTS 没有返回音频文件。")
        if not 44 < len(content) <= 30 * 1024**2:
            raise TrainerError("TTS 音频为空或超过30MB，已停止。")
        metadata = {
            "stage": "tts",
            "request_id": data.get("request_id"),
            "usage": data.get("usage", {}),
        }
        self.usage.append(metadata)
        return content, metadata
