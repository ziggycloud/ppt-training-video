import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ppt_trainer.api import BailianClient
from ppt_trainer.audio import split_utterances, synthesize_slide, wav_info
from ppt_trainer.config import load_config
from ppt_trainer.deck import Deck, Slide, extract_ooxml, related_path
from ppt_trainer.errors import TrainerError
from ppt_trainer.models import PageAnalysis
from ppt_trainer.planning import make_plan
from ppt_trainer.render import srt_time, wrap_caption
from ppt_trainer.util import file_hash
from conftest import make_pptx


def test_config_secret_never_printed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "bailian:\n  api_key: TOP_SECRET_VALUE\n  timeout_seconds: nope\n", encoding="utf-8"
    )
    with pytest.raises(TrainerError) as exc:
        load_config(path)
    assert "TOP_SECRET" not in str(exc.value)


def test_environment_overrides_key(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("bailian:\n  api_key: yaml-value\n")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-value")
    cfg = load_config(p)
    assert cfg.bailian.api_key.get_secret_value() == "env-value"
    assert Path(cfg.cache_dir).is_absolute()


def test_utterances_keep_content_and_boundaries():
    text = "接口为2.5GbE，需要检查设备兼容条件。第二句内容较长，请仔细核对参数以及版本。"
    chunks = split_utterances(text, 18)
    assert "".join(chunks) == text
    assert all(0 < len(x) <= 18 for x in chunks)
    assert any("2.5GbE" in x for x in chunks)


def test_ooxml_order(tmp_path):
    p = make_pptx(tmp_path / "test.pptx", ["第一章", "第二章"])
    pages = extract_ooxml(p, 10)
    assert "第一章" in pages[0][0] and "第二章" in pages[1][0]
    assert (
        related_path("ppt/slides/slide1.xml", "../notesSlides/n1.xml") == "ppt/notesSlides/n1.xml"
    )
    with pytest.raises(TrainerError):
        related_path("ppt/presentation.xml", "../../../bad")


def test_api_uses_multimodal_schema_and_cache(cfg, image, fake_client):
    cfg.require_api()
    first = fake_client.json_model("vision", "return JSON", "source", PageAnalysis, image=image)
    body = json.loads(fake_client.test_calls[0].content)
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert body["response_format"]["json_schema"]["strict"] is True
    again = fake_client.json_model("vision", "return JSON", "source", PageAnalysis, image=image)
    assert first == again and len(fake_client.test_calls) == 1


def test_tts_measured_timing_and_cache(cfg, fake_client, tmp_path):
    a = synthesize_slide("第一句话。第二句话。", cfg, fake_client, tmp_path / "a")
    assert len(a.cues) == 2
    assert a.cues[1].start == pytest.approx(a.cues[0].end)
    frames, sr = wav_info(a.path)
    assert frames / sr == a.duration
    assert a.frames / cfg.video.fps == a.duration
    count = len(fake_client.test_calls)
    synthesize_slide("第一句话。第二句话。", cfg, fake_client, tmp_path / "b")
    assert len(fake_client.test_calls) == count
    cfg.tts.rate = 1.2
    synthesize_slide("第一句话。第二句话。", cfg, fake_client, tmp_path / "c")
    assert len(fake_client.test_calls) > count


def test_errors_hide_provider_secret(cfg):
    key = "private-test-value"
    cfg.bailian.api_key = SecretStr(key)
    client = BailianClient(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"message": key}))
    )
    with pytest.raises(TrainerError) as exc:
        client.synthesize("hello")
    assert key not in str(exc.value)
    client.close()


def test_compare_uses_second_deck_and_custom_focus(cfg, image, fake_client):
    def deck(name):
        return Deck(name, name, [Slide(1, str(image), "演示参数", "", file_hash(image))])

    plan = make_plan(
        deck("old.pptx"), deck("new.pptx"), cfg, fake_client, "只讲售后差异", lambda _: None
    )
    assert plan.mode == "compare" and plan.focus == "只讲售后差异"
    assert plan.scenes[0].fact_refs[0].startswith("B_") or plan.scenes[0].fact_refs[0].startswith(
        "A_"
    )
    assert list(plan.comparisons) == ["B_p0001"]
    assert plan.source_names == ["old.pptx", "new.pptx"]
    texts = [r.content.decode() for r in fake_client.test_calls if r.method == "POST"]
    assert any("只讲售后差异" in t for t in texts)


def test_subtitle_escape_and_time():
    assert srt_time(61.125) == "00:01:01,125"
    assert "{" not in wrap_caption("{\\an8}恶意字幕", 22)


def test_packaged_template_matches_root():
    from importlib.resources import files

    root = Path(__file__).resolve().parents[1]
    assert files("ppt_trainer").joinpath("config.example.yaml").read_text(encoding="utf-8") == (
        root / "config.example.yaml"
    ).read_text(encoding="utf-8")
