"""G8: рисунки и подписи к ним.

Подпись к рисунку — одно из самых «безопасных» мест для ошибки: её rarely
проверяют так же внимательно, как основной текст, а числа и утверждения там
бывают не менее важными. Классические проблемы, которые ловит этот гейт:

- рисунок не пересобран после изменения данных (файл старше results.json);
- файл рисунка отсутствует (ссылка на несуществующую фигуру);
- в подписи число, которого нет в результатах;
- рисунок не имеет \\label или нигде не упомянут в тексте.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from . import latex
from .gates import Finding, GateResult, declared_numbers, load_policy

import re  # noqa: E402  (используется только в этом модуле)

FIG_ENV = re.compile(r"\\begin\{figure\*?\}(?:\[[^\]]*\])?(.*?)\\end\{figure\*?\}", re.S)
CAPTION_RE = re.compile(r"\\caption(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}")
LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")
REF_RE = re.compile(r"\\(?:ref|autoref|cref|Cref|eqref|figref|figurename)\{([^}]*)\}")
MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
IMAGE_EXTENSIONS = ("", ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps", ".tif", ".tiff")


@dataclass
class Figure:
    label: str = ""
    caption: str = ""
    path: str = ""
    resolved: str = ""
    sha256: str = ""
    line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_figures(text: str, kind: str = "tex") -> List[Figure]:
    figures: List[Figure] = []
    if kind == "tex":
        for match in FIG_ENV.finditer(text):
            body = match.group(1)
            caption_match = CAPTION_RE.search(body)
            label_match = LABEL_RE.search(body)
            graphics_match = GRAPHICS_RE.search(body)
            figures.append(Figure(
                label=(label_match.group(1) if label_match else ""),
                caption=(caption_match.group(1) if caption_match else ""),
                path=(graphics_match.group(1) if graphics_match else ""),
                line=_line_of(text, match.start()),
            ))
    else:
        for match in MD_IMAGE_RE.finditer(text):
            figures.append(Figure(
                label="", caption=match.group(1), path=match.group(2),
                line=_line_of(text, match.start()),
            ))
    return figures


def resolve_path(path: str, base_dir: str) -> str:
    if not path:
        return ""
    candidates = [os.path.join(base_dir, path + ext) for ext in IMAGE_EXTENSIONS]
    candidates += [os.path.join(base_dir, path)]
    candidates += [os.path.expanduser(path + ext) for ext in IMAGE_EXTENSIONS]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""


def check_figures(text: str, values: Optional[Dict[str, Any]] = None,
                  base_dir: str = "", policy: Optional[Dict[str, Any]] = None,
                  kind: str = "tex", results_path: str = "") -> GateResult:
    started = time.time()
    policy = policy or load_policy()
    cfg = policy.get("gates", {}).get("G8_figures", {})
    allow = set(cfg.get("allow", [])) or set(policy.get("gates", {}).get("G5_numbers", {}).get("allow", []))
    require_label = bool(cfg.get("require_label", True))
    check_stale = bool(cfg.get("check_stale", True))
    ignore_context = [re.compile(p, re.IGNORECASE) for p in cfg.get("ignore_context", [])]

    allowed_values = declared_numbers(values, allow)
    figures = extract_figures(text, kind)
    refs = set(REF_RE.findall(text)) if kind == "tex" else set()

    results_mtime = os.path.getmtime(results_path) if (results_path and os.path.exists(results_path)) else 0
    findings: List[Finding] = []
    seen_labels: Dict[str, int] = {}
    resolved_count = 0
    with_path = 0

    for figure in figures:
        where = "line:%d" % figure.line
        name = figure.label or figure.path or "без названия"

        if not figure.caption.strip():
            findings.append(Finding("G8", "FIG-NO-CAPTION", "error",
                                    "рисунок %s без подписи" % name, where,
                                    "добавьте \\caption{}"))
        if kind == "tex" and not figure.label and require_label:
            findings.append(Finding("G8", "FIG-NO-LABEL", "warning",
                                    "рисунок %s без \\label" % name, where,
                                    "добавьте \\label{fig:...}"))
        if figure.label:
            if figure.label in seen_labels:
                findings.append(Finding("G8", "FIG-DUP-LABEL", "error",
                                        "метка %s повторяется" % figure.label, where,
                                        "метки должны быть уникальны"))
            seen_labels[figure.label] = figure.line
            if figure.label not in refs:
                findings.append(Finding("G8", "FIG-UNCITED", "warning",
                                        "рисунок %s не упомянут в тексте (нет \\ref)" % figure.label,
                                        where, "добавьте ссылку на рисунок в тексте"))

        if figure.path:
            with_path += 1
            resolved = resolve_path(figure.path, base_dir)
            figure.resolved = resolved
            if not resolved:
                findings.append(Finding("G8", "FIG-FILE-MISSING", "error",
                                        "файл рисунка не найден: %s" % figure.path, where,
                                        "проверьте путь или пересоберите фигуру"))
            else:
                resolved_count += 1
                figure.sha256 = latex.sha256_file(resolved)
                if check_stale and results_mtime and os.path.getmtime(resolved) < results_mtime:
                    findings.append(Finding("G8", "FIG-STALE", "warning",
                                            "рисунок %s старше results.json — не пересобран" % name,
                                            where, "выполните make reproduce"))

        if figure.caption.strip() and values is not None:
            for item in latex.extract_numbers(figure.caption):
                if any(rx.search(item.get("near", item["context"])) for rx in ignore_context):
                    continue
                if item["value"] not in allowed_values:
                    findings.append(Finding("G8", "FIG-NUM-UNTRACED", "warning",
                                            "число %s в подписи %s не найдено в results.json" % (item["value"], name),
                                            where, "перегенерируйте подпись из данных или проверьте вручную"))

    errors = [f for f in findings if f.severity == "error"]
    return GateResult(
        gate="G8_figures",
        verdict="fail" if errors else ("warn" if findings else "pass"),
        blocking=bool(errors) if cfg.get("blocking", True) else False,
        findings=findings[:300],
        stats={"figures": len(figures), "with_file": with_path,
               "files_found": resolved_count, "missing_files": with_path - resolved_count,
               "errors": len(errors), "warnings": len(findings) - len(errors)},
        duration_ms=int((time.time() - started) * 1000),
        cost_usd=0.0,
    )
