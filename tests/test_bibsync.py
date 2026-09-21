"""Тесты синхронизации .bib ↔ реестр evidence."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import bibsync, db  # noqa: E402

BIB = """@article{smith2020,
  title  = {Reproducibility in computational experiments},
  author = {Smith, J. and Doe, A.},
  year   = {2020},
  journal= {Journal of Open Research},
  doi    = {10.1000/xyz123}
}
@article{ivanov2024,
  title  = {Открытые данные в прикладных исследованиях},
  author = {Иванов, И. И. and Петров, П. П.},
  year   = {2024},
  journal= {Вестник данных},
  doi    = {10.2000/abc456}
}
@eprint{petrov2025,
  title  = {Registered reports: a meta-study},
  author = {Petrov, P.},
  year   = {2025},
  doi    = {10.48550/arXiv.2501.01234}
}
@misc{graynote,
  title  = {Отчёт без DOI},
  year   = {2019}
}
"""


class BibSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-bib-")
        self.bib_path = os.path.join(self.tmp, "refs.bib")
        with open(self.bib_path, "w", encoding="utf-8") as fh:
            fh.write(BIB)
        self.conn = db.connect(os.path.join(self.tmp, "journal.db"))

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_import_creates_evidence(self):
        stats = bibsync.import_bib(self.conn, self.bib_path, project="demo")
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["imported"], 4)
        rows = {r["source_id"]: r for r in db.list_evidence(self.conn)}
        self.assertEqual(rows["smith2020"]["doi"], "10.1000/xyz123")
        self.assertEqual(rows["smith2020"]["year"], "2020")
        self.assertEqual(rows["smith2020"]["type"], "article")
        self.assertIn("Smith", rows["smith2020"]["author"])
        self.assertEqual(rows["smith2020"]["venue"], "Journal of Open Research")
        self.assertIn("Reproducibility", rows["smith2020"]["title"])

    def test_import_marks_unverified(self):
        bibsync.import_bib(self.conn, self.bib_path)
        rows = db.list_evidence(self.conn)
        self.assertTrue(all(int(r["metadata_verified"] or 0) == 0 for r in rows))

    def test_trust_tiers_guessed(self):
        bibsync.import_bib(self.conn, self.bib_path)
        rows = {r["source_id"]: r for r in db.list_evidence(self.conn)}
        self.assertEqual(rows["smith2020"]["trust_tier"], "primary")
        self.assertEqual(rows["petrov2025"]["trust_tier"], "preprint")
        self.assertEqual(rows["petrov2025"]["type"], "preprint")
        self.assertEqual(rows["graynote"]["trust_tier"], "gray")

    def test_reimport_updates_not_duplicates(self):
        bibsync.import_bib(self.conn, self.bib_path)
        stats = bibsync.import_bib(self.conn, self.bib_path)
        self.assertEqual(stats["updated"], 4)
        self.assertEqual(stats["imported"], 0)
        self.assertEqual(len(db.list_evidence(self.conn)), 4)

    def test_citation_built(self):
        bibsync.import_bib(self.conn, self.bib_path)
        rows = {r["source_id"]: r for r in db.list_evidence(self.conn)}
        self.assertIn("Smith", rows["smith2020"]["citation"])
        self.assertIn("2020", rows["smith2020"]["citation"])

    def test_export_only_verified_by_default(self):
        bibsync.import_bib(self.conn, self.bib_path)
        out = os.path.join(self.tmp, "verified.bib")
        result = bibsync.export_bib(self.conn, out)
        self.assertEqual(result["records"], 0)
        self.assertEqual(result["skipped"], 4)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("только проверенные: да", content)

    def test_export_after_verification(self):
        bibsync.import_bib(self.conn, self.bib_path)
        db.upsert_evidence(self.conn, {"source_id": "smith2020", "metadata_verified": 1,
                                       "trust_tier": "primary"})
        out = os.path.join(self.tmp, "verified.bib")
        result = bibsync.export_bib(self.conn, out)
        self.assertEqual(result["records"], 1)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("@article{smith2020,", content)
        self.assertIn("doi = {10.1000/xyz123}", content)
        self.assertIn("journal = {Journal of Open Research}", content)
        self.assertIn("trust: primary", content)

    def test_export_all_includes_unverified(self):
        bibsync.import_bib(self.conn, self.bib_path)
        out = os.path.join(self.tmp, "all.bib")
        result = bibsync.export_bib(self.conn, out, only_verified=False)
        self.assertEqual(result["records"], 4)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("@misc{graynote,", fh.read())

    def test_export_escapes_special_characters(self):
        db.upsert_evidence(self.conn, {"source_id": "amp2020", "title": "A & B {test}",
                                       "author": "X, Y", "year": "2020",
                                       "metadata_verified": 1, "type": "article"})
        out = os.path.join(self.tmp, "esc.bib")
        bibsync.export_bib(self.conn, out)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("A \\& B \\{test\\}", content)

    def test_project_filter(self):
        bibsync.import_bib(self.conn, self.bib_path, project="demo")
        db.upsert_evidence(self.conn, {"source_id": "other2020", "project": "other",
                                       "metadata_verified": 1, "title": "T", "type": "article"})
        out = os.path.join(self.tmp, "proj.bib")
        result = bibsync.export_bib(self.conn, out, project="demo")
        self.assertEqual(result["records"], 0)
        result = bibsync.export_bib(self.conn, out, project="other")
        self.assertEqual(result["records"], 1)


if __name__ == "__main__":
    unittest.main()
