"""CLI 진입점: 스캔 → Vitis 분류 → 통계 → 감지 → C 파싱/호출 그래프 → 출력."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import detectors, languages, scanner, vitis
from .graph import CodeIndex
from .models import EntryPoint, FileInfo, LanguageStats, ProjectReport
from .parsers.c_parser import parse_file
from .reporters import markdown, text

C_LANGS = {"C", "C++", "C/C++ Header"}


def build_report(root: Path, excludes: set[str], include_generated: bool = False) -> ProjectReport:
    report = ProjectReport(root=root)
    paths, skipped = scanner.scan(root, excludes)
    report.skipped_paths = [p.relative_to(root) for p in skipped]
    report.skipped = len(skipped)

    rels = [p.relative_to(root) for p in paths]
    report.vitis = vitis.analyze(root, rels + report.skipped_paths)

    for p, rel in zip(paths, rels):
        lang = languages.detect_language(p)
        f = FileInfo(path=rel, size=p.stat().st_size, language=lang)
        f.generated = report.vitis.is_generated(rel) and not include_generated
        f.code, f.comment, f.blank = languages.count_lines(p, lang)
        report.files.append(f)
        if lang and not f.generated:
            s = report.languages.setdefault(lang, LanguageStats(lang))
            s.files += 1
            s.code += f.code
            s.comment += f.comment
            s.blank += f.blank

    user = report.user_files
    report.build_systems = detectors.detect_build_systems(root, user)
    report.frameworks, report.entry_points = detectors.detect(root, user)

    # C/C++ 파싱: 사용자 코드는 본문까지, 자동 생성(BSP) 코드는 정의만 색인
    user_parsed = [pf for f in user if f.language in C_LANGS
                   if (pf := parse_file(root, f.path, analyze_bodies=True))]
    lib_parsed = [pf for f in report.generated_files if f.language in C_LANGS
                  if (pf := parse_file(root, f.path, analyze_bodies=False))]
    for pf in lib_parsed:
        pf.generated = True
    report.index = CodeIndex(user_parsed, lib_parsed, report.vitis.hls_tops)

    for fn in report.index.async_functions():
        how = ", ".join(sorted({r.via or "함수 포인터 대입" for _, r in report.index.registered[fn]}))
        report.entry_points.append(EntryPoint(fn.path, f"{fn.name}() — 콜백/인터럽트 핸들러 (등록: {how})"))
    for fn in report.index.funcs:
        if fn.name in report.vitis.hls_tops:
            report.entry_points.append(EntryPoint(fn.path, f"{fn.name}() — HLS top 함수"))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="프로젝트 구조 분석기 (C / Lua / Python, Xilinx Vitis 지원)")
    ap.add_argument("path", nargs="?", default=".", help="분석할 프로젝트(또는 Vitis 워크스페이스) 경로")
    ap.add_argument("-f", "--format", choices=("text", "md"), default="text",
                    help="text: 터미널 요약 / md: 상세 Markdown 리포트 (구조·함수 상세·실행 흐름)")
    ap.add_argument("-o", "--output", help="결과를 파일로 저장")
    ap.add_argument("-d", "--max-depth", type=int, default=None, help="트리 출력 최대 깊이")
    ap.add_argument("-e", "--exclude", default="", help="추가로 제외할 폴더/파일 이름 (쉼표 구분)")
    ap.add_argument("--flow-depth", type=int, default=8, help="실행 흐름 트리 최대 깊이 (기본 8)")
    ap.add_argument("--no-source", action="store_true", help="md 리포트에 함수 소스 코드를 넣지 않음")
    ap.add_argument("--include-generated", action="store_true",
                    help="Vitis BSP/FSBL 등 자동 생성 코드도 사용자 코드처럼 분석")
    args = ap.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"오류: 폴더가 아닙니다: {root}", file=sys.stderr)
        return 1

    excludes = {x.strip() for x in args.exclude.split(",") if x.strip()}
    report = build_report(root, excludes, args.include_generated)
    if args.format == "md":
        result = markdown.render(report, max_depth=args.max_depth, flow_depth=args.flow_depth,
                                 with_source=not args.no_source)
    else:
        result = text.render(report, args.max_depth)

    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
        print(f"저장 완료: {args.output}")
    else:
        print(result)
    return 0
