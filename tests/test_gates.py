"""Тесты детерминированных гейтов."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import gates  # noqa: E402

CLEAN_RU = (
    "Выборка включала 42 участника. Критерии включения описаны в разделе 2. "
    "Наблюдаемая разница между группами составила 3,1 балла по шкале HAM-D. "
    "Различия сохранялись после поправки на множественные сравнения."
)

DIRTY_RU = (
    "В современном мире данная проблема играет важную роль. Исследования показывают, что метод эффективен. "
    "Следует отметить, что результаты впечатляют, подчёркивая значимость подхода."
)


class HygieneGateTest(unittest.TestCase):
    def test_detects_zero_width(self):
        text = "Обычный текст​ со скрытым символом."
        result = gates.check_hygiene(text)
        self.assertTrue(result.blocking)
        self.assertTrue(any(f.code == "HYG-ZERO-WIDTH" for f in result.findings))

    def test_detects_bidi(self):
        text = "Текст с ‮bidi-переопределением."
        result = gates.check_hygiene(text)
        self.assertTrue(result.blocking)

    def test_clean_text_passes(self):
        result = gates.check_hygiene(CLEAN_RU)
        self.assertFalse(result.blocking)
        self.assertEqual(result.verdict, "pass")

    def test_fix_removes_invisible(self):
        text = "Абзац​ один.\nАбзац﻿ два."
        fixed = gates.fix_hygiene(text)
        self.assertNotIn("​", fixed)
        self.assertNotIn("﻿", fixed)
        self.assertFalse(gates.check_hygiene(fixed).blocking)


class StyleGateTest(unittest.TestCase):
    def test_unsourced_claim_blocks(self):
        result = gates.check_style(DIRTY_RU)
        self.assertTrue(result.blocking)
        self.assertTrue(any(f.code == "UNSOURCED" for f in result.findings))

    def test_clean_text_passes(self):
        result = gates.check_style(CLEAN_RU)
        self.assertFalse(result.blocking)
        self.assertGreaterEqual(result.stats["score"], 90)

    def test_score_degrades_with_slop(self):
        clean = gates.check_style(CLEAN_RU).stats["score"]
        dirty = gates.check_style(DIRTY_RU).stats["score"]
        self.assertLess(dirty, clean)

    def test_rules_file_loads(self):
        rules = gates.load_rules()
        self.assertIn("ai_vocabulary", rules)
        self.assertIn("patterns", rules)


class BibliographyGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-bib-")
        self.tex = os.path.join(self.tmp, "main.tex")
        self.bib = os.path.join(self.tmp, "refs.bib")
        with open(self.tex, "w", encoding="utf-8") as fh:
            fh.write("\\section{Обзор}\nСогласно \\cite{smith2020} и \\cite{ghost2021}, метод применим.\n")
        with open(self.bib, "w", encoding="utf-8") as fh:
            fh.write("@article{smith2020,\n title = {A real paper},\n author = {Smith, J.},\n"
                     " year = {2020},\n doi = {10.1000/xyz123}\n}\n"
                     "@article{unused2019,\n title = {Never cited},\n author = {Doe, A.},\n"
                     " year = {2019}\n}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_key_blocks(self):
        result = gates.check_bibliography(self.tex, self.bib, offline=True)
        self.assertTrue(result.blocking)
        self.assertTrue(any(f.code == "BIB-MISSING" for f in result.findings))

    def test_unused_entry_reported(self):
        result = gates.check_bibliography(self.tex, self.bib, offline=True)
        self.assertTrue(any(f.code == "BIB-UNUSED" for f in result.findings))

    def test_offline_does_not_call_network(self):
        result = gates.check_bibliography(self.tex, self.bib, offline=True)
        self.assertEqual(result.stats["doi_verified"], 0)
        self.assertTrue(result.stats["offline"])

    def test_all_keys_resolved(self):
        tex = os.path.join(self.tmp, "ok.tex")
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write("Согласно \\cite{smith2020}, метод применим.\n")
        result = gates.check_bibliography(tex, self.bib, offline=True)
        self.assertFalse(result.blocking)


class NumbersGateTest(unittest.TestCase):
    def test_traced_number_passes(self):
        text = "Выборка включала 42 участника."
        result = gates.check_numbers(text, {"n_participants": 42}, kind="md")
        self.assertEqual(result.stats["untraced"], 0)

    def test_untraced_number_reported(self):
        text = "Показатель составил 137 единиц."
        result = gates.check_numbers(text, {"n_participants": 42}, kind="md")
        self.assertGreaterEqual(result.stats["untraced"], 1)
        self.assertTrue(any(f.code == "NUM-UNTRACED" for f in result.findings))

    def test_declared_but_unused_is_info(self):
        text = "Метод применён на выборке."
        result = gates.check_numbers(text, {"effect": 0.42}, kind="md")
        self.assertTrue(any(f.code == "NUM-UNUSED" for f in result.findings))

    def test_citation_commands_ignored(self):
        text = "Как показано в \\cite{smith2020} и разделе 2026 года, метод работает."
        result = gates.check_numbers(text, {}, kind="tex")
        values = [f["value"] for f in result.findings if f.code == "NUM-UNTRACED"]
        self.assertNotIn("2020", values)


class GateRunnerTest(unittest.TestCase):
    def test_unknown_gate_raises(self):
        with self.assertRaises(ValueError):
            gates.run_gate("G4", text="x")

    def test_run_g0(self):
        self.assertTrue(gates.run_gate("G0", text="чистый текст").verdict == "pass")

    def test_result_serialisable(self):
        payload = json.loads(gates.run_gate("G0", text="текст​").to_json())
        self.assertEqual(payload["gate"], "G0_hygiene")
        self.assertIn("findings", payload)


if __name__ == "__main__":
    unittest.main()
