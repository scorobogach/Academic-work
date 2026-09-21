"""Генератор раздела «Использование ИИ» из лога журнала.

Disclosure собирается из машиночитаемых записей (`payload.kind == "ai_use"`),
а не из памяти: так формулировка всегда соответствует тому, что реально делалось.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

AUTHORSHIP_ORDER = [
    "human_only",
    "human_draft_model_edit",
    "model_draft_human_edit",
    "model_draft_human_rewrite",
    "model_generated_unchecked",
]

AUTHORSHIP_RU = {
    "human_only": "без использования ИИ",
    "human_draft_model_edit": "текст автора, правка моделью",
    "model_draft_human_edit": "черновик модели, переработан автором",
    "model_draft_human_rewrite": "черновик модели, переписан автором",
    "model_generated_unchecked": "текст модели без проверки (недопустимо)",
}


@dataclass
class AiUseRecord:
    section: str
    authorship: str
    model: str = ""
    tasks: List[str] = None  # type: ignore[assignment]
    human_verification: List[str] = None  # type: ignore[assignment]
    disclosure_required: bool = True
    date: str = ""
    journal_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "authorship": self.authorship,
            "model": self.model,
            "tasks": self.tasks or [],
            "human_verification": self.human_verification or [],
            "disclosure_required": self.disclosure_required,
            "date": self.date,
            "journal_id": self.journal_id,
        }


def collect(journal, actor: Optional[str] = None) -> List[AiUseRecord]:
    """Собрать записи об использовании ИИ из журнала."""
    records: List[AiUseRecord] = []
    for rec in journal.iter_records():
        payload = rec.get("payload") or {}
        if not isinstance(payload, dict) or payload.get("kind") != "ai_use":
            continue
        records.append(AiUseRecord(
            section=str(payload.get("section") or "—"),
            authorship=str(payload.get("authorship") or "unknown"),
            model=str(payload.get("model") or ""),
            tasks=list(payload.get("tasks") or []),
            human_verification=list(payload.get("human_verification") or []),
            disclosure_required=bool(payload.get("disclosure_required", True)),
            date=str(payload.get("date") or rec.get("ts", "")[:10]),
            journal_id=str(rec.get("id") or ""),
        ))
    return records


def warnings_for(records: List[AiUseRecord]) -> List[str]:
    problems = []
    for rec in records:
        if rec.authorship == "model_generated_unchecked":
            problems.append("раздел «%s»: текст модели без проверки человеком (блокирует сдачу)" % rec.section)
        if rec.authorship not in AUTHORSHIP_RU:
            problems.append("раздел «%s»: неизвестное значение authorship «%s»" % (rec.section, rec.authorship))
        if rec.disclosure_required and not rec.human_verification:
            problems.append("раздел «%s»: требуется disclosure, но не указана проверка человеком" % rec.section)
        if rec.authorship != "human_only" and not rec.model:
            problems.append("раздел «%s»: не указана модель" % rec.section)
    if not records:
        problems.append("в журнале нет ни одной записи kind=ai_use: disclosure не из чего собрать")
    return problems


def _highest(records: List[AiUseRecord]) -> str:
    best = "human_only"
    for rec in records:
        if rec.authorship in AUTHORSHIP_ORDER and AUTHORSHIP_ORDER.index(rec.authorship) > AUTHORSHIP_ORDER.index(best):
            best = rec.authorship
    return best


def build(journal) -> Dict[str, Any]:
    records = collect(journal)
    problems = warnings_for(records)
    level = _highest(records)
    models = sorted({r.model for r in records if r.model})
    return {
        "records": [r.to_dict() for r in records],
        "models": models,
        "level": level,
        "level_ru": AUTHORSHIP_RU.get(level, level),
        "warnings": problems,
        "markdown": render(records, models, problems),
        "ok": not problems,
    }


def render(records: List[AiUseRecord], models: List[str], problems: List[str]) -> str:
    lines = ["# Раскрытие использования ИИ (собрано из журнала)", ""]
    if not records:
        lines.append("_В журнале нет записей об использовании ИИ (kind=ai_use)._")
        lines.append("")
        lines.append("Добавьте записи командой:")
        lines.append("")
        lines.append("```bash")
        lines.append("python3 scripts/journal_cli.py append --type note \\")
        lines.append("  --payload '{\"kind\":\"ai_use\",\"section\":\"Методы\","
                     "\"authorship\":\"human_draft_model_edit\",\"model\":\"<модель>\","
                     "\"tasks\":[\"правка стиля\"],\"human_verification\":[\"числа сверены\"],"
                     "\"disclosure_required\":true}'")
        lines.append("```")
        return "\n".join(lines) + "\n"

    lines.append("Модели: %s" % (", ".join(models) or "—"))
    lines.append("")
    lines.append("| Раздел | Роль | Задачи | Проверка человеком | Раскрывать |")
    lines.append("|---|---|---|---|---|")
    for rec in records:
        lines.append("| %s | %s | %s | %s | %s |" % (
            rec.section,
            AUTHORSHIP_RU.get(rec.authorship, rec.authorship),
            "; ".join(rec.tasks) or "—",
            "; ".join(rec.human_verification) or "—",
            "да" if rec.disclosure_required else "нет",
        ))
    lines.append("")

    if problems:
        lines.append("## Замечания")
        lines.append("")
        for problem in problems:
            lines.append("- [ ] %s" % problem)
        lines.append("")

    level = _highest(records)
    lines.append("## Рекомендуемая формулировка")
    lines.append("")
    lines.append(suggest_text(level, models))
    lines.append("")
    lines.append("> Проверьте требования конкретного журнала или вуза: формулировка и объём")
    lines.append("> раскрытия различаются. Источник данных — журнал, ссылки на записи:")
    lines.append("")
    for rec in records:
        lines.append("- %s — `%s`" % (rec.section, rec.journal_id or "id не сохранён"))
    return "\n".join(lines) + "\n"


def suggest_text(level: str, models: List[str]) -> str:
    models_text = ", ".join(models) if models else "<модель>"
    if level == "human_only":
        return ("При подготовке рукописи генеративные модели не использовались.")
    if level in ("human_draft_model_edit",):
        return ("При подготовке рукописи автор использовал языковую модель (%s) исключительно "
                "для стилистической правки собственного текста. Все идеи, данные, анализ, "
                "интерпретация и выводы принадлежат автору; каждое утверждение, цитата и число "
                "проверены по первоисточникам." % models_text)
    if level in ("model_draft_human_edit", "model_draft_human_rewrite"):
        return ("При подготовке рукописи использовалась языковая модель (%s): черновики "
                "отдельных разделов были подготовлены моделью и существенно переработаны "
                "автором. Весь текст, включая интерпретацию результатов и выводы, "
                "написан и проверен автором; все источники сверены с первоисточниками."
                % models_text)
    return ("**Внимание:** в журнале есть разделы, созданные моделью без проверки "
            "человеком. Такой текст нельзя подавать; сначала проведите проверку и "
            "обновите записи в журнале.")
