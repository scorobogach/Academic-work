#!/usr/bin/env python3
"""MCP-сервер журнала Academic-work (stdio, JSON-RPC 2.0, без внешних зависимостей).

Запуск: python3 scripts/mcp_server.py
Переменные окружения:
  ACADEMIC_DB          — путь к SQLite (по умолчанию ~/.academic/journal.db)
  ACADEMIC_JOURNAL_DIR — каталог journal/YYYY-MM.jsonl (по умолчанию ~/.academic/journal)
  ACADEMIC_PROJECT     — проект по умолчанию
  ACADEMIC_SESSION     — идентификатор сессии (рекомендуется задавать в конфиге агента)
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from academic_journal import db, gates as gates_mod, resume as resume_mod  # noqa: E402
from academic_journal.journal import Journal, ulid  # noqa: E402

SERVER_INFO = {"name": "academic-journal", "version": "0.1.0"}
SUPPORTED_PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[-1]

TOOLS = [
    {
        "name": "journal.append",
        "description": "Добавить событие в неизменяемый журнал (хеш-цепочка). Обязательно для сообщений, вызовов инструментов и прогонов гейтов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "message|tool_call|tool_result|file|claim|evidence|decision|gate_run|note|signoff"},
                "payload": {"type": "object", "description": "содержимое события"},
                "actor": {"type": "string", "description": "human|agent:claude-code|tool:citecheck|..."},
                "session": {"type": "string"},
                "project": {"type": "string"},
                "refs": {"type": "object", "description": "ссылки: parent, artifact, claim_id, source_id"},
            },
            "required": ["type", "payload"],
        },
    },
    {
        "name": "journal.search",
        "description": "Полнотекстовый поиск по журналу (SQLite FTS5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "type": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {"name": "journal.verify", "description": "Проверить хеш-цепочку журнала (целостность).", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "claims.upsert",
        "description": "Записать/обновить проверяемое утверждение.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"}, "project": {"type": "string"}, "section": {"type": "string"},
                "text": {"type": "string"}, "assertion_type": {"type": "string"},
                "source_id": {"type": "string"}, "quote": {"type": "string"}, "quote_locator": {"type": "string"},
                "verbatim_match": {"type": "string"}, "verdict": {"type": "string"},
                "checked_by": {"type": "string"}, "evidence_sha": {"type": "string"},
                "gate_run_id": {"type": "string"}, "status": {"type": "string"},
            },
            "required": ["claim_id", "text"],
        },
    },
    {
        "name": "claims.list",
        "description": "Список утверждений с фильтрами по статусу/вердикту.",
        "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "verdict": {"type": "string"}, "project": {"type": "string"}}},
    },
    {"name": "claims.summary", "description": "Счётчики утверждений по статусам.", "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}}},
    {
        "name": "evidence.upsert",
        "description": "Записать/обновить источник (evidence).",
        "inputSchema": {"type": "object", "properties": {"source_id": {"type": "string"}, "doi": {"type": "string"}, "pmid": {"type": "string"}, "url": {"type": "string"}, "citation": {"type": "string"}, "year": {"type": "string"}, "type": {"type": "string"}, "trust_tier": {"type": "string"}, "retriever": {"type": "string"}, "pdf_sha256": {"type": "string"}, "local_path": {"type": "string"}, "access": {"type": "string"}, "license": {"type": "string"}, "metadata_verified": {"type": "integer"}, "notes": {"type": "string"}}, "required": ["source_id"]},
    },
    {"name": "evidence.list", "description": "Список источников.", "inputSchema": {"type": "object", "properties": {"trust_tier": {"type": "string"}}}},
    {
        "name": "decisions.upsert",
        "description": "Записать/обновить решение (decision log).",
        "inputSchema": {"type": "object", "properties": {"decision_id": {"type": "string"}, "project": {"type": "string"}, "question": {"type": "string"}, "chosen": {"type": "string"}, "rationale": {"type": "string"}, "rejected": {"type": "string"}, "scope": {"type": "string"}, "supersedes": {"type": "string"}, "evidence_refs": {}, "journal_refs": {}, "status": {"type": "string"}, "reopen_if": {"type": "string"}}, "required": ["decision_id", "question"]},
    },
    {"name": "decisions.list", "description": "Активные решения.", "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "default": "active"}, "project": {"type": "string"}}}},
    {
        "name": "gates.record",
        "description": "Зафиксировать результат прогона гейта (включая модельные G2/G4/G6/G7).",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "project": {"type": "string"}, "gate": {"type": "string"}, "verdict": {"type": "string"}, "blocking": {"type": "boolean"}, "inputs_sha": {"type": "string"}, "checks": {}, "cost_usd": {"type": "number"}, "duration_ms": {"type": "integer"}, "artifact": {"type": "string"}, "human_ack": {"type": "boolean"}}, "required": ["gate", "verdict"]},
    },
    {"name": "gates.latest", "description": "Последние прогоны гейтов.", "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "limit": {"type": "integer", "default": 20}}}},
    {
        "name": "resume.build",
        "description": "Собрать контекст-пак для старта сессии (решения, блокирующие утверждения, гейты, следующие действия).",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "limit_events": {"type": "integer", "default": 15}}},
    },
    {
        "name": "text.check",
        "description": "Детерминированные проверки текста: G0 (невидимые символы) и G1 (стиль/вода).",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "gate": {"type": "string", "default": "all"}}, "required": ["text"]},
    },
]


class Store:
    def __init__(self):
        self.db_path = _env_path("ACADEMIC_DB", "~/.academic/journal.db")
        self.journal_dir = _env_path("ACADEMIC_JOURNAL_DIR", "~/.academic/journal")
        self.project = os.environ.get("ACADEMIC_PROJECT", "default")
        self.session = os.environ.get("ACADEMIC_SESSION", "s-" + time.strftime("%Y%m%d"))
        self.conn = db.connect(self.db_path)
        self.journal = Journal(self.journal_dir, session=self.session, project=self.project,
                               indexer=lambda rec: db.index_event(self.conn, rec))


def _env_path(name: str, default: str) -> str:
    raw = os.environ.get(name, default)
    if raw.startswith("sqlite:///"):
        raw = raw[len("sqlite:///"):]
        if not raw.startswith("/"):
            raw = "/" + raw
    elif raw.startswith("sqlite://"):
        raw = raw[len("sqlite://"):]
    return os.path.expanduser(raw)


STORE: Store | None = None


def store() -> Store:
    global STORE
    if STORE is None:
        STORE = Store()
    return STORE


def handle_tool(name: str, args: dict):
    st = store()
    if name == "journal.append":
        rec = st.journal.append(
            type=args["type"], payload=args.get("payload", {}), actor=args.get("actor", "agent:mcp"),
            session=args.get("session"), project=args.get("project"), refs=args.get("refs"),
        )
        return {"id": rec["id"], "hash": rec["hash"], "prev": rec["prev"], "ts": rec["ts"],
                "journal": os.path.join(st.journal.root, rec["ts"][:7] + ".jsonl")}
    if name == "journal.search":
        return {"results": db.search_events(st.conn, args["query"], int(args.get("limit", 20)),
                                            args.get("type"), args.get("project"))}
    if name == "journal.verify":
        return st.journal.verify()
    if name == "claims.upsert":
        return db.upsert_claim(st.conn, args)
    if name == "claims.list":
        return {"claims": db.list_claims(st.conn, args.get("status"), args.get("verdict"), args.get("project"))}
    if name == "claims.summary":
        return db.claim_summary(st.conn, args.get("project"))
    if name == "evidence.upsert":
        return db.upsert_evidence(st.conn, args)
    if name == "evidence.list":
        return {"evidence": db.list_evidence(st.conn, args.get("trust_tier"))}
    if name == "decisions.upsert":
        return db.upsert_decision(st.conn, args)
    if name == "decisions.list":
        return {"decisions": db.list_decisions(st.conn, args.get("status", "active"), args.get("project"))}
    if name == "gates.record":
        args.setdefault("run_id", ulid())
        return db.record_gate(st.conn, args)
    if name == "gates.latest":
        return {"gates": db.latest_gates(st.conn, args.get("project"), int(args.get("limit", 20)))}
    if name == "resume.build":
        text = resume_mod.build(st.conn, args.get("project") or st.project, int(args.get("limit_events", 15)))
        return {"markdown": text}
    if name == "text.check":
        text = args["text"]
        gate = args.get("gate", "all")
        out = {}
        if gate in ("all", "G0", "hygiene"):
            out["G0"] = gates_mod.check_hygiene(text).to_dict()
        if gate in ("all", "G1", "style"):
            out["G1"] = gates_mod.check_style(text).to_dict()
        return out
    raise KeyError(name)


def _result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload, "isError": False}


def _error(message, code=-32602):
    return {"content": [{"type": "text", "text": message}], "isError": True,
            "error": {"code": code, "message": message}}


def handle(req: dict):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion") or LATEST_PROTOCOL
        protocol = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": True}, "logging": {}},
            "serverInfo": SERVER_INFO,
            "instructions": "Журнал научной работы. Правило: ни одного фактического утверждения без источника; "
                            "каждое действие фиксируется в журнале; саммари не заменяет журнал."}}
    if method == "notifications/initialized" or (method or "").startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": _result(handle_tool(name, args))}
        except KeyError:
            return {"jsonrpc": "2.0", "id": rid, "result": _error("неизвестный инструмент: %s" % name, -32601)}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": _error("%s: %s" % (type(exc).__name__, exc))}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
