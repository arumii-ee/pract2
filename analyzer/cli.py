"""CLI 진입점: 인자 파싱 → 스캔 → 통계 → 감지 → 출력."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import detectors, languages, scanner
from .models import FileInfo, LanguageStats, ProjectReport
from .reporters import text


def build_report(root: Path, excludes: set[str]) -> ProjectReport:
    report = ProjectReport(root=root)
    paths, report.skipped = scanner.scan(root, excludes)

    for p in paths:
        lang = languages.detect_language(p)
        f = FileInfo(path=p.relative_to(root), size=p.stat().st_size, language=lang)
        f.code, f.comment, f.blank = languages.count_lines(p, lang)
        report.files.append(f)
        if lang:
            s = report.languages.setdefault(lang, LanguageStats(lang))
            s.files += 1
            s.code += f.code
            s.comment += f.comment
            s.blank += f.blank

    report.build_systems = detectors.detect_build_systems(root, report.files)
    report.frameworks, report.entry_points = detectors.detect(root, report.files)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="프로젝트 구조 분석기 (C / Lua / Python 중심)")
    ap.add_argument("path", nargs="?", default=".", help="분석할 프로젝트 경로 (기본: 현재 폴더)")
    ap.add_argument("-d", "--max-depth", type=int, default=None, help="트리 출력 최대 깊이")
    ap.add_argument("-e", "--exclude", default="", help="추가로 제외할 폴더/파일 이름 (쉼표 구분)")
    ap.add_argument("-o", "--output", help="결과를 파일로 저장")
    args = ap.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"오류: 폴더가 아닙니다: {root}", file=sys.stderr)
        return 1

    excludes = {x.strip() for x in args.exclude.split(",") if x.strip()}
    result = text.render(build_report(root, excludes), args.max_depth)

    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
        print(f"저장 완료: {args.output}")
    else:
        print(result)
    return 0
