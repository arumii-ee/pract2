"""터미널용 텍스트 리포트: 요약, Vitis 정보, 언어 통계, 진입점, 디렉터리 트리."""

from __future__ import annotations

from ..models import ProjectReport


def build_tree(report: ProjectReport, max_depth: int | None) -> list[str]:
    """디렉터리 트리. 자동 생성 파일만 있는 폴더는 펼치지 않고 접는다."""
    tree: dict = {}
    info = {f.path.as_posix(): f for f in report.files}
    for f in report.files:
        node = tree
        for part in f.path.parts[:-1]:
            node = node.setdefault(part + "/", {})
        node[f.path.name] = None

    def stats(node: dict, rel: str) -> tuple[int, int]:
        total = user = 0
        for name, child in node.items():
            if child is None:
                total += 1
                user += 0 if info[rel + name].generated else 1
            else:
                t, u = stats(child, rel + name)
                total, user = total + t, user + u
        return total, user

    lines = [report.root.name + "/"]

    def walk(node: dict, prefix: str, depth: int, rel: str):
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            if child is None:
                f = info[rel + name]
                if f.generated:
                    continue
                lines.append(prefix + branch + name + (f"  ({f.total} lines)" if f.total else ""))
                continue
            total, user = stats(child, rel + name)
            if user == 0:
                lines.append(prefix + branch + name + f"  [자동 생성 · {total} files, 펼치지 않음]")
                continue
            extra = f", 자동 생성 {total - user}개 숨김" if total > user else ""
            lines.append(prefix + branch + name + f"  [{user} files{extra}]")
            if max_depth is None or depth < max_depth:
                walk(child, prefix + ("    " if last else "│   "), depth + 1, rel + name)

    walk(tree, "", 1, "")
    return lines


def render(report: ProjectReport, max_depth: int | None = None) -> str:
    out: list[str] = []
    user, gen = report.user_files, report.generated_files
    total_code = sum(s.code for s in report.languages.values())
    vi = report.vitis

    out.append("=" * 60)
    out.append(f" 프로젝트 구조 분석: {report.root}")
    out.append("=" * 60)
    out.append(f"사용자 코드 파일: {len(user)}개   자동 생성(통계 제외): {len(gen)}개   "
               f"건너뜀(바이너리/대용량): {report.skipped}개")
    out.append(f"총 코드 줄(사용자 코드): {total_code:,}")
    if report.excluded:
        out.append(f"탐색 제외: {len(report.excluded)}개 (폴더 {len(report.excluded_dirs)}개)")
        shown = [e for e in report.excluded if e.kind != "default"]
        for e in shown[:30]:
            out.append(f"  - {e.path.as_posix()}{'/' if e.is_dir else ''}  ← {e.detail}")
        if len(shown) > 30:
            out.append(f"  … 외 {len(shown) - 30}개")
    warning = report.exclusion_warning()
    if warning:
        out.append(f"⚠ 분석할 소스 코드가 없습니다: {warning}")
    out.append("")

    if vi and vi.detected:
        out.append("[Xilinx Vitis 워크스페이스]")
        for p in vi.platforms:
            out.append(f"  플랫폼  {p.name}: CPU {', '.join(sorted(p.processors)) or '?'}"
                       f" / 도메인 {', '.join(sorted(p.domains)) or '?'}"
                       + (f" / XSA {', '.join(p.xsa)}" if p.xsa else ""))
        for a in vi.apps:
            attrs = ", ".join(f"{k}={v}" for k, v in a.attrs.items() if k != "name")
            out.append(f"  앱      {a.name} ({a.path.as_posix()}/)" + (f"  {attrs}" if attrs else ""))
        for s in vi.systems:
            out.append(f"  시스템  {s}")
        for h in vi.hls:
            out.append(f"  HLS     {h.name}: top = {h.top or '?'}")
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

    out.append("[언어별 통계] (자동 생성 코드 제외)")
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
    out.extend("  " + l for l in build_tree(report, max_depth))
    if report.index and report.index.funcs:
        out.append("")
        out.append("※ 함수 상세 · 실행 흐름 · 호출 관계는 --format md -o STRUCTURE.md 로 확인하세요.")
    return "\n".join(out)
