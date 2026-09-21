"""Тесты canary-теста контура проверок."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import canary as canary_mod, gates as gates_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUSCRIPT = os.path.join(ROOT, "examples", "minimal", "main.tex")
BIB = os.path.join(ROOT, "examples", "minimal", "refs.bib")
VALUES = os.path.join(ROOT, "examples", "minimal", "analysis", "results.json")


class InjectionTest(unittest.TestCase):
    def test_inject_adds_all_items(self):
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        injected, manifest = canary_mod.inject(text, kind="tex", seed=3)
        types = [i["type"] for i in manifest["items"]]
        self.assertEqual(types.count("unsourced"), 5)
        self.assertEqual(types.count("reference"), 3)
        self.assertEqual(types.count("number"), 1)
        self.assertGreater(len(injected), len(text))
        for item in manifest["items"]:
            self.assertIn(item["id"], injected)

    def test_injection_is_deterministic(self):
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        first, _ = canary_mod.inject(text, kind="tex", seed=11)
        second, _ = canary_mod.inject(text, kind="tex", seed=11)
        self.assertEqual(first, second)

    def test_injected_lines_recorded(self):
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        injected, manifest = canary_mod.inject(text, kind="tex", seed=5)
        lines = injected.splitlines()
        for item in manifest["items"]:
            line = lines[item["expect"]["line"] - 1]
            self.assertIn(item["id"], line)
            self.assertIn(item["text"][:20], line)


class CanaryCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-canary-")
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        self.injected, self.manifest = canary_mod.inject(text, kind="tex", seed=7)
        self.canary_path = os.path.join(self.tmp, "draft.canary.tex")
        with open(self.canary_path, "w", encoding="utf-8") as fh:
            fh.write(self.injected)
        self.reports = {}
        g1 = gates_mod.check_style(self.injected)
        g3 = gates_mod.check_bibliography(self.canary_path, BIB, offline=True)
        with open(VALUES, encoding="utf-8") as fh:
            values = json.load(fh)
        g5 = gates_mod.check_numbers(self.injected, values, kind="tex")
        for name, result in (("G1", g1), ("G3", g3), ("G5", g5)):
            path = os.path.join(self.tmp, name + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(result.to_json())
            self.reports[name] = path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_canaries_detected(self):
        report = canary_mod.evaluate(self.manifest, self.reports)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["recall"], 1.0)
        self.assertEqual(report["detected"], report["total"])

    def test_missing_report_lowers_recall(self):
        report = canary_mod.evaluate(self.manifest, {"G1": self.reports["G1"]})
        self.assertLess(report["recall"], 1.0)
        self.assertEqual(report["verdict"], "fail")
        self.assertTrue(any(r["note"].startswith("нет отчёта") for r in report["results"]))

    def test_clean_text_produces_no_false_detections(self):
        """На чистом тексте канарских находок быть не должно (иначе recall бессмыслен)."""
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            clean = fh.read()
        g1 = gates_mod.check_style(clean)
        self.assertFalse(any(f.code == "UNSOURCED" for f in g1.findings))

    def test_markdown_render(self):
        report = canary_mod.evaluate(self.manifest, self.reports)
        text = canary_mod.render_markdown(report)
        self.assertIn("Canary-тест", text)
        self.assertIn("Recall", text)
        self.assertIn("unsourced-1", text)


if __name__ == "__main__":
    unittest.main()
