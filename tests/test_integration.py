import json
import shutil
from pathlib import Path

import pytest

from ppt_trainer.deck import Deck, Slide, convert, prepare_deck
from ppt_trainer.errors import TrainerError
from ppt_trainer.pipeline import load_plan, produce
from ppt_trainer.planning import make_plan
from ppt_trainer.tools import ffmpeg, font_path, libreoffice
from ppt_trainer.util import file_hash, run, write_json
from conftest import make_pptx


@pytest.mark.integration
def test_mp4_both_avatar_modes_and_resume(tmp_path, cfg, image, fake_client):
    try:
        font_path(cfg.subtitles.font_path)
    except TrainerError as e:
        pytest.skip(str(e))
    deck = Deck(
        "fixture.pptx",
        "fixture",
        [
            Slide(1, str(image), "演示参数", "", file_hash(image)),
            Slide(2, str(image), "演示参数", "", file_hash(image)),
        ],
    )
    plan = make_plan(deck, None, cfg, fake_client, "", lambda _: None)
    output = tmp_path / "动画 有字幕.mp4"
    report = produce(plan, cfg, fake_client, output, lambda _: None)
    assert output.stat().st_size > 2000
    assert report["timeline"][1]["start"] == report["timeline"][0]["end"]
    assert "measured_per_utterance" in report["subtitle_timing"]
    # Decode every frame/audio packet: no credentials, no paid provider calls.
    ff = ffmpeg(cfg.tools.ffmpeg)
    run([ff, "-v", "error", "-i", str(output), "-f", "null", "-"])
    calls = len(fake_client.test_calls)
    produce(plan, cfg, fake_client, tmp_path / "resumed.mp4", lambda _: None)
    assert len(fake_client.test_calls) == calls
    cfg.avatar.mode = "loop"
    cfg.avatar.video_path = str(output)
    loop = tmp_path / "loop.mp4"
    produce(plan, cfg, fake_client, loop, lambda _: None)
    run([ff, "-v", "error", "-i", str(loop), "-f", "null", "-"])
    plan_path = output.with_suffix(".plan.json")
    edited = load_plan(plan_path)
    edited.scenes[1].narration = "改过的第二页。"
    write_json(plan_path, edited.model_dump())
    changed = load_plan(plan_path)
    produce(changed, cfg, fake_client, tmp_path / "edited.mp4", lambda _: None)
    assert len(fake_client.test_calls) > calls


@pytest.mark.integration
def test_real_libreoffice_pptx_and_ppt(tmp_path, cfg):
    try:
        libreoffice(cfg.tools.libreoffice)
    except TrainerError as e:
        pytest.skip(str(e))
    pptx = make_pptx(tmp_path / "中文 空格.pptx", ["参数一", "参数二"])
    deck = prepare_deck(pptx, cfg)
    assert len(deck.slides) == 2 and "参数一" in deck.slides[0].native_text
    assert all(Path(s.image_path).is_file() for s in deck.slides)
    # Real legacy PPT conversion + input round trip, not just extension renaming.
    legacy = convert(pptx, tmp_path / "legacy", "ppt", cfg)
    other = prepare_deck(legacy, cfg)
    assert len(other.slides) == 2


@pytest.mark.integration
def test_generate_command_full_pipeline(tmp_path, cfg, fake_client, monkeypatch):
    import yaml
    from typer.testing import CliRunner
    from ppt_trainer import cli

    source = make_pptx(tmp_path / "输入.pptx", ["第一页"])
    config = tmp_path / "config.yaml"
    raw = cfg.model_dump(mode="json")
    raw["bailian"]["api_key"] = "unit-test-token"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(cli, "BailianClient", lambda *args, **kwargs: fake_client)
    output = tmp_path / "cli.mp4"
    result = CliRunner().invoke(
        cli.app,
        ["generate", "--ppt", str(source), "--config", str(config), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert output.with_suffix(".plan.json").is_file()
    assert output.with_suffix(".manifest.json").is_file()
    assert "实际时长" in result.output
