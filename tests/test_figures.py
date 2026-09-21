"""Тесты G8: рисунки и подписи."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import figures as figures_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUSCRIPT = os.path.join(ROOT, "examples", "minimal", "main.tex")
VALUES = os.path.join(ROOT, "examples", "minimal", "analysis", "results.json")

TEX = """\\section{Результаты}
См. рисунок~\\ref{fig:one}.

\\begin{figure}[htbp]
  \\centering
  \\includegraphics{figures/fig1}
  \\caption{Разница между группами (n = 42) составила 3,1 балла.}
  \\label{fig:one}
\\end{figure}

\\begin{figure}[htbp]
  \\includegraphics{figures/missing}
  \\label{fig:two}
\\end{figure}
"""


class ExtractTest(unittest.TestCase):
    def test_figures_extracted_with_caption_and_label(self):
        figures = figures_mod.extract_figures(TEX, kind="tex")
        self.assertEqual(len(figures), 2)
        first = figures[0]
        self.assertEqual(first.label, "fig:one")
        self.assertIn("Разница между группами", first.caption)
        self.assertEqual(first.path, "figures/fig1")
        self.assertGreater(first.line, 1)

    def test_markdown_images(self):
        figures = figures_mod.extract_figures("![Подпись](figures/a.png)\n", kind="md")
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0].path, "figures/a.png")
        self.assertEqual(figures[0].caption, "Подпись")

    def test_line_numbers_are_human_readable(self):
        figures = figures_mod.extract_figures(TEX, kind="tex")
        lines = TEX.splitlines()
        for figure in figures:
            self.assertIn("\\begin{figure}", lines[figure.line - 1])


class FigureGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-fig-")
        os.makedirs(os.path.join(self.tmp, "figures"))
        self.fig_path = os.path.join(self.tmp, "figures", "fig1.pdf")
        with open(self.fig_path, "w", encoding="utf-8") as fh:
            fh.write("%PDF-1.4 placeholder")
        self.results = os.path.join(self.tmp, "results.json")
        with open(self.results, "w", encoding="utf-8") as fh:
            json.dump({"n_participants": 42, "effect_hamd": 3.1}, fh)
        with open(VALUES, encoding="utf-8") as fh:
            self.values = json.load(fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_is_blocking_error(self):
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex")
        self.assertTrue(result.blocking)
        self.assertTrue(any(f.code == "FIG-FILE-MISSING" for f in result.findings))
        self.assertEqual(result.stats["missing_files"], 1)
        self.assertEqual(result.stats["files_found"], 1)

    def test_caption_without_text_flagged(self):
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex")
        self.assertTrue(any(f.code == "FIG-NO-CAPTION" for f in result.findings))

    def test_uncited_figure_warned(self):
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex")
        codes = [f.code for f in result.findings]
        self.assertNotIn("FIG-UNCITED-fig:one", codes)  # fig:one упомянут через \ref
        self.assertTrue(any(f.code == "FIG-UNCITED" and "fig:two" in f.message for f in result.findings))

    def test_duplicate_label_is_error(self):
        tex = TEX.replace("\\label{fig:two}", "\\label{fig:one}")
        result = figures_mod.check_figures(tex, self.values, base_dir=self.tmp, kind="tex")
        self.assertTrue(any(f.code == "FIG-DUP-LABEL" for f in result.findings))

    def test_untraced_number_in_caption(self):
        tex = TEX.replace("составила 3,1 балла", "составила 137,5 балла")
        result = figures_mod.check_figures(tex, self.values, base_dir=self.tmp, kind="tex")
        self.assertTrue(any(f.code == "FIG-NUM-UNTRACED" and "137.5" in f.message
                            for f in result.findings))

    def test_stale_figure_detected(self):
        old = time.time() - 7200
        os.utime(self.fig_path, (old, old))
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex",
                                           results_path=self.results)
        self.assertTrue(any(f.code == "FIG-STALE" for f in result.findings))

    def test_fresh_figure_not_flagged(self):
        # рисунок новее результатов — всё в порядке
        past = time.time() - 7200
        os.utime(self.results, (past, past))
        os.utime(self.fig_path, None)
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex",
                                           results_path=self.results)
        self.assertFalse(any(f.code == "FIG-STALE" for f in result.findings))

    def test_clean_example_passes(self):
        with open(MANUSCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        result = figures_mod.check_figures(text, self.values,
                                           base_dir=os.path.dirname(MANUSCRIPT), kind="tex",
                                           results_path=VALUES)
        self.assertFalse(result.blocking, result.to_json())
        self.assertEqual(result.stats["figures"], 1)
        self.assertEqual(result.stats["missing_files"], 0)

    def test_sha_recorded_for_resolved_figure(self):
        result = figures_mod.check_figures(TEX, self.values, base_dir=self.tmp, kind="tex")
        self.assertEqual(result.gate, "G8_figures")
        payload = json.loads(result.to_json())
        self.assertEqual(payload["stats"]["files_found"], 1)

    def test_markdown_mode_extracts_images(self):
        md = "Результаты на рисунке ниже.\n\n![Разница 3,1 балла](figures/fig1.pdf)\n"
        result = figures_mod.check_figures(md, self.values, base_dir=self.tmp, kind="md")
        self.assertEqual(result.stats["figures"], 1)
        self.assertEqual(result.stats["missing_files"], 0)


if __name__ == "__main__":
    unittest.main()
