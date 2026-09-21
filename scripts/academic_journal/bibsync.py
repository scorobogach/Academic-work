"""Двусторонняя синхронизация .bib ↔ реестр evidence.

Импорт: .bib → реестр источников (source_id = cite key). Полезно, чтобы реестр
не расходился с библиографией: источник, которого нет в реестре, не проверен.

Экспорт: реестр → .bib. Полезно после проверки: в рукопись уходят только те
записи, чьи метаданные подтверждены (`metadata_verified = 1`).

Важно: импорт не выставляет `metadata_verified`. Запись из .bib — это заявка,
а не подтверждение.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from . import db, latex

TYPE_TO_BIB = {
    "article": "article",
    "inproceedings": "inproceedings",
    "incollection": "incollection",
    "book": "book",
    "chapter": "incollection",
    "thesis": "phdthesis",
    "report": "techreport",
    "dataset": "misc",
    "preprint": "misc",
    "gray": "misc",
    "other": "misc",
}

BIB_TO_TYPE = {
    "article": "article",
    "inproceedings": "article",
    "conference": "article",
    "incollection": "chapter",
    "inbook": "chapter",
    "book": "book",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "misc": "gray",
    "online": "gray",
    "dataset": "dataset",
    "eprint": "preprint",
    "preprint": "preprint",
    "unpublished": "gray",
}

VENUE_FIELDS = {
    "article": "journal",
    "inproceedings": "booktitle",
    "incollection": "booktitle",
    "phdthesis": "school",
    "mastersthesis": "school",
    "techreport": "institution",
    "misc": "howpublished",
}


def guess_tier(entry: Dict[str, str]) -> str:
    entry_type = (entry.get("entry_type") or "").lower()
    doi = (entry.get("doi") or "").lower()
    url = (entry.get("url") or "").lower()
    if entry_type in ("eprint", "preprint") or "10.48550" in doi or "arxiv" in doi or "arxiv" in url:
        return "preprint"
    if "biorxiv" in doi or "biorxiv" in url or "medrxiv" in doi or "medrxiv" in url:
        return "preprint"
    if entry_type in ("article", "inproceedings", "conference"):
        return "primary"
    if entry_type in ("book", "incollection", "inbook", "phdthesis", "mastersthesis"):
        return "secondary"
    return "gray"


def build_citation(entry: Dict[str, str]) -> str:
    authors = entry.get("author", "")
    if authors:
        first = authors.split(" and ")[0].strip()
        if "," in first:
            first = first.split(",")[0].strip()
        authors_text = first + (" et al." if " and " in authors else "")
    else:
        authors_text = "Без автора"
    title = entry.get("title", "Без названия")
    year = entry.get("year", "б.г.")
    venue = entry.get("journal") or entry.get("booktitle") or entry.get("publisher") or ""
    authors_text = authors_text.rstrip(".")
    return "%s. %s. %s%s" % (authors_text, title, venue + ", " if venue else "", year)


def import_bib(conn, bib_path: str, project: Optional[str] = None,
               default_trust: Optional[str] = None) -> Dict[str, int]:
    entries = latex.parse_bib(bib_path)
    stats = {"total": len(entries), "imported": 0, "updated": 0}
    for key, entry in entries.items():
        data = {
            "source_id": key,
            "doi": entry.get("doi", ""),
            "url": entry.get("url", ""),
            "author": entry.get("author", ""),
            "title": entry.get("title", ""),
            "venue": (entry.get("journal") or entry.get("booktitle") or entry.get("publisher") or ""),
            "year": entry.get("year", ""),
            "type": BIB_TO_TYPE.get((entry.get("entry_type") or "").lower(), "other"),
            "trust_tier": default_trust or guess_tier(entry),
            "citation": build_citation(entry),
            "metadata_verified": 0,
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "retriever": "tool:bibsync",
            "notes": "импортировано из %s; метаданные не проверены" % os.path.basename(bib_path),
            "project": project,
        }
        existing = db._existing(conn, "evidence", "source_id", key)
        db.upsert_evidence(conn, data)
        stats["updated" if existing else "imported"] += 1
    return stats


def _bib_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("&", "\\&")


def export_bib(conn, out_path: str, only_verified: bool = True,
               project: Optional[str] = None) -> Dict[str, Any]:
    rows = db.list_evidence(conn)
    if project:
        rows = [r for r in rows if (r.get("project") or "") == project]
    selected = [r for r in rows if (not only_verified or int(r.get("metadata_verified") or 0) == 1)]
    lines: List[str] = ["%% сгенерировано Academic-work (bibsync), %s"
                        % time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
                        "%% записей: %d из %d (только проверенные: %s)"
                        % (len(selected), len(rows), "да" if only_verified else "нет"), ""]
    for row in selected:
        evidence_type = (row.get("type") or "other").lower()
        bib_type = TYPE_TO_BIB.get(evidence_type, "misc")
        fields: List[str] = []
        if row.get("author"):
            fields.append("  author = {%s}" % _bib_escape(row["author"]))
        if row.get("title"):
            fields.append("  title = {%s}" % _bib_escape(row["title"]))
        venue_field = VENUE_FIELDS.get(bib_type, "howpublished")
        if row.get("venue"):
            fields.append("  %s = {%s}" % (venue_field, _bib_escape(row["venue"])))
        if row.get("year"):
            fields.append("  year = {%s}" % _bib_escape(str(row["year"])))
        if row.get("doi"):
            fields.append("  doi = {%s}" % _bib_escape(row["doi"]))
        if row.get("url"):
            fields.append("  url = {%s}" % _bib_escape(row["url"]))
        if row.get("pmid"):
            fields.append("  pmid = {%s}" % _bib_escape(row["pmid"]))
        note = "trust: %s" % (row.get("trust_tier") or "?")
        if evidence_type == "preprint":
            note += "; preprint"
        fields.append("  note = {%s}" % note)
        lines.append("@%s{%s," % (bib_type, row["source_id"]))
        lines.append(",\n".join(fields))
        lines.append("}")
        lines.append("")
    with open(os.path.expanduser(out_path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return {"written": out_path, "records": len(selected), "skipped": len(rows) - len(selected)}
