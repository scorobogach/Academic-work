"""SQLite-слой: индекс журнала + реестры evidence / claims / decisions / gates.

Журнал (JSONL) остаётся источником истины; SQLite — поисковый индекс и
структурированные реестры, которые удобно редактировать и версионировать в git.

Схема намеренно простая и переносимая: тот же SQL работает и в PostgreSQL
после минимальной адаптации (см. docs/storage-decision.md).
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS events (
    id       TEXT PRIMARY KEY,
    ts       TEXT NOT NULL,
    session  TEXT,
    project  TEXT,
    actor    TEXT,
    type     TEXT NOT NULL,
    refs     TEXT NOT NULL DEFAULT '{}',
    payload  TEXT NOT NULL,
    prev     TEXT NOT NULL DEFAULT '',
    hash     TEXT NOT NULL,
    seq      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    id UNINDEXED, project, actor, type, text, tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS evidence (
    source_id   TEXT PRIMARY KEY,
    doi         TEXT,
    pmid        TEXT,
    url         TEXT,
    citation    TEXT,
    year        TEXT,
    type        TEXT,
    trust_tier  TEXT,
    retrieved_at TEXT,
    retriever   TEXT,
    pdf_sha256  TEXT,
    local_path  TEXT,
    access      TEXT,
    license     TEXT,
    metadata_verified INTEGER DEFAULT 0,
    notes       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id     TEXT PRIMARY KEY,
    project      TEXT,
    section      TEXT,
    text         TEXT NOT NULL,
    assertion_type TEXT,
    source_id    TEXT,
    quote        TEXT,
    quote_locator TEXT,
    verbatim_match TEXT,
    verdict      TEXT NOT NULL DEFAULT 'unverifiable',
    checked_by   TEXT,
    checked_at   TEXT,
    evidence_sha TEXT,
    gate_run_id  TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_verdict ON claims(verdict);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id  TEXT PRIMARY KEY,
    project      TEXT,
    date         TEXT,
    question     TEXT NOT NULL,
    chosen       TEXT,
    rationale    TEXT,
    rejected     TEXT,
    scope        TEXT,
    supersedes   TEXT,
    evidence_refs TEXT,
    journal_refs TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    reopen_if    TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS gates (
    run_id      TEXT PRIMARY KEY,
    project     TEXT,
    gate        TEXT NOT NULL,
    started_at  TEXT,
    verdict     TEXT NOT NULL,
    blocking    INTEGER DEFAULT 0,
    inputs_sha  TEXT,
    checks      TEXT NOT NULL DEFAULT '[]',
    cost_usd    REAL DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    artifact    TEXT,
    human_ack   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gates_gate ON gates(gate);
CREATE INDEX IF NOT EXISTS idx_gates_started ON gates(started_at);
"""


def connect(path: str) -> sqlite3.Connection:
    path = os.path.expanduser(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------- журнал ----------

def index_event(conn: sqlite3.Connection, rec: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO events (id, ts, session, project, actor, type, refs, payload, prev, hash, seq)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            rec.get("id"),
            rec.get("ts"),
            rec.get("session"),
            rec.get("project"),
            rec.get("actor"),
            rec.get("type"),
            json.dumps(rec.get("refs", {}), ensure_ascii=False),
            json.dumps(rec.get("payload", {}), ensure_ascii=False),
            rec.get("prev", ""),
            rec.get("hash", ""),
            None,
        ),
    )
    conn.execute("DELETE FROM events_fts WHERE id = ?", (rec.get("id"),))
    conn.execute(
        "INSERT INTO events_fts (id, project, actor, type, text) VALUES (?,?,?,?,?)",
        (
            rec.get("id"),
            rec.get("project", ""),
            rec.get("actor", ""),
            rec.get("type", ""),
            json.dumps(rec.get("payload", {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def search_events(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    type: Optional[str] = None,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # подзапрос по FTS: MATCH обязан ссылаться на имя таблицы, а не на алиас
    sql = ("SELECT e.* FROM events e WHERE e.id IN "
           "(SELECT id FROM events_fts WHERE events_fts MATCH ?)")
    params: List[Any] = [_fts_query(query)]
    if type:
        sql += " AND e.type = ?"
        params.append(type)
    if project:
        sql += " AND e.project = ?"
        params.append(project)
    sql += " ORDER BY e.ts DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # запасной путь: LIKE без учёта регистра (важно для кириллицы)
        rows = conn.execute(
            "SELECT * FROM events WHERE lower(payload) LIKE lower(?) ORDER BY ts DESC LIMIT ?",
            ("%" + query + "%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _fts_query(query: str) -> str:
    """Безопасная обёртка: пользовательский ввод как одну FTS-фразу."""
    cleaned = (query or "").replace('"', " ").strip()
    if not cleaned:
        cleaned = '""'
    return '"%s"' % cleaned


# ---------- evidence ----------

EVIDENCE_FIELDS = [
    "source_id", "doi", "pmid", "url", "citation", "year", "type", "trust_tier",
    "retrieved_at", "retriever", "pdf_sha256", "local_path", "access", "license",
    "metadata_verified", "notes",
]


def _existing(conn: sqlite3.Connection, table: str, key_field: str, key: Any) -> Dict[str, Any]:
    """Существующая запись — чтобы обновление по id не требовало повторно передавать все поля."""
    if key is None:
        return {}
    cur = conn.execute("SELECT * FROM %s WHERE %s = ?" % (table, key_field), (key,))
    found = cur.fetchone()
    return dict(found) if found else {}


def _merge(data: Dict[str, Any], fields, conn, table: str, key_field: str) -> Dict[str, Any]:
    key = data.get(key_field)
    previous = _existing(conn, table, key_field, key)
    row = {}
    for field_name in fields:
        value = data.get(field_name, None)
        if value is None:
            value = previous.get(field_name, None)
        row[field_name] = value
    return row


def upsert_evidence(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    row = _merge(data, EVIDENCE_FIELDS, conn, "evidence", "source_id")
    if not row.get("source_id"):
        raise ValueError("source_id обязателен")
    row["updated_at"] = data.get("updated_at") or _now()
    cols = list(row.keys())
    conn.execute(
        "INSERT INTO evidence (%s) VALUES (%s) ON CONFLICT(source_id) DO UPDATE SET %s"
        % (
            ",".join(cols),
            ",".join("?" * len(cols)),
            ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "source_id"),
        ),
        [row[c] for c in cols],
    )
    conn.commit()
    return row


def list_evidence(conn: sqlite3.Connection, trust_tier: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM evidence"
    params: List[Any] = []
    if trust_tier:
        sql += " WHERE trust_tier = ?"
        params.append(trust_tier)
    sql += " ORDER BY source_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- claims ----------

CLAIM_FIELDS = [
    "claim_id", "project", "section", "text", "assertion_type", "source_id",
    "quote", "quote_locator", "verbatim_match", "verdict", "checked_by",
    "checked_at", "evidence_sha", "gate_run_id", "status",
]

VALID_VERDICTS = {
    "supported", "partially_supported", "not_addressed", "unverifiable",
    "self_computed", "contradicted",
}
VALID_STATUS = {"open", "blocking", "resolved", "dropped"}


def upsert_claim(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    row = _merge(data, CLAIM_FIELDS, conn, "claims", "claim_id")
    if not row.get("claim_id"):
        raise ValueError("claim_id обязателен")
    if not row.get("text"):
        raise ValueError("text утверждения обязателен")
    verdict = row.get("verdict") or "unverifiable"
    if verdict not in VALID_VERDICTS:
        raise ValueError("недопустимый verdict: %s" % verdict)
    status = row.get("status") or "open"
    if status not in VALID_STATUS:
        raise ValueError("недопустимый status: %s" % status)
    row["verdict"], row["status"] = verdict, status
    row["updated_at"] = data.get("updated_at") or _now()
    cols = list(row.keys())
    conn.execute(
        "INSERT INTO claims (%s) VALUES (%s) ON CONFLICT(claim_id) DO UPDATE SET %s"
        % (
            ",".join(cols),
            ",".join("?" * len(cols)),
            ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "claim_id"),
        ),
        [row[c] for c in cols],
    )
    conn.commit()
    return row


def list_claims(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    verdict: Optional[str] = None,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM claims WHERE 1=1"
    params: List[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if verdict:
        sql += " AND verdict = ?"
        params.append(verdict)
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY claim_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def claim_summary(conn: sqlite3.Connection, project: Optional[str] = None) -> Dict[str, int]:
    sql = "SELECT status, COUNT(*) c FROM claims"
    params: List[Any] = []
    if project:
        sql += " WHERE project = ?"
        params.append(project)
    sql += " GROUP BY status"
    out = {s: 0 for s in VALID_STATUS}
    for r in conn.execute(sql, params).fetchall():
        out[r["status"]] = r["c"]
    return out


# ---------- decisions ----------

DECISION_FIELDS = [
    "decision_id", "project", "date", "question", "chosen", "rationale", "rejected",
    "scope", "supersedes", "evidence_refs", "journal_refs", "status", "reopen_if",
]


def upsert_decision(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    row = _merge(data, DECISION_FIELDS, conn, "decisions", "decision_id")
    if not row.get("decision_id"):
        raise ValueError("decision_id обязателен")
    if not row.get("question"):
        raise ValueError("question обязателен")
    for key in ("evidence_refs", "journal_refs"):
        if isinstance(row.get(key), (list, dict)):
            row[key] = json.dumps(row[key], ensure_ascii=False)
    row["status"] = row.get("status") or "active"
    row["updated_at"] = data.get("updated_at") or _now()
    cols = list(row.keys())
    conn.execute(
        "INSERT INTO decisions (%s) VALUES (%s) ON CONFLICT(decision_id) DO UPDATE SET %s"
        % (
            ",".join(cols),
            ",".join("?" * len(cols)),
            ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "decision_id"),
        ),
        [row[c] for c in cols],
    )
    conn.commit()
    return row


def list_decisions(conn: sqlite3.Connection, status: str = "active", project: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM decisions WHERE status = ?"
    params: List[Any] = [status]
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY decision_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- gates ----------

def record_gate(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "run_id": data.get("run_id"),
        "project": data.get("project"),
        "gate": data.get("gate"),
        "started_at": data.get("started_at") or _now(),
        "verdict": data.get("verdict", "fail"),
        "blocking": int(bool(data.get("blocking", 0))),
        "inputs_sha": data.get("inputs_sha", ""),
        "checks": json.dumps(data.get("checks", []), ensure_ascii=False),
        "cost_usd": float(data.get("cost_usd", 0) or 0),
        "duration_ms": int(data.get("duration_ms", 0) or 0),
        "artifact": data.get("artifact", ""),
        "human_ack": int(bool(data.get("human_ack", 0))),
    }
    if not row["run_id"] or not row["gate"]:
        raise ValueError("run_id и gate обязательны")
    conn.execute(
        "INSERT OR REPLACE INTO gates (run_id, project, gate, started_at, verdict, blocking,"
        " inputs_sha, checks, cost_usd, duration_ms, artifact, human_ack)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [row[k] for k in ("run_id", "project", "gate", "started_at", "verdict", "blocking",
                          "inputs_sha", "checks", "cost_usd", "duration_ms", "artifact", "human_ack")],
    )
    conn.commit()
    return row


def latest_gates(conn: sqlite3.Connection, project: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM gates"
    params: List[Any] = []
    if project:
        sql += " WHERE project = ?"
        params.append(project)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
