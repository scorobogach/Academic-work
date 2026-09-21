"""Экспорт реестров в CSV и Markdown.

Зачем: реестры — это доказательная база работы. Их нужно прикладывать к
рукописи (как приложение), вкладывать в ответ рецензенту и передавать
соавторам, которые не пользуются этим инструментом.
"""
from __future__ import annotations

import csv
import io
import os
from typing import Any, Dict, Iterable, List, Optional

from . import db

EXPORTERS = {
    "evidence": (db.EVIDENCE_FIELDS, db.list_evidence),
    "claims": (db.CLAIM_FIELDS, db.list_claims),
    "decisions": (db.DECISION_FIELDS, db.list_decisions),
}


def _rows(conn, what: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    if what == "gates":
        return db.latest_gates(conn, project=project, limit=1000)
    fields, getter = EXPORTERS[what]
    if what == "decisions":
        rows = [dict(r) for r in getter(conn, status="active", project=project)]
        rows += [dict(r) for r in getter(conn, status="superseded", project=project)]
    else:
        rows = [dict(r) for r in getter(conn)]
        if project:
            rows = [r for r in rows if (r.get("project") or "") == project]
    return rows


def to_csv(rows: Iterable[Dict[str, Any]], fields: List[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore",
                            quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})
    return buffer.getvalue()


def _md_table(rows: List[Dict[str, Any]], fields: List[str]) -> str:
    if not rows:
        return "_пусто_\n"
    header = "| " + " | ".join(fields) + " |"
    sep = "|" + "|".join(["---"] * len(fields)) + "|"
    lines = [header, sep]
    for row in rows:
        cells = []
        for field in fields:
            value = row.get(field)
            value = "" if value is None else str(value)
            value = value.replace("|", "\\|").replace("\n", " ")
            cells.append(value[:200])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


TITLES = {
    "evidence": "Реестр источников",
    "claims": "Реестр утверждений",
    "decisions": "Журнал решений",
    "gates": "Прогоны гейтов",
}


def to_markdown(rows: List[Dict[str, Any]], fields: List[str], title: str) -> str:
    lines = ["# %s" % title, ""]
    lines.append(_md_table(rows, fields))
    return "\n".join(lines) + "\n"


def export(conn, out_dir: str, what: str = "all", fmt: str = "both",
           project: Optional[str] = None) -> List[str]:
    """Экспортировать реестры. Возвращает список созданных файлов."""
    os.makedirs(os.path.expanduser(out_dir), exist_ok=True)
    targets = ["evidence", "claims", "decisions", "gates"] if what == "all" else [what]
    created = []
    for name in targets:
        rows = _rows(conn, name, project)
        fields = (["run_id", "project", "gate", "started_at", "verdict", "blocking",
                   "cost_usd", "duration_ms", "artifact", "human_ack"]
                  if name == "gates" else EXPORTERS[name][0])
        if fmt in ("csv", "both"):
            path = os.path.expanduser(os.path.join(out_dir, name + ".csv"))
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(to_csv(rows, fields))
            created.append(path)
        if fmt in ("md", "markdown", "both"):
            path = os.path.expanduser(os.path.join(out_dir, name + ".md"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(to_markdown(rows, fields, TITLES.get(name, name)))
            created.append(path)
    return created
