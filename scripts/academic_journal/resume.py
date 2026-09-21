"""Контекст-пак для старта сессии: «не возвращаться к уже решённому».

Выгружает из БД детерминированную выжимку: активные решения, блокирующие
утверждения, статус гейтов, последние события, следующие действия. Каждый пункт
содержит ссылку на id журнала, чтобы при необходимости достать полный контекст,
не передавая весь архив в окно модели.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import db


def build(conn, project: Optional[str] = None, limit_events: int = 15,
          limit_items: int = 12) -> str:
    decisions = db.list_decisions(conn, status="active", project=project)[:limit_items]
    blocking = db.list_claims(conn, status="blocking", project=project)
    open_claims = db.list_claims(conn, status="open", project=project)
    summary = db.claim_summary(conn, project)
    gates = db.latest_gates(conn, project=project, limit=12)
    evidence = db.list_evidence(conn)[:limit_items]

    lines: List[str] = []
    lines.append("# Контекст-пак сессии")
    lines.append("")
    lines.append("Проект: `%s` · сформирован из БД (журнал — источник истины, здесь только индексы)" % (project or "все"))
    lines.append("")

    lines.append("## 1. Активные решения")
    if decisions:
        for d in decisions:
            lines.append("- **%s** — %s" % (d["decision_id"], d["question"]))
            lines.append("  - выбрано: %s" % (d["chosen"] or "—"))
            if d["rationale"]:
                lines.append("  - почему: %s" % d["rationale"])
            if d["reopen_if"]:
                lines.append("  - пересмотреть, если: %s" % d["reopen_if"])
    else:
        lines.append("- нет активных решений")
    lines.append("")

    lines.append("## 2. Блокирующие утверждения (не сдавать, пока не закрыты)")
    if blocking:
        for c in blocking:
            lines.append("- `%s` [%s] %s" % (c["claim_id"], c["verdict"], _clip(c["text"], 160)))
            lines.append("  - источник: %s · локатор: %s" % (c["source_id"] or "—", c["quote_locator"] or "—"))
    else:
        lines.append("- нет")
    lines.append("")

    lines.append("## 3. Реестр утверждений")
    lines.append("- всего по статусам: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(summary.items())))
    for c in open_claims[:limit_items]:
        lines.append("  - `%s` %s" % (c["claim_id"], _clip(c["text"], 120)))
    lines.append("")

    lines.append("## 4. Статус гейтов")
    if gates:
        latest: Dict[str, Any] = {}
        for g in gates:
            latest.setdefault(g["gate"], g)
        for gate, g in sorted(latest.items()):
            flag = "БЛОКИРУЕТ" if g["blocking"] else "ok"
            lines.append("- **%s**: %s · %s · %s" % (gate, g["verdict"], flag, g["started_at"]))
    else:
        lines.append("- прогонов не было")
    lines.append("")

    lines.append("## 5. Следующие действия (выводятся автоматически)")
    for action in _next_actions(summary, blocking, gates):
        lines.append("- [ ] %s" % action)
    lines.append("")

    lines.append("## 6. Источники в реестре")
    if evidence:
        for e in evidence:
            lines.append("- `%s` %s (%s, %s)" % (e["source_id"], e["citation"] or e["doi"] or e["url"] or "—",
                                                 e["year"] or "год?", e["trust_tier"] or "trust?"))
    else:
        lines.append("- реестр пуст")
    lines.append("")
    lines.append("> Полный контекст: `python3 scripts/journal_cli.py search --query ...` или MCP-инструмент `journal.search`.")
    return "\n".join(lines)


def _next_actions(summary: Dict[str, int], blocking: List[Dict[str, Any]], gates: List[Dict[str, Any]]) -> List[str]:
    actions = []
    failed = [g for g in gates if g["blocking"] and g["verdict"] != "pass"]
    failed_gates = sorted({g["gate"] for g in failed})
    if failed_gates:
        actions.append("устранить замечания гейтов: %s" % ", ".join(failed_gates))
    if blocking:
        actions.append("закрыть %d блокирующих утверждений (нужен локатор + verbatim-проверка)" % len(blocking))
    if summary.get("open", 0):
        actions.append("разобрать %d утверждений в статусе open (проверить или пометить unverifiable)" % summary["open"])
    unver = summary.get("blocking", 0) + summary.get("open", 0)
    if unver == 0:
        actions.append("прогнать полный набор гейтов G0→G7 и записать отчёты в журнал")
    actions.append("заполнить SIGN-OFF.md (человеческая подпись обязательна перед сдачей)")
    actions.append("сгенерировать раздел AI-use disclosure из лога журнала")
    return actions


def _clip(text: str, size: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= size else text[: size - 1] + "…"
