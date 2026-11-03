"""파일 탐색: 기본 제외 목록 + .gitignore(간이 지원) 적용."""

from __future__ import annotations

import fnmatch
import os
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


class GitIgnore:
    """.gitignore의 흔한 패턴만 지원하는 간이 구현 (부정 패턴 '!' 포함)."""

    def __init__(self, root: Path):
        self.rules: list[tuple[str, bool, bool]] = []  # (pattern, negate, dir_only)
        gi = root / ".gitignore"
        if not gi.is_file():
            return
        for line in gi.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.strip("/")
            if line:
                self.rules.append((line, negate, dir_only))

    def ignored(self, rel: str, is_dir: bool) -> bool:
        name = rel.rsplit("/", 1)[-1]
        result = False
        for pattern, negate, dir_only in self.rules:
            if dir_only and not is_dir:
                continue
            target = rel if "/" in pattern else name
            if fnmatch.fnmatch(target, pattern):
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
