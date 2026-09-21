#!/usr/bin/env python3
"""Проверка внутренних ссылок в Markdown: docs, templates, schemas.

Зачем: документация — часть пайплайна. Битая ссылка в политике означает, что
человек не дойдёт до правила в момент, когда оно нужно.

  python3 scripts/check_links.py [--root .]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Tuple

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "doi:")


def iter_markdown(root: str) -> List[str]:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        for name in files:
            if name.endswith(".md"):
                out.append(os.path.join(base, name))
    return sorted(out)


def check(root: str) -> List[Tuple[str, str, str]]:
    problems = []
    for md in iter_markdown(root):
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        for target in LINK.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(SKIP_PREFIXES):
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(md), target))
            if not os.path.exists(resolved):
                problems.append((md, target, resolved))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проверка внутренних ссылок в Markdown")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    problems = check(args.root)
    if problems:
        print("Битые внутренние ссылки (%d):" % len(problems))
        for md, target, resolved in problems:
            print("  %s -> %s (%s)" % (md, target, resolved))
        return 1
    print("Внутренние ссылки в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())
