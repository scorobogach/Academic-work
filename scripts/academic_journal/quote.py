"""G4: verbatim-сверка цитаты с текстом источника.

Ключевое свойство — честность результата. Если текст источника извлечь не
удалось (например, PDF без текстового слоя или с CID-шрифтами), проверка
возвращает `unverifiable`, а не «похоже на правду». Лучше признать, что
проверить не удалось, чем выдать зелёный вердикт по мусору.

Источники текста, в порядке попытки:
  1. `pdftotext` (poppler), если установлен — даёт разбивку по страницам;
  2. собственный минимальный извлекатель текста из PDF (только stdlib, zlib);
  3. готовый текстовый файл (`.txt`, `.md`) рядом с источником.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
import zlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .gates import Finding, GateResult

STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
OP_RE = re.compile(
    rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ"          # [ ... ] TJ
    rb"|\(((?:\\.|[^\\()])*)\)\s*(?:Tj|'|\")"  # ( ... ) Tj  / ' / "
    rb"|<([0-9A-Fa-f\s]+)>\s*Tj",              # <hex> Tj
    re.S,
)
INNER_STR_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)|<([0-9A-Fa-f\s]+)>", re.S)
NUMBER_RE = re.compile(rb"-?\d+(?:\.\d+)?")

QUOTES = {"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
          "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'"}
DASHES = {"—": "-", "–": "-", "‒": "-", "‑": "-", "−": "-", "―": "-"}


# ---------- извлечение текста ----------

def _decode_literal(body: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(body):
        byte = body[i]
        if byte == 0x5C:  # backslash
            i += 1
            if i >= len(body):
                break
            nxt = body[i]
            if nxt in b"nrtbf":
                out += {"n": b"\n", "r": b"\r", "t": b"\t", "b": b"\b", "f": b"\f"}[chr(nxt)]
                i += 1
            elif nxt in b"()\\":
                out.append(nxt)
                i += 1
            elif 0x30 <= nxt <= 0x37:
                digits = bytes([nxt])
                i += 1
                while i < len(body) and len(digits) < 3 and 0x30 <= body[i] <= 0x37:
                    digits += bytes([body[i]])
                    i += 1
                out.append(int(digits, 8))
            else:
                out.append(nxt)
                i += 1
        else:
            out.append(byte)
            i += 1
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return out.decode(encoding)
        except UnicodeDecodeError:
            continue
    return out.decode("latin-1", errors="replace")


def _decode_hex(hexstr: bytes) -> str:
    digits = re.sub(rb"\s+", b"", hexstr)
    if len(digits) % 4 == 0 and len(digits) >= 4:
        # двухбайтовые коды: считаем, что это UTF-16BE, иначе это CID и текст будет мусором
        try:
            text = digits.decode("utf-16-be", errors="strict")
            if text and sum(1 for ch in text if ch.isprintable()) / len(text) > 0.8:
                return text
        except Exception:
            pass
        return "".join(chr(int(digits[i:i + 4], 16)) for i in range(0, len(digits), 4))
    if len(digits) % 2:
        digits += b"0"
    return "".join(chr(int(digits[i:i + 2], 16)) for i in range(0, len(digits), 2))


def _extract_ops(content: bytes) -> str:
    parts: List[str] = []
    for match in OP_RE.finditer(content):
        array, literal, hexstr = match.group(1), match.group(2), match.group(3)
        if array is not None:
            for inner in INNER_STR_RE.finditer(array):
                if inner.group(1) is not None:
                    parts.append(_decode_literal(inner.group(1)))
                else:
                    parts.append(_decode_hex(inner.group(2)))
            # числа в TJ-массиве — кернинг; большой отрицательный сдвиг ≈ пробел
            for num in NUMBER_RE.findall(array):
                if float(num) <= -100:
                    parts.append(" ")
        elif literal is not None:
            parts.append(_decode_literal(literal))
        elif hexstr is not None:
            parts.append(_decode_hex(hexstr))
    return "".join(parts)


def _pdf_text_stdlib(path: str) -> Tuple[List[str], str]:
    with open(path, "rb") as fh:
        data = fh.read()
    chunks: List[str] = []
    for match in STREAM_RE.finditer(data):
        raw = match.group(1)
        content = None
        try:
            content = zlib.decompress(raw)
        except Exception:
            if b"Tj" in raw or b"TJ" in raw:
                content = raw
        if not content or (b"Tj" not in content and b"TJ" not in content):
            continue
        text = _extract_ops(content)
        if text.strip():
            chunks.append(text)
    return chunks, "pdf-stdlib"


def _pdf_text_pdftotext(path: str) -> Tuple[List[str], str]:
    proc = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"],
                          capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("pdftotext завершился с кодом %s" % proc.returncode)
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = [p for p in text.split("\f") if p.strip()]
    return pages, "pdftotext"


def source_pages(path: str) -> Tuple[List[str], str]:
    """Вернуть страницы источника и способ извлечения."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return [], "missing"
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".tex"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [fh.read()], "plain-text"
    if ext == ".pdf":
        if shutil.which("pdftotext"):
            try:
                pages, method = _pdf_text_pdftotext(path)
                if pages and _quality_ok("".join(pages)):
                    return pages, method
            except Exception:
                pass
        return _pdf_text_stdlib(path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [fh.read()], "plain-text"


def _quality_ok(text: str) -> bool:
    if len(text.strip()) < 50:
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    return letters / max(1, len(text)) > 0.4


# ---------- нормализация и сравнение ----------

def normalize(text: str, letters_only: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("­", "")
    text = "".join(QUOTES.get(ch, ch) for ch in text)
    text = "".join(DASHES.get(ch, ch) for ch in text)
    text = re.sub(r"-\s*\n\s*", "", text)  # перенос с дефисом
    text = re.sub(r"\s+", " ", text)
    if letters_only:
        text = re.sub(r"[^\w\s]", " ", text.lower())
        text = re.sub(r"\s+", " ", text)
    return text.strip()


def _windows(hay: str, size: int) -> List[Tuple[int, str]]:
    words = hay.split()
    if size <= 0 or len(words) < size:
        return []
    return [(i, " ".join(words[i:i + size])) for i in range(0, len(words) - size + 1)]


def _ratio(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def compare(quote: str, pages: List[str]) -> Dict[str, Any]:
    """Сравнить цитату со всеми страницами. Возвращает лучшее совпадение."""
    raw_quote = (quote or "").strip()
    if not raw_quote:
        return {"match": "mismatch", "ratio": 0.0, "page": None, "window": "",
                "note": "пустая цитата"}
    norm_quote = normalize(raw_quote)
    letter_quote = normalize(raw_quote, letters_only=True)
    best = {"match": "mismatch", "ratio": 0.0, "page": None, "window": "", "note": ""}
    words = len(norm_quote.split())

    for idx, page in enumerate(pages, 1):
        if raw_quote in page:
            return {"match": "exact", "ratio": 1.0, "page": idx, "window": raw_quote, "note": ""}
    for idx, page in enumerate(pages, 1):
        page_norm = normalize(page)
        if norm_quote and norm_quote in page_norm:
            return {"match": "normalized", "ratio": 1.0, "page": idx, "window": norm_quote, "note": ""}
    for idx, page in enumerate(pages, 1):
        page_letters = normalize(page, letters_only=True)
        if not page_letters or not letter_quote:
            continue
        size = len(letter_quote.split())
        max_size = max(size, 1)
        for _, window in _windows(page_letters, max_size):
            ratio = _ratio(letter_quote, window)
            if ratio > best["ratio"]:
                best = {"match": "fuzzy", "ratio": round(ratio, 3), "page": idx,
                        "window": window, "note": ""}
        if best["ratio"] >= 0.995:
            best["match"] = "normalized"
            break
    if best["match"] == "fuzzy" and best["ratio"] < 0.8:
        best["match"] = "mismatch"
        best["note"] = "цитата не найдена в тексте источника"
    return best


def parse_locator(locator: str) -> Optional[int]:
    if not locator:
        return None
    match = re.search(r"(?:p{1,2}\.?\s*|с\.?\s*|страница\s*)(\d+)", locator, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.match(r"^\s*(\d+)\s*$", locator)
    return int(match.group(1)) if match else None


@dataclass
class QuoteCheck:
    source_id: str = ""
    claim_id: str = ""
    quote: str = ""
    locator: str = ""
    match: str = "mismatch"
    ratio: float = 0.0
    page: Optional[int] = None
    expected_page: Optional[int] = None
    method: str = ""
    window: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_quote(quote: str, source_path: str, locator: str = "") -> QuoteCheck:
    result = QuoteCheck(quote=quote, locator=locator,
                        expected_page=parse_locator(locator))
    pages, method = source_pages(source_path)
    result.method = method
    if method == "missing":
        result.match = "unverifiable"
        result.note = "файл источника недоступен: проверьте local_path в реестре evidence"
        return result
    if not pages or not _quality_ok("".join(pages)):
        result.match = "unverifiable"
        result.note = "текстовый слой не извлечён (скан или CID-шрифт): нужен pdftotext или текстовый файл"
        return result
    comparison = compare(quote, pages)
    result.match = comparison["match"]
    result.ratio = comparison["ratio"]
    result.page = comparison["page"]
    result.window = comparison["window"]
    result.note = comparison["note"]
    if result.expected_page and result.page and abs(result.expected_page - result.page) > 1:
        result.note = (result.note + "; " if result.note else "") + \
            "цитата найдена на стр. %s, а в локаторе указана стр. %s" % (result.page, result.expected_page)
    return result


def check_claims(conn, project: Optional[str] = None, limit: int = 100,
                 policy: Optional[Dict[str, Any]] = None) -> GateResult:
    """Прогнать G4 по реестру утверждений: нужны quote, source_id и файл источника."""
    import time

    from . import db

    started = time.time()
    policy = policy or {}
    cfg = policy.get("gates", {}).get("G4_claim_alignment", {})
    blocking_verdicts = set(cfg.get("blocking_verdicts",
                                    ["not_addressed", "unverifiable", "contradicted"]))
    min_ratio = float(cfg.get("min_fuzzy_ratio", 0.8))

    claims = db.list_claims(conn, project=project)
    evidence = {e["source_id"]: e for e in db.list_evidence(conn)}
    findings: List[Finding] = []
    checked = 0

    for claim in claims[:limit]:
        quote = (claim.get("quote") or "").strip()
        source_id = claim.get("source_id") or ""
        if not quote:
            findings.append(Finding("G4", "QUOTE-MISSING", "error",
                                    "у утверждения %s нет цитаты" % claim["claim_id"],
                                    claim["claim_id"], "добавьте цитату из источника"))
            continue
        source = evidence.get(source_id)
        path = (source or {}).get("local_path") or ""
        if not path or not os.path.exists(os.path.expanduser(path)):
            findings.append(Finding("G4", "SOURCE-FILE-MISSING", "error",
                                    "нет файла источника %s для %s" % (source_id or "—", claim["claim_id"]),
                                    claim["claim_id"], "укажите local_path в реестре evidence"))
            if claim.get("status") != "blocking":
                db.upsert_claim(conn, {"claim_id": claim["claim_id"], "status": "blocking"})
            continue

        check = check_quote(quote, path, claim.get("quote_locator") or "")
        checked += 1
        match = check.match
        if match == "fuzzy" and check.ratio < min_ratio:
            match = "mismatch"
        db.upsert_claim(conn, {
            "claim_id": claim["claim_id"],
            "verbatim_match": match,
            "checked_by": "tool",
            "gate_run_id": "G4",
        })
        if match in ("exact", "normalized"):
            continue
        severity = "error" if match in ("mismatch", "unverifiable") else "warning"
        findings.append(Finding("G4", "QUOTE-" + match.upper(), severity,
                                "%s: совпадение %s (ratio %.2f)%s"
                                % (claim["claim_id"], match, check.ratio,
                                   "; " + check.note if check.note else ""),
                                claim["claim_id"], "сверить цитату с источником"))

    errors = [f for f in findings if f.severity == "error"]
    return GateResult(
        gate="G4_claim_alignment",
        verdict="fail" if errors else ("warn" if findings else "pass"),
        blocking=bool(errors),
        findings=findings[:300],
        stats={"claims": len(claims), "checked": checked, "errors": len(errors),
               "blocking_verdicts": sorted(blocking_verdicts)},
        duration_ms=int((time.time() - started) * 1000),
        cost_usd=0.0,
    )
