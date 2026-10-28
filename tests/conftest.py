from __future__ import annotations

import io
import json
import math
import wave
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import numpy as np
import pytest
from PIL import Image
from pydantic import SecretStr

from ppt_trainer.api import BailianClient
from ppt_trainer.config import Settings


def test_tone(seconds: float = 0.36) -> bytes:
    """Synthetic test signal, never represented as real synthesized speech."""
    sr = 24000
    t = np.arange(round(seconds * sr)) / sr
    samples = (0.15 * np.sin(2 * math.pi * 440 * t)).astype(np.float32)
    samples[len(samples) // 3 : len(samples) // 2] = 0
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((samples * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def make_pptx(path: Path, titles: list[str]) -> Path:
    """Minimal editable OOXML fixture used only to test parsing and office conversion."""
    types = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    ]
    ids, rels = [], []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        )
        for i, title in enumerate(titles, 1):
            types.append(
                f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            )
            ids.append(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>')
            rels.append(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
            )
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                f"""<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="500000" y="500000"/><a:ext cx="8000000" cy="1800000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="3600"><a:solidFill><a:srgbClr val="203050"/></a:solidFill></a:rPr><a:t>{escape(title)}</a:t></a:r></a:p><a:p><a:r><a:rPr sz="2200"/><a:t>演示参数：{i * 100} Mbps</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>""",
            )
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            + "".join(types)
            + "</Types>",
        )
        z.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst>'
            + "".join(ids)
            + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
        )
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
    return path


@pytest.fixture
def cfg(tmp_path) -> Settings:
    s = Settings()
    s.bailian.api_key = SecretStr("unit-test-token")
    s.bailian.chat_base_url = "http://localhost/compatible-mode/v1"
    s.bailian.native_base_url = "http://localhost/api/v1"
    s.cache_dir = str(tmp_path / "cache")
    s.video.width = 640
    s.video.height = 360
    s.video.fps = 25
    s.video.preset = "ultrafast"
    s.subtitles.font_size = 18
    s.subtitles.max_chars_per_line = 18
    s.tts.pause_after_slide_seconds = 0.12
    return s


@pytest.fixture
def image(tmp_path) -> Path:
    p = tmp_path / "slide.png"
    Image.new("RGB", (640, 360), "#ffffff").save(p)
    return p


@pytest.fixture
def fake_client(cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=test_tone())
        body = json.loads(request.content)
        if "SpeechSynthesizer" in request.url.path:
            assert body["input"]["format"] == "wav"
            return httpx.Response(
                200,
                json={
                    "request_id": "test-tts",
                    "output": {"audio": {"url": "https://audio.example/test.wav"}},
                    "usage": {"characters": len(body["input"]["text"])},
                },
            )
        name = body["response_format"]["json_schema"]["name"]
        if name == "PageAnalysis":
            answer = {
                "title": "培训示例",
                "summary": "参数与条件",
                "facts": [
                    {
                        "fact_id": "temporary",
                        "text": "参数为100 Mbps",
                        "evidence": "演示参数",
                        "evidence_type": "native_text",
                    }
                ],
                "unknowns": [],
            }
        elif name == "Alignment":
            # Parse generated facts from the supplied context rather than invent IDs.
            text = body["messages"][1]["content"][0]["text"]
            import re

            old = re.findall(r"A_p\d+_f\d+", text)
            new = re.findall(r"B_p\d+_f\d+", text)
            answer = {
                "old_slide_numbers": [1],
                "differences": [
                    {
                        "kind": "changed",
                        "description": "示例变化",
                        "old_fact_refs": old[:1],
                        "new_fact_refs": new[:1],
                    }
                ],
                "focus_reason": "按新版讲解",
            }
        else:
            text = body["messages"][1]["content"][0]["text"]
            import re

            refs = list(dict.fromkeys(re.findall(r"[AB]_p\d+_f\d+", text)))
            answer = {
                "narration": "这是测试讲稿。请核对参数。",
                "fact_refs": refs[:1],
                "warnings": [],
            }
        return httpx.Response(
            200,
            json={
                "id": "test-chat",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(answer, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    client = BailianClient(cfg, transport=httpx.MockTransport(handler))
    client.test_calls = calls
    yield client
    client.close()
