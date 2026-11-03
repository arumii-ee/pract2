"""터미널용 텍스트 리포트: 요약, 언어 통계, 진입점, 디렉터리 트리."""

from __future__ import annotations

from pathlib import Path

from ..models import ProjectReport


def _tree(report: ProjectReport, max_depth: int | None) -> list[str]:
    # 상대 경로들로 중첩 dict 트리 구성
    tree: dict = {}
    info = {f.path.as_posix(): f for f in report.files}
    for f in report.files:
        node = tree
        for part in f.path.parts[:-1]:
            node = node.setdefault(part + "/", {})
        node[f.path.name] = None

    lines = [report.root.name + "/"]

    def walk(node: dict, prefix: str, depth: int, rel: str):
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            if child is None:
                f = info.get(rel + name)
                suffix = f"  ({f.total} lines)" if f and f.total else ""
                lines.append(prefix + branch + name + suffix)
            else:
                count = _count_files(child)
                lines.append(prefix + branch + name + f"  [{count} files]")
                if max_depth is None or depth < max_depth:
                    walk(child, prefix + ("    " if last else "│   "), depth + 1, rel + name)

    walk(tree, "", 1, "")
    return lines


def _count_files(node: dict) -> int:
    return sum(1 if v is None else _count_files(v) for v in node.values())


def render(report: ProjectReport, max_depth: int | None = None) -> str:
    out: list[str] = []
    total_files = len(report.files)
    total_code = sum(s.code for s in report.languages.values())

    out.append("=" * 60)
    out.append(f" 프로젝트 구조 분석: {report.root}")
    out.append("=" * 60)
    out.append(f"분석 파일: {total_files}개   건너뜀(바이너리/대용량): {report.skipped}개")
    out.append(f"총 코드 줄: {total_code:,}")
    out.append("")

    out.append("[빌드 시스템 / 프로젝트 설정]")
    out.extend(f"  - {b}" for b in report.build_systems) if report.build_systems else out.append("  (감지 안 됨)")
    out.append("")

    out.append("[사용 라이브러리 / 프레임워크]")
    out.extend(f"  - {fw}" for fw in report.frameworks) if report.frameworks else out.append("  (감지 안 됨)")
    out.append("")

    out.append("[진입점 후보]")
    if report.entry_points:
        for e in report.entry_points:
            out.append(f"  - {e.path.as_posix()}  ← {e.reason}")
    else:
        out.append("  (감지 안 됨)")
    out.append("")

    out.append("[언어별 통계]")
    stats = sorted(report.languages.values(), key=lambda s: s.code, reverse=True)
    if stats:
        out.append(f"  {'언어':<16}{'파일':>6}{'코드':>10}{'주석':>10}{'빈 줄':>10}{'비율':>8}")
        out.append("  " + "-" * 60)
        for s in stats:
            pct = (s.code / total_code * 100) if total_code else 0
            out.append(f"  {s.name:<16}{s.files:>6}{s.code:>10,}{s.comment:>10,}{s.blank:>10,}{pct:>7.1f}%")
    else:
        out.append("  (분석할 소스 없음)")
    out.append("")

    out.append("[디렉터리 트리]" + (f" (깊이 {max_depth}까지)" if max_depth else ""))
    out.extend("  " + l for l in _tree(report, max_depth))
    return "\n".join(out)
