"""Тесты экспорта реестров и генератора disclosure."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import (  # noqa: E402
    db,
    disclosure as disclosure_mod,
    export as export_mod,
)
from academic_journal.journal import Journal  # noqa: E402


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-export-")
        self.conn = db.connect(os.path.join(self.tmp, "journal.db"))
        db.upsert_evidence(self.conn, {"source_id": "S-01", "doi": "10.1000/xyz",
                                       "citation": "Smith 2020", "year": "2020",
                                       "trust_tier": "primary"})
        db.upsert_claim(self.conn, {"claim_id": "C-01", "project": "demo",
                                    "text": "Выборка включала 42 участника",
                                    "verdict": "supported", "status": "resolved",
                                    "source_id": "S-01", "quote_locator": "p. 7"})
        db.upsert_decision(self.conn, {"decision_id": "D-01", "project": "demo",
                                       "question": "Какой дизайн?", "chosen": "лонгитюд",
                                       "rationale": "нужны повторные измерения",
                                       "reopen_if": "сменится доступ к данным"})
        db.record_gate(self.conn, {"run_id": "R-1", "project": "demo",
                                   "gate": "G0_hygiene", "verdict": "pass", "blocking": False})

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_csv_export(self):
        out = os.path.join(self.tmp, "csv")
        created = export_mod.export(self.conn, out, what="all", fmt="csv", project="demo")
        self.assertEqual(len(created), 4)
        with open(os.path.join(out, "claims.csv"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("claim_id,project,section", content)
        self.assertIn("C-01", content)

    def test_markdown_export(self):
        out = os.path.join(self.tmp, "md")
        export_mod.export(self.conn, out, what="claims", fmt="md", project="demo")
        with open(os.path.join(out, "claims.md"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("# Реестр утверждений", content)
        self.assertIn("| claim_id |", content)

    def test_project_filter(self):
        db.upsert_claim(self.conn, {"claim_id": "C-02", "project": "other",
                                    "text": "другой проект", "status": "open"})
        out = os.path.join(self.tmp, "filtered")
        export_mod.export(self.conn, out, what="claims", fmt="csv", project="demo")
        with open(os.path.join(out, "claims.csv"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("C-01", content)
        self.assertNotIn("C-02", content)

    def test_empty_registry_renders_placeholder(self):
        conn = db.connect(os.path.join(self.tmp, "empty.db"))
        text = export_mod.to_markdown([], ["a", "b"], "Пусто")
        self.assertIn("_пусто_", text)


class DisclosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-disclosure-")
        self.journal = Journal(self.tmp, session="s-test", project="demo")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, **payload):
        payload.setdefault("kind", "ai_use")
        self.journal.append("note", payload, actor="human")

    def test_empty_journal_warns(self):
        result = disclosure_mod.build(self.journal)
        self.assertFalse(result["ok"])
        self.assertTrue(any("нет ни одной записи" in w for w in result["warnings"]))

    def test_records_collected_and_rendered(self):
        self._add(section="Методы", authorship="human_draft_model_edit",
                  model="claude-opus-4.5", tasks=["правка стиля"],
                  human_verification=["числа сверены"], disclosure_required=True)
        result = disclosure_mod.build(self.journal)
        self.assertTrue(result["ok"])
        self.assertEqual(result["level"], "human_draft_model_edit")
        self.assertIn("Методы", result["markdown"])
        self.assertIn("claude-opus-4.5", result["markdown"])
        self.assertEqual(len(result["records"]), 1)

    def test_unchecked_model_text_blocks(self):
        self._add(section="Результаты", authorship="model_generated_unchecked",
                  model="gpt-5", tasks=["генерация"], human_verification=[])
        result = disclosure_mod.build(self.journal)
        self.assertFalse(result["ok"])
        self.assertTrue(any("без проверки человеком" in w for w in result["warnings"]))
        self.assertIn("Внимание", result["markdown"])

    def test_missing_model_warned(self):
        self._add(section="Введение", authorship="human_draft_model_edit",
                  tasks=["правка"], human_verification=["проверено"])
        result = disclosure_mod.build(self.journal)
        self.assertTrue(any("не указана модель" in w for w in result["warnings"]))

    def test_suggested_text_differs_by_level(self):
        minimal = disclosure_mod.suggest_text("human_draft_model_edit", ["model-x"])
        expanded = disclosure_mod.suggest_text("model_draft_human_rewrite", ["model-x"])
        self.assertNotEqual(minimal, expanded)
        self.assertIn("model-x", minimal)


if __name__ == "__main__":
    unittest.main()
