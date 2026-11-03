"""확장자 → 언어 매핑, 코드/주석/빈 줄 세기."""

from __future__ import annotations

import re
from pathlib import Path

EXT_MAP = {
    ".c": "C", ".h": "C/C++ Header",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".lua": "Lua",
    ".py": "Python",
    ".sh": "Shell", ".bash": "Shell",
    ".cmake": "CMake",
    ".mk": "Makefile",
    ".json": "JSON", ".toml": "TOML", ".yml": "YAML", ".yaml": "YAML",
    ".ini": "INI", ".cfg": "INI",
    ".md": "Markdown", ".txt": "Text", ".rst": "Text",
}

NAME_MAP = {
    "Makefile": "Makefile", "makefile": "Makefile", "GNUmakefile": "Makefile",
    "CMakeLists.txt": "CMake",
    "SConstruct": "Python", "SConscript": "Python",
}

# 주석 문법: (한 줄 주석, 블록 시작 정규식, 블록 종료 문자열 생성 함수)
C_LIKE = {"C", "C/C++ Header", "C++"}
HASH_COMMENT = {"Python", "Shell", "CMake", "Makefile", "TOML", "YAML", "INI"}


def detect_language(path: Path) -> str | None:
    if path.name in NAME_MAP:
        return NAME_MAP[path.name]
    lang = EXT_MAP.get(path.suffix.lower())
    if lang:
        return lang
    # 확장자 없는 스크립트: shebang 확인
    if not path.suffix:
        try:
            with open(path, "r", errors="ignore") as f:
                first = f.readline()
        except OSError:
            return None
        if first.startswith("#!"):
            if "lua" in first:
                return "Lua"
            if "python" in first:
                return "Python"
            if "sh" in first:
                return "Shell"
    return None


def _strip_strings(line: str, quotes: str) -> str:
    """문자열 리터럴 안의 주석 기호를 오인하지 않도록 문자열을 비운다."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch in quotes:
            q = ch
            i += 1
            while i < n and line[i] != q:
                i += 2 if line[i] == "\\" else 1
            out.append(q + q)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _count_c(lines):
    code = comment = blank = 0
    in_block = False
    for raw in lines:
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if in_block:
            if "*/" in line:
                in_block = False
                rest = line.split("*/", 1)[1].strip()
                if rest and not rest.startswith("//"):
                    code += 1
                    continue
            comment += 1
            continue
        s = _strip_strings(line, "\"'")
        if s.startswith("//"):
            comment += 1
        elif s.startswith("/*"):
            after = s[2:]
            if "*/" in after:
                rest = after.split("*/", 1)[1].strip()
                if rest and not rest.startswith("//"):
                    code += 1
                else:
                    comment += 1
            else:
                in_block = True
                comment += 1
        else:
            code += 1
            if "/*" in s and "*/" not in s.split("/*", 1)[1]:
                in_block = True
    return code, comment, blank


_LUA_BLOCK = re.compile(r"--\[(=*)\[")


def _count_lua(lines):
    code = comment = blank = 0
    close: str | None = None  # 블록 주석 종료 문자열 (예: "]]", "]==]")
    for raw in lines:
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if close:
            if close in line:
                rest = line.split(close, 1)[1].strip()
                close = None
                if rest and not rest.startswith("--"):
                    code += 1
                    continue
            comment += 1
            continue
        s = _strip_strings(line, "\"'")
        m = _LUA_BLOCK.search(s)
        if s.startswith("--"):
            if m and m.start() == 0:
                end = "]" + m.group(1) + "]"
                tail = s[m.end():]
                if end not in tail:
                    close = end
            comment += 1
        else:
            code += 1
            if m:
                end = "]" + m.group(1) + "]"
                if end not in s[m.end():]:
                    close = end
    return code, comment, blank


def _count_python(lines):
    code = comment = blank = 0
    in_doc: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if in_doc:
            comment += 1
            if in_doc in line:
                in_doc = None
            continue
        if line.startswith("#"):
            comment += 1
            continue
        # 줄 맨 앞의 삼중따옴표 문자열은 docstring/주석으로 취급
        for q in ('"""', "'''"):
            if line.lstrip("rRbBuU").startswith(q):
                body = line.lstrip("rRbBuU")[3:]
                if q not in body:
                    in_doc = q
                comment += 1
                break
        else:
            code += 1
    return code, comment, blank


def _count_hash(lines):
    code = comment = blank = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            blank += 1
        elif line.startswith("#"):
            comment += 1
        else:
            code += 1
    return code, comment, blank


def _count_plain(lines):
    blank = sum(1 for l in lines if not l.strip())
    return len(lines) - blank, 0, blank


def count_lines(path: Path, language: str | None) -> tuple[int, int, int]:
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return 0, 0, 0
    if language in C_LIKE:
        return _count_c(lines)
    if language == "Lua":
        return _count_lua(lines)
    if language == "Python":
        return _count_python(lines)
    if language in HASH_COMMENT:
        return _count_hash(lines)
    return _count_plain(lines)
