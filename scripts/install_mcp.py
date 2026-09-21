#!/usr/bin/env python3
"""Установка MCP-сервера журнала в конфиг агента (Claude Code / Cursor / Codex CLI).

  python3 scripts/install_mcp.py --agent claude      # .mcp.json в корне репозитория
  python3 scripts/install_mcp.py --agent cursor      # ~/.cursor/mcp.json (слияние)
  python3 scripts/install_mcp.py --agent codex       # ~/.codex/config.toml (добавление блока)
  python3 scripts/install_mcp.py --agent all --dry-run

Скрипт делает резервную копию существующего файла (суффикс .bak) и не удаляет
уже подключённые серверы. Секреты в конфиг не пишутся — только пути к БД и журналу.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from typing import Dict, Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(REPO_ROOT, "scripts", "mcp_server.py")

DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".academic", "journal.db")
DEFAULT_JOURNAL = os.path.join(os.path.expanduser("~"), ".academic", "journal")


def server_entry(python: str, db: str, journal: str, project: str) -> Dict[str, Any]:
    return {
        "command": python,
        "args": ["-u", SERVER_PY],
        "env": {
            "ACADEMIC_DB": db,
            "ACADEMIC_JOURNAL_DIR": journal,
            "ACADEMIC_PROJECT": project,
            "PYTHONPATH": os.path.join(REPO_ROOT, "scripts"),
        },
    }


def claude_target(project_root: str) -> str:
    return os.path.join(project_root, ".mcp.json")


def cursor_target() -> str:
    return os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json")


def codex_target() -> str:
    return os.path.join(os.path.expanduser("~"), ".codex", "config.toml")


def _backup(path: str) -> None:
    if os.path.exists(path) and not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def install_claude(entry: Dict[str, Any], project_root: str, dry: bool) -> str:
    path = claude_target(project_root)
    data: Dict[str, Any] = {"mcpServers": {"academic-journal": entry}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        existing.setdefault("mcpServers", {})["academic-journal"] = entry
        data = existing
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if not dry:
        _backup(path)
        _atomic_write(path, text)
    return "%s\n%s" % (path, text)


def install_cursor(entry: Dict[str, Any], dry: bool) -> str:
    path = cursor_target()
    data: Dict[str, Any] = {"mcpServers": {"academic-journal": entry}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        existing.setdefault("mcpServers", {})["academic-journal"] = entry
        data = existing
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if not dry:
        _backup(path)
        _atomic_write(path, text)
    return "%s\n%s" % (path, text)


def install_codex(entry: Dict[str, Any], dry: bool) -> str:
    path = codex_target()
    block = [
        "",
        "# Academic-work journal (добавлено scripts/install_mcp.py)",
        "[mcp_servers.academic-journal]",
        'command = "%s"' % entry["command"],
        "args = [%s]" % ", ".join('"%s"' % a for a in entry["args"]),
        "[mcp_servers.academic-journal.env]",
        'ACADEMIC_DB = "%s"' % entry["env"]["ACADEMIC_DB"],
        'ACADEMIC_JOURNAL_DIR = "%s"' % entry["env"]["ACADEMIC_JOURNAL_DIR"],
        'ACADEMIC_PROJECT = "%s"' % entry["env"]["ACADEMIC_PROJECT"],
        'PYTHONPATH = "%s"' % entry["env"]["PYTHONPATH"],
        "",
    ]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if "mcp_servers.academic-journal" in content:
            return "%s (уже настроен, пропущено)" % path
        text = content.rstrip("\n") + "\n" + "\n".join(block)
    else:
        text = "\n".join(block).lstrip("\n")
    if not dry:
        _backup(path)
        _atomic_write(path, text)
    return "%s\n%s" % (path, text)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Установка MCP-сервера Academic-work")
    p.add_argument("--agent", choices=["claude", "cursor", "codex", "all"], default="claude")
    p.add_argument("--python", default=sys.executable or "python3")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--journal-dir", default=DEFAULT_JOURNAL)
    p.add_argument("--project", default=os.environ.get("ACADEMIC_PROJECT", "default"))
    p.add_argument("--project-root", default=os.getcwd())
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    entry = server_entry(args.python, os.path.expanduser(args.db),
                         os.path.expanduser(args.journal_dir), args.project)
    agents = ["claude", "cursor", "codex"] if args.agent == "all" else [args.agent]
    for agent in agents:
        print("=== %s ===" % agent)
        if agent == "claude":
            print(install_claude(entry, args.project_root, args.dry_run))
        elif agent == "cursor":
            print(install_cursor(entry, args.dry_run))
        else:
            print(install_codex(entry, args.dry_run))
    if args.dry_run:
        print("\n(dry-run: файлы не изменены)")
    else:
        print("\nГотово. Перезапустите агента, чтобы он подхватил MCP-сервер.")
        print("Проверка: попросите агента вызвать инструмент journal.verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
