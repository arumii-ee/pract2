"""C / C++ 경량 파서 (컴파일러 없이 정적 분석).

주석·문자열을 같은 길이의 공백으로 지워 오프셋(=줄 번호)을 유지한 채로
중괄호 깊이를 추적해 함수 정의, 프로토타입, 매크로, 자료형, 전역 변수를 찾고
함수 본문에서 호출 순서 / 반복문 / 함수 포인터 전달(콜백 등록)을 뽑아낸다.

한계: #ifdef 분기는 양쪽 모두 읽고, 매크로 전개는 하지 않는다.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..languages import read_source

C_KEYWORDS = {
    "if", "else", "for", "while", "do", "switch", "case", "default", "return",
    "sizeof", "goto", "break", "continue", "typedef", "struct", "union", "enum",
    "static", "extern", "const", "volatile", "inline", "register", "auto",
    "signed", "unsigned", "void", "char", "short", "int", "long", "float",
    "double", "_Bool", "bool", "defined", "alignof", "_Alignof", "__alignof__",
    "typeof", "__typeof__", "_Static_assert", "static_assert", "__attribute__",
    "__asm__", "asm", "__asm", "__volatile__", "new", "delete", "throw", "catch",
    "try", "operator", "template", "typename", "class", "namespace", "using",
    "public", "private", "protected", "virtual", "decltype", "noexcept",
    "nullptr", "this", "true", "false", "NULL", "__inline", "__inline__",
}
TYPE_WORDS = {
    "void", "char", "short", "int", "long", "float", "double", "signed",
    "unsigned", "_Bool", "bool", "const", "volatile", "static", "extern",
    "inline", "struct", "union", "enum",
}
INFINITE_CONDS = {"1", "TRUE", "true", "1U", "1u", "(1)", "1==1"}

RE_LEX = re.compile(
    r'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
RE_PP = re.compile(r'^[ \t]*#(?:[^\n]*\\\n)*[^\n]*', re.M)
RE_INCLUDE = re.compile(r'#\s*include\s*([<"])([^>"]+)[>"]')
RE_DEFINE = re.compile(r'#\s*define\s+(\w+)(\([^)]*\))?(.*)', re.S)
RE_ATTR = re.compile(
    r'__attribute__\s*\(\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\)|__declspec\s*\([^()]*\)')
RE_NAME_PAREN = re.compile(r'([A-Za-z_~][\w:~]*)\s*\(')
RE_PRE_OK = re.compile(r'[\w\s\*&:<>,~]*')
RE_POST_OK = re.compile(
    r'(?:const|noexcept|override|final|volatile|throw\s*\(\s*\)|\s)*(?::.*|->.*)?', re.S)
RE_TOP = re.compile(r'[{};]')
RE_BRACE = re.compile(r'[{}]')
RE_PAREN = re.compile(r'[()]')
RE_LOOP = re.compile(r'\b(while|for)\s*\(|\bdo\b\s*\{')
RE_IF = re.compile(r'\b(?:else\s+)?if\s*\(|\belse\b')
RE_CALL = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
RE_IDENT = re.compile(r'\b[A-Za-z_]\w*\b')
RE_FP_NAME = re.compile(r'\(\s*\*\s*(\w+)\s*\)')
RE_XPAR = re.compile(r'\bXPAR_\w+')


# ───────────────────────── 데이터 모델 ─────────────────────────

@dataclass(eq=False)
class Call:
    name: str
    line: int
    pos: int
    args: str
    indirect: bool = False      # ptr->fn(...) / obj.fn(...)
    target: object = None       # graph 단계에서 채움 (graph.Target)


@dataclass(eq=False)
class Loop:
    kind: str                   # while / for / do / wait(폴링 대기)
    line: int
    start: int
    end: int
    cond: str
    infinite: bool = False


@dataclass(eq=False)
class IdentRef:
    """본문에서 호출 없이 이름만 쓰인 식별자 (함수 포인터 전달/대입, 전역 변수 사용 판별용)."""
    name: str
    line: int
    pos: int
    via: str | None = None      # 인자로 넘겨진 호출 이름 (예: XScuGic_Connect)
    via_pos: int | None = None


@dataclass(eq=False)
class Function:
    name: str
    path: Path
    line: int
    end_line: int
    signature: str
    return_type: str
    params: str
    is_static: bool
    doc: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    branches: list[Loop] = field(default_factory=list)   # kind: if / elif / else
    refs: list[IdentRef] = field(default_factory=list)
    pragmas: list[tuple[int, str]] = field(default_factory=list)
    # graph 단계에서 채움
    callbacks: list = field(default_factory=list)       # [(IdentRef, Target)]
    globals_used: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end_line - self.line + 1


@dataclass
class Macro:
    name: str
    line: int
    params: str | None
    value: str


@dataclass
class TypeDef:
    kind: str
    name: str
    line: int
    decl: str


@dataclass
class GlobalVar:
    name: str
    line: int
    decl: str
    is_static: bool
    is_extern: bool


@dataclass
class ParsedFile:
    path: Path
    source: str
    generated: bool = False
    includes: list[tuple[str, int, bool]] = field(default_factory=list)  # (이름, 줄, <시스템>)
    functions: list[Function] = field(default_factory=list)
    prototypes: dict[str, tuple[int, str]] = field(default_factory=dict)
    macros: dict[str, Macro] = field(default_factory=dict)
    types: list[TypeDef] = field(default_factory=list)
    globals: list[GlobalVar] = field(default_factory=list)
    pragmas: list[tuple[int, str]] = field(default_factory=list)
    header_doc: list[str] = field(default_factory=list)
    xpar_refs: dict[str, int] = field(default_factory=dict)

    @property
    def lines(self) -> list[str]:
        return self.source.splitlines()


# ───────────────────────── 유틸 ─────────────────────────

def _blank(s: str) -> str:
    return re.sub(r'[^\n]', ' ', s)


RE_COND = re.compile(r'^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b(.*)$')
RE_ZERO = re.compile(r'^\(*\s*(0|FALSE|false|0U|0u)\s*\)*\s*$')
RE_ONE = re.compile(r'^\(*\s*(1|TRUE|true|1U|1u)\s*\)*\s*$')


def dead_ranges(text: str) -> list[tuple[int, int]]:
    """`#if 0` 블록(과 `#if 1`의 `#else` 쪽)처럼 절대 컴파일되지 않는 구간의 (시작, 끝) 오프셋.
    text는 주석을 지운 상태여야 한다(`#if 0 // 옛 코드` 처리). 다른 조건은 판단하지 않고 살려 둔다."""
    out: list[tuple[int, int]] = []
    stack: list[str] = []          # zero(죽은 쪽) / one(살아 있는 쪽, else가 죽음) / dead_else / live / other
    start = None
    pos = 0
    for line in text.split("\n"):
        m = RE_COND.match(line)
        if m:
            kw, cond = m.group(1), m.group(2).strip()
            if kw in ("if", "ifdef", "ifndef"):
                stack.append("zero" if kw == "if" and RE_ZERO.match(cond)
                             else "one" if kw == "if" and RE_ONE.match(cond) else "other")
            elif kw in ("elif", "else") and stack:
                top = stack[-1]
                if top == "zero":
                    stack[-1] = "live" if kw == "else" else "other"
                elif top in ("one", "dead_else"):
                    stack[-1] = "dead_else"
            elif kw == "endif" and stack:
                stack.pop()
        dead = any(f in ("zero", "dead_else") for f in stack)
        if dead and start is None:
            start = pos
        elif not dead and start is not None:
            out.append((start, pos))
            start = None
        pos += len(line) + 1
    if start is not None:
        out.append((start, len(text)))
    return out


def _blank_ranges(s: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return s
    parts, last = [], 0
    for a, b in ranges:
        parts += [s[last:a], _blank(s[a:b])]
        last = b
    return "".join(parts) + s[last:]


def _collapse(s: str, limit: int | None = None) -> str:
    s = " ".join(s.replace("\\\n", " ").split())
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def _match(text: str, i: int, pattern: re.Pattern, open_ch: str, end: int | None = None) -> int:
    """text[i]의 여는 괄호에 대응하는 닫는 괄호 위치. 못 찾으면 -1."""
    depth = 0
    for m in pattern.finditer(text, i, end if end is not None else len(text)):
        depth += 1 if m.group() == open_ch else -1
        if depth == 0:
            return m.start()
    return -1


def _split_top(s: str, sep: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


LICENSE_HINTS = ("Copyright", "Permission is hereby granted", "SPDX-License", "All rights reserved")


def clean_doc(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        line = re.sub(r'^(/\*+!?|//+!?|\*+/?)', '', line).strip()
        line = re.sub(r'\*+/$', '', line).strip()
        if not line or re.fullmatch(r'[\*\-=_/#~+.\s]*', line):
            continue
        out.append(line)
    return out


RE_CODE_LIKE = re.compile(
    r'^\s*#\s*\w+'                       # #include, #define …
    r'|;\s*(//.*)?$'                      # 문장 끝 ;
    r'|[{}]\s*$'                          # 블록 괄호
    r'|^\s*[\w.\[\]>-]+\s*[+\-*/|&]?=[^=]'  # 대입
    r'|^\s*(if|for|while|switch|return|else)\b')


def is_prose(line: str) -> bool:
    """파일 설명으로 쓸 만한 문장인지: 주석 처리된 코드, 숫자/기호만, 한 단어짜리 줄은 제외."""
    if RE_CODE_LIKE.search(line):
        return False
    words = re.findall(r'[^\W\d_][\w\-]*', line)       # 숫자로 시작하지 않는 단어
    if len(words) < 2 and not re.search(r'[가-힣]{2,}', line):
        return False                                     # 'function', 'MAIN_H_', '0x001f3fff', '16 16'
    return True


# ───────────────────────── 파서 ─────────────────────────

class _Parser:
    def __init__(self, path: Path, src: str, analyze_bodies: bool):
        self.pf = ParsedFile(path=path, source=src)
        self.src = src
        self.analyze_bodies = analyze_bodies
        self.line_starts = [0] + [m.end() for m in re.finditer(r'\n', src)]
        self.comments: list[tuple[int, int, str]] = []

    def line(self, off: int) -> int:
        return bisect.bisect_right(self.line_starts, off)

    # 1) 주석/문자열 제거
    def lex(self) -> str:
        def repl(m):
            t = m.group()
            if t.startswith("/"):
                self.comments.append((m.start(), m.end(), t))
                return _blank(t)
            return t[0] + _blank(t[1:-1]) + t[-1] if len(t) >= 2 else t
        # 표시용: 주석만 지우고 문자열 리터럴은 살린 원문 (오프셋은 src와 동일)
        self.code = RE_LEX.sub(lambda m: _blank(m.group()) if m.group().startswith("/") else m.group(), self.src)
        text = RE_LEX.sub(repl, self.src)
        # #if 0 블록은 주석처럼 지운다 (안에 있던 주석도 함수 설명으로 쓰지 않음)
        dead = dead_ranges(text)
        if dead:
            self.code = _blank_ranges(self.code, dead)
            self.comments = [c for c in self.comments if not any(a <= c[0] < b for a, b in dead)]
            text = _blank_ranges(text, dead)
        return text

    # 2) 전처리기 줄 수집 후 제거
    def preprocess(self, text: str) -> str:
        pf = self.pf
        for m in RE_XPAR.finditer(text):
            pf.xpar_refs.setdefault(m.group(), self.line(m.start()))
        for m in RE_PP.finditer(text):
            orig = self.src[m.start():m.end()]
            ln = self.line(m.start())
            if (inc := RE_INCLUDE.search(orig)):
                pf.includes.append((inc.group(2).strip(), ln, inc.group(1) == "<"))
            elif (d := RE_DEFINE.search(m.group())):
                pf.macros.setdefault(d.group(1), Macro(
                    d.group(1), ln, d.group(2), _collapse(d.group(3), 80)))
            elif re.match(r'\s*#\s*pragma\b', orig):
                pf.pragmas.append((ln, _collapse(orig)))
        return RE_PP.sub(lambda m: _blank(m.group()), text)

    # 함수 바로 위 주석
    def doc_before(self, off: int, text: str) -> list[str]:
        ends = [c[1] for c in self.comments]
        i = bisect.bisect_right(ends, off) - 1
        picked, limit = [], off
        while i >= 0:
            s, e, body = self.comments[i]
            gap = text[e:limit]
            if gap.strip() or gap.count("\n") > 2 or any(h in body for h in LICENSE_HINTS):
                break
            picked.append(body)
            limit = s
            i -= 1
        lines = clean_doc("\n".join(reversed(picked)))
        return [l for l in lines if not RE_CODE_LIKE.search(l)][:12]   # 주석 처리된 코드는 설명에서 제외

    def header_doc(self, text: str) -> list[str]:
        m = re.search(r'\S', text)
        first_code = m.start() if m else len(text)
        out: list[str] = []
        for s, e, body in self.comments:
            if s > first_code:
                break
            if any(h in body for h in LICENSE_HINTS):
                continue
            out.extend(l for l in clean_doc(body) if is_prose(l))
        return out[:4]

    # 3) 최상위 스캔
    def run(self) -> ParsedFile:
        text = self.preprocess(self.lex())
        self.text = text
        self.pf.header_doc = self.header_doc(text)
        head, head_off, i, n = "", None, 0, len(text)
        while True:
            m = RE_TOP.search(text, i)
            if not m:
                break
            k, ch = m.start(), m.group()
            piece = text[i:k]
            if head_off is None and piece.strip():
                head_off = i + len(piece) - len(piece.lstrip())
            head += piece
            if ch == "{":
                hs = _collapse(RE_ATTR.sub(" ", head))
                if re.fullmatch(r'namespace(\s+[\w:]+)?|extern\s*"\s*"', hs):
                    head, head_off, i = "", None, k + 1      # 투명 블록: 안쪽을 계속 최상위로 취급
                    continue
                end = _match(text, k, RE_BRACE, "{")
                if end < 0:
                    break
                fh = None if hs.endswith("=") else self.func_head(hs)
                if fh:
                    self.add_function(fh, head_off if head_off is not None else k, k, end)
                    head, head_off, i = "", None, end + 1
                else:
                    head += " {} "                         # struct 본문 / 초기화 목록
                    i = end + 1
            elif ch == "}":                                # 투명 블록 종료
                head, head_off, i = "", None, k + 1
            else:
                if head.strip():
                    self.statement(_collapse(RE_ATTR.sub(" ", head)), head_off or k)
                head, head_off, i = "", None, k + 1
        self.assign_pragmas()
        return self.pf

    @staticmethod
    def func_head(h: str):
        if not h or h.startswith(("typedef", "#")):
            return None
        m = RE_NAME_PAREN.search(h)
        if not m:
            return None
        name = m.group(1)
        if name in C_KEYWORDS or name.split("::")[-1] in C_KEYWORDS:
            return None
        pre = h[:m.start()].strip()
        if "=" in pre or not RE_PRE_OK.fullmatch(pre):
            return None
        if not pre and "::" not in name and name != "main":
            return None
        p_open = m.end() - 1
        p_close = _match(h, p_open, RE_PAREN, "(")
        if p_close < 0:
            return None
        post = h[p_close + 1:].strip()
        if post and not RE_POST_OK.fullmatch(post):
            return None
        params = _collapse(h[p_open + 1:p_close])
        return {"name": name, "pre": pre, "params": params,
                "sig": _collapse(f"{pre} {name}({params})" + (f" {post}" if post else ""))}

    def add_function(self, fh, head_off: int, brace: int, end: int):
        pre_words = fh["pre"].split()
        ret = " ".join(w for w in pre_words if w not in ("static", "inline", "extern", "__inline", "__inline__"))
        fn = Function(
            name=fh["name"], path=self.pf.path,
            line=self.line(head_off), end_line=self.line(end),
            signature=fh["sig"], return_type=ret, params=fh["params"],
            is_static="static" in pre_words,
            doc=self.doc_before(head_off, self.text),
        )
        fn._range = (brace, end)  # type: ignore[attr-defined]
        if self.analyze_bodies:
            self.analyze_body(fn, brace + 1, end)
        self.pf.functions.append(fn)

    def statement(self, h: str, off: int):
        pf, ln = self.pf, self.line(off)
        if h.startswith("typedef"):
            fp = RE_FP_NAME.search(h)
            ids = re.findall(r'[A-Za-z_]\w*', re.sub(r'\[[^\]]*\]', '', h))
            name = fp.group(1) if fp else (ids[-1] if ids else "?")
            kind = next((k for k in ("struct", "union", "enum") if re.search(rf'\b{k}\b', h)), "typedef")
            pf.types.append(TypeDef(kind, name, ln, _collapse(h, 90)))
            return
        m = re.match(r'(struct|union|enum|class)\s+(\w+)\s*\{\}', h)
        if m:
            pf.types.append(TypeDef(m.group(1), m.group(2), ln, _collapse(h, 90)))
            return
        if re.fullmatch(r'(struct|union|enum|class)\s+\w+', h) or \
                h.startswith(("using ", "template", "return", "static_assert", "_Static_assert")):
            return
        fh = self.func_head(h)
        if fh and "{}" not in h:
            pf.prototypes.setdefault(fh["name"], (ln, fh["sig"]))
            return
        is_static = bool(re.match(r'(static\b)', h))
        is_extern = bool(re.match(r'extern\b', h))
        for idx, part in enumerate(_split_top(h, ",")):
            decl = re.sub(r'\[[^\]]*\]', '', _split_top(part, "=")[0])
            fp = RE_FP_NAME.search(decl)
            if "(" in decl and not fp:
                break
            ids = [x for x in re.findall(r'[A-Za-z_]\w*', decl)]
            if fp:
                name = fp.group(1)
            elif ids and (idx > 0 or len(ids) >= 2):
                name = ids[-1]
            else:
                break
            if name not in C_KEYWORDS:
                pf.globals.append(GlobalVar(name, ln, _collapse(h, 90), is_static, is_extern))

    # 4) 함수 본문 분석
    def analyze_body(self, fn: Function, bs: int, be: int):
        text = self.text
        do_loops: dict[int, Loop] = {}
        for m in RE_LOOP.finditer(text, bs, be):
            if m.group(1) is None:  # do { ... } while (...);
                b = m.end() - 1
                close = _match(text, b, RE_BRACE, "{", be)
                if close < 0:
                    continue
                lp = Loop("do", self.line(m.start()), m.start(), close + 1, "")
                do_loops[close] = lp
                fn.loops.append(lp)
                continue
            kind = m.group(1)
            p = m.end() - 1
            q = _match(text, p, RE_PAREN, "(", be)
            if q < 0:
                continue
            raw = _collapse(text[p + 1:q]).replace(" ", "")   # 판정용 (문자열 비움)
            cond = _collapse(self.code[p + 1:q])               # 표시용 (문자열 살림)
            j = q + 1
            while j < be and text[j].isspace():
                j += 1
            if j < be and text[j] == "{":
                close = _match(text, j, RE_BRACE, "{", be)
                end = (close if close >= 0 else be) + 1
            elif j < be and text[j] == ";":
                prev = m.start() - 1
                while prev > bs and text[prev].isspace():
                    prev -= 1
                if prev in do_loops:
                    lp = do_loops[prev]
                    lp.cond, lp.infinite = cond, raw in INFINITE_CONDS
                    continue
                kind, end = "wait", j + 1
            else:
                semi = text.find(";", q, be)
                end = (semi if semi >= 0 else be) + 1
            infinite = (kind == "for" and raw == ";;") or \
                       (kind in ("while", "wait") and raw in INFINITE_CONDS)
            fn.loops.append(Loop(kind, self.line(m.start()), m.start(), end, cond, infinite))
        fn.loops.sort(key=lambda lp: lp.start)

        for m in RE_IF.finditer(text, bs, be):
            t = m.group()
            if t.endswith("("):
                kind = "elif" if t.startswith("else") else "if"
                p = m.end() - 1
                q = _match(text, p, RE_PAREN, "(", be)
                if q < 0:
                    continue
                cond, body_from = _collapse(self.code[p + 1:q], 60), q + 1
            else:
                kind, cond, body_from = "else", "", m.end()
            j = body_from
            while j < be and text[j].isspace():
                j += 1
            if j < be and text[j] == "{":
                close = _match(text, j, RE_BRACE, "{", be)
                end = (close if close >= 0 else be) + 1
            else:
                semi = text.find(";", j, be)
                end = (semi if semi >= 0 else be) + 1
            # 조건식 안의 호출은 분기 바깥(항상 실행)으로 두기 위해 본문 시작부터 블록으로 잡는다
            fn.branches.append(Loop(kind, self.line(m.start()), body_from, end, cond))

        for m in RE_CALL.finditer(text, bs, be):
            name = m.group(1)
            if name in C_KEYWORDS:
                continue
            k = m.start() - 1
            while k > bs and text[k] in " \t\n":
                k -= 1
            indirect = text[k] == "." or text[k - 1:k + 1] == "->"
            p = m.end() - 1
            q = _match(text, p, RE_PAREN, "(", be)
            args = _collapse(self.code[p + 1:q] if q > 0 else "", 48)
            fn.calls.append(Call(("->" if indirect else "") + name, self.line(m.start()),
                                 m.start(), args, indirect))

        for m in RE_IDENT.finditer(text, bs, be):
            name = m.group()
            if name in C_KEYWORDS:
                continue
            j = m.end()
            while j < be and text[j] in " \t\n":
                j += 1
            if j < be and text[j] == "(":
                continue
            k = m.start() - 1
            while k > bs and text[k] in " \t\n":
                k -= 1
            if text[k] == "." or text[k - 1:k + 1] == "->":
                continue
            via, via_pos = self.enclosing_call(m.start(), bs)
            fn.refs.append(IdentRef(name, self.line(m.start()), m.start(), via, via_pos))

    def enclosing_call(self, pos: int, bs: int):
        text, depth, k = self.text, 0, pos - 1
        while k >= bs:
            c = text[k]
            if c == ")":
                depth += 1
            elif c == "(":
                if depth == 0:
                    m = re.search(r'([A-Za-z_]\w*)\s*$', text[max(bs, k - 80):k])
                    if m and m.group(1) not in C_KEYWORDS:
                        return m.group(1), max(bs, k - 80) + m.start(1)
                    return None, None
                depth -= 1
            elif c in ";{}" and depth == 0:
                return None, None
            k -= 1
        return None, None

    def assign_pragmas(self):
        for ln, txt in self.pf.pragmas:
            for fn in self.pf.functions:
                if fn.line <= ln <= fn.end_line:
                    fn.pragmas.append((ln, txt))
                    break


def parse_file(root: Path, rel: Path, analyze_bodies: bool = True) -> ParsedFile | None:
    try:
        src = read_source(root / rel)
    except OSError:
        return None
    return _Parser(rel, src, analyze_bodies).run()
