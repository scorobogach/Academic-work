"""Смоук-тест CLI: команды из README и Makefile должны работать."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "scripts", "journal_cli.py")
INSTALLER = os.path.join(ROOT, "scripts", "install_mcp.py")


def run(args, env=None):
    proc = subprocess.run([sys.executable, CLI] + args, capture_output=True, text=True,
                          env=env or os.environ.copy(), timeout=60)
    return proc


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-cli-")
        self.db = os.path.join(self.tmp, "journal.db")
        self.journal = os.path.join(self.tmp, "journal")
        self.base = ["--db", self.db, "--journal-dir", self.journal, "--project", "demo"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_and_append_search_verify(self):
        self.assertEqual(run(["init"] + self.base).returncode, 0, run(["init"] + self.base).stderr)
        r = run(["append", "--type", "note", "--payload", '{"text":"обсуждали дизайн выборки"}'] + self.base)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hash", r.stdout)
        s = run(["search", "--query", "выборки", "--indexed"] + self.base)
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(len(json.loads(s.stdout)), 1)
        v = run(["verify"] + self.base)
        self.assertTrue(json.loads(v.stdout)["ok"])

    def test_gates_on_example(self):
        manuscript = os.path.join(ROOT, "examples", "minimal", "main.tex")
        bib = os.path.join(ROOT, "examples", "minimal", "refs.bib")
        values = os.path.join(ROOT, "examples", "minimal", "analysis", "results.json")
        run(["init"] + self.base)
        for gate, extra in (("G0", []), ("G1", [])):
            r = run(["run-gate", gate, "--file", manuscript, "--record"] + self.base)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = run(["run-gate", "G3", "--tex", manuscript, "--bib", bib] + self.base)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(["run-gate", "G5", "--file", manuscript, "--values", values] + self.base)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = run(["resume"] + self.base)
        self.assertIn("G0_hygiene", res.stdout)
        self.assertIn("G1_style", res.stdout)

    def test_slop_demo_fails_gate(self):
        demo = os.path.join(ROOT, "examples", "slop-demo.md")
        r = run(["run-gate", "G1", "--file", demo] + self.base)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNSOURCED", r.stdout)

    def test_extract(self):
        r = run(["extract", "--tex", os.path.join(ROOT, "examples", "minimal", "main.tex"),
                 "--bib", os.path.join(ROOT, "examples", "minimal", "refs.bib")])
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("ivanov2024", data["cite_keys"])
        self.assertTrue(any(n["value"] == "42" for n in data["numbers"]))

    def test_installer_dry_run(self):
        for agent in ("claude", "cursor", "codex"):
            r = subprocess.run([sys.executable, INSTALLER, "--agent", agent, "--dry-run",
                                "--project-root", self.tmp, "--db", self.db],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("academic-journal", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".mcp.json")))

    def test_installer_claude_writes_config(self):
        r = subprocess.run([sys.executable, INSTALLER, "--agent", "claude", "--project-root", self.tmp,
                            "--db", self.db, "--journal-dir", self.journal],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(self.tmp, ".mcp.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("academic-journal", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["academic-journal"]["env"]["ACADEMIC_DB"], self.db)


if __name__ == "__main__":
    unittest.main()
