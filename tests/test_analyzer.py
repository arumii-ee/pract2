"""실행: python -m unittest discover -s tests"""

import tempfile
import re
import unittest
from pathlib import Path

from analyzer.cli import build_report
from analyzer.graph import flow_tree
from analyzer.parsers.c_parser import _Parser
from analyzer.reporters import markdown, text
from analyzer.scanner import GitIgnore, scan

FIXTURE = Path(__file__).parent / "fixtures" / "vitis_ws"


def parse(src: str):
    return _Parser(Path("t.c"), src, True).run()


class CParserTest(unittest.TestCase):
    def test_functions_prototypes_globals(self):
        pf = parse(
            '#include "a.h"\n'
            '#define N(x) ((x)+1)\n'
            'static int g = 0, h;\n'
            'typedef struct { int a; } S;\n'
            'int proto(void);\n'
            '/* adds */\n'
            'static int add(int a, int b) { return a + b; }\n'
            'int main(void) { printf("a // b"); return add(1, 2); }\n')
        self.assertEqual([f.name for f in pf.functions], ["add", "main"])
        self.assertEqual(pf.functions[0].doc, ["adds"])
        self.assertTrue(pf.functions[0].is_static)
        self.assertIn("proto", pf.prototypes)
        self.assertEqual([g.name for g in pf.globals], ["g", "h"])
        self.assertEqual(pf.types[0].name, "S")
        self.assertIn("N", pf.macros)
        self.assertEqual([c.name for c in pf.functions[1].calls], ["printf", "add"])

    def test_loops_branches_callbacks(self):
        pf = parse(
            'void isr(void *p) {}\n'
            'int main() {\n'
            '  reg(1, isr, 0);\n'
            '  while (!flag);\n'
            '  if (init() != 0) { fail(); } else { ok(); }\n'
            '  do { step(); } while (1);\n'
            '}\n')
        fn = pf.functions[1]
        kinds = [(lp.kind, lp.infinite) for lp in fn.loops]
        self.assertIn(("wait", False), kinds)
        self.assertIn(("do", True), kinds)
        self.assertEqual([b.kind for b in fn.branches], ["if", "else"])
        ref = next(r for r in fn.refs if r.name == "isr")
        self.assertEqual(ref.via, "reg")


class VitisWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report(FIXTURE, set())

    def test_vitis_detection(self):
        vi = self.report.vitis
        self.assertTrue(vi.detected)
        self.assertEqual([a.name for a in vi.apps], ["led_app"])
        self.assertEqual(vi.platforms[0].processors, {"ps7_cortexa9_0"})
        self.assertIn("standalone_domain", vi.platforms[0].domains)
        self.assertEqual(vi.hls_tops, {"vadd"})
        self.assertIn(("ps7_ddr_0", "0x100000", "0x1FF00000", "led_app/src/lscript.ld"), vi.memory)
        self.assertIn(("AXI_GPIO_0", "0x41200000"), vi.peripherals)

    def test_generated_code_excluded(self):
        user = {f.path.as_posix() for f in self.report.user_files}
        self.assertIn("led_app/src/main.c", user)
        self.assertFalse(any("libsrc" in p or "zynq_fsbl" in p or ".metadata" in p for p in user))
        self.assertTrue(all(f.generated for f in self.report.files if "design_1_wrapper" in f.path.parts))

    def test_call_graph_and_isr(self):
        idx = self.report.index
        self.assertEqual([f.name for f in idx.async_functions()], ["timer_isr"])
        main = next(f for f in idx.by_name["main"] if f.path.name == "main.c")
        kinds = {c.name: c.target.kind for c in main.calls}
        self.assertEqual(kinds["init_platform"], "user")
        self.assertEqual(kinds["XTmrCtr_Start"], "lib")
        tree = "\n".join(flow_tree(idx, main))
        self.assertIn("⚡ timer_isr 등록", tree)
        self.assertIn("🔁 while (1) — 무한 반복", tree)
        self.assertIn("⏳ while (!done);", tree)
        self.assertLess(tree.index("init_platform"), tree.index("cleanup_platform"))

    def test_markdown_renders(self):
        md = markdown.render(self.report)
        for heading in ("## 1. 개요", "## 2. 전체 구조", "## 3. 실행 흐름", "## 4. 함수 상세", "```mermaid"):
            self.assertIn(heading, md)


def make_tree(base: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


MAIN_C = "#include <stdio.h>\nstatic void init(void) {}\nint main(void) { init(); return 0; }\n"


class GitIgnoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def gi(self, content: str, base: str = "") -> GitIgnore:
        make_tree(self.root, {base + ".gitignore": content})
        return GitIgnore.load(self.root, base)

    def ignored(self, gi, rel, is_dir=False):
        rule = gi.match(rel, is_dir)
        return bool(rule and not rule.negate)

    def test_anchored_pattern_only_applies_at_root(self):
        gi = self.gi("/*\n!/PMS_MK2_APP/\n")
        self.assertTrue(self.ignored(gi, "other", True))
        self.assertFalse(self.ignored(gi, "PMS_MK2_APP", True))
        self.assertFalse(self.ignored(gi, "PMS_MK2_APP/src", True))
        self.assertFalse(self.ignored(gi, "PMS_MK2_APP/src/main.c"))

    def test_unanchored_and_double_star(self):
        gi = self.gi("*.o\nDebug/\ndocs/**/*.tmp\n**/gen\nfoo/bar\n")
        self.assertTrue(self.ignored(gi, "a/b/x.o"))
        self.assertTrue(self.ignored(gi, "a/Debug", True))
        self.assertFalse(self.ignored(gi, "a/Debug"))           # dir_only 규칙은 파일에 적용 안 됨
        self.assertTrue(self.ignored(gi, "docs/x.tmp"))
        self.assertTrue(self.ignored(gi, "docs/a/b/x.tmp"))
        self.assertTrue(self.ignored(gi, "x/y/gen", True))
        self.assertTrue(self.ignored(gi, "foo/bar", True))
        self.assertFalse(self.ignored(gi, "x/foo/bar", True))    # 중간 '/' → 루트 고정

    def test_project_in_nested_subfolder(self):
        make_tree(self.root, {
            ".gitignore": "/*\n!/.gitignore\n!/PMS_MK2_APP/\n*.o\n",
            "notes.txt": "x",
            "PMS_MK2_APP/src/main.c": MAIN_C,
            "PMS_MK2_APP/src/drv/uart.c": "void uart_init(void) {}\n",
            "PMS_MK2_APP/src/drv/uart.o": "obj",
            "PMS_MK2_APP/Debug/src/main.c": MAIN_C,
        })
        report = build_report(self.root, set())
        user = {f.path.as_posix() for f in report.user_files}
        self.assertIn("PMS_MK2_APP/src/main.c", user)
        self.assertIn("PMS_MK2_APP/src/drv/uart.c", user)
        self.assertNotIn("notes.txt", user)
        self.assertFalse(any("Debug" in p or p.endswith(".o") for p in user))
        self.assertIn("main", {f.name for f in report.index.funcs})
        self.assertIsNone(report.exclusion_warning())

    def test_nested_gitignore_and_warning(self):
        make_tree(self.root, {
            ".gitignore": "*\n",
            "app/src/main.c": MAIN_C,
        })
        report = build_report(self.root, set())
        self.assertEqual(report.user_files, [])
        self.assertIn("--no-gitignore", report.exclusion_warning())
        md = markdown.render(report)
        self.assertIn("분석할 소스 코드가 없습니다", md)
        self.assertIn(".gitignore:1 '*'", md)
        self.assertIn("탐색 제외", text.render(report))

        report = build_report(self.root, set(), use_gitignore=False)
        self.assertIn("app/src/main.c", {f.path.as_posix() for f in report.user_files})

        make_tree(self.root, {".gitignore": "", "app/.gitignore": "src/\n"})
        files, _, excluded = scan(self.root)
        self.assertFalse(any("main.c" in p.as_posix() for p in files))
        self.assertIn("app/.gitignore:1 'src/'", [e.detail for e in excluded])


class ImprovementsTest(unittest.TestCase):
    """src 분석 리포트에서 발견된 문제들의 회귀 테스트."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._tmp.name)
        (root / "Lib").mkdir()
        (root / "Lib_ssh").mkdir()
        # CP949로 저장된 한글 주석 (goose_common.h 사례)
        (root / "goose.h").write_bytes("/* GOOSE 메시지 수신 처리 */\nvoid goose(void);\n".encode("cp949"))
        (root / "main.c").write_text(
            '/* #include "DFT_calc.h" */\n'
            '#include "goose.h"\n'
            '#include <pthread.h>\n'
            'static void *worker(void *p) { perror("x"); return 0; }\n'
            'int main(void) {\n'
            '  pthread_t t;\n'
            '  if (strcmp(name, "eth0") == 0 /* lan */) { pthread_create(&t, 0, worker, 0); }\n'
            '  while (1) { sleep(1); mz_compress(0); }\n'
            '}\n')
        (root / "miniz.c").write_text(
            '#ifdef _WIN32\n#include <windows.h>\n#endif\n'
            'static void *def_alloc(void *o, int n) { return 0; }\n'
            'int mz_compress(int x) { void *(*f)(void *, int) = def_alloc; return x; }\n'
            '#ifdef A\nint mz_crc32(void) { return 1; }\n#else\nint mz_crc32(void) { return 2; }\n#endif\n')
        (root / "Lib" / "gpiod.h").write_text("static inline int gpiod_x(void) { return 0; }\n")
        (root / "main.c.bak").write_text("int old;\n")
        (root / "Lib_ssh" / "libssh.a").write_bytes(b"!<arch>\n\0\0")
        (root / "big.c").write_text("int a;\n" * 200_000)                    # 약 1.4MB
        cls.report = build_report(root, set(), libs={"miniz.c", "Lib"})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_cp949_header_doc(self):
        pf = next(p for p in self.report.index.user_files if p.path.name == "goose.h")
        self.assertEqual(pf.header_doc, ["GOOSE 메시지 수신 처리"])

    def test_commented_code_not_description(self):
        pf = next(p for p in self.report.index.user_files if p.path.name == "main.c")
        self.assertEqual(pf.header_doc, [])

    def test_condition_keeps_string_drops_comment(self):
        main = self.report.index.by_name["main"][0]
        tree = "\n".join(flow_tree(self.report.index, main))
        self.assertIn('if (strcmp(name, "eth0") == 0) 일 때', tree)
        self.assertIn("🔁 while (1) — 무한 반복", tree)

    def test_std_and_posix(self):
        idx = self.report.index
        kinds = {c.name: c.target.kind for f in idx.funcs for c in f.calls}
        self.assertEqual(kinds["perror"], "std")
        self.assertEqual(kinds["pthread_create"], "posix")
        self.assertEqual(kinds["sleep"], "posix")
        self.assertEqual(kinds["mz_compress"], "lib")          # 외부 라이브러리 함수로 해석

    def test_library_excluded(self):
        user = {f.path.as_posix() for f in self.report.user_files}
        self.assertNotIn("miniz.c", user)
        self.assertNotIn("Lib/gpiod.h", user)
        self.assertEqual({f.path.as_posix() for f in self.report.library_files}, {"miniz.c", "Lib/gpiod.h"})
        self.assertNotIn("Win32 API", self.report.frameworks)
        self.assertEqual([f.name for f in self.report.index.async_functions()], ["worker"])

    def test_backup_and_skipped(self):
        names = {f.path.as_posix() for f in self.report.files}
        self.assertNotIn("main.c.bak", names)
        reasons = {s.path.as_posix(): s.reason for s in self.report.skipped_files}
        self.assertEqual(reasons["Lib_ssh/libssh.a"], "바이너리")
        self.assertTrue(reasons["big.c"].startswith("대용량"))
        bigger = build_report(self.root, set(), libs={"miniz.c", "Lib"}, max_size=2 * 1024 * 1024)
        self.assertIn("big.c", {f.path.as_posix() for f in bigger.user_files})

    def test_duplicate_definitions_get_unique_anchors(self):
        rep = build_report(self.root, set())                     # miniz.c 를 사용자 코드로
        md = markdown.render(rep)
        ids = re.findall(r'<a id="([^"]+)"', md)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("2번째 정의", md)

    def test_split_output_links_resolve(self):
        pages = markdown.render_split(self.report)
        self.assertIn("index.md", pages)
        self.assertIn("files/main.c.md", pages)
        anchors = {page: set(re.findall(r'<a id="([^"]+)"', body)) for page, body in pages.items()}
        for page, body in pages.items():
            for target, frag in re.findall(r'\]\(([^)#]*)#(fn-[^)]+)\)', body):
                if not target:
                    dest = page
                elif page == "index.md":
                    dest = target
                else:
                    dest = "files/" + target
                self.assertIn(frag, anchors.get(dest, set()), f"{page} → {target}#{frag}")
        self.assertLess(len(pages["index.md"]), len(markdown.render(self.report)))


if __name__ == "__main__":
    unittest.main()
