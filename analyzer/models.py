"""분석 결과를 담는 데이터 모델. 분석 단계와 출력(reporter)을 분리하는 경계."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileInfo:
    path: Path              # 프로젝트 루트 기준 상대 경로
    size: int
    language: str | None = None
    code: int = 0
    comment: int = 0
    blank: int = 0
    generated: bool = False      # Vitis BSP/FSBL 등 자동 생성 코드

    @property
    def total(self) -> int:
        return self.code + self.comment + self.blank


@dataclass
class LanguageStats:
    name: str
    files: int = 0
    code: int = 0
    comment: int = 0
    blank: int = 0


@dataclass
class EntryPoint:
    path: Path
    reason: str


@dataclass
class ProjectReport:
    root: Path
    files: list[FileInfo] = field(default_factory=list)
    skipped: int = 0
    skipped_paths: list[Path] = field(default_factory=list)
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    build_systems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
    vitis: object = None         # vitis.VitisInfo
    index: object = None         # graph.CodeIndex

    @property
    def user_files(self) -> list[FileInfo]:
        return [f for f in self.files if not f.generated]

    @property
    def generated_files(self) -> list[FileInfo]:
        return [f for f in self.files if f.generated]
