"""Тесты ядра журнала. Запуск: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from academic_journal import db  # noqa: E402
from academic_journal.journal import Journal, ulid  # noqa: E402


class JournalChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-journal-")
        self.journal = Journal(self.tmp, session="s-test", project="p-test")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_and_verify(self):
        r1 = self.journal.append("message", {"text": "первое сообщение"})
        r2 = self.journal.append("message", {"text": "второе сообщение"})
        self.assertEqual(r2["prev"], r1["hash"])
        status = self.journal.verify()
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["records"], 2)

    def test_chain_detects_tampering(self):
        self.journal.append("message", {"text": "оригинал"})
        self.journal.append("message", {"text": "второе"})
        path = os.path.join(self.tmp, self.journal.tail(2)[0]["ts"][:7] + ".jsonl")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        rec = json.loads(lines[0])
        rec["payload"]["text"] = "подменённый текст"
        lines[0] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        status = self.journal.verify()
        self.assertFalse(status["ok"])
        self.assertEqual(status["error"], "hash mismatch")

    def test_chain_detects_deletion(self):
        self.journal.append("message", {"text": "один"})
        self.journal.append("message", {"text": "два"})
        self.journal.append("message", {"text": "три"})
        path = os.path.join(self.tmp, self.journal.tail(3)[0]["ts"][:7] + ".jsonl")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join([lines[0], lines[2]]) + "\n")
        status = self.journal.verify()
        self.assertFalse(status["ok"])
        self.assertEqual(status["error"], "chain broken")

    def test_manifest_and_search(self):
        self.journal.append("note", {"topic": "дизайн выборки"})
        manifest = self.journal.manifest()
        self.assertEqual(len(manifest), 1)
        self.assertTrue(all(len(v) == 64 for v in manifest.values()))
        self.assertEqual(len(self.journal.search("выборки")), 1)

    def test_ulid_monotonic(self):
        ids = [ulid() for _ in range(50)]
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(sorted(ids), sorted(ids))


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-db-")
        self.conn = db.connect(os.path.join(self.tmp, "journal.db"))

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_claim_lifecycle(self):
        db.upsert_claim(self.conn, {"claim_id": "C-001", "text": "Выборка составила 42 участника",
                                    "verdict": "unverifiable", "status": "open", "project": "p"})
        db.upsert_claim(self.conn, {"claim_id": "C-001", "verdict": "supported", "status": "resolved",
                                    "source_id": "S-01", "quote_locator": "p.7", "project": "p"})
        claims = db.list_claims(self.conn)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["verdict"], "supported")
        self.assertEqual(claims[0]["status"], "resolved")
        self.assertEqual(db.claim_summary(self.conn)["resolved"], 1)

    def test_invalid_verdict_rejected(self):
        with self.assertRaises(ValueError):
            db.upsert_claim(self.conn, {"claim_id": "C-002", "text": "x", "verdict": "maybe"})

    def test_claim_requires_id(self):
        with self.assertRaises(ValueError):
            db.upsert_claim(self.conn, {"text": "без идентификатора"})

    def test_evidence_and_decisions(self):
        db.upsert_evidence(self.conn, {"source_id": "S-01", "doi": "10.1000/xyz", "year": "2024",
                                       "trust_tier": "primary"})
        self.assertEqual(len(db.list_evidence(self.conn)), 1)
        db.upsert_decision(self.conn, {"decision_id": "D-01", "question": "Какой дизайн?",
                                       "chosen": "лонгитюд", "status": "active"})
        self.assertEqual(len(db.list_decisions(self.conn)), 1)

    def test_gate_recording(self):
        db.record_gate(self.conn, {"run_id": "R-1", "gate": "G0_hygiene", "verdict": "fail", "blocking": True})
        rows = db.latest_gates(self.conn)
        self.assertEqual(rows[0]["gate"], "G0_hygiene")
        self.assertTrue(rows[0]["blocking"])

    def test_fts_search(self):
        db.upsert_claim(self.conn, {"claim_id": "C-10", "text": "результаты воспроизводятся на открытых данных"})
        found = db.list_claims(self.conn)
        self.assertIn("воспроизводятся", found[0]["text"])


if __name__ == "__main__":
    unittest.main()
