"""Детерминированные quality-gates.

Принцип: сначала всё, что проверяется без модели и без сети (быстро, бесплатно,
воспроизводимо), затем — модельные и ручные проверки, результат которых
фиксируется извне через `gates record`.

G0 hygiene    — невидимые символы, bidi-контролы, смешанная типографика
G1 style      — шаблонные обороты и «вода» (словари: config/style-rules.ru.json)
G3 bibliography — есть ли DOI/URL у каждой ссылки, существует ли DOI (Crossref, опционально)
G5 numbers    — каждое число в тексте должно трассироваться на машиночитаемый результат
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from . import latex

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_RULES = os.path.join(HERE, "config", "style-rules.ru.json")
DEFAULT_POLICY = os.path.join(HERE, "config", "gates.json")

ZERO_WIDTH = ["​", "‌", "‍", "‎", "‏", "⁠", "﻿", "­", "⠀"]
BIDI = ["‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"]
SUSPICIOUS_SPACES = [" ", " ", " ", " ", "　"]

UNSOURCED_PATTERNS = [
    (r"\bисследования\s+(?:показывают|демонстрируют|свидетельствуют)\b", "ссылка на «исследования» без источника"),
    (r"\b(?:учёные|эксперты|специалисты)\s+(?:считают|утверждают|полагают)\b", "апелляция к анонимным экспертам"),
    (r"\b(?:как\s+известно|общеизвестно|широко\s+известно)\b", "«общеизвестно» без источника"),
    (r"\bмногие\s+(?:авторы|исследователи)\b", "размытая ссылка на «многих авторов»"),
    (r"\bпо\s+данным\s+исследований\b", "«по данным исследований» без указания работы"),
    (r"\bstudies\s+show\b", "unsourced 'studies show'"),
    (r"\bexperts\s+agree\b", "unsourced 'experts agree'"),
]


@dataclass
class Finding:
    gate: str
    code: str
    severity: str  # error | warning | info
    message: str
    locator: str = ""
    hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    gate: str
    verdict: str  # pass | warn | fail
    blocking: bool
    findings: List[Finding] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    artifact: str = ""
    inputs_sha: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["findings"] = [f if isinstance(f, dict) else f.to_dict() for f in self.findings]
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _timer():
    return time.time()


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- G0: гигиена ----------

def check_hygiene(text: str, gate: str = "G0") -> GateResult:
    started = _timer()
    findings: List[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for ch in ZERO_WIDTH:
            if ch in line:
                findings.append(Finding(gate, "HYG-ZERO-WIDTH", "error",
                                        "невидимый символ U+%04X" % ord(ch), "line:%d" % i,
                                        "удалить: python3 scripts/journal_cli.py hygiene --fix FILE"))
        for ch in BIDI:
            if ch in line:
                findings.append(Finding(gate, "HYG-BIDI", "error",
                                        "bidi-управляющий символ U+%04X" % ord(ch), "line:%d" % i,
                                        "удалить вручную или через --fix"))
        if " " in line and re.search(r"\d \d", line):
            findings.append(Finding(gate, "HYG-NBSP-NUM", "info",
                                    "неразрывный пробел в числе", "line:%d" % i,
                                    "в русской типографике допустимо, проверьте осознанно"))
        if line.count("—") == 1 and line.strip().startswith("—"):
            findings.append(Finding(gate, "HYG-DASH-LIST", "info",
                                    "тире как маркер списка", "line:%d" % i, "привести к единому стилю"))
    errors = [f for f in findings if f.severity == "error"]
    return GateResult(
        gate="G0_hygiene",
        verdict="fail" if errors else ("warn" if findings else "pass"),
        blocking=bool(errors),
        findings=findings[:200],
        stats={"lines": len(text.splitlines()), "errors": len(errors), "warnings": len(findings) - len(errors)},
        duration_ms=int((_timer() - started) * 1000),
        inputs_sha=_sha256_text(text),
    )


def fix_hygiene(text: str) -> str:
    for ch in ZERO_WIDTH + BIDI:
        if unicodedata.category(ch) not in ("Cf", "Zs", "Zl", "Zp") or ch in ZERO_WIDTH + BIDI:
            text = text.replace(ch, "")
    return text


# ---------- G1: стиль ----------

def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or DEFAULT_RULES
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or DEFAULT_POLICY
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


_CODE_BLOCK = re.compile(r"```.*?```|`[^`]*`", re.S)
_QUOTE_BLOCK = re.compile(r"(?:^|\n)>.*", re.S)


def check_style(text: str, rules: Optional[Dict[str, Any]] = None, policy: Optional[Dict[str, Any]] = None,
                gate: str = "G1") -> GateResult:
    started = _timer()
    rules = rules or load_rules()
    policy = policy or load_policy()
    threshold = int(policy.get("gates", {}).get("G1_style", {}).get("threshold", 90))
    blocking_codes = set(policy.get("gates", {}).get("G1_style", {}).get("blocking_codes", ["UNSOURCED"]))

    scan = _CODE_BLOCK.sub(" ", text)
    scan = _QUOTE_BLOCK.sub(" ", scan)
    scan = re.sub(r"<!--.*?-->", " ", scan, flags=re.S)
    lines = scan.splitlines()

    findings: List[Finding] = []
    penalty = 0.0
    word_count = max(1, len(re.findall(r"[\wЀ-ӿ]+", scan)))

    for group, weight in (("ai_vocabulary", 1.0), ("empty_phrases", 1.0), ("canned_openers", 0.5),
                          ("chatbot_leftovers", 1.5), ("filler_words", 0.5), ("sweeping_words", 0.5)):
        for phrase in rules.get(group, []):
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            for i, line in enumerate(lines, 1):
                for m in pattern.finditer(line):
                    penalty += weight
                    findings.append(Finding(gate or "G1", "STYLE-" + group.upper(), "warning",
                                             "шаблонный оборот: «%s»" % phrase, "line:%d" % i,
                                             "заменить на конкретику или удалить"))

    for pattern, message in UNSOURCED_PATTERNS:
        regex = re.compile(pattern, re.IGNORECASE)
        for i, line in enumerate(lines, 1):
            for m in regex.finditer(line):
                penalty += 3.0
                findings.append(Finding(gate or "G1", "UNSOURCED", "error", message,
                                        "line:%d" % i, "добавить источник или удалить утверждение"))

    for pattern in rules.get("patterns", []):
        regex = re.compile(pattern.get("regex", ""), re.IGNORECASE)
        for i, line in enumerate(lines, 1):
            for m in regex.finditer(line):
                penalty += float(pattern.get("weight", 1.0))
                findings.append(Finding(gate or "G1", pattern.get("code", "STYLE-PATTERN"), "warning",
                                        pattern.get("message", pattern.get("regex", "")), "line:%d" % i, ""))

    # ритм: четыре подряд коротких или четыре подряд длинных предложения
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", scan) if s.strip()]
    short_run = long_run = 0
    for i, s in enumerate(sentences, 1):
        words = len(s.split())
        short_run = short_run + 1 if words <= 6 else 0
        long_run = long_run + 1 if words >= 35 else 0
        if short_run >= 4:
            findings.append(Finding(gate or "G1", "RHYTHM-SHORT", "info", "четыре коротких предложения подряд",
                                    "sentence:%d" % i, "объединить"))
            short_run = 0
        if long_run >= 3:
            findings.append(Finding(gate or "G1", "RHYTHM-LONG", "info", "три длинных предложения подряд",
                                    "sentence:%d" % i, "разбить"))
            long_run = 0

    if len(re.findall(r"—", scan)) / word_count * 1000 > float(rules.get("em_dash_per_1k", 8)):
        findings.append(Finding(gate or "G1", "RHYTHM-EMDASH", "warning",
                                "слишком много тире на 1000 слов", "",
                                "часть тире заменить на запятые или точки"))

    score = max(0.0, 100.0 - (penalty / word_count) * 1000.0)
    errors = [f for f in findings if f.code in blocking_codes and f.severity == "error"]
    verdict = "fail" if (errors or score < threshold) else "pass"
    return GateResult(
        gate="G1_style",
        verdict=verdict,
        blocking=bool(errors or score < threshold),
        findings=findings[:300],
        stats={"score": round(score, 1), "threshold": threshold, "words": word_count,
               "errors": len(errors), "findings": len(findings)},
        duration_ms=int((_timer() - started) * 1000),
        inputs_sha=_sha256_text(text),
    )


# ---------- G3: библиография ----------

CROSSREF = "https://api.crossref.org/works/"


def check_bibliography(tex_path: str, bib_path: Optional[str] = None, offline: bool = True,
                       timeout: int = 8, gate: str = "G3") -> GateResult:
    started = _timer()
    findings: List[Finding] = []
    text = latex.expand_inputs(tex_path)
    keys = sorted(set(latex.extract_cite_keys(text)))
    entries = latex.parse_bib(bib_path) if bib_path else {}

    for key in keys:
        entry = entries.get(key)
        if entry is None:
            findings.append(Finding(gate, "BIB-MISSING", "error", "ключ %s не найден в .bib" % key, key,
                                    "добавить запись или исправить ключ"))
            continue
        if not entry.get("doi") and not entry.get("url"):
            findings.append(Finding(gate, "BIB-NO-DOI", "warning",
                                    "у записи %s нет DOI или URL" % key, key,
                                    "добавить DOI — иначе проверка существования невозможна"))
        for required in ("author", "title", "year"):
            if not entry.get(required):
                findings.append(Finding(gate, "BIB-INCOMPLETE", "warning",
                                        "в записи %s нет поля %s" % (key, required), key, ""))

    verified = 0
    if not offline:
        for key, entry in entries.items():
            doi = entry.get("doi")
            if not doi:
                continue
            try:
                req = urllib.request.Request(CROSSREF + urllib.parse.quote(doi.strip()),
                                             headers={"User-Agent": "Academic-work/0.1 (mailto:you@example.org)"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                title = (data.get("message", {}).get("title") or [""])[0].lower()
                verified += 1
                if title and entry.get("title") and title[:25] not in entry["title"].lower():
                    findings.append(Finding(gate, "BIB-TITLE-MISMATCH", "error",
                                            "название %s не совпадает с Crossref" % key, key,
                                            "сверить запись с doi.org"))
            except urllib.error.HTTPError:
                findings.append(Finding(gate, "BIB-DOI-NOT-FOUND", "error",
                                        "DOI %s не найден в Crossref" % doi, key,
                                        "проверить DOI или заменить источник"))
            except Exception as exc:  # сеть недоступна — не роняем пайплайн
                findings.append(Finding(gate, "BIB-CHECK-ERROR", "warning",
                                        "проверка %s не выполнена: %s" % (key, exc), key, ""))

    errors = [f for f in findings if f.severity == "error"]
    used = {k for k in keys}
    unused = sorted(set(entries) - used)
    for key in unused:
        findings.append(Finding(gate, "BIB-UNUSED", "info", "источник %s не процитирован" % key, key, ""))
    return GateResult(
        gate="G3_bibliography",
        verdict="fail" if errors else "pass",
        blocking=bool(errors),
        findings=findings[:300],
        stats={"cite_keys": len(keys), "bib_entries": len(entries), "doi_verified": verified,
               "errors": len(errors), "offline": offline},
        duration_ms=int((_timer() - started) * 1000),
        inputs_sha=latex.sha256_file(tex_path),
        cost_usd=0.0,
    )


# ---------- G5: числа ----------

def check_numbers(text: str, values: Optional[Dict[str, Any]] = None, policy: Optional[Dict[str, Any]] = None,
                  kind: str = "tex", gate: str = "G5") -> GateResult:
    """Каждое число в тексте должно быть найдено в машиночитаемых результатах.

    values — JSON вида {"n_participants": 42, "effect_size": 0.31, ...}
    либо {"numbers": ["42", "0.31", ...]}. Числа, которых нет в results.json,
    считаются не подтверждёнными трассировкой (это не значит «ошибка», но
    требует ручной пометки в claims как self_computed или background).
    """
    started = _timer()
    policy = policy or load_policy()
    cfg = policy.get("gates", {}).get("G5_numbers", {})
    allow = set(cfg.get("allow", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "100", "1000", "0", "1.0", "0.05"]))
    ignored_context = [re.compile(p, re.IGNORECASE) for p in cfg.get("ignore_context", [])]

    cleaned = latex.strip_tex_noise(text) if kind == "tex" else text
    found = latex.extract_numbers(cleaned)

    if values and "numbers" not in values:
        allowed_values = set()
        for v in values.values():
            allowed_values.update(_as_number_strings(v))
    else:
        allowed_values = set()
        for v in (values or {}).get("numbers", []):
            allowed_values.update(_as_number_strings(v))
    allowed_values |= allow

    findings: List[Finding] = []
    unmatched = []
    for item in found:
        if any(rx.search(item["context"]) for rx in ignored_context):
            continue
        if item["value"] not in allowed_values:
            unmatched.append(item)
            findings.append(Finding(gate, "NUM-UNTRACED", "warning",
                                    "число %s не найдено в results.json" % item["value"],
                                    "offset:%d" % item["offset"],
                                    "добавить в results.json или пометить утверждение в claims"))

    used = {i["value"] for i in found}
    for value in sorted(allowed_values - used - allow):
        if value in {"0", "1", "2", "3"}:
            continue
        findings.append(Finding(gate, "NUM-UNUSED", "info",
                                "результат %s не упомянут в тексте" % value, "", ""))

    table_untraced = 0
    if bool(cfg.get("check_tables", True)):
        for block in latex.extract_table_blocks(text):
            for item in latex.extract_numbers(block):
                if any(rx.search(item["context"]) for rx in ignored_context):
                    continue
                if item["value"] not in allowed_values:
                    table_untraced += 1
                    findings.append(Finding(gate, "NUM-TABLE-UNTRACED", "warning",
                                            "число %s в таблице не найдено в results.json" % item["value"],
                                            "offset:%d" % item["offset"],
                                            "перегенерируйте таблицу из данных или добавьте значение"))

    verdict = "pass" if not unmatched else "warn"
    return GateResult(
        gate="G5_numbers",
        verdict=verdict,
        blocking=bool(cfg.get("blocking", False)),
        findings=findings[:300],
        stats={"numbers_in_text": len(found), "untraced": len(unmatched),
               "untraced_in_tables": table_untraced,
               "declared_values": len(allowed_values), "has_results": bool(values)},
        duration_ms=int((_timer() - started) * 1000),
        inputs_sha=_sha256_text(text),
    )


def _as_number_strings(value: Any) -> List[str]:
    out = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        s = ("%g" % float(value))
        out.append(s)
        out.append(s.replace("-", "−"))
        if abs(value) >= 1000:
            out.append("{:,}".format(int(value)).replace(",", " "))
    elif isinstance(value, str):
        for m in re.findall(r"[-+]?\d[\d\s]*(?:[.,]\d+)?", value):
            out.append(m.strip().replace(",", ".").replace(" ", "").replace(" ", ""))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_as_number_strings(v))
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            out.extend(_as_number_strings(v))
    return [o for o in out if o]


# ---------- реестр гейтов ----------

DETERMINISTIC_GATES = {
    "G0": "hygiene",
    "G1": "style",
    "G3": "bibliography",
    "G5": "numbers",
}


def run_gate(name: str, **kwargs: Any) -> GateResult:
    name = name.upper()
    if name == "G0":
        return check_hygiene(kwargs["text"])
    if name == "G1":
        return check_style(kwargs["text"], kwargs.get("rules"), kwargs.get("policy"))
    if name == "G3":
        return check_bibliography(kwargs["tex"], kwargs.get("bib"), offline=bool(kwargs.get("offline", True)))
    if name == "G5":
        return check_numbers(kwargs["text"], kwargs.get("values"), kwargs.get("policy"), kind=kwargs.get("kind", "tex"))
    raise ValueError("детерминированный гейт %s не реализован (модельные гейты G2/G4/G6/G7 ведутся вне CLI)" % name)
