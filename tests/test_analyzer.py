"""실행: python -m unittest discover -s tests"""

import unittest
from pathlib import Path

from analyzer.cli import build_report
from analyzer.graph import flow_tree
from analyzer.parsers.c_parser import _Parser
from analyzer.reporters import markdown

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


if __name__ == "__main__":
    unittest.main()
