"""Xilinx/AMD Vitis (Classic, 2019.2~2023.1 — 2020.2 포함) 워크스페이스 분석.

Classic Vitis 워크스페이스 구조:
  <workspace>/
  ├── .metadata/                       Eclipse 메타데이터 (제외)
  ├── <platform>/platform.spr          플랫폼 프로젝트
  │   ├── hw/<design>.xsa              하드웨어 설계
  │   ├── <cpu>/<os>_domain/bsp/...    BSP (libsrc 드라이버, include/xparameters.h)
  │   ├── zynq_fsbl/                   FSBL
  │   └── export/                      내보낸 플랫폼
  ├── <app>/<app>.prj, .cproject       애플리케이션 프로젝트
  │   ├── src/ (main.c, platform.c, lscript.ld …)
  │   ├── Debug/ Release/              빌드 산출물 (제외)
  │   └── _ide/                        실행/디버그 설정
  ├── <app>_system/<app>_system.sprj   시스템 프로젝트
  └── <hls_proj>/hls.app, solution1/   Vitis HLS 프로젝트
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

GENERATED_PARTS = {
    "libsrc", "bsp", "bspinclude", "zynq_fsbl", "zynqmp_fsbl", "zynqmp_pmufw",
    "fsbl", "pmufw", "export", "_ide", "logs", ".autopilot",
}
HLS_OUTPUT_PARTS = {"syn", "impl", "sim", "csim", ".debug", "hls_impl"}
RE_PROC = re.compile(
    r'(ps7_cortexa9_\d|psu_cortexa53_\d|psu_cortexr5_\d|psv_cortexa72_\d|psv_cortexr5_\d|'
    r'microblaze_\d+|psu_pmu_\d)$')
RE_MEM = re.compile(r'(\w+)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+)\s*,\s*LENGTH\s*=\s*(0x[0-9A-Fa-f]+)')
RE_SIZE = re.compile(r'(_STACK_SIZE|_HEAP_SIZE)\s*=\s*DEFINED\(\s*\1\s*\)\s*\?\s*\1\s*:\s*(0x[0-9A-Fa-f]+)')
RE_BASEADDR = re.compile(r'#define\s+XPAR_(\w+?)_BASEADDR\s+(0x[0-9A-Fa-f]+)')
RE_XML_ATTR = re.compile(r'(\w+)="([^"]*)"')
PRJ_KEYS = ("name", "platform", "sysConfig", "runtime", "cpu", "os", "processor",
            "domain", "type", "location")


@dataclass
class AppProject:
    name: str
    path: Path
    attrs: dict = field(default_factory=dict)


@dataclass
class PlatformProject:
    name: str
    path: Path
    xsa: list[str] = field(default_factory=list)
    processors: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    bsp_libs: set[str] = field(default_factory=set)
    has_fsbl: bool = False


@dataclass
class HlsProject:
    name: str
    path: Path
    top: str | None = None


@dataclass
class VitisInfo:
    apps: list[AppProject] = field(default_factory=list)
    platforms: list[PlatformProject] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    hls: list[HlsProject] = field(default_factory=list)
    memory: list[tuple[str, str, str, str]] = field(default_factory=list)  # (영역, 시작, 크기, 파일)
    sizes: dict[str, str] = field(default_factory=dict)
    peripherals: list[tuple[str, str]] = field(default_factory=list)
    xparameters: str | None = None
    xpar_values: dict[str, str] = field(default_factory=dict)
    platform_dirs: set[Path] = field(default_factory=set)

    @property
    def detected(self) -> bool:
        return bool(self.apps or self.platforms or self.systems or self.hls)

    @property
    def hls_tops(self) -> set[str]:
        return {h.top for h in self.hls if h.top}

    def is_generated(self, rel: Path) -> bool:
        if not self.detected:
            return False
        parts = rel.parts[:-1]
        if any(p in GENERATED_PARTS for p in parts):
            return True
        for i, p in enumerate(parts[:-1]):
            if p.startswith("solution") and parts[i + 1] in HLS_OUTPUT_PARTS:
                return True
        return any(d in rel.parents for d in self.platform_dirs)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def analyze(root: Path, rels: list[Path]) -> VitisInfo:
    info = VitisInfo()
    info.platform_dirs = {r.parent for r in rels if r.name == "platform.spr"}

    for pd in sorted(info.platform_dirs):
        plat = PlatformProject(pd.name or root.name, pd)
        for r in rels:
            if pd not in r.parents:
                continue
            if r.suffix == ".xsa":
                plat.xsa.append(r.name)
            parts = r.parts
            if any(p in ("zynq_fsbl", "zynqmp_fsbl") for p in parts):
                plat.has_fsbl = True
                continue
            for i, p in enumerate(parts[:-1]):
                if RE_PROC.match(p):
                    plat.processors.add(p)
                if p.endswith("_domain"):
                    plat.domains.add(p)
                if p == "libsrc" and i + 1 < len(parts) - 1:
                    plat.bsp_libs.add(parts[i + 1])
        info.platforms.append(plat)

    for r in rels:
        if any(pd in r.parents for pd in info.platform_dirs):
            continue
        if r.suffix == ".prj" and r.name != "hls.app":
            text = _read(root / r)
            tag = re.search(r'<\s*\w*[Pp]roject\b[^>]*>', text)
            attrs = dict(RE_XML_ATTR.findall(tag.group())) if tag else {}
            app = AppProject(r.stem, r.parent,
                             {k: attrs[k] for k in PRJ_KEYS if k in attrs and attrs[k]})
            info.apps.append(app)
        elif r.suffix == ".sprj":
            info.systems.append(r.stem)
        elif r.name == "hls.app":
            text = _read(root / r)
            top = re.search(r'\btop="([^"]+)"', text)
            info.hls.append(HlsProject(r.parent.name, r.parent, top.group(1) if top else None))

    # HLS top: tcl의 set_top / cfg의 syn.top 도 확인
    for r in rels:
        if r.suffix in (".tcl", ".cfg"):
            m = re.search(r'^\s*(?:set_top\s+|syn\.top\s*=\s*)(\S+)', _read(root / r), re.M)
            if m:
                owner = next((h for h in info.hls if h.path in r.parents), None)
                if owner and not owner.top:
                    owner.top = m.group(1)
                elif not owner:
                    info.hls.append(HlsProject(r.parent.name, r.parent, m.group(1)))

    if not info.detected:
        return info

    for r in rels:
        if r.name == "lscript.ld" and not info.is_generated(r):
            text = _read(root / r)
            for name, org, length in RE_MEM.findall(text):
                info.memory.append((name, org, length, r.as_posix()))
            for key, val in RE_SIZE.findall(text):
                info.sizes.setdefault(key, val)

    xpars = sorted((r for r in rels if r.name == "xparameters.h"),
                   key=lambda r: ("fsbl" in r.as_posix(), "include" not in r.parts, len(r.parts)))
    if xpars:
        info.xparameters = xpars[0].as_posix()
        text = _read(root / xpars[0])
        for m in re.finditer(r'#define\s+(XPAR_\w+)\s+([^\n/]+)', text):
            info.xpar_values.setdefault(m.group(1), m.group(2).strip())
        seen = set()
        for name, addr in RE_BASEADDR.findall(text):
            # XPAR_XGPIO_0_ 같은 정규화(canonical) 이름은 중복이라 제외
            if name.startswith("X") or addr in seen:
                continue
            seen.add(addr)
            info.peripherals.append((name, addr))
    return info
