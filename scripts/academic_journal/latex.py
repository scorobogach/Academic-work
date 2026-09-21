"""Минимальные детерминированные парсеры для .tex и .bib.

Никаких внешних зависимостей: только регулярные выражения.
Задача — не «понять LaTeX», а надёжно извлечь из рукописи то, что проверяют
гейты: ключи цитирования, числа, секции, среду таблиц.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

_COMMENT = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_CITE = re.compile(r"\\(?:cite|citep|citet|citealp|citeauthor|autocite|textcite|parencite)\*?(?:\[[^\]]*\])*\{([^}]*)\}")
_BIBITEM = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}")
_INCLUDE = re.compile(r"\\(?:input|include)\{([^}]*)\}")
_VERBATIM = re.compile(r"\\begin\{(?:verbatim|lstlisting|minted)\}.*?\\end\{(?:verbatim|lstlisting|minted)\}", re.S)
_MATH = re.compile(r"\$[^$]*\$|\\\\[\(\[](?:.|\n)*?\\\\[\)\]]")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d(?:[\d\s]*\d)?(?:[.,]\d+)?\s*(?:%|\\%)?")
_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,]+),", re.M)
_BIB_FIELD = re.compile(r"(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", re.S)
_SECTION = re.compile(r"\\(?:section|subsection|subsubsection)\*?\{([^}]*)\}")
_TABLE_ENV = re.compile(r"\\begin\{(tabular|tabularx|longtable|array)\*?\}.*?\\end\{\1\*?\}", re.S)
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def read_text(path: str) -> str:
    with open(os.path.expanduser(path), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def strip_tex_noise(text: str, keep_math: bool = False) -> str:
    """Убирает комментарии, verbatim-блоки и (опционально) формулы."""
    text = _VERBATIM.sub(" ", text)
    if not keep_math:
        text = _MATH.sub(" ", text)
    text = _COMMENT.sub("", text)
    return text


def expand_inputs(path: str, seen: int = 0) -> str:
    """Склеивает \\input/\\include (без рекурсивной защиты от циклов глубже 5 уровней)."""
    if seen > 5:
        return ""
    text = read_text(path)
    base = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
    out = []
    for line in text.splitlines():
        m = _INCLUDE.search(line)
        if m:
            candidate = os.path.join(base, m.group(1))
            for ext in ("", ".tex"):
                if os.path.exists(candidate + ext):
                    out.append(expand_inputs(candidate + ext, seen + 1))
                    break
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


def extract_cite_keys(text: str) -> List[str]:
    keys: List[str] = []
    for m in _CITE.finditer(text):
        for key in m.group(1).split(","):
            key = key.strip()
            if key:
                keys.append(key)
    for m in _BIBITEM.finditer(text):
        keys.append(m.group(1).strip())
    return keys


def extract_sections(text: str) -> List[str]:
    return [m.group(1).strip() for m in _SECTION.finditer(text)]


def extract_numbers(text: str, drop_commands: bool = True) -> List[Dict[str, str]]:
    """Числа с контекстом. По умолчанию игнорирует содержимое команд (\\cite, \\ref, \\label)."""
    cleaned = text
    if drop_commands:
        cleaned = re.sub(r"\\(?:cite|ref|label|eqref|pageref|cite)\w*\*?(?:\[[^\]]*\])*\{[^}]*\}", " ", cleaned)
        cleaned = re.sub(r"\\(?:includegraphics|input|include)(?:\[[^\]]*\])*\{[^}]*\}", " ", cleaned)
        cleaned = re.sub(r"\\\w+", " ", cleaned)  # остальные команды: параметры не числа
    out = []
    for m in _NUMBER.finditer(cleaned):
        raw = m.group(0).strip()
        if not raw or raw in {"-", "+"}:
            continue
        left = max(0, m.start() - 60)
        out.append({
            "value": _normalize_number(raw),
            "raw": raw,
            "context": " ".join(cleaned[left:m.end() + 60].split()),
            "offset": m.start(),
        })
    return out


def _normalize_number(raw: str) -> str:
    value = raw.replace("\\%", "%").replace("%", "").replace(" ", "").strip()
    value = value.replace(",", ".")
    if value.endswith("."):
        value = value[:-1]
    return value


def parse_bib(path: str) -> Dict[str, Dict[str, str]]:
    """Простой парсер BibTeX: тип, ключ и поля верхнего уровня."""
    text = read_text(path)
    entries: Dict[str, Dict[str, str]] = {}
    matches = list(_BIB_ENTRY.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        fields = {k.lower(): " ".join(v.split()) for k, v in _BIB_FIELD.findall(body)}
        fields["entry_type"] = m.group(1).lower()
        entries[m.group(2).strip()] = fields
    return entries


def extract_table_blocks(text: str) -> List[str]:
    """Таблицы: LaTeX-окружения и Markdown-таблицы (по строкам со |)."""
    blocks = [m.group(0) for m in _TABLE_ENV.finditer(text)]
    md_blocks, current = [], []
    for line in text.splitlines():
        if _MD_TABLE_ROW.match(line):
            current.append(line)
        elif current:
            if len(current) >= 2:
                md_blocks.append("\n".join(current))
            current = []
    if len(current) >= 2:
        md_blocks.append("\n".join(current))
    return blocks + md_blocks


def sha256_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(os.path.expanduser(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_values(path: str) -> Dict[str, Any]:
    """Читает машиночитаемые результаты анализа (JSON) — эталон для гейта G5."""
    import json

    with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
        return json.load(fh)
