"""상세 Markdown 리포트: 개요 / 전체 구조 / 실행 흐름 / 함수 상세 / 자료형·매크로·전역 변수."""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from pathlib import Path

from .. import __version__
from ..graph import (KIND_TAG, CodeIndex, Target, _skel_items, call_order, callees_in_order, file_call_edges,
                     flow_tree, loop_label, skeleton, thread_map)
from ..models import ProjectReport
from ..parsers.c_parser import Function, ParsedFile
from ..summary import from_doc, from_name, split_words, summarize
from .text import _size, build_tree

MAX_SOURCE_LINES = 80
MAX_MERMAID_EDGES = 120
MAX_MODULE_EDGES = 40         # 파일 간 호출도에 그릴 최대 화살표 수
MAX_MAIN_CHILDREN = 20        # 핵심 흐름 그림: 진입점이 직접 호출하는 함수 최대 수
MAX_SUB_CHILDREN = 3          # 핵심 흐름 그림: 그 아래 단계 최대 수
MAX_CRUMB = 6                # 카드 위 호출 경로에 보이는 최대 함수 수
MAX_INDENT = 6               # 요약표 들여쓰기 최대 단계
MAX_CARD_CALLEES = 10         # 함수 카드에 바로 보이는 하위 함수 수
MAX_FLOW_NODES = 45           # 핵심 흐름 그림 전체 노드 수 상한 (넘으면 아래 단계는 생략)
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


class _Ctx:
    """렌더링 중 링크 계산용 상태. split=True면 함수 상세가 호출 그룹(진입점/스레드)별 페이지로 나뉜다."""
    anchors: dict = {}           # Function → 앵커 id (같은 파일의 동명 함수는 -2, -3 …)
    nth: dict = {}               # Function → 같은 파일 안에서 몇 번째 동명 정의인지
    split = False
    current = "index.md"         # 지금 쓰고 있는 페이지
    groups: list = []            # graph.call_order 결과
    pages: dict = {}             # Function → 상세 페이지 (분할 모드)
    order: dict = {}             # Function → 함수 상세에서 몇 번째로 나오는지


def group_page(n: int, kind: str, root) -> str:
    """분할 모드에서 n번째 호출 그룹의 상세 페이지 경로"""
    if kind == "unreached":
        return "files/99_unreached.md"
    return f"files/{n:02d}_{_slug(root.name).replace('-', '_')}.md"


def _prepare(idx: CodeIndex, split: bool):
    _Ctx.anchors, _Ctx.nth, _Ctx.split, _Ctx.current = {}, {}, split, "index.md"
    _Ctx.groups = call_order(idx)
    _Ctx.pages, _Ctx.order = {}, {}
    for n, (kind, root, items) in enumerate(_Ctx.groups, 1):
        page = group_page(n, kind, root)
        for fn, _ in items:
            _Ctx.pages[fn] = page
            _Ctx.order[fn] = len(_Ctx.order)
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
    target = _Ctx.pages.get(fn, "index.md")
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


# ───────────────────────── 0. 한눈에 보기 ─────────────────────────

def _desc(fn: Function) -> str:
    """태그 없이 짧은 설명 (주석 첫 문장 또는 이름 기반 추정)"""
    return from_doc(fn) or from_name(fn.name)


def _m(s: str, n: int = 28) -> str:
    """Mermaid 라벨에 안전한 문자열"""
    s = re.sub(r'["`<>|#{}\[\]]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _node(fn: Function, ids: dict, shape: str = "box") -> str:
    label = f"{fn.name}()<br/>{_m(_desc(fn))}"
    nid = ids.setdefault(fn, f"n{len(ids)}")
    if shape == "entry":
        return f'{nid}(["{label}"])'
    if shape == "async":
        return f'{nid}{{{{"{label}"}}}}'
    return f'{nid}["{label}"]'


def _thread_diagram(idx: CodeIndex) -> list[str]:
    edges = thread_map(idx)
    out = ["### 0.1 스레드 / 진입점 구성도", ""]
    if not edges:
        out += ["(함수 포인터로 등록되는 스레드·콜백 없음)", ""]
        return out
    out += ["진입점이 **어떤 순서로** 어떤 스레드·콜백을 시작/등록하는지입니다. 번호(①②…)가 등록 순서이고, "
            "`안에서`는 그 진입점이 호출한 다른 함수 안에서 등록된 경우입니다.", "",
            "```mermaid", "flowchart LR"]
    ids: dict = {}
    declared: set = set()
    entries = set(idx.entry_functions())

    def decl(fn):
        if fn not in declared:
            declared.add(fn)
            out.append("    " + _node(fn, ids, "entry" if fn in entries else "async"))
    order: dict = defaultdict(int)
    for owner, caller, tgt, r in edges:
        decl(owner)
        decl(tgt)
        order[owner] += 1
        k = order[owner]
        num = CIRCLED[k - 1] if k <= len(CIRCLED) else f"{k}."
        via = r.via or "대입"
        inside = f" · {caller.name}() 안에서" if caller is not owner else ""
        arrow = "-->" if owner in entries else "-.->"
        out.append(f'    {ids[owner]} {arrow}|"{num} {_m(via + inside, 48)}"| {ids[tgt]}')
    out += ["```", "", "둥근 모양: 진입점 · 육각형: 스레드/콜백 · 점선: 스레드·콜백 안에서 다시 등록", ""]
    return out


def _module_diagram(idx: CodeIndex) -> list[str]:
    out = ["### 0.2 모듈(파일) 간 호출도", ""]
    edges = file_call_edges(idx)
    if not edges:
        out += ["(파일 사이의 호출 없음)", ""]
        return out
    shown = sorted(edges.items(), key=lambda e: -e[1])[:MAX_MODULE_EDGES]
    names = defaultdict(list)
    for pf in idx.user_files:
        names[pf.path.name].append(pf.path)
    def label(path: Path) -> str:
        return path.name if len(names[path.name]) == 1 else path.as_posix()
    fcount = defaultdict(int)
    for f in idx.funcs:
        fcount[f.path] += 1
    out += ["함수 단위로는 너무 많아서 **파일 단위**로 묶었습니다. 화살표 숫자는 호출 수(콜백 등록 포함)입니다."
            + (f" 호출이 많은 상위 {MAX_MODULE_EDGES}개만 그렸습니다 (전체 {len(edges)}개)." if len(edges) > len(shown)
               else ""), "", "```mermaid", "graph LR"]
    ids: dict = {}
    for (a, b), _ in shown:
        for p in (a, b):
            if p not in ids:
                ids[p] = f"m{len(ids)}"
                out.append(f'    {ids[p]}["{_m(label(p), 40)}<br/>함수 {fcount[p]}개"]')
    for (a, b), n in shown:
        out.append(f"    {ids[a]} -->|{n}| {ids[b]}")
    out += ["```", ""]
    return out


def _flat_calls(items) -> list[tuple[str, object]]:
    """핵심 흐름 항목을 순서대로 펼친다: [('call', Function) | ('reg', Function)] (중복 제거)"""
    out, seen = [], set()
    def walk(its):
        for it in its:
            if it[0] == "loop":
                walk(it[2])
            elif it[0] == "call":
                if it[1] not in seen:
                    seen.add(it[1])
                    out.append(("call", it[1]))
            else:
                for f in it[1]:
                    if f not in seen:
                        seen.add(f)
                        out.append(("reg", f))
    walk(items)
    return out


def _entry_diagram(idx: CodeIndex, root: Function) -> list[str]:
    first = _flat_calls(_skel_items(root))
    if not first:
        return []
    ids: dict = {}
    lines = ["```mermaid", "flowchart TD", "    " + _node(root, ids, "entry")]
    edges = []
    for i, (kind, f) in enumerate(first[:MAX_MAIN_CHILDREN], 1):
        if f in ids:
            continue
        lines.append("    " + _node(f, ids, "async" if kind == "reg" else "box"))
        edges.append(f'    {ids[root]} {"-.->" if kind == "reg" else "-->"}|"{i}{" 등록" if kind == "reg" else ""}"| {ids[f]}')
        if kind == "call" and len(first) * (MAX_SUB_CHILDREN + 1) <= MAX_FLOW_NODES:
            subs = [g for k, g in _flat_calls(_skel_items(f))]
            for g in subs[:MAX_SUB_CHILDREN]:
                if g not in ids:
                    lines.append("    " + _node(g, ids))
                edges.append(f"    {ids[f]} --> {ids[g]}")
            if len(subs) > MAX_SUB_CHILDREN:
                more = f"more{len(ids)}"
                ids[more] = more
                lines.append(f'    {more}["… 외 {len(subs) - MAX_SUB_CHILDREN}개"]')
                edges.append(f"    {ids[f]} --> {more}")
    if len(first) > MAX_MAIN_CHILDREN:
        lines.append(f'    rest["… 외 {len(first) - MAX_MAIN_CHILDREN}개 — 아래 목록 참고"]')
        edges.append(f"    {ids[root]} --> rest")
    return lines + edges + ["```", ""]


def _entry_flows(idx: CodeIndex) -> list[str]:
    out = ["### 0.3 진입점별 핵심 흐름", "",
           "`printf`·`memcpy` 같은 표준 함수와 조건 분기는 빼고 **사용자 함수만** 호출 순서대로 남겼습니다. "
           "최상위 번호가 실행 순서입니다. (`⚡` 스레드/콜백 시작 · `🔁` 반복 · `↑` 위에서 이미 펼침)", ""]
    entries = idx.entry_functions()
    if not entries:
        out += ["진입점(`main()` 등)을 찾지 못했습니다.", ""]
    for fn in entries:
        out += [f"#### {fn.name}() — 상세: {link(fn)}", ""]
        out += _entry_diagram(idx, fn)
        out += ["```"] + skeleton(idx, fn, max_depth=3, max_lines=60, describe=_desc) + ["```", ""]
    asyncs = idx.async_functions()
    if asyncs:
        order = [t for _, _, t, _ in thread_map(idx)]
        asyncs = sorted(asyncs, key=lambda f: order.index(f) if f in order else len(order))
        out += ["#### 스레드 / 콜백 (클릭해서 펼치기)", ""]
        for fn in asyncs:
            sk = skeleton(idx, fn, max_depth=2, max_lines=40, describe=_desc)
            n = len(_user_calls(fn))
            out += [f"<details><summary>⚡ <code>{fn.name}()</code> — {html_escape(_desc(fn))} "
                    f"(사용자 함수 {n}개 호출)</summary>", "", "```"] + sk + ["```", "", "</details>", ""]
    return out


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _module_table(idx: CodeIndex) -> list[str]:
    from ..summary import VERBS
    out = ["### 0.4 모듈 요약", "", "| 파일 | 역할 | 함수 | 스레드/콜백 | 대표 함수 |", "|---|---|---:|---|---|"]
    for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()):
        if not pf.functions:
            continue
        verbs = defaultdict(int)
        for f in pf.functions:
            for w in split_words(f.name):
                if w.lower() in VERBS:
                    verbs[VERBS[w.lower()]] += 1
                    break
        top = sorted(verbs.items(), key=lambda kv: -kv[1])[:3]
        role = ("주요 동작: " + " · ".join(f"{k} {v}" for k, v in top)) if top else ""
        threads = [f for f in pf.functions if f in idx.registered]
        rank = sorted(pf.functions, key=lambda f: (f not in idx.registered and f.name not in ("main",),
                                                   -len(_caller_list(idx, f)), -f.length))
        name = f"`{pf.path.as_posix()}`"
        out.append(f"| {name} | {cell(role)} | {len(pf.functions)} | "
                   f"{', '.join(f'`{f.name}`' for f in threads[:3])}{' …' if len(threads) > 3 else ''} | "
                   f"{', '.join(link(f) for f in rank[:3])} |")
    out.append("")
    return out


def _glance(r: ProjectReport, idx: CodeIndex) -> list[str]:
    total_code = sum(s.code for s in r.languages.values())
    asyncs = idx.async_functions()
    out = ["## 0. 한눈에 보기", "",
           f"> 파일 **{len(r.user_files)}개** · 코드 **{total_code:,}줄** · 함수 **{len(idx.funcs)}개** · "
           f"진입점 {len(idx.entry_functions())}개 · 스레드/콜백 {len(asyncs)}개 · "
           f"진입점에서 도달하지 않는 함수 {len(idx.unreached())}개", "",
           "읽는 순서: **0.1 구성도**로 큰 그림 → **0.3 핵심 흐름**으로 실행 순서 → 궁금한 함수는 "
           "**4. 함수 상세**의 요약표에서 찾아 클릭", ""]
    out += _thread_diagram(idx)
    out += _module_diagram(idx)
    out += _entry_flows(idx)
    out += _module_table(idx)
    return out


# ───────────────────────── 섹션 ─────────────────────────

def _exclusions(r: ProjectReport) -> list[str]:
    """탐색에서 제외된 폴더/파일 요약. 기본 제외는 개수만, .gitignore/옵션/링크는 경로와 규칙까지."""
    if not r.excluded:
        return []
    labels = {"default": "기본 제외", "option": "-e 옵션", "gitignore": ".gitignore", "link": "링크/정션"}
    counts = defaultdict(int)
    for e in r.excluded:
        counts[e.kind] += 1
    summary = ", ".join(f"{labels[k]} {counts[k]}" for k in labels if counts[k])
    out = [f"- 탐색에서 제외된 폴더/파일: {len(r.excluded)}개 ({summary})"]
    warning = r.exclusion_warning()
    if warning:
        out.append(f"- ⚠ **분석할 소스 코드가 없습니다.** {warning}")
    shown = [e for e in r.excluded if e.kind != "default"]
    if shown:
        out += ["", "<details><summary>제외된 항목 보기 (기본 제외 제외)</summary>", "",
                "| 경로 | 종류 | 이유 |", "|---|---|---|"]
        for e in shown[:300]:
            out.append(f"| `{e.path.as_posix()}{'/' if e.is_dir else ''}` | {'폴더' if e.is_dir else '파일'} "
                       f"| {cell(e.detail)} |")
        if len(shown) > 300:
            out.append(f"| … 외 {len(shown) - 300}개 | | |")
        out += ["", "</details>", ""]
    return out


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
    out += _exclusions(r)
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

    out += ["### 2.2 파일별 함수", "", "| 파일 | 코드 줄 | 정의된 함수 |", "|---|---:|---|"]
    finfo = {f.path: f for f in r.files}
    for pf in sorted(idx.user_files, key=lambda p: p.path.as_posix()):
        fi = finfo.get(pf.path)
        fns = ", ".join(link(f) for f in pf.functions[:12]) + (" …" if len(pf.functions) > 12 else "")
        out.append(f"| `{pf.path.as_posix()}` | {fi.code if fi else ''} | {fns} |")
    out.append("")

    # 호출 관계도
    out += ["### 2.3 함수 호출 관계도", "",
            "실선: 직접 호출 · 점선: 함수 포인터로 등록(인터럽트/콜백)", ""]
    call_edges = {(fn, c.target.func) for fn in idx.funcs for c in fn.calls
                  if c.target and c.target.kind == "user" and c.target.func is not fn}
    cb_edges = {(fn, t.func) for fn in idx.funcs for _, t in fn.callbacks if t.kind == "user"}
    if not call_edges and not cb_edges:
        out += ["(사용자 함수 간 호출 없음)", ""]
    elif len(call_edges) + len(cb_edges) > MAX_MERMAID_EDGES:
        out += [f"(호출 관계 {len(call_edges) + len(cb_edges)}개 — 많아서 그림 생략. "
                "[0.2 모듈 간 호출도](#02-모듈파일-간-호출도)와 [0.3 진입점별 핵심 흐름](#03-진입점별-핵심-흐름)을 "
                "보거나, 함수 상세의 '호출되는 곳 / 호출' 참고)", ""]
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
           "소스에 적힌 **호출 순서**대로 진입점부터 따라간 **전체** 트리입니다. 표준 함수·조건 분기까지 모두 "
           "들어 있어 길기 때문에 접어 두었습니다. 사용자 함수만 추린 흐름은 "
           "[0.3 진입점별 핵심 흐름](#03-진입점별-핵심-흐름)을 보세요.", "",
           "(런타임 분기 조건은 반영하지 않음 · `→` 사용자 함수 위치 · `[ ]` 외부 함수 종류 · "
           "`⚡` 콜백 등록 · `🔁` 반복문 · `⏳` 폴링 대기 · `❓` 조건 분기)", ""]
    entries = idx.entry_functions()
    n = 1
    if not entries:
        out += ["진입점(`main()` 등)을 찾지 못했습니다.", ""]
    for fn in entries:
        out += [f"### 3.{n} {fn.name}() — {', '.join(idx.role(fn)) or '진입점'}", "",
                f"위치: `{fn.path.as_posix()}:{fn.line}` · 상세: {link(fn)}", ""]
        out += _folded_tree(flow_tree(idx, fn, depth))
        n += 1

    asyncs = idx.async_functions()
    if asyncs:
        out += [f"### 3.{n} 비동기 진입점 (인터럽트 핸들러 / 콜백)", "",
                "아래 함수들은 직접 호출되지 않고 함수 포인터로 **등록**되어, "
                "인터럽트 발생 시 또는 라이브러리에 의해 호출됩니다.", ""]
        for fn in asyncs:
            regs = [f"{link(caller)}에서 `{r.via or '함수 포인터 대입'}`로 등록 (`{caller.path.name}:{r.line}`)"
                    for caller, r in idx.registered[fn]]
            out += [f"#### ⚡ {fn.name}()", "", "- " + "\n- ".join(regs), ""]
            out += _folded_tree(flow_tree(idx, fn, depth))
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
        out += [f"- {link(f)} — {cell(_desc(f))} · `{f.path.as_posix()}:{f.line}`" for f in unreached]
        out.append("")
    return out


def _folded_tree(lines: list[str]) -> list[str]:
    return [f"<details><summary>전체 호출 트리 ({len(lines)}줄, 표준 함수·분기 포함)</summary>", "", "```"] \
        + lines + ["```", "", "</details>", ""]


def _user_calls(fn: Function) -> list[Function]:
    """이 함수가 호출·등록하는 사용자 함수 (등장 순서, 중복 제거)"""
    seen: dict[Function, None] = {}
    events = [(c.pos, c.target.func) for c in fn.calls if c.target and c.target.kind == "user"]
    events += [(r.pos, t.func) for r, t in fn.callbacks if t.kind == "user"]
    for _, f in sorted(events, key=lambda e: e[0]):
        if f is not fn:
            seen.setdefault(f, None)
    return list(seen)


def _caller_list(idx: CodeIndex, fn: Function) -> list[Function]:
    uniq: dict[Function, None] = {}
    for caller, _ in idx.callers.get(fn, []):
        uniq.setdefault(caller, None)
    for caller, _ in idx.registered.get(fn, []):
        uniq.setdefault(caller, None)
    return list(uniq)


def _links(fns: list[Function], limit: int) -> str:
    s = ", ".join(link(f) for f in fns[:limit])
    return s + (f" 외 {len(fns) - limit}개" if len(fns) > limit else "")


def _function_detail(idx: CodeIndex, fn: Function, with_source: bool, path: list[str]) -> list[str]:
    pf = idx_file(idx, fn)
    nth = _Ctx.nth.get(fn, 1)
    summary, src = summarize(fn, idx)
    title = f"#### `{fn.name}()` — {summary}" + (f" · {nth}번째 정의 (`#if` 분기 추정)" if nth > 1 else "")
    out = [f'<a id="{anchor(fn)}"></a>', "", title, ""]
    head = []
    if len(path) > 1:
        head.append(_crumb(path))
    role = [t for t in idx.role(fn) if t != "프로그램 진입점"]
    head.append(f"`{fn.path.as_posix()}:{fn.line}` ({fn.length}줄)" + (f" · {', '.join(role)}" if role else "")
                + ("" if src == "주석" else " · _요약은 이름 기반 추정_"))
    out += [" · ".join(head), ""]

    # → 하위 함수: 호출 순서대로, 각각 한 줄 요약
    callees = callees_in_order(fn)
    me = _Ctx.order.get(fn, 0)
    if callees:
        out.append("→ **호출하는 함수** (순서대로)")
        out.append("")
        for i, (kind, f, obj) in enumerate(callees[:MAX_CARD_CALLEES], 1):
            mark = ""
            if f in idx.registered and kind == "reg":
                mark = f"⚡ `{obj.via or '대입'}`로 시작/등록 · "
            later = _Ctx.order.get(f, -1)
            note = " · ↑ 앞에서 설명" if 0 <= later < me else ""
            out.append(f"{i}. {mark}{link(f)} — {cell(_desc(f))}{note}")
        if len(callees) > MAX_CARD_CALLEES:
            out.append(f"{MAX_CARD_CALLEES + 1}. … 외 {len(callees) - MAX_CARD_CALLEES}개 (자세히 참고)")
    else:
        out.append("→ 호출하는 사용자 함수 없음")

    # 나머지는 접기
    more: list[str] = [f"- **시그니처**: `{fn.signature}` · `{fn.path.as_posix()}:{fn.line}-{fn.end_line}`"]
    if fn.doc:
        more.append("- **설명 주석**: " + "<br>".join(cell(d) for d in fn.doc[:8]))
    callers = _caller_list(idx, fn)
    if idx.callers.get(fn):
        uniq: dict = {}
        for caller, c in idx.callers[fn]:
            uniq.setdefault(caller, []).append(c.line)
        more.append("- **호출되는 곳**: " + ", ".join(
            f"{link(caller)} (`{caller.path.name}:{','.join(map(str, lns))}`)" for caller, lns in uniq.items()))
    if fn in idx.registered:
        more.append("- **등록되는 곳**: " + ", ".join(
            f"{link(c)}의 `{r.via or '대입'}` (L{r.line})" for c, r in idx.registered[fn]))
    if not callers:
        more.append("- **호출되는 곳**: (사용자 코드에서 호출 없음)")
    seen: dict[str, list] = {}
    if fn.calls:
        more.append("- **호출하는 함수 전체** (표준·외부 함수 포함, 등장 순서):")
        for c in fn.calls:
            seen.setdefault(c.name, []).append(c)
        for i, (name, cs) in enumerate(seen.items(), 1):
            lines_ = ", ".join(str(c.line) for c in cs[:5]) + (" …" if len(cs) > 5 else "")
            cnt = f" ×{len(cs)}" if len(cs) > 1 else ""
            more.append(f"  {i}. {_target_desc(cs[0].target)}{cnt} (L{lines_})")
    if fn.callbacks:
        more.append("- **등록/전달하는 함수 포인터**: " + ", ".join(
            (link(t.func) if t.kind == "user" else f"`{t.name}`") + f" ← `{r.via or '대입'}` (L{r.line})"
            for r, t in fn.callbacks))
    if fn.globals_used:
        more.append("- **사용하는 전역 변수**: " + ", ".join(f"`{g}`" for g in fn.globals_used))
    if fn.loops:
        more.append("- **반복/대기**:")
        more += [f"  - L{lp.line} {cell(loop_label(lp))}" for lp in fn.loops]
    if fn.pragmas:
        more.append("- **HLS/pragma**: " + "; ".join(f"L{ln} `{cell(t)}`" for ln, t in fn.pragmas))
    if with_source and pf is not None:
        src_lines = pf.lines[fn.line - 1:fn.end_line]
        cut = len(src_lines) > MAX_SOURCE_LINES
        src_lines = src_lines[:MAX_SOURCE_LINES] + (["    /* … 이하 생략 … */"] if cut else [])
        lang = "cpp" if fn.path.suffix in (".cpp", ".cc", ".cxx", ".hpp") else "c"
        more += ["", "소스:", "", f"```{lang}"] + src_lines + ["```"]
    parts = [f"호출되는 곳 {len(callers)}"] + ([f"외부 함수 포함 호출 {len(seen)}종"] if seen else []) \
        + ([f"전역 {len(fn.globals_used)}개"] if fn.globals_used else []) + (["소스"] if with_source else [])
    out += ["", f"<details><summary>자세히 ({' · '.join(parts)})</summary>", ""] + more + ["", "</details>", ""]
    return out


def _crumb(path: list[str]) -> str:
    """진입점부터의 호출 경로. 길면 가운데를 줄인다."""
    if len(path) <= MAX_CRUMB:
        return " › ".join(path)
    return " › ".join(path[:1] + [f"…({len(path) - MAX_CRUMB + 1}단계)…"] + path[-(MAX_CRUMB - 2):])


def idx_file(idx: CodeIndex, fn: Function):
    for pf in idx.user_files:
        if pf.path == fn.path:
            return pf
    return None


def _group_title(n: int, kind: str, root) -> str:
    if kind == "entry":
        return f"4.{n} {root.name}() 에서 시작"
    if kind == "async":
        return f"4.{n} ⚡ {root.name}() 에서 시작 (스레드/콜백)"
    return f"4.{n} 진입점에서 도달하지 않는 함수"


def _group_detail(idx: CodeIndex, n: int, kind: str, root, items, with_source: bool) -> list[str]:
    out = [f"### {_group_title(n, kind, root)}", ""]
    if kind == "unreached":
        out += ["다른 프로젝트에서 호출되거나, 매크로/함수 포인터 배열을 통해 호출되거나, 사용하지 않는 코드일 수 있습니다.", ""]
    out += [f"함수 {len(items)}개 · 위에서 아래로 호출 순서 (들여쓰기 = 호출 깊이)", "",
            "| 함수 | 한 줄 요약 | 파일 |", "|---|---|---|"]
    for fn, path in items:
        depth = len(path) - 1
        indent = "&nbsp;&nbsp;&nbsp;" * min(depth, MAX_INDENT) + ("└ " if depth else "") \
            + (f"({depth}단계) " if depth > MAX_INDENT else "")
        out.append(f"| {indent}{link(fn)} | {cell(summarize(fn, idx)[0])} | `{fn.path.name}:{fn.line}` |")
    out.append("")
    for fn, path in items:
        out += _function_detail(idx, fn, with_source, path)
    return out


DETAIL_INTRO = ("`main()`부터 **호출하는 함수 → 하위 함수** 순서로 설명합니다. 스레드·콜백은 자기 절에서 따로 시작하고, "
                "이미 설명한 함수는 다시 쓰지 않고 `↑ 앞에서 설명`으로 표시합니다.")


def _details(idx: CodeIndex, with_source: bool) -> list[str]:
    out = ["## 4. 함수 상세", "", DETAIL_INTRO, ""]
    for n, (kind, root, items) in enumerate(_Ctx.groups, 1):
        out += _group_detail(idx, n, kind, root, items, with_source)
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
          "_정적 분석 결과입니다. 주석과 `#if 0` 블록은 제외하고, 그 밖의 `#ifdef` 분기는 모두 포함합니다. 매크로 내부 호출·함수 포인터 배열을 통한 호출은 "
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
        "**목차**: [0. 한눈에 보기](#0-한눈에-보기) · [1. 개요](#1-개요) · [2. 전체 구조](#2-전체-구조) · "
        "[3. 실행 흐름](#3-실행-흐름) · "
        "[4. 함수 상세](#4-함수-상세) · [5. 자료형·매크로·전역 변수](#5-자료형--매크로--전역-변수)", ""]
    out += _glance(report, idx)
    out += _overview(report, idx)
    out += _structure(report, idx, max_depth)
    out += _flow(idx, flow_depth)
    out += _details(idx, with_source)
    out += _types(idx)
    out += FOOTER
    return "\n".join(out)


def render_split(report: ProjectReport, max_depth=None, flow_depth: int = 8,
                 with_source: bool = True) -> dict[str, str]:
    """index.md(개요·구조·실행 흐름·자료형) + files/<번호>_<진입점>.md(호출 순서 함수 상세)로 나눈다. {상대 경로: 내용}"""
    idx: CodeIndex = report.index
    _prepare(idx, split=True)

    out = _header(report) + [
        "**목차**: [0. 한눈에 보기](#0-한눈에-보기) · [1. 개요](#1-개요) · [2. 전체 구조](#2-전체-구조) · "
        "[3. 실행 흐름](#3-실행-흐름) · "
        "[4. 함수 상세](#4-함수-상세) · [5. 자료형·매크로·전역 변수](#5-자료형--매크로--전역-변수)", ""]
    out += _glance(report, idx)
    out += _overview(report, idx)
    out += _structure(report, idx, max_depth)
    out += _flow(idx, flow_depth)
    out += ["## 4. 함수 상세", "", DETAIL_INTRO, "", "| 절 | 함수 수 | 페이지 |", "|---|---:|---|"]
    for n, (kind, root, items) in enumerate(_Ctx.groups, 1):
        page = group_page(n, kind, root)
        out.append(f"| {_group_title(n, kind, root)} | {len(items)} | [{page}]({page}) |")
    out.append("")
    out += _types(idx)
    out += FOOTER
    pages = {"index.md": "\n".join(out)}

    for n, (kind, root, items) in enumerate(_Ctx.groups, 1):
        _Ctx.current = page = group_page(n, kind, root)
        body = [f"# {_group_title(n, kind, root)} — 함수 상세", "",
                f"[← index](../index.md) · [실행 흐름](../index.md#3-실행-흐름) · 함수 {len(items)}개", ""]
        body += _group_detail(idx, n, kind, root, items, with_source)
        pages[page] = "\n".join(body)
    _Ctx.current = "index.md"
    return pages
