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
    excluded: list = field(default_factory=list)   # scanner.Excluded: 탐색에서 제외된 폴더/파일과 이유
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
    def excluded_dirs(self) -> list:
        return [e for e in self.excluded if e.is_dir]

    def exclusion_warning(self) -> str | None:
        """사용자 코드가 하나도 없는데 .gitignore/링크로 빠진 것이 있으면 경고 문구."""
        if any(f.language for f in self.user_files):
            return None
        gi = [e for e in self.excluded if e.kind == "gitignore"]
        links = [e for e in self.excluded if e.kind == "link"]
        msgs = []
        if gi:
            msgs.append(f".gitignore 규칙으로 {len(gi)}개 항목(폴더 {sum(e.is_dir for e in gi)}개)이 제외되었습니다. "
                        "--no-gitignore 로 다시 실행해 보세요")
        if links:
            msgs.append(f"링크/정션 폴더 {len(links)}개를 탐색하지 않았습니다. --follow-links 로 다시 실행해 보세요")
        return " / ".join(msgs) or None

    @property
    def generated_files(self) -> list[FileInfo]:
        return [f for f in self.files if f.generated]
