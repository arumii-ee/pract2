"""프로젝트 종류(빌드 시스템), 프레임워크/라이브러리, 진입점 감지."""

from __future__ import annotations

import re
from pathlib import Path

from .models import EntryPoint, FileInfo

# 파일 이름(또는 glob) → 빌드 시스템/프로젝트 설정 이름
BUILD_MARKERS = [
    ("Makefile", "Make"), ("makefile", "Make"), ("GNUmakefile", "Make"),
    ("CMakeLists.txt", "CMake"),
    ("meson.build", "Meson"),
    ("configure.ac", "Autotools"), ("configure", "Autotools"),
    ("SConstruct", "SCons"),
    ("premake5.lua", "Premake"), ("premake4.lua", "Premake"),
    ("xmake.lua", "xmake"),
    ("*.vcxproj", "Visual Studio"), ("*.sln", "Visual Studio"),
    ("*.rockspec", "LuaRocks"), (".luarc.json", "Lua LSP 설정"),
    ("pyproject.toml", "Python (pyproject)"), ("setup.py", "Python (setup.py)"),
    ("requirements.txt", "Python (pip requirements)"),
]

# C 소스의 #include → 사용 중인 라이브러리
C_INCLUDE_LIBS = {
    "lua.h": "Lua C API (Lua 임베딩/확장 모듈)",
    "lauxlib.h": "Lua C API (Lua 임베딩/확장 모듈)",
    "lualib.h": "Lua C API (Lua 임베딩/확장 모듈)",
    "luajit.h": "LuaJIT",
    "SDL.h": "SDL", "SDL2/SDL.h": "SDL2", "SDL3/SDL.h": "SDL3",
    "raylib.h": "raylib",
    "GLFW/glfw3.h": "GLFW", "GL/gl.h": "OpenGL",
    "pthread.h": "POSIX Threads", "windows.h": "Win32 API",
    "Python.h": "Python C API",
    "curl/curl.h": "libcurl", "sqlite3.h": "SQLite",
}

# Lua require → 라이브러리
LUA_REQUIRE_LIBS = {
    "socket": "LuaSocket", "lfs": "LuaFileSystem", "cjson": "lua-cjson",
    "lpeg": "LPeg", "ffi": "LuaJIT FFI", "busted": "busted (테스트)",
    "luaunit": "LuaUnit (테스트)", "lapis": "Lapis", "openresty": "OpenResty",
    "resty": "OpenResty",
}

PY_IMPORT_LIBS = {
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI",
    "numpy": "NumPy", "pandas": "pandas", "torch": "PyTorch",
    "pytest": "pytest", "pygame": "pygame", "lupa": "lupa (Python↔Lua)",
}

RE_C_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
RE_C_MAIN = re.compile(r"^\s*(?:int|void)\s+(?:w?main|WinMain|wWinMain)\s*\(", re.M)
RE_C_LUAOPEN = re.compile(r"\bluaopen_(\w+)\s*\(")
RE_LUA_REQUIRE = re.compile(r"""\brequire\s*\(?\s*["']([\w.\-]+)["']""")
RE_PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w]+)", re.M)
RE_PY_MAIN = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.M)

LUA_ENTRY_NAMES = {"main.lua", "init.lua"}
PY_ENTRY_NAMES = {"main.py", "__main__.py", "app.py", "manage.py", "run.py"}


def _read(root: Path, f: FileInfo) -> str:
    try:
        return (root / f.path).read_text(errors="ignore")
    except OSError:
        return ""


def detect_build_systems(root: Path, files: list[FileInfo]) -> list[str]:
    """각 빌드 시스템별로 발견된 설정 파일 경로를 모아 "이름: 경로들" 형태로 반환."""
    found: dict[str, list[str]] = {}
    for pattern, label in BUILD_MARKERS:
        for f in files:
            if f.path.match(pattern):
                found.setdefault(label, []).append(f.path.as_posix())
    result = []
    for label, paths in found.items():
        paths = sorted(set(paths), key=lambda p: (p.count("/"), p))
        shown = ", ".join(paths[:3]) + (f" 외 {len(paths) - 3}개" if len(paths) > 3 else "")
        result.append(f"{label}: {shown}")
    return result


def detect(root: Path, files: list[FileInfo]):
    """(frameworks, entry_points) 반환."""
    libs: dict[str, None] = {}  # 순서 유지 set
    entries: list[EntryPoint] = []

    for f in files:
        lang, name = f.language, f.path.name
        if lang not in ("C", "C++", "C/C++ Header", "Lua", "Python"):
            continue
        text = _read(root, f)

        if lang in ("C", "C++", "C/C++ Header"):
            for inc in RE_C_INCLUDE.findall(text):
                if inc in C_INCLUDE_LIBS:
                    libs[C_INCLUDE_LIBS[inc]] = None
            if RE_C_MAIN.search(text):
                entries.append(EntryPoint(f.path, "main() 함수 정의"))
            for mod in RE_C_LUAOPEN.findall(text):
                if mod not in ("base", "string", "table", "math", "io", "os",
                               "coroutine", "debug", "utf8", "package"):
                    entries.append(EntryPoint(f.path, f"Lua C 모듈 진입점 luaopen_{mod}()"))

        elif lang == "Lua":
            for mod in RE_LUA_REQUIRE.findall(text):
                top = mod.split(".")[0]
                if top in LUA_REQUIRE_LIBS:
                    libs[LUA_REQUIRE_LIBS[top]] = None
            if name == "conf.lua" and "love.conf" in text:
                libs["LÖVE (love2d)"] = None
            if name == "main.lua" and "love." in text:
                libs["LÖVE (love2d)"] = None
                entries.append(EntryPoint(f.path, "LÖVE 진입점 (main.lua)"))
            elif name in LUA_ENTRY_NAMES:
                entries.append(EntryPoint(f.path, f"관례적 진입 파일 ({name})"))
            elif text.startswith("#!"):
                entries.append(EntryPoint(f.path, "shebang 실행 스크립트"))

        elif lang == "Python":
            for mod in RE_PY_IMPORT.findall(text):
                if mod in PY_IMPORT_LIBS:
                    libs[PY_IMPORT_LIBS[mod]] = None
            if RE_PY_MAIN.search(text):
                entries.append(EntryPoint(f.path, "if __name__ == '__main__'"))
            elif name in PY_ENTRY_NAMES:
                entries.append(EntryPoint(f.path, f"관례적 진입 파일 ({name})"))

    return list(libs), entries
