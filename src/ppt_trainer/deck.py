from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

import pypdfium2 as pdfium
from defusedxml import ElementTree as ET

from .config import Settings
from .errors import TrainerError
from .tools import libreoffice
from .util import CACHE_VERSION, digest, file_hash, run, write_json


NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


@dataclass
class Slide:
    number: int
    image_path: str
    native_text: str
    notes: str
    image_sha256: str


@dataclass
class Deck:
    name: str
    sha256: str
    slides: list[Slide]


def related_path(parent: str, target: str) -> str:
    parts: list[str] = []
    for part in (
        target if target.startswith("/") else str(PurePosixPath(parent).parent / target)
    ).split("/"):
        if part == "..":
            if not parts:
                raise TrainerError("PPTX 关系路径越界。")
            parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    return "/".join(parts)


def paragraphs(root) -> str:
    values = [
        "".join(t.text or "" for t in p.findall(".//a:t", NS)).strip()
        for p in root.findall(".//a:p", NS)
    ]
    return "\n".join(value for value in values if value)


def extract_ooxml(path: Path, max_slides: int) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > 20000 or sum(e.file_size for e in entries) > 300 * 1024**2:
                raise TrainerError("PPTX 解压大小或条目数量超过上限。")
            if any(e.flag_bits & 1 for e in entries):
                raise TrainerError("不支持加密 PPTX。")

            def read(name: str):
                info = z.getinfo(name)
                if info.file_size > 16 * 1024**2:
                    raise TrainerError("PPTX XML 单文件过大。")
                return ET.fromstring(z.read(name))

            def relations(name: str) -> dict[str, str]:
                return {
                    e.attrib["Id"]: e.attrib["Target"]
                    for e in read(name).iter(REL)
                    if e.attrib.get("TargetMode") != "External"
                }

            pres = read("ppt/presentation.xml")
            ids = pres.findall(".//p:sldId", NS)
            if not 1 <= len(ids) <= max_slides:
                raise TrainerError(f"PPT 页数必须在 1–{max_slides} 页之间。")
            rels = relations("ppt/_rels/presentation.xml.rels")
            result = []
            for element in ids:
                target = rels[element.attrib[f"{{{NS['r']}}}id"]]
                page_path = related_path("ppt/presentation.xml", target)
                page = read(page_path)
                note = ""
                p = PurePosixPath(page_path)
                rel_path = str(p.parent / "_rels" / (p.name + ".rels"))
                if rel_path in z.namelist():
                    for rel in read(rel_path).iter(REL):
                        if (
                            rel.attrib.get("Type", "").endswith("/notesSlide")
                            and rel.attrib.get("TargetMode") != "External"
                        ):
                            notes_root = read(related_path(page_path, rel.attrib["Target"]))
                            # Skip slide-number/header/footer placeholders in notes.
                            values = []
                            for shape in notes_root.findall(".//p:sp", NS):
                                ph = shape.find(".//p:ph", NS)
                                if ph is not None and ph.attrib.get("type") in {
                                    "sldNum",
                                    "hdr",
                                    "ftr",
                                    "dt",
                                    "sldImg",
                                }:
                                    continue
                                values.append(paragraphs(shape))
                            note = "\n".join(x for x in values if x)
                result.append((paragraphs(page), note))
            return result
    except TrainerError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        raise TrainerError("PPTX 无法解析，可能已损坏、加密或不是标准演示文稿。") from exc


def convert(source: Path, dest: Path, extension: str, cfg: Settings) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo-", dir=dest.parent) as profile:
        fmt = extension
        if extension == "pdf":
            fmt = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
        run(
            [
                libreoffice(cfg.tools.libreoffice),
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--headless",
                "--convert-to",
                fmt,
                "--outdir",
                str(dest),
                str(source),
            ],
            timeout=cfg.tools.conversion_timeout_seconds,
        )
    output = dest / (source.stem + "." + extension)
    if not output.is_file():
        raise TrainerError(
            f"LibreOffice 未生成 {extension} 文件。请检查课件是否可正常打开，或是否存在密码/不支持对象。"
        )
    return output


def prepare_deck(source: Path, cfg: Settings, *, refresh: bool = False) -> Deck:
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() not in {".ppt", ".pptx"}:
        raise TrainerError("输入必须是存在的 .ppt 或 .pptx 文件。")
    if source.stat().st_size > cfg.course.max_file_mb * 1024**2:
        raise TrainerError(f"输入超过 {cfg.course.max_file_mb} MB。")
    sha = file_hash(source)
    key = digest([CACHE_VERSION, sha, cfg.video.width, cfg.video.height, cfg.course.max_slides])
    folder = Path(cfg.cache_dir) / "decks" / key
    manifest = folder / "deck.json"
    if not refresh and manifest.is_file():
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        slides = [Slide(**s) for s in raw["slides"]]
        if all(
            Path(s.image_path).is_file() and file_hash(Path(s.image_path)) == s.image_sha256
            for s in slides
        ):
            return Deck(source.name, sha, slides)
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / ("source" + source.suffix.lower())
    shutil.copyfile(source, original)
    pptx = (
        convert(original, folder / "pptx", "pptx", cfg)
        if source.suffix.lower() == ".ppt"
        else original
    )
    extracted = extract_ooxml(pptx, cfg.course.max_slides)
    pdf = convert(pptx, folder / "pdf", "pdf", cfg)
    slides = []
    with pdfium.PdfDocument(str(pdf)) as document:
        if len(document) != len(extracted):
            raise TrainerError(
                "PPTX 页面数与渲染页数不一致，请检查隐藏页或转换失败；为避免错配已停止。"
            )
        for index, (native, notes) in enumerate(extracted, 1):
            page = document[index - 1]
            width, height = page.get_size()
            scale = min(cfg.video.width / width, cfg.video.height / height)
            bitmap = page.render(scale=scale)
            image_path = folder / f"p{index:04}.png"
            bitmap.to_pil().save(image_path)
            bitmap.close()
            page.close()
            slides.append(
                Slide(index, str(image_path.resolve()), native, notes, file_hash(image_path))
            )
    deck = Deck(source.name, sha, slides)
    write_json(manifest, asdict(deck))
    return deck
