from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

from .api import BailianClient
from .config import load_config
from .deck import prepare_deck
from .errors import TrainerError
from .pipeline import load_plan, produce
from .planning import make_plan
from .tools import doctor as check_tools
from .util import write_json

app = typer.Typer(
    no_args_is_help=True, help="百炼低成本 PPT 培训视频：逐页理解、配音、字幕与讲师动画。"
)
ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="本地YAML配置")]


def handle_error(error: Exception) -> None:
    if isinstance(error, TrainerError):
        typer.secho(f"错误：{error}", fg=typer.colors.RED, err=True)
    elif isinstance(error, KeyboardInterrupt):
        typer.echo("已中断；成功阶段的缓存保留，可用相同参数继续。", err=True)
    else:
        # Never dump raw HTTP/config errors containing credentials.
        typer.secho(
            f"运行失败（{type(error).__name__}）。请检查输入文件、模型配置和系统依赖。",
            fg=typer.colors.RED,
            err=True,
        )
    raise typer.Exit(1)


@app.command()
def init(config: ConfigOpt = Path("config.yaml")) -> None:
    """生成本地配置模板，不覆盖已有文件。"""
    if config.exists():
        raise typer.BadParameter("配置已存在，拒绝覆盖。")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        files("ppt_trainer").joinpath("config.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    typer.echo(f"已生成 {config}；填写 Key 与两类 Base URL。请勿提交真实配置。")


@app.command()
def doctor(config: ConfigOpt = Path("config.yaml")) -> None:
    """检查LibreOffice、FFmpeg和中文字体，不调用收费API。"""
    try:
        for message in check_tools(load_config(config)):
            typer.echo(message)
    except Exception as exc:
        handle_error(exc)


@app.command()
def generate(
    ppt: Annotated[Path, typer.Option("--ppt", help="单课件，或双资料中的前者/参考版")],
    compare: Annotated[
        Path | None, typer.Option("--compare", help="后者/新版，以它的页面顺序和内容为主")
    ] = None,
    config: ConfigOpt = Path("config.yaml"),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output/lesson.mp4"),
    focus: Annotated[
        str | None, typer.Option("--focus", help="讲解或比较侧重点；空字符串使用默认策略")
    ] = None,
    focus_file: Annotated[
        Path | None, typer.Option("--focus-file", help="UTF-8提示词文件，避免多行命令转义")
    ] = None,
    target_minutes: Annotated[
        float | None, typer.Option("--target-minutes", min=0.1, max=240)
    ] = None,
    plan_only: Annotated[
        bool, typer.Option("--plan-only", help="只生成可编辑讲稿，不配音、不渲染")
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="忽略缓存，重新调用模型，可能重复计费")
    ] = False,
) -> None:
    """从单份或两份PPT生成MP4；默认复用成功缓存。"""
    client = None
    try:
        if focus is not None and focus_file is not None:
            raise TrainerError("--focus 和 --focus-file 只能选一个。")
        cfg = load_config(config)
        cfg.require_api()
        check_tools(cfg)
        if target_minutes is not None:
            cfg.course.target_minutes = target_minutes
        prompt = (
            focus_file.read_text(encoding="utf-8-sig")
            if focus_file
            else (cfg.course.focus if focus is None else focus)
        )
        if len(prompt) > 8000:
            raise TrainerError("侧重点提示词不得超过8000字符。")
        output = output.resolve()
        if output.suffix.lower() != ".mp4":
            raise TrainerError("--output 必须为 .mp4。")
        typer.echo("解析与渲染课件")
        old = prepare_deck(ppt, cfg, refresh=refresh)
        newer = prepare_deck(compare, cfg, refresh=refresh) if compare else None
        client = BailianClient(cfg, refresh=refresh)
        plan = make_plan(old, newer, cfg, client, prompt, typer.echo)
        path = output.with_suffix(".plan.json")
        write_json(path, plan.model_dump())
        warnings = sum(len(s.warnings) for s in plan.scenes)
        typer.echo(f"讲稿已保存：{path}；待核对提示 {warnings} 条。")
        if plan_only:
            typer.echo("已停在讲稿阶段。编辑 narration 后执行 ppt-trainer render --plan ...")
            return
        result = produce(plan, cfg, client, output, typer.echo)
        typer.echo(
            f"完成：{output}\n实际时长：{result['duration_seconds'] / 60:.2f} 分钟（目标仅用于规划）。"
        )
    except (Exception, KeyboardInterrupt) as exc:
        handle_error(exc)
    finally:
        if client:
            client.close()


@app.command()
def render(
    plan: Annotated[Path, typer.Option("--plan", help="generate --plan-only 产生的JSON")],
    config: ConfigOpt = Path("config.yaml"),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output/lesson.mp4"),
    refresh: Annotated[bool, typer.Option("--refresh")] = False,
) -> None:
    """从已检查/编辑的讲稿继续配音与视频合成，不重新调用理解模型。"""
    client = None
    try:
        cfg = load_config(config)
        cfg.require_api(chat=False)
        check_tools(cfg)
        lesson = load_plan(plan)
        client = BailianClient(cfg, refresh=refresh)
        result = produce(lesson, cfg, client, output, typer.echo)
        typer.echo(f"完成：{output.resolve()}；{result['duration_seconds'] / 60:.2f} 分钟。")
    except (Exception, KeyboardInterrupt) as exc:
        handle_error(exc)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    app()
