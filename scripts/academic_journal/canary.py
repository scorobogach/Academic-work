"""Canary-тест контура проверок.

Гейт, который никто не измеряет, — это декоративный гейт. Раз в месяц (и перед
сдачей) в копию черновика подмешиваются заведомо ложные элементы, после чего
измеряется, какая доля из них реально найдена. Recall ниже порога означает, что
«зелёные» отчёты этого контура больше нельзя принимать на веру.

Работает полностью офлайн: подмешивание детерминировано (seed), а проверка
сравнивает JSON-отчёты гейтов с манифестом ожидаемых находок.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

UNSOURCED_SENTENCES = [
    "Исследования показывают, что этот эффект устойчив во всех популяциях.",
    "Эксперты считают, что данный подход не имеет альтернатив.",
    "Как известно, воспроизводимость напрямую зависит от качества данных.",
    "Многие авторы отмечают рост интереса к открытым данным.",
    "По данным исследований, метод даёт стабильный прирост точности.",
]

NUMBER_SENTENCES = [
    "Дополнительный контрольный показатель составил 987654 единицы.",
    "Вспомогательная метрика достигла 987654 пунктов.",
]

DEFAULT_COUNTS = {"unsourced": 5, "references": 3, "numbers": 1}


@dataclass
class CanaryItem:
    id: str
    type: str
    text: str
    expect: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def inject(text: str, kind: str = "md", seed: int = 42,
           counts: Optional[Dict[str, int]] = None) -> Tuple[str, Dict[str, Any]]:
    """Подмешать канарские элементы. Возвращает (текст, манифест)."""
    counts = dict(DEFAULT_COUNTS if counts is None else {**DEFAULT_COUNTS, **counts})
    rng = random.Random(seed)
    original = text.splitlines()
    marker_comment = "% canary:" if kind == "tex" else "<!-- canary:"
    marker_tail = "" if kind == "tex" else " -->"

    items: List[CanaryItem] = []

    for i in range(int(counts.get("unsourced", 0))):
        items.append(CanaryItem(
            id="unsourced-%d" % (i + 1), type="unsourced",
            text=UNSOURCED_SENTENCES[i % len(UNSOURCED_SENTENCES)],
            expect={"gate": "G1", "code": "UNSOURCED"}))

    for i in range(int(counts.get("references", 0))):
        key = "canaryghost%d" % (i + 1)
        sentence = ("Дополнительные сведения приведены в работе \\cite{%s}." % key
                    if kind == "tex" else "Дополнительные сведения приведены в работе [%s]." % key)
        items.append(CanaryItem(id="ref-%d" % (i + 1), type="reference", text=sentence,
                                expect={"gate": "G3", "code": "BIB-MISSING", "key": key}))

    for i in range(int(counts.get("numbers", 0))):
        sentence = NUMBER_SENTENCES[i % len(NUMBER_SENTENCES)]
        items.append(CanaryItem(id="number-%d" % (i + 1), type="number", text=sentence,
                                expect={"gate": "G5", "code": "NUM-UNTRACED", "value": "987654"}))

    # позиции считаются по исходному списку строк, а строки вставляются за один проход —
    # иначе номера строк в манифесте разъезжаются после каждой вставки
    positions = _spread(len(original), len(items))
    rng.shuffle(items)
    schedule: Dict[int, List[CanaryItem]] = {}
    for position, item in zip(positions, items):
        schedule.setdefault(position, []).append(item)

    out: List[str] = []
    for index, line in enumerate(original):
        for item in schedule.get(index, []):
            item.expect["line"] = len(out) + 1
            out.append("%s %s%s%s" % (item.text, marker_comment, item.id, marker_tail))
        out.append(line)
    for item in schedule.get(len(original), []):
        item.expect["line"] = len(out) + 1
        out.append("%s %s%s%s" % (item.text, marker_comment, item.id, marker_tail))

    order = {item.id: index for index, item in enumerate(items)}
    items.sort(key=lambda item: order[item.id])

    if kind != "tex":
        for item in items:
            if item.type == "reference":
                item.expect["note"] = "G3 проверяет только .tex: для Markdown ключ не будет найден"

    manifest = {
        "version": 1,
        "seed": seed,
        "kind": kind,
        "counts": counts,
        "items": [item.to_dict() for item in items],
    }
    return "\n".join(out) + "\n", manifest


def _spread(total_lines: int, count: int) -> List[int]:
    if count <= 0:
        return []
    if total_lines <= 1:
        return [max(0, total_lines - 1)] * count
    step = max(1, total_lines // (count + 1))
    return [min(total_lines, step * (i + 1) + i) for i in range(count)]


def _load_report(path: str) -> Dict[str, Any]:
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        return json.load(fh)


def _gate_key(gate: str) -> str:
    gate = (gate or "").upper()
    return gate if gate.startswith("G") else "G" + gate


def evaluate(manifest: Dict[str, Any], reports: Dict[str, str],
             threshold: float = 0.8) -> Dict[str, Any]:
    """Сравнить отчёты гейтов с манифестом и посчитать recall."""
    loaded: Dict[str, Dict[str, Any]] = {}
    for name, path in reports.items():
        try:
            loaded[_gate_key(name)] = _load_report(path)
        except Exception as exc:
            loaded[_gate_key(name)] = {"error": str(exc), "findings": []}

    results = []
    for item in manifest.get("items", []):
        expect = item.get("expect", {})
        gate = _gate_key(expect.get("gate", ""))
        report = loaded.get(gate)
        found = False
        note = ""
        if report is None:
            note = "нет отчёта гейта %s" % gate
        elif report.get("error"):
            note = "отчёт %s не прочитан: %s" % (gate, report["error"])
        else:
            findings = report.get("findings", [])
            code = expect.get("code")
            for finding in findings:
                if finding.get("code") != code:
                    continue
                if code == "UNSOURCED":
                    locator = str(finding.get("locator", ""))
                    if locator.startswith("line:"):
                        line = int(locator.split(":")[1])
                        if abs(line - int(expect.get("line", -999))) <= 2:
                            found = True
                            break
                elif code == "BIB-MISSING":
                    if str(finding.get("locator", "")) == expect.get("key"):
                        found = True
                        break
                elif code == "NUM-UNTRACED":
                    if str(expect.get("value", "")) in str(finding.get("message", "")):
                        found = True
                        break
                else:
                    found = True
                    break
            if not found:
                note = "находка %s не обнаружена в отчёте %s" % (code, gate)
        results.append({"id": item.get("id"), "type": item.get("type"),
                        "expected_gate": gate, "expected_code": expect.get("code"),
                        "found": found, "note": note})

    total = len(results)
    detected = sum(1 for r in results if r["found"])
    recall = (detected / total) if total else 0.0

    by_type: Dict[str, Dict[str, int]] = {}
    for r in results:
        stat = by_type.setdefault(r["type"], {"total": 0, "found": 0})
        stat["total"] += 1
        stat["found"] += int(bool(r["found"]))

    return {
        "recall": round(recall, 3),
        "threshold": threshold,
        "verdict": "pass" if recall >= threshold else "fail",
        "detected": detected,
        "total": total,
        "by_type": by_type,
        "results": results,
        "advice": ("контур работает: «зелёным» отчётам можно доверять"
                   if recall >= threshold else
                   "контур НЕ работает: «зелёные» отчёты гейтов не учитывать, разобрать причины"),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Canary-тест контура проверок", ""]
    lines.append("- Recall: **%.2f** (порог %.2f) — %s" % (
        report["recall"], report["threshold"], report["verdict"]))
    lines.append("- Найдено: %d из %d" % (report["detected"], report["total"]))
    lines.append("- Вывод: %s" % report["advice"])
    lines.append("")
    lines.append("| Тип | Найдено | Всего |")
    lines.append("|---|---|---|")
    for type_name, stat in sorted(report.get("by_type", {}).items()):
        lines.append("| %s | %d | %d |" % (type_name, stat["found"], stat["total"]))
    lines.append("")
    lines.append("| ID | Гейт | Код | Найдено | Примечание |")
    lines.append("|---|---|---|---|---|")
    for r in report.get("results", []):
        lines.append("| %s | %s | %s | %s | %s |" % (
            r["id"], r["expected_gate"], r["expected_code"],
            "да" if r["found"] else "НЕТ", r.get("note", "")))
    return "\n".join(lines) + "\n"
