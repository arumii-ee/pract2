"""실행: python -m unittest discover -s tests"""

import tempfile
import re
import unittest
from pathlib import Path

from analyzer.cli import build_report
from analyzer.graph import file_call_edges, flow_tree, skeleton, thread_map
from analyzer.parsers.c_parser import _Parser
from analyzer.reporters import markdown, text
from analyzer.scanner import GitIgnore, scan
from analyzer.summary import from_name, summarize

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

    def test_if0_blocks_are_excluded(self):
        pf = parse(
            '#if 0 // old\n'
            'void old_func(void) { old_call(); }\n'
            '#ifdef X\n'
            'void nested(void) {}\n'
            '#endif\n'
            '#else\n'
            'void new_func(void) { new_call(); }\n'
            '#endif\n'
            '#if 1\n'
            'void keep(void) {}\n'
            '#else\n'
            'void drop(void) {}\n'
            '#endif\n'
            '#ifdef CFG\n'
            'void cfg_a(void) {}\n'
            '#else\n'
            'void cfg_b(void) {}\n'
            '#endif\n'
            'int main(void) {\n'
            '#if (0)\n'
            '  dead();\n'
            '#endif\n'
            '  /* commented(); */ real(); // trailing();\n'
            '}\n')
        self.assertEqual([f.name for f in pf.functions], ["new_func", "keep", "cfg_a", "cfg_b", "main"])
        self.assertEqual(pf.functions[0].line, 7)                       # 줄 번호 유지
        self.assertEqual([c.name for c in pf.functions[-1].calls], ["real"])

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
        for heading in ("## 1. 개요", "## 2. 전체 구조", "## 3. 함수 상세", "## 4. 자료형", "```mermaid"):
            self.assertIn(heading, md)
        self.assertNotIn("실행 흐름", md)


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
        rep = build_report(self.root, set(), auto_lib=False)     # miniz.c 를 사용자 코드로
        md = markdown.render(rep)
        ids = re.findall(r'<a id="([^"]+)"', md)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("2번째 정의", md)

    def test_split_output_links_resolve(self):
        pages = markdown.render_split(self.report)
        self.assertIn("index.md", pages)
        self.assertIn("files/01_main.md", pages)
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


THREADED = {
    "app/src/main.c": (
        "/* 프로그램 시작 */\n"
        "int main(void) {\n"
        "  get_ip();\n"
        "  pthread_create(&a, NULL, server_thread, NULL);\n"
        "  pthread_create(&b, NULL, lcd_thread, NULL);\n"
        "  while (1) { update_map(); }\n"
        "}\n"
        "/* void old_code(){\n int x = 1;\n */\n"
        "void update_map(void) { printf(\"x\"); }\n"),
    "app/src/net.c": (
        "int get_ip(void) { getifaddrs(&x); return 0; }\n"
        "void *client_handler(void *p) { recv(fd, b, 1, 0); return 0; }\n"
        "void *server_thread(void *p) { while (1) { accept(s, 0, 0); "
        "pthread_create(&t, NULL, client_handler, NULL); } }\n"),
    "app/src/hmi.c": (
        "void hmi_init(void) { signal(SIGUSR1, lcd_rx_handler); }\n"
        "void lcd_rx_handler(int s) { if (s) { lcd_rx_process(); } }\n"
        "void lcd_rx_process(void) { memcpy(a, b, 1); }\n"
        "void *lcd_thread(void *p) { hmi_init(); while (1) { usleep(1); } }\n"),
}


class SummaryViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_tree(Path(cls.tmp.name), THREADED)
        cls.report = build_report(Path(cls.tmp.name), set())
        cls.idx = cls.report.index
        cls.fn = {f.name: f for f in cls.idx.funcs}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_name_based_summary(self):
        self.assertEqual(from_name("get_local_ip"), "local ip 조회")
        self.assertEqual(from_name("uart_buffer_write"), "uart buffer 쓰기")
        self.assertEqual(from_name("lcd_rx_process"), "lcd rx 처리")
        self.assertEqual(from_name("register_map_init"), "register map 초기화")
        self.assertEqual(from_name("modbus_tcp_server_thread"), "modbus tcp server 스레드")

    def test_summary_prefers_comment_and_adds_tags(self):
        self.assertEqual(summarize(self.fn["main"], self.idx), ("프로그램 시작 · 🔁 상주 루프 · 스레드 생성", "주석"))
        text_, src = summarize(self.fn["server_thread"], self.idx)
        self.assertEqual(src, "추정")
        self.assertIn("🔁 상주 루프", text_)
        self.assertIn("네트워크", text_)
        self.assertIn("⚡ 콜백/핸들러", summarize(self.fn["lcd_rx_handler"], self.idx)[0])

    def test_commented_out_code_is_not_doc(self):
        self.assertEqual(self.fn["update_map"].doc, [])

    def test_skeleton_keeps_only_user_calls_in_order(self):
        sk = "\n".join(skeleton(self.idx, self.fn["main"]))
        self.assertNotIn("printf", sk)
        self.assertNotIn("pthread_create(", sk)
        self.assertIn("1. get_ip()", sk)
        self.assertIn("2. ⚡ server_thread() 시작/등록", sk)
        self.assertIn("3. ⚡ lcd_thread() 시작/등록", sk)
        self.assertIn("4. 🔁 무한 반복", sk)
        self.assertLess(sk.index("get_ip"), sk.index("update_map"))

    def test_thread_map_order_and_nesting(self):
        edges = [(o.name, c.name, t.name, r.via) for o, c, t, r in thread_map(self.idx)]
        self.assertEqual(edges, [
            ("main", "main", "server_thread", "pthread_create"),
            ("main", "main", "lcd_thread", "pthread_create"),
            ("server_thread", "server_thread", "client_handler", "pthread_create"),
            ("lcd_thread", "hmi_init", "lcd_rx_handler", "signal"),
        ])

    def test_file_call_edges(self):
        edges = {(a.name, b.name): n for (a, b), n in file_call_edges(self.idx).items()}
        self.assertEqual(edges[("main.c", "net.c")], 2)           # get_ip 호출 + server_thread 등록
        self.assertEqual(edges[("main.c", "hmi.c")], 1)
        self.assertNotIn(("main.c", "main.c"), edges)

    def test_details_follow_call_order(self):
        md = markdown.render(self.report)
        det = md[md.index("## 3. 함수 상세"):md.index("## 4.")]
        self.assertIn("### 3.1 main() 에서 시작", det)
        self.assertLess(det.index("<summary><b><code>main()</code></b>"), det.index("<summary><b><code>get_ip()</code></b>"))
        self.assertLess(det.index("<summary><b><code>get_ip()</code></b>"), det.index("<summary><b><code>update_map()</code></b>"))
        self.assertLess(det.index("<summary><b><code>main()</code></b>"), det.index("<summary><b><code>server_thread()</code></b>"))
        self.assertLess(det.index("<summary><b><code>lcd_thread()</code></b>"), det.index("<summary><b><code>hmi_init()</code></b>"))
        self.assertIn("main › get_ip", det)                              # 경로 표시
        self.assertIn("lcd_thread › hmi_init", det)
        main_card = det[det.index("<summary><b><code>main()</code></b>"):det.index("<summary><b><code>get_ip()</code></b>")]
        self.assertIn("→ **호출하는 함수** (순서대로)", main_card)
        self.assertRegex(main_card, r"1\. \[`get_ip\(\)`\].* — ip 조회")
        self.assertIn("⚡ `pthread_create`로 시작/등록", main_card)
        self.assertNotIn("← **호출되는 곳**", det)                       # 호출되는 곳은 '자세히' 안으로

    def test_no_file_header_comment_or_include_section(self):
        md = markdown.render(self.report)
        self.assertNotIn("include 관계", md)
        self.assertNotIn("파일 상단 주석", md)
        self.assertIn("### 2.3 함수 호출 관계도", md)

    def test_glance_section(self):
        md = markdown.render(self.report)
        self.assertLess(md.index("## 0. 한눈에 보기"), md.index("## 1. 개요"))
        glance = md[md.index("## 0. 한눈에 보기"):md.index("## 1. 개요")]
        for h in ("### 0.1 스레드 / 진입점 구성도", "### 0.2 모듈(파일) 간 호출도",
                  "### 0.3 진입점별 핵심 흐름", "### 0.4 모듈 요약"):
            self.assertIn(h, glance)
        self.assertEqual(glance.count("```mermaid"), 3)
        self.assertIn('"② pthread_create"', glance)
        self.assertIn("signal · hmi_init() 안에서", glance)
        self.assertIn("| 파일 | 대표 함수 |", glance)                    # 모듈 요약은 2열
        self.assertNotIn("스레드/콜백 |", glance)
        self.assertNotIn("<details><summary>전체 호출 트리", md)       # 실행 흐름 삭제
        self.assertIn("<details open><summary>함수 ", md)              # 그룹 접기
        self.assertEqual(md.count("<details"), md.count("</details>"))
        self.assertIn("| 함수 | 한 줄 요약 | 파일 |", md)                 # 그룹별 요약표
        self.assertIn("<summary><b><code>get_ip()</code></b> — ip 조회 · 네트워크</summary>", md)


class ReportQualityTest(unittest.TestCase):
    """261104 리포트 검토에서 나온 개선 사항"""

    def test_auto_library_detection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src" / "Lib").mkdir(parents=True)
            (root / "src" / "main.c").write_text("int main(void) { mz_crc32(); return 0; }\n")
            (root / "src" / "miniz.c").write_text(
                "#ifdef _WIN32\n#include <windows.h>\n#else\n#include <pthread.h>\n#endif\n"
                "static void *cb(void *p) { return 0; }\n"
                "int mz_crc32(void) { pthread_t t; pthread_create(&t, 0, cb, 0); return 0; }\n")
            (root / "src" / "Lib" / "gpiod.h").write_text("static inline int gpiod_x(void) { return 0; }\n")
            rep = build_report(root, set())
            libs = {f.path.as_posix(): f.library_reason for f in rep.library_files}
            self.assertEqual(set(libs), {"src/miniz.c", "src/Lib/gpiod.h"})
            self.assertIn("폴더 `Lib/`", libs["src/Lib/gpiod.h"])
            self.assertEqual(rep.index.async_functions(), [])          # 라이브러리 내부 콜백은 스레드 목록에서 빠짐
            self.assertEqual(rep.frameworks, [])
            md = markdown.render(rep)
            self.assertIn("외부 라이브러리로 분류한 파일 2개", md)
            off = build_report(root, set(), auto_lib=False)
            self.assertEqual(off.library_files, [])
            self.assertIn("Win32 API (플랫폼 조건부 #include)", off.frameworks)
            self.assertIn("POSIX Threads (플랫폼 조건부 #include)", off.frameworks)

    def test_commented_code_and_decoration_not_summary(self):
        pf = parse(
            "// void eMMC_File(char storage,char turnCnt)\n"
            "void eMMC_Load(void) { }\n"
            "// FPGA_IO_REG.LINUX_EVENT.bit.EV1\n"
            "void ev1_interrupt(void) { }\n"
            "/* ------------------- zlib-style API's ------------------- */\n"
            "int mz_adler32(void) { return 0; }\n"
            "// eth0 primary, eth1 backup\n"
            "int get_ip(void) { return 0; }\n")
        doc = {f.name: summarize(f)[0] for f in pf.functions}
        self.assertNotIn("eMMC_File", doc["eMMC_Load"])               # 주석 처리된 함수 선언
        self.assertNotIn("FPGA_IO_REG", doc["ev1_interrupt"])          # 주석 처리된 멤버 접근 코드
        self.assertTrue(doc["mz_adler32"].startswith("zlib-style API's"))  # 장식선 제거
        self.assertTrue(doc["get_ip"].startswith("eth0 primary, eth1 backup"))
        self.assertEqual(from_name("LCD_clear"), "LCD 지우기")

    def test_struct_member_count(self):
        pf = parse("typedef struct { int a; char b[4]; void (*cb)(int x, int y); } S;\n"
                   "typedef enum { A, B, C } E;\n"
                   "struct P { struct { int x; } in; int y; };\n")
        members = {t.name: t.members for t in pf.types}
        self.assertEqual(members, {"S": 3, "E": 3, "P": 2})

    def test_callers_and_source_are_capped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            callers = "".join(f"void c{i}(void) {{ util(); }}\n" for i in range(40))
            body = "".join(f"  x += {i};\n" for i in range(100))
            (root / "main.c").write_text(
                "static int x;\nvoid util(void) {\n" + body + "}\n" + callers
                + "int main(void) { util(); return 0; }\n")
            rep = build_report(root, set())
            md = markdown.render(rep)
            self.assertIn("- **호출되는 곳** (41곳): ", md)
            self.assertIn("<details><summary>외 11곳 더 보기</summary>", md)
            self.assertIn("이하 22줄 생략 (전체 102줄: main.c:2-103)", md)
            self.assertNotIn("생략", markdown.render(rep, source_lines=0).split("## 4.")[0].split("util()</code>")[-1]
                             .split("</details>")[0])
            self.assertIn("함수 40개 · 파일·줄 순서", md)                     # 도달하지 않는 함수 절 문구
            self.assertEqual(md.count("<details"), md.count("</details>"))


if __name__ == "__main__":
    unittest.main()
