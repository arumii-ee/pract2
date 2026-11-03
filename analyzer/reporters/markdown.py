"""상세 Markdown 리포트: 개요 / 전체 구조 / 실행 흐름 / 함수 상세 / 자료형·매크로·전역 변수."""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from pathlib import Path

from .. import __version__
from ..graph import KIND_TAG, CodeIndex, Target, flow_tree, loop_label
from ..models import ProjectReport
from ..parsers.c_parser import Function, ParsedFile
from .text import _size, build_tree

MAX_SOURCE_LINES = 80
MAX_MERMAID_EDGES = 120


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


class _Ctx:
    """렌더링 중 링크 계산용 상태. split=True면 함수 상세가 파일별 페이지로 나뉜다."""
    anchors: dict = {}           # Function → 앵커 id (같은 파일의 동명 함수는 -2, -3 …)
    nth: dict = {}               # Function → 같은 파일 안에서 몇 번째 동명 정의인지
    split = False
    current = "index.md"         # 지금 쓰고 있는 페이지


def page_of(path: Path) -> str:
    """분할 모드에서 소스 파일의 상세 페이지 경로 (하위 폴더는 __ 로 펼침)"""
    return "files/" + path.as_posix().replace("/", "__") + ".md"


def _prepare(idx: CodeIndex, split: bool):
    _Ctx.anchors, _Ctx.nth, _Ctx.split, _Ctx.current = {}, {}, split, "index.md"
    for pf in idx.user_files:
        count: dict[str, int] = defaultdict(int)
        for fn in pf.functions:
            count[fn.name] += 1
            n = count[fn.name]
            base = f"fn-{_slug(fn.path.as_posix())}-{_slug(fn.name)}"
            _Ctx.anchors[fn] = base if n == 1 else f"{base}-{n}"
            _Ctx.nth[fn] = n


def anchor(fn: Function) -> str:
    return _Ctx.anchors.get(fn) or f"fn-{_slug(fn.path.as_posix())}-{_slug(fn.name)}"


def href(fn: Function) -> str:
    if not _Ctx.split:
        return f"#{anchor(fn)}"
    target = page_of(fn.path)
    if target == _Ctx.current:
        return f"#{anchor(fn)}"
    if _Ctx.current == "index.md":
        return f"{target}#{anchor(fn)}"
    return f"{Path(target).name}#{anchor(fn)}"          # files/ 끼리는 같은 폴더


def link(fn: Function) -> str:
    return f"[`{fn.name}()`]({href(fn)})"


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", "<br>")


def _target_desc(t: Target) -> str:
    if t.kind == "user":
        return f"{link(t.func)} — `{t.loc}`"
    where = f" `{Path(t.loc.split(':')[0]).name}`" if t.loc else ""
    doc = f" · {t.doc}" if t.doc else ""
    return f"`{t.name}()` — {KIND_TAG.get(t.kind, t.kind)}{where}{doc}"


# ───────────────────────── 섹션 ─────────────────────────

def _overview(r: ProjectReport, idx: CodeIndex) -> list[str]:
    vi = r.vitis
    user = r.user_files
    total_code = sum(s.code for s in r.languages.values())
    out = ["## 1. 개요", ""]
    if vi and vi.detected:
        out.append("**프로젝트 형태**: Xilinx/AMD Vitis Classic 워크스페이스 (Eclipse 기반, 2019.2~2023.1 구조)")
        out.append("")
        out += ["| 구분 | 이름 | 내용 |", "|---|---|---|"]
        for p in vi.platforms:
            desc = [f"CPU: {', '.join(sorted(p.processors)) or '?'}",
                    f"도메인: {', '.join(sorted(p.domains)) or '?'}"]
            if p.xsa:
                desc.append(f"XSA: {', '.join(p.xsa)}")
            if p.has_fsbl:
                desc.append("FSBL 포함")
            out.append(f"| 플랫폼 | `{p.name}` | {cell(' · '.join(desc))} |")
        for a in vi.apps:
            attrs = ", ".join(f"{k}={v}" for k, v in a.attrs.items() if k != "name")
            out.append(f"| 애플리케이션 | `{a.name}` | `{a.path.as_posix()}/` {cell(attrs)} |")
        for s in vi.systems:
            out.append(f"| 시스템 | `{s}` | 앱 + 플랫폼 묶음 (부팅 이미지 생성 단위) |")
        for h in vi.hls:
            out.append(f"| HLS | `{h.name}` | top 함수: `{h.top or '?'}` |")
        out.append("")
        libs = sorted({lib for p in vi.platforms for lib in p.bsp_libs})
        if libs:
            out.append(f"**BSP 구성 드라이버/라이브러리** ({len(libs)}개): " + ", ".join(f"`{l}`" for l in libs))
            out.append("")
    out += [f"- 사용자 코드: **{len(user)}개 파일, 코드 {total_code:,}줄**, 함수 {len(idx.funcs)}개",
            f"- 자동 생성 코드(BSP/FSBL 등): {len(r.generated_files)}개 파일",
            f"- 외부 라이브러리(`--lib` 지정): {len(r.library_files)}개 파일"
            + (" — " + ", ".join(f"`{f.path.as_posix()}`" for f in r.library_files[:8])
               + (" …" if len(r.library_files) > 8 else "") if r.library_files else ""),
            f"  - 자동 생성·외부 라이브러리 코드는 통계·상세 분석에서 제외하고, "
            f"함수 {len(idx.lib_funcs)}개는 호출 대상 해석용으로만 색인",
            f"- 건너뛴 파일: {r.skipped}개" + (" (아래 목록)" if r.skipped else "")]
    if r.build_systems:
        out.append("- 빌드/프로젝트 설정: " + "; ".join(r.build_systems))
    if r.frameworks:
        out.append("- 사용 라이브러리: " + ", ".join(r.frameworks))
    out.append("")

    stats = sorted(r.languages.values(), key=lambda s: s.code, reverse=True)
    if stats:
        out += ["| 언어 | 파일 | 코드 | 주석 | 빈 줄 |", "|---|---:|---:|---:|---:|"]
        out += [f"| {s.name} | {s.files} | {s.code:,} | {s.comment:,} | {s.blank:,} |" for s in stats]
        out.append("")

    if r.skipped_files:
        out += [f"<details><summary>건너뛴 파일 {len(r.skipped_files)}개 (바이너리/대용량 등)</summary>", "",
                "| 파일 | 크기 | 이유 |", "|---|---:|---|"]
        out += [f"| `{s.path.as_posix()}` | {_size(s.size)} | {s.reason} |" for s in r.skipped_files]
        out += ["", "대용량 파일도 분석하려면 `--max-size 5` 처럼 기준(MB)을 올리세요.", "", "</details>", ""]

    if vi and vi.detected:
        if vi.memory or vi.sizes:
            out += ["### 메모리 맵 (lscript.ld)", "", "| 영역 | 시작 주소 | 크기 | 파일 |", "|---|---|---|---|"]
            for name, org, length, f in vi.memory:
                kb = int(length, 16) / 1024
                size = f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
                out.append(f"| {name} | `{org}` | `{length}` ({size}) | `{f}` |")
            for k, v in vi.sizes.items():
                out.append(f"| {'스택' if 'STACK' in k else '힙'} 크기 (`{k}`) | | `{v}` ({int(v, 16)} B) | |")
            out.append("")
        if vi.peripherals:
            out += [f"### 하드웨어 주변장치 (`{vi.xparameters}`)", "",
                    "| 인스턴스 | 베이스 주소 |", "|---|---|"]
            out += [f"| `{n}` | `{a}` |" for n, a in vi.peripherals[:40]]
            if len(vi.peripherals) > 40:
                out.append(f"| … 외 {len(vi.peripherals) - 40}개 | |")
            out.append("")
        used = defaultdict(list)
        for pf in idx.user_files:
            for name, ln in pf.xpar_refs.items():
                used[name].append(f"{pf.path.as_posix()}:{ln}")
        if used:
            out += ["### 코드에서 사용하는 하드웨어 파라미터 (XPAR_*)", "",
                    "| 매크로 | 값 | 사용 위치 |", "|---|---|---|"]
            for name in sorted(used):
                val = vi.xpar_values.get(name, "?")
                out.append(f"| `{name}` | `{cell(val)}` | {', '.join(f'`{u}`' for u in used[name][:4])} |")
            out.append("")
    return out


def _structure(r: ProjectReport, idx: CodeIndex, max_depth) -> list[str]:
    out = ["## 2. 전체 구조", "", "### 2.1 디렉터리 트리", "",
           "자동 생성 코드(BSP, FSBL, export 등)나 외부 라이브러리만 있는 폴더는 접어서 표시합니다.", "", "```"]
    out += build_tree(r, max_depth)
    out += ["```", ""]

    out += ["### 2.2 파일별 역할", "", "| 파일 | 코드 줄 | 설명 (파일 상단 주석) | 정의된 함수 |", "|---|---:|---|---|"]
    finfo = {f.path: f for f in r.files}
    for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()):
        fi = finfo.get(pf.path)
        is_header = pf.path.suffix in (".h", ".hpp", ".hh")
        desc = " ".join(pf.header_doc[:2]) or (
            "선언: " + ", ".join(f"`{n}()`" for n in sorted(pf.prototypes)[:6]) if is_header and pf.prototypes else "")
        fns = ", ".join(link(f) for f in pf.functions[:12]) + (" …" if len(pf.functions) > 12 else "")
        name = f"`{pf.path.as_posix()}`"
        if _Ctx.split and pf.functions:
            name = f"[{name}]({page_of(pf.path)})"
        out.append(f"| {name} | {fi.code if fi else ''} | {cell(desc)} | {fns} |")
    out.append("")

    # include 관계
    by_base: dict[str, list[ParsedFile]] = defaultdict(list)
    for pf in idx.user_files:
        by_base[pf.path.name].append(pf)
    edges, lib_inc = [], defaultdict(set)
    for pf in idx.user_files:
        for name, _, _system in pf.includes:
            tgt = by_base.get(Path(name).name)
            if tgt:
                edges.append((pf, tgt[0]))
            else:
                lib_inc[pf.path.as_posix()].add(name)
    out += ["### 2.3 include 관계", ""]
    if edges and len(edges) <= MAX_MERMAID_EDGES:
        ids = {pf.path: f"f{i}" for i, pf in enumerate(idx.user_files)}
        out += ["```mermaid", "graph LR"]
        used_nodes = {e[0].path for e in edges} | {e[1].path for e in edges}
        for pf in idx.user_files:
            if pf.path in used_nodes:
                out.append(f'    {ids[pf.path]}["{pf.path.as_posix()}"]')
        for a, b in edges:
            out.append(f"    {ids[a.path]} --> {ids[b.path]}")
        out += ["```", ""]
    elif edges:
        out += [f"(사용자 파일 간 include {len(edges)}개 — 많아서 그림 생략)", ""]
    if lib_inc:
        out += ["| 파일 | 외부/BSP 헤더 |", "|---|---|"]
        for f in sorted(lib_inc):
            out.append(f"| `{f}` | {', '.join(f'`{h}`' for h in sorted(lib_inc[f]))} |")
        out.append("")

    # 호출 관계도
    out += ["### 2.4 함수 호출 관계도", "",
            "실선: 직접 호출 · 점선: 함수 포인터로 등록(인터럽트/콜백)", ""]
    call_edges = {(fn, c.target.func) for fn in idx.funcs for c in fn.calls
                  if c.target and c.target.kind == "user" and c.target.func is not fn}
    cb_edges = {(fn, t.func) for fn in idx.funcs for _, t in fn.callbacks if t.kind == "user"}
    if not call_edges and not cb_edges:
        out += ["(사용자 함수 간 호출 없음)", ""]
    elif len(call_edges) + len(cb_edges) > MAX_MERMAID_EDGES:
        out += [f"(호출 관계 {len(call_edges) + len(cb_edges)}개 — 많아서 그림 생략, 함수 상세의 "
                "'호출하는 함수 / 호출되는 곳' 참고)", ""]
    else:
        ids = {fn: f"n{i}" for i, fn in enumerate(idx.funcs)}
        nodes = {a for a, _ in call_edges | cb_edges} | {b for _, b in call_edges | cb_edges}
        out += ["```mermaid", "flowchart TD"]
        entries = set(idx.entry_functions())
        for fn in idx.funcs:
            if fn in nodes:
                shape = f'(["{fn.name}()"])' if fn in entries else (
                    f'{{{{"{fn.name}()"}}}}' if fn in idx.registered else f'["{fn.name}()"]')
                out.append(f"    {ids[fn]}{shape}")
        for a, b in sorted(call_edges, key=lambda e: (e[0].line, e[1].line)):
            out.append(f"    {ids[a]} --> {ids[b]}")
        for a, b in cb_edges:
            out.append(f"    {ids[a]} -. 등록 .-> {ids[b]}")
        out += ["```", "", "둥근 모양: 진입점 · 육각형: 인터럽트/콜백 핸들러", ""]
    return out


def _flow(idx: CodeIndex, depth: int) -> list[str]:
    out = ["## 3. 실행 흐름", "",
           "소스에 적힌 **호출 순서**대로 진입점부터 따라간 트리입니다. "
           "(런타임 분기 조건은 반영하지 않음 · `→` 사용자 함수 위치 · `[ ]` 외부 함수 종류 · "
           "`⚡` 콜백 등록 · `🔁` 반복문 · `⏳` 폴링 대기 · `❓` 조건 분기)", ""]
    entries = idx.entry_functions()
    n = 1
    if not entries:
        out += ["진입점(`main()` 등)을 찾지 못했습니다.", ""]
    for fn in entries:
        out += [f"### 3.{n} {fn.name}() — {', '.join(idx.role(fn)) or '진입점'}", "",
                f"위치: `{fn.path.as_posix()}:{fn.line}` · 상세: {link(fn)}", "", "```"]
        out += flow_tree(idx, fn, depth)
        out += ["```", ""]
        n += 1

    asyncs = idx.async_functions()
    if asyncs:
        out += [f"### 3.{n} 비동기 진입점 (인터럽트 핸들러 / 콜백)", "",
                "아래 함수들은 직접 호출되지 않고 함수 포인터로 **등록**되어, "
                "인터럽트 발생 시 또는 라이브러리에 의해 호출됩니다.", ""]
        for fn in asyncs:
            regs = [f"{link(caller)}에서 `{r.via or '함수 포인터 대입'}`로 등록 (`{caller.path.name}:{r.line}`)"
                    for caller, r in idx.registered[fn]]
            out += [f"#### ⚡ {fn.name}()", "", "- " + "\n- ".join(regs), "", "```"]
            out += flow_tree(idx, fn, depth)
            out += ["```", ""]
        n += 1

    lib_handlers = {(t.name, r.via, caller.name) for caller in idx.funcs for r, t in caller.callbacks
                    if t.kind == "lib"}
    if lib_handlers:
        out += [f"### 3.{n} BSP 함수를 핸들러로 등록", "", "| BSP 핸들러 | 등록 함수 | 등록 위치 |", "|---|---|---|"]
        for name, via, caller in sorted(lib_handlers, key=lambda x: (x[0], x[1] or "")):
            out.append(f"| `{name}` | `{via or '대입'}` | `{caller}()` |")
        out += ["", "> 예: `XScuGic_InterruptHandler`를 `Xil_ExceptionRegisterHandler`로 등록하면, "
                "IRQ 발생 시 GIC 드라이버가 `XScuGic_Connect`로 연결해 둔 사용자 핸들러를 호출합니다.", ""]
        n += 1

    unreached = idx.unreached()
    if unreached:
        out += [f"### 3.{n} 진입점에서 도달하지 않는 함수", "",
                "다른 프로젝트에서 호출되거나, 매크로/함수 포인터 배열을 통해 호출되거나, 사용하지 않는 코드일 수 있습니다.", ""]
        out += [f"- {link(f)} — `{f.path.as_posix()}:{f.line}`" for f in unreached]
        out.append("")
    return out


def _function_detail(idx: CodeIndex, fn: Function, with_source: bool, pf: ParsedFile) -> list[str]:
    nth = _Ctx.nth.get(fn, 1)
    title = f"#### `{fn.name}()`" + (f" — {nth}번째 정의 (`#if` 분기 추정)" if nth > 1 else "")
    out = [f'<a id="{anchor(fn)}"></a>', "", title, ""]
    role = idx.role(fn)
    out.append(f"- **시그니처**: `{fn.signature}`")
    out.append(f"- **위치**: `{fn.path.as_posix()}:{fn.line}-{fn.end_line}` ({fn.length}줄)"
               + (f" · {', '.join(role)}" if role else ""))
    if fn.doc:
        out.append("- **설명**: " + "<br>".join(cell(d) for d in fn.doc[:8]))

    if fn.calls:
        out.append("- **호출하는 함수** (등장 순서):")
        seen: dict[str, list] = {}
        for c in fn.calls:
            seen.setdefault(c.name, []).append(c)
        for i, (name, cs) in enumerate(seen.items(), 1):
            lines_ = ", ".join(str(c.line) for c in cs[:5]) + (" …" if len(cs) > 5 else "")
            cnt = f" ×{len(cs)}" if len(cs) > 1 else ""
            out.append(f"  {i}. {_target_desc(cs[0].target)}{cnt} (L{lines_})")
    callers = idx.callers.get(fn, [])
    if callers:
        uniq = {}
        for caller, c in callers:
            uniq.setdefault(caller, []).append(c.line)
        out.append("- **호출되는 곳**: " + ", ".join(
            f"{link(caller)} (`{caller.path.name}:{','.join(map(str, lns))}`)" for caller, lns in uniq.items()))
    elif fn in idx.registered:
        out.append("- **호출되는 곳**: 직접 호출 없음 — 함수 포인터로 등록됨: " + ", ".join(
            f"{link(c)}의 `{r.via or '대입'}` (L{r.line})" for c, r in idx.registered[fn]))
    else:
        out.append("- **호출되는 곳**: (사용자 코드에서 호출 없음)")
    if fn.callbacks:
        out.append("- **등록/전달하는 함수 포인터**: " + ", ".join(
            (link(t.func) if t.kind == "user" else f"`{t.name}`") + f" ← `{r.via or '대입'}` (L{r.line})"
            for r, t in fn.callbacks))
    if fn.globals_used:
        out.append("- **사용하는 전역 변수**: " + ", ".join(f"`{g}`" for g in fn.globals_used))
    if fn.loops:
        out.append("- **반복/대기**: " + "; ".join(f"L{lp.line} {loop_label(lp)}" for lp in fn.loops))
    if fn.pragmas:
        out.append("- **HLS/pragma**: " + "; ".join(f"L{ln} `{cell(t)}`" for ln, t in fn.pragmas))

    if with_source:
        src = pf.lines[fn.line - 1:fn.end_line]
        cut = len(src) > MAX_SOURCE_LINES
        src = src[:MAX_SOURCE_LINES] + (["    /* … 이하 생략 … */"] if cut else [])
        lang = "cpp" if fn.path.suffix in (".cpp", ".cc", ".cxx", ".hpp") else "c"
        out += ["", "<details><summary>소스 보기</summary>", "", f"```{lang}"] + src + ["```", "", "</details>"]
    out.append("")
    return out


def _file_detail(idx: CodeIndex, pf: ParsedFile, with_source: bool) -> list[str]:
    out = [f"### 📄 {pf.path.as_posix()}", ""]
    if pf.header_doc:
        out += ["> " + " ".join(pf.header_doc), ""]
    for fn in pf.functions:
        out += _function_detail(idx, fn, with_source, pf)
    return out


def _details(idx: CodeIndex, with_source: bool) -> list[str]:
    out = ["## 4. 함수 상세", ""]
    for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()):
        if pf.functions:
            out += _file_detail(idx, pf, with_source)
    return out


def _types(idx: CodeIndex) -> list[str]:
    out = ["## 5. 자료형 · 매크로 · 전역 변수", ""]
    any_ = False
    for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()):
        gvars = [g for g in pf.globals if not g.is_extern]
        macros = [m for m in pf.macros.values() if not re.fullmatch(r'_*[A-Z0-9_]+_H_*', m.name)]
        if not (pf.types or gvars or macros):
            continue
        any_ = True
        out += [f"### 📄 {pf.path.as_posix()}", ""]
        if pf.types:
            out += ["| 자료형 | 종류 | 줄 | 선언 |", "|---|---|---:|---|"]
            out += [f"| `{t.name}` | {t.kind} | {t.line} | `{cell(t.decl)}` |" for t in pf.types]
            out.append("")
        if gvars:
            out += ["| 전역 변수 | 줄 | 선언 | 사용하는 함수 |", "|---|---:|---|---|"]
            for g in gvars:
                users = ", ".join(link(f) for f in idx.global_users.get(g.name, []))
                out.append(f"| `{g.name}`{' (static)' if g.is_static else ''} | {g.line} | `{cell(g.decl)}` | {users} |")
            out.append("")
        if macros:
            out += ["| 매크로 | 줄 | 값 |", "|---|---:|---|"]
            for m in macros[:40]:
                out.append(f"| `{m.name}{m.params or ''}` | {m.line} | `{cell(m.value) or ' '}` |")
            if len(macros) > 40:
                out.append(f"| … 외 {len(macros) - 40}개 | | |")
            out.append("")
    if not any_:
        out += ["(없음)", ""]
    return out


FOOTER = ["---", "",
          "_정적 분석 결과입니다. `#ifdef` 분기는 모두 포함되고, 매크로 내부 호출·함수 포인터 배열을 통한 호출은 "
          "누락될 수 있습니다. C/C++ 이외(Lua/Python)의 함수 분석은 아직 지원하지 않습니다._"]


def _header(report: ProjectReport) -> list[str]:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return [f"# 프로젝트 구조 분석: {report.root.name}", "",
            f"> `analyze.py` v{__version__} · {now} 생성 · 경로 `{report.root}`", ""]


def render(report: ProjectReport, max_depth=None, flow_depth: int = 8, with_source: bool = True) -> str:
    """리포트 전체를 한 파일로."""
    idx: CodeIndex = report.index
    _prepare(idx, split=False)
    out = _header(report) + [
        "**목차**: [1. 개요](#1-개요) · [2. 전체 구조](#2-전체-구조) · [3. 실행 흐름](#3-실행-흐름) · "
        "[4. 함수 상세](#4-함수-상세) · [5. 자료형·매크로·전역 변수](#5-자료형--매크로--전역-변수)", ""]
    out += _overview(report, idx)
    out += _structure(report, idx, max_depth)
    out += _flow(idx, flow_depth)
    out += _details(idx, with_source)
    out += _types(idx)
    out += FOOTER
    return "\n".join(out)


def render_split(report: ProjectReport, max_depth=None, flow_depth: int = 8,
                 with_source: bool = True) -> dict[str, str]:
    """index.md(개요·구조·실행 흐름·자료형) + files/<소스>.md(함수 상세)로 나눈다. {상대 경로: 내용}"""
    idx: CodeIndex = report.index
    _prepare(idx, split=True)
    files = [pf for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()) if pf.functions]

    out = _header(report) + [
        "**목차**: [1. 개요](#1-개요) · [2. 전체 구조](#2-전체-구조) · [3. 실행 흐름](#3-실행-흐름) · "
        "[4. 함수 상세 (파일별)](#4-함수-상세-파일별) · [5. 자료형·매크로·전역 변수](#5-자료형--매크로--전역-변수)", ""]
    out += _overview(report, idx)
    out += _structure(report, idx, max_depth)
    out += _flow(idx, flow_depth)
    out += ["## 4. 함수 상세 (파일별)", "", "함수 상세는 소스 파일별 페이지로 나뉘어 있습니다.", "",
            "| 파일 | 함수 수 | 페이지 |", "|---|---:|---|"]
    out += [f"| `{pf.path.as_posix()}` | {len(pf.functions)} | [{page_of(pf.path)}]({page_of(pf.path)}) |"
            for pf in files]
    out.append("")
    out += _types(idx)
    out += FOOTER
    pages = {"index.md": "\n".join(out)}

    for pf in files:
        _Ctx.current = page = page_of(pf.path)
        body = [f"# {pf.path.as_posix()} — 함수 상세", "",
                f"[← index](../index.md) · [실행 흐름](../index.md#3-실행-흐름) · "
                f"함수 {len(pf.functions)}개", ""]
        body += _file_detail(idx, pf, with_source)
        pages[page] = "\n".join(body)
    _Ctx.current = "index.md"
    return pages
