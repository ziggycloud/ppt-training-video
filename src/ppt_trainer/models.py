from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(Record):
    fact_id: str
    text: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(max_length=2000)
    evidence_type: Literal["native_text", "notes", "visual"]


class PageAnalysis(Record):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=3000)
    facts: list[Fact] = Field(max_length=60)
    unknowns: list[str] = Field(max_length=30)


class Difference(Record):
    kind: Literal["unchanged", "changed", "not_mentioned", "conflict", "uncertain"]
    description: str
    old_fact_refs: list[str]
    new_fact_refs: list[str]


class Alignment(Record):
    old_slide_numbers: list[int]
    differences: list[Difference]
    focus_reason: str


class Narration(Record):
    narration: str = Field(min_length=1, max_length=16000)
    fact_refs: list[str]
    warnings: list[str]


class Scene(Record):
    scene_id: str
    slide_number: int = Field(ge=1)
    title: str
    image_path: str
    image_sha256: str
    narration: str = Field(min_length=1, max_length=16000)
    fact_refs: list[str]
    warnings: list[str]


class Plan(Record):
    format_version: Literal[1] = 1
    mode: Literal["single", "compare"]
    source_names: list[str]
    source_hashes: list[str]
    target_minutes: float
    focus: str
    scenes: list[Scene] = Field(min_length=1)
    evidence: dict[str, str]
    analyses: dict[str, PageAnalysis]
    comparisons: dict[str, Alignment]
