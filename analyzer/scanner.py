"""파일 탐색: 기본 제외 목록 + .gitignore(git과 같은 규칙) 적용, 제외 내역 기록."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "dist",
    "build", "out", "bin", "obj", "Debug", "Release", "CMakeFiles",
    # Vitis / Eclipse
    ".metadata", "RemoteSystemsTempFiles", ".Xil", ".autopilot", ".settings",
}

BINARY_EXTS = {
    ".o", ".obj", ".a", ".lib", ".so", ".dll", ".dylib", ".exe", ".out",
    ".pyc", ".pyo", ".luac", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".ico", ".wav", ".mp3", ".ogg", ".ttf", ".otf", ".zip", ".gz", ".tar",
    ".7z", ".pdf", ".bin", ".xsa", ".bit", ".elf", ".mmi",
}

MAX_FILE_SIZE = 1024 * 1024  # 기본 1MB (--max-size 로 변경)

# 백업·임시 파일 (sd_card.c.bak 같은 사본이 통계를 부풀리지 않도록)
BACKUP_PATTERNS = ("*.bak", "*.orig", "*.old", "*.tmp", "*.swp", "*~", "#*#")

# 제외 이유 종류
REASON_DEFAULT = "default"      # 기본 제외 목록 (Debug, .metadata …)
REASON_OPTION = "option"        # -e 옵션
REASON_GITIGNORE = "gitignore"  # .gitignore 규칙
REASON_LINK = "link"            # 심볼릭 링크 / 정션


@dataclass
class Excluded:
    path: Path          # 루트 기준 상대 경로
    is_dir: bool
    kind: str           # REASON_*
    detail: str = ""    # 사람이 읽을 이유 (예: ".gitignore:3 '/*'")


def _compile(pattern: str) -> re.Pattern:
    """gitignore 패턴(앞뒤 '/' 제거된 상태)을 정규식으로 변환."""
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if pattern.startswith("**/", i) and (i == 0 or pattern[i - 1] == "/"):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i) and i + 2 == n and (i == 0 or pattern[i - 1] == "/"):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 2 if pattern[i + 1:i + 2] in ("!", "]") else i + 1)
            if j < 0:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


@dataclass
class _Rule:
    regex: re.Pattern
    negate: bool
    dir_only: bool
    anchored: bool      # '/'가 앞/중간에 있으면 .gitignore 위치 기준, 아니면 모든 깊이의 이름과 매칭
    source: str         # 표시용: "PMS_MK2_APP/.gitignore:3 '/*'"


@dataclass
class GitIgnore:
    """하나의 .gitignore 파일. base: 루트 기준 그 파일이 있는 폴더("" 또는 "a/b/")."""

    base: str
    rules: list[_Rule] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path, base: str = "") -> "GitIgnore | None":
        gi = root / base / ".gitignore"
        if not gi.is_file():
            return None
        self = cls(base)
        label = f"{base}.gitignore"
        for no, raw in enumerate(gi.read_text(errors="ignore").splitlines(), 1):
            line = raw.rstrip()
            if line.endswith("\\") and raw.endswith(" "):   # "\ " 로 끝나는 공백 보존
                line += " "
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            elif line.startswith("\\!") or line.startswith("\\#"):
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = "/" in line
            line = line.lstrip("/")
            if not line:
                continue
            self.rules.append(_Rule(_compile(line), negate, dir_only, anchored,
                                    f"{label}:{no} '{raw.strip()}'"))
        return self

    def match(self, rel: str, is_dir: bool) -> _Rule | None:
        """rel(루트 기준)에 마지막으로 매칭되는 규칙. 이 파일 범위 밖이면 None."""
        if not rel.startswith(self.base):
            return None
        local = rel[len(self.base):]
        name = local.rsplit("/", 1)[-1]
        hit = None
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.match(local if rule.anchored else name):
                hit = rule
        return hit


def _gitignore_decision(stack: list[GitIgnore], rel: str, is_dir: bool) -> _Rule | None:
    """안쪽 .gitignore가 바깥보다 우선. 마지막 매칭 규칙을 반환 (negate면 포함)."""
    for gi in reversed(stack):
        rule = gi.match(rel, is_dir)
        if rule:
            return rule
    return None


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(4096)
    except OSError:
        return True


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)   # Python 3.12+
        return bool(isjunction and isjunction(path))
    except OSError:
        return False


def scan(root: Path, extra_excludes: set[str] | None = None, max_size: int = MAX_FILE_SIZE, *,
         use_gitignore: bool = True, follow_links: bool = False):
    """(분석 대상 파일 목록, [(건너뛴 파일, 이유)], 제외 내역 목록)을 반환."""
    extra = extra_excludes or set()
    files: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    excluded: list[Excluded] = []
    stacks: dict[str, list[GitIgnore]] = {}
    visited: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
        current = Path(dirpath)
        if follow_links:
            real = os.path.realpath(dirpath)
            if real in visited:          # 링크 순환 방지
                dirnames[:] = []
                continue
            visited.add(real)
        rel_dir = current.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"

        parent = stacks.pop(rel_dir, [])
        stack = parent
        if use_gitignore:
            gi = GitIgnore.load(root, rel_dir)
            if gi:
                stack = parent + [gi]

        def reason(name: str, is_dir: bool):
            if name in extra:
                return REASON_OPTION, "-e 옵션"
            if is_dir and (name in DEFAULT_EXCLUDE_DIRS or fnmatch.fnmatch(name, "*.egg-info")):
                return REASON_DEFAULT, "기본 제외"
            if not is_dir and name in DEFAULT_EXCLUDE_DIRS:
                return REASON_DEFAULT, "기본 제외"
            if not is_dir and any(fnmatch.fnmatch(name, p) for p in BACKUP_PATTERNS):
                return REASON_DEFAULT, "백업/임시 파일"
            rule = _gitignore_decision(stack, rel_dir + name, is_dir)
            if rule and not rule.negate:
                return REASON_GITIGNORE, rule.source
            if is_dir and not follow_links and _is_link(current / name):
                return REASON_LINK, "심볼릭 링크/정션 (--follow-links 로 포함)"
            return None

        keep = []
        for d in sorted(dirnames):
            why = reason(d, True)
            if why:
                excluded.append(Excluded(Path(rel_dir + d), True, *why))
            else:
                keep.append(d)
                stacks[rel_dir + d + "/"] = stack
        dirnames[:] = keep

        for name in sorted(filenames):
            path = current / name
            why = reason(name, False)
            if why:
                excluded.append(Excluded(Path(rel_dir + name), False, *why))
                continue
            try:
                if path.is_symlink():
                    skipped.append((path, "심볼릭 링크"))
                    continue
                if _is_binary(path):
                    skipped.append((path, "바이너리"))
                    continue
                if path.stat().st_size > max_size:
                    skipped.append((path, f"대용량 (> {max_size / 1024 / 1024:g}MB)"))
                    continue
            except OSError:
                skipped.append((path, "읽기 실패"))
                continue
            files.append(path)

    return files, skipped, excluded
