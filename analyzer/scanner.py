"""파일 탐색: 기본 제외 목록 + 루트 .gitignore 적용."""

from __future__ import annotations

import fnmatch
import os
import re
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

MAX_FILE_SIZE = 1024 * 1024  # 1MB


def _gitignore_regex(pattern: str) -> re.Pattern:
    """gitignore 패턴 하나를 경로 전체(rel)와 비교하는 정규식으로 변환."""
    # 앞이나 중간에 '/'가 있으면 루트 기준(anchored), 없으면 어느 깊이에서든 매칭
    anchored = "/" in pattern
    pattern = pattern.lstrip("/")
    out, i, n = [], 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == n:
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 1:]:
            j = pattern.index("]", i + 1)
            body = pattern[i + 1:j]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + "".join(out) + r"\Z")


class GitIgnore:
    """루트 .gitignore 지원 (루트 기준 '/', '**', 부정 패턴 '!', 디렉터리 전용 '/' 포함).

    상위 디렉터리가 제외되면 그 안은 탐색하지 않으므로(git과 동일),
    하위 파일을 다시 포함시키려면 디렉터리도 '!dir/' 로 되살려야 한다.
    """

    def __init__(self, root: Path):
        self.rules: list[tuple[re.Pattern, bool, bool]] = []  # (regex, negate, dir_only)
        gi = root / ".gitignore"
        if not gi.is_file():
            return
        for line in gi.read_text(errors="ignore").splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if line.strip("/"):
                self.rules.append((_gitignore_regex(line), negate, dir_only))

    def ignored(self, rel: str, is_dir: bool) -> bool:
        result = False
        for regex, negate, dir_only in self.rules:
            if dir_only and not is_dir:
                continue
            if regex.match(rel):
                result = not negate
        return result


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(4096)
    except OSError:
        return True


def scan(root: Path, extra_excludes: set[str] | None = None):
    """(분석 대상 파일 경로 목록, 건너뛴 파일 경로 목록)을 반환."""
    excludes = DEFAULT_EXCLUDE_DIRS | (extra_excludes or set())
    gitignore = GitIgnore(root)
    files: list[Path] = []
    skipped: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"

        dirnames[:] = sorted(
            d for d in dirnames
            if d not in excludes
            and not fnmatch.filter([d], "*.egg-info")
            and not gitignore.ignored(rel_dir + d, True)
        )

        for name in sorted(filenames):
            path = current / name
            if gitignore.ignored(rel_dir + name, False) or name in excludes:
                continue
            try:
                if path.is_symlink() or path.stat().st_size > MAX_FILE_SIZE:
                    skipped.append(path)
                    continue
            except OSError:
                skipped.append(path)
                continue
            if _is_binary(path):
                skipped.append(path)
                continue
            files.append(path)

    return files, skipped
