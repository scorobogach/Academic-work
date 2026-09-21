"""Смоук-тест MCP-сервера: рукопожатие, список инструментов, вызов инструментов."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "scripts", "mcp_server.py")


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-mcp-")
        self.env = dict(os.environ)
        self.env.update({
            "ACADEMIC_DB": os.path.join(self.tmp, "journal.db"),
            "ACADEMIC_JOURNAL_DIR": os.path.join(self.tmp, "journal"),
            "ACADEMIC_PROJECT": "p-test",
            "ACADEMIC_SESSION": "s-test",
            "PYTHONPATH": os.path.join(ROOT, "scripts"),
        })
        self.proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=self.env, text=True, bufsize=1)

    def tearDown(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rpc(self, method, params=None, rid=1):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            # важно: читать stderr только в случае ошибки, иначе блокируемся на EOF
            self.proc.terminate()
            err = self.proc.stderr.read()
            self.fail("сервер не ответил на %s. stderr: %s" % (method, err[-2000:] or "(пусто)"))
        return json.loads(line)

    def _notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": {}}) + "\n")
        self.proc.stdin.flush()

    def test_handshake_and_tools(self):
        res = self._rpc("initialize", {"protocolVersion": "2025-06-18",
                                       "capabilities": {}, "clientInfo": {"name": "pytest"}})
        self.assertEqual(res["result"]["serverInfo"]["name"], "academic-journal")
        self._notify("notifications/initialized")
        tools = self._rpc("tools/list", {}, rid=2)["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("journal.append", names)
        self.assertIn("claims.upsert", names)
        self.assertIn("resume.build", names)

    def test_append_search_verify(self):
        self._rpc("initialize", {"protocolVersion": "2025-06-18"})
        self._notify("notifications/initialized")
        res = self._rpc("tools/call", {"name": "journal.append",
                                       "arguments": {"type": "message",
                                                     "payload": {"text": "обсуждали дизайн выборки"},
                                                     "actor": "human"}}, rid=3)
        payload = res["result"]["structuredContent"]
        self.assertTrue(payload["hash"])
        found = self._rpc("tools/call", {"name": "journal.search",
                                         "arguments": {"query": "выборки"}}, rid=4)
        self.assertGreaterEqual(len(found["result"]["structuredContent"]["results"]), 1)
        verify = self._rpc("tools/call", {"name": "journal.verify", "arguments": {}}, rid=5)
        self.assertTrue(verify["result"]["structuredContent"]["ok"])

    def test_claim_and_resume(self):
        self._rpc("initialize", {"protocolVersion": "2025-06-18"})
        self._notify("notifications/initialized")
        self._rpc("tools/call", {"name": "claims.upsert", "arguments": {
            "claim_id": "C-1", "text": "Метод воспроизводится на открытых данных",
            "verdict": "unverifiable", "status": "blocking", "project": "p-test"}}, rid=3)
        out = self._rpc("tools/call", {"name": "resume.build", "arguments": {"project": "p-test"}}, rid=4)
        markdown = out["result"]["structuredContent"]["markdown"]
        self.assertIn("C-1", markdown)
        self.assertIn("SIGN-OFF", markdown)

    def test_unknown_tool_is_error(self):
        self._rpc("initialize", {"protocolVersion": "2025-06-18"})
        self._notify("notifications/initialized")
        res = self._rpc("tools/call", {"name": "нет.такого", "arguments": {}}, rid=3)
        self.assertTrue(res["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
