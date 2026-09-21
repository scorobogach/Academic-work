"""Тесты G4: verbatim-сверка цитаты с источником."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import db, quote as quote_mod  # noqa: E402

SOURCE_TEXT = (
    "Reproducibility in computational experiments.\n"
    "We report that 68% of the analysed pipelines failed to reproduce on the first attempt.\n"
    "The effect was stable across disciplines.\n"
)

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 140>>\n"
    b"stream\n"
    b"BT /F1 12 Tf 72 720 Td (Reproducibility in computational experiments.) Tj "
    b"0 -20 Td (Sixty-eight percent of pipelines failed.) Tj ET\n"
    b"endstream\n"
    b"endobj\n"
    b"trailer<</Root 1 0 R>>\n"
)


class QuoteNormalizationTest(unittest.TestCase):
    def test_quotes_and_dashes_normalized(self):
        self.assertEqual(quote_mod.normalize("«цитата» — текст"),
                         quote_mod.normalize('"цитата" - текст'))

    def test_hyphenation_removed(self):
        self.assertEqual(quote_mod.normalize("воспроиз-\nводимость"),
                         quote_mod.normalize("воспроизводимость"))

    def test_letters_only_mode(self):
        self.assertEqual(quote_mod.normalize("Мы наблюдали, что 68% пайплайнов!",
                                             letters_only=True),
                         "мы наблюдали что 68 пайплайнов")

    def test_locator_parsed(self):
        self.assertEqual(quote_mod.parse_locator("p. 7"), 7)
        self.assertEqual(quote_mod.parse_locator("с. 12"), 12)
        self.assertEqual(quote_mod.parse_locator("страница 3"), 3)
        self.assertIsNone(quote_mod.parse_locator(""))


class QuoteCompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-quote-")
        self.source = os.path.join(self.tmp, "smith2020.txt")
        with open(self.source, "w", encoding="utf-8") as fh:
            fh.write(SOURCE_TEXT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exact_match(self):
        check = quote_mod.check_quote(
            "We report that 68% of the analysed pipelines failed to reproduce on the first attempt.",
            self.source)
        self.assertEqual(check.match, "exact")
        self.assertEqual(check.ratio, 1.0)
        self.assertEqual(check.page, 1)

    def test_normalized_match_after_typography(self):
        quote_text = "We report that 68% of the analysed pipelines failed to reproduce on the first attempt."
        check = quote_mod.check_quote(quote_text, self.source)
        self.assertIn(check.match, ("exact", "normalized"))

    def test_mismatch_reported(self):
        check = quote_mod.check_quote(
            "We report that 42% of the pipelines reproduced immediately without any problems.",
            self.source)
        self.assertEqual(check.match, "mismatch")
        self.assertLess(check.ratio, 0.8)

    def test_missing_file_is_unverifiable(self):
        check = quote_mod.check_quote("любая цитата", os.path.join(self.tmp, "nope.txt"))
        self.assertEqual(check.match, "unverifiable")
        self.assertIn("недоступен", check.note)

    def test_locator_mismatch_noted(self):
        check = quote_mod.check_quote(
            "We report that 68% of the analysed pipelines failed to reproduce on the first attempt.",
            self.source, locator="с. 9")
        self.assertEqual(check.page, 1)
        self.assertIn("в локаторе указана стр. 9", check.note)

    def test_empty_quote_is_mismatch(self):
        check = quote_mod.check_quote("   ", self.source)
        self.assertEqual(check.match, "mismatch")


class PdfExtractionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-pdf-")
        self.pdf = os.path.join(self.tmp, "paper.pdf")
        with open(self.pdf, "wb") as fh:
            fh.write(MINIMAL_PDF)
        self.garbage = os.path.join(self.tmp, "scan.pdf")
        with open(self.garbage, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_text_extracted_from_minimal_pdf(self):
        pages, method = quote_mod.source_pages(self.pdf)
        self.assertEqual(method, "pdf-stdlib")
        self.assertIn("Reproducibility in computational experiments.", "".join(pages))

    def test_quote_matched_in_pdf(self):
        check = quote_mod.check_quote("Reproducibility in computational experiments.", self.pdf)
        self.assertIn(check.match, ("exact", "normalized"))

    def test_pdf_without_text_layer_is_unverifiable(self):
        check = quote_mod.check_quote("anything at all", self.garbage)
        self.assertEqual(check.match, "unverifiable")
        self.assertIn("текстовый слой", check.note)


class ClaimAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-claims-")
        self.conn = db.connect(os.path.join(self.tmp, "journal.db"))
        self.source = os.path.join(self.tmp, "smith2020.txt")
        with open(self.source, "w", encoding="utf-8") as fh:
            fh.write(SOURCE_TEXT)
        db.upsert_evidence(self.conn, {"source_id": "S-01", "citation": "Smith 2020",
                                       "local_path": self.source, "trust_tier": "primary"})
        db.upsert_claim(self.conn, {
            "claim_id": "C-01", "project": "demo", "text": "68% пайплайнов не воспроизводятся",
            "assertion_type": "empirical", "source_id": "S-01",
            "quote": "We report that 68% of the analysed pipelines failed to reproduce on the first attempt.",
            "quote_locator": "с. 1", "status": "open"})
        db.upsert_claim(self.conn, {
            "claim_id": "C-02", "project": "demo", "text": "эффект зависел от дисциплины",
            "assertion_type": "empirical", "source_id": "S-01",
            "quote": "The effect depended on the discipline and the journal ranking.",
            "quote_locator": "с. 1", "status": "open"})
        db.upsert_claim(self.conn, {
            "claim_id": "C-03", "project": "demo", "text": "утверждение без цитаты",
            "assertion_type": "empirical", "source_id": "S-01", "status": "open"})

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_g4_updates_verbatim_match(self):
        result = quote_mod.check_claims(self.conn, project="demo")
        claims = {c["claim_id"]: c for c in db.list_claims(self.conn)}
        self.assertEqual(claims["C-01"]["verbatim_match"], "exact")
        self.assertIn(claims["C-02"]["verbatim_match"], ("mismatch", "fuzzy"))
        self.assertTrue(result.blocking)
        self.assertTrue(any(f.code == "QUOTE-MISSING" for f in result.findings))

    def test_g4_real_finding_is_error(self):
        result = quote_mod.check_claims(self.conn, project="demo")
        errors = [f for f in result.findings if f.severity == "error"]
        self.assertGreaterEqual(len(errors), 2)
        payload = json.loads(result.to_json())
        self.assertEqual(payload["gate"], "G4_claim_alignment")


if __name__ == "__main__":
    unittest.main()
