"""호출 그래프 구성: 호출 대상 해석, 호출되는 곳(역방향), 콜백 등록, 전역 변수 사용, 실행 흐름 트리."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .parsers.c_parser import Call, Function, Loop, ParsedFile

STD_FUNCS = {
    "printf", "sprintf", "snprintf", "fprintf", "puts", "putchar", "getchar",
    "scanf", "sscanf", "memset", "memcpy", "memmove", "memcmp", "strlen",
    "strcpy", "strncpy", "strcmp", "strncmp", "strcat", "strchr", "strstr",
    "malloc", "calloc", "realloc", "free", "exit", "abort", "atoi", "atol",
    "strtol", "strtoul", "abs", "rand", "srand", "assert", "fopen", "fclose",
    "fread", "fwrite", "fgets", "time", "clock",
}
ENTRY_NAMES = ("main", "wmain", "WinMain", "wWinMain")


@dataclass
class Target:
    kind: str                       # user / macro / lib / libmacro / proto / std / unknown / indirect
    name: str
    func: Function | None = None
    loc: str = ""
    doc: str = ""


class CodeIndex:
    def __init__(self, user: list[ParsedFile], lib: list[ParsedFile], hls_tops: set[str] | None = None):
        self.user_files = user
        self.lib_files = lib
        self.hls_tops = hls_tops or set()
        self.funcs: list[Function] = [f for pf in user for f in pf.functions]
        self.by_name: dict[str, list[Function]] = defaultdict(list)
        for f in self.funcs:
            self.by_name[f.name].append(f)

        self.user_macros = {n: (pf.path, m) for pf in user for n, m in pf.macros.items()}
        self.user_protos = {n: (pf.path, v) for pf in user for n, v in pf.prototypes.items()}
        self.lib_funcs: dict[str, Function] = {}
        self.lib_macros: dict = {}
        self.lib_protos: dict = {}
        for pf in lib:
            for f in pf.functions:
                self.lib_funcs.setdefault(f.name, f)
            for n, m in pf.macros.items():
                self.lib_macros.setdefault(n, (pf.path, m))
            for n, v in pf.prototypes.items():
                self.lib_protos.setdefault(n, (pf.path, v))

        self.globals: dict[str, list] = defaultdict(list)
        for pf in user:
            for g in pf.globals:
                self.globals[g.name].append((pf.path, g))

        self.callers: dict[Function, list[tuple[Function, Call]]] = defaultdict(list)
        self.registered: dict[Function, list[tuple[Function, object]]] = defaultdict(list)
        self.global_users: dict[str, list[Function]] = defaultdict(list)
        self._link()

    # ── 해석 ──
    def resolve(self, name: str, fn: Function) -> Target:
        if name.startswith("->"):
            return Target("indirect", name)
        cands = self.by_name.get(name)
        if cands:
            same = [c for c in cands if c.path == fn.path]
            public = [c for c in cands if not c.is_static]
            f = (same or public or cands)[0]
            return Target("user", name, f, f"{f.path.as_posix()}:{f.line}")
        if name in self.user_macros:
            p, m = self.user_macros[name]
            return Target("macro", name, loc=f"{p.as_posix()}:{m.line}", doc=m.value)
        if name in self.lib_funcs:
            f = self.lib_funcs[name]
            return Target("lib", name, f, f"{f.path.as_posix()}:{f.line}", f.doc[0] if f.doc else "")
        if name in self.lib_macros:
            p, m = self.lib_macros[name]
            return Target("libmacro", name, loc=f"{p.as_posix()}:{m.line}", doc=m.value)
        if name in self.user_protos:
            p, (ln, _) = self.user_protos[name]
            return Target("proto", name, loc=f"{p.as_posix()}:{ln}")
        if name in self.lib_protos:
            p, (ln, _) = self.lib_protos[name]
            return Target("lib", name, loc=f"{p.as_posix()}:{ln}")
        if name in STD_FUNCS:
            return Target("std", name)
        return Target("unknown", name)

    def _link(self):
        func_names = set(self.by_name) | set(self.lib_funcs)
        for fn in self.funcs:
            for c in fn.calls:
                c.target = self.resolve(c.name, fn)
                if c.target.kind == "user":
                    self.callers[c.target.func].append((fn, c))
            seen_g = set()
            for r in fn.refs:
                if r.name in func_names and r.name not in self.globals:
                    t = self.resolve(r.name, fn)
                    fn.callbacks.append((r, t))
                    if t.kind == "user":
                        self.registered[t.func].append((fn, r))
                elif r.name in self.globals and r.name not in seen_g:
                    seen_g.add(r.name)
                    fn.globals_used.append(r.name)
                    self.global_users[r.name].append(fn)

    # ── 진입점 ──
    def entry_functions(self) -> list[Function]:
        out = [f for n in ENTRY_NAMES for f in self.by_name.get(n, [])]
        out += [f for n in sorted(self.hls_tops) for f in self.by_name.get(n, []) if f not in out]
        out += [f for f in self.funcs if f.name.startswith("luaopen_") and f not in out]
        return out

    def async_functions(self) -> list[Function]:
        return [f for f in self.funcs if f in self.registered]

    def unreached(self) -> list[Function]:
        roots = self.entry_functions() + self.async_functions()
        seen, stack = set(roots), list(roots)
        while stack:
            f = stack.pop()
            nxt = [c.target.func for c in f.calls if c.target and c.target.kind == "user"]
            nxt += [t.func for _, t in f.callbacks if t.kind == "user"]
            for g in nxt:
                if g not in seen:
                    seen.add(g)
                    stack.append(g)
        return [f for f in self.funcs if f not in seen]

    def role(self, fn: Function) -> list[str]:
        tags = []
        if fn.name in ENTRY_NAMES:
            tags.append("프로그램 진입점")
        if fn.name in self.hls_tops:
            tags.append("HLS top 함수")
        if fn.name.startswith("luaopen_"):
            tags.append("Lua C 모듈 진입점")
        if fn in self.registered:
            tags.append("콜백/인터럽트 핸들러")
        if fn.is_static:
            tags.append("static (파일 내부 전용)")
        return tags


# ───────────────────────── 실행 흐름 트리 ─────────────────────────

KIND_TAG = {
    "lib": "BSP/라이브러리", "libmacro": "BSP 매크로", "macro": "매크로",
    "std": "표준 C", "proto": "선언만 있음", "unknown": "외부/미확인",
    "indirect": "함수 포인터 호출",
}


def _body_tree(fn: Function):
    loops: list[Loop] = sorted(fn.loops + fn.branches, key=lambda b: (b.start, -b.end))
    parent: dict[int, int | None] = {}
    for i, lp in enumerate(loops):
        parent[i] = next((j for j in range(i - 1, -1, -1)
                          if loops[j].start <= lp.start and lp.end <= loops[j].end), None)
    children: dict[int | None, list] = defaultdict(list)
    for i, lp in enumerate(loops):
        children[parent[i]].append((lp.start, "loop", i))
    for c in fn.calls:
        inner = None
        for i, lp in enumerate(loops):
            if lp.start <= c.pos < lp.end:
                inner = i
        children[inner].append((c.pos, "call", c))

    def build(key):
        out = []
        for _, kind, obj in sorted(children[key], key=lambda x: x[0]):
            if kind == "loop":
                sub = build(obj)
                if loops[obj].kind in BRANCH_KINDS and not sub:
                    continue                 # 호출이 없는 분기는 생략
                out.append(("loop", loops[obj], sub))
            else:
                out.append(("call", obj))
        return out
    return build(None)


BRANCH_KINDS = {"if", "elif", "else"}


def loop_label(lp: Loop) -> str:
    if lp.kind == "if":
        return f"❓ if ({lp.cond}) 일 때"
    if lp.kind == "elif":
        return f"❓ else if ({lp.cond}) 일 때"
    if lp.kind == "else":
        return "❓ else 일 때"
    if lp.kind == "wait":
        return f"⏳ while ({lp.cond}); — 조건이 바뀔 때까지 대기(폴링)"
    if lp.kind == "do":
        return f"🔁 do … while ({lp.cond})" + (" — 무한 반복" if lp.infinite else "")
    return f"🔁 {lp.kind} ({lp.cond})" + (" — 무한 반복" if lp.infinite else " 반복")


def flow_tree(index: CodeIndex, root: Function, max_depth: int = 8, max_lines: int = 400) -> list[str]:
    lines = [f"{root.name}()    [{root.path.as_posix()}:{root.line}]"]
    expanded: set[Function] = {root}

    def call_label(fn: Function, c: Call, count: int) -> str:
        t: Target = c.target
        s = f"{c.name}({c.args})" + (f" ×{count}" if count > 1 else "")
        if t.kind == "user":
            s += f"    → {t.loc}"
        else:
            where = f": {Path(t.loc.split(':')[0]).name}" if t.loc and t.kind in ("lib", "libmacro") else ""
            s += f"    [{KIND_TAG.get(t.kind, t.kind)}{where}]"
        regs = [r.name for r, _ in fn.callbacks if r.via_pos == c.pos]
        if regs:
            s += "    ⚡ " + ", ".join(regs) + " 등록"
        return s

    def render(fn: Function, items, prefix: str, depth: int, stack: frozenset):
        # 연속으로 같은 함수를 호출하면 하나로 합친다
        merged = []
        for it in items:
            if it[0] == "call" and merged and merged[-1][0] == "call" and merged[-1][1].name == it[1].name:
                merged[-1] = ("call", merged[-1][1], merged[-1][2] + 1)
            else:
                merged.append(it if it[0] == "loop" else ("call", it[1], 1))
        for i, it in enumerate(merged):
            if len(lines) >= max_lines:
                lines.append(prefix + "… (출력 줄 수 제한으로 생략)")
                return
            last = i == len(merged) - 1
            conn, child = ("└─ ", "   ") if last else ("├─ ", "│  ")
            if it[0] == "loop":
                lines.append(prefix + conn + loop_label(it[1]))
                render(fn, it[2], prefix + child, depth, stack)
                continue
            c, count = it[1], it[2]
            label = call_label(fn, c, count)
            sub = c.target.func if c.target and c.target.kind == "user" else None
            note, expand = "", False
            if sub is not None:
                if sub in stack:
                    note = "    ↺ 재귀 호출"
                elif depth >= max_depth:
                    note = "    … (깊이 제한)"
                elif sub in expanded:
                    note = "    ↑ 위에서 이미 펼침" if sub.calls else ""
                else:
                    expand = True
            lines.append(prefix + conn + label + note)
            if expand:
                expanded.add(sub)
                render(sub, _body_tree(sub), prefix + child, depth + 1, stack | {sub})

    render(root, _body_tree(root), "", 1, frozenset({root}))
    return lines
