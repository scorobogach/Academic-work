#!/usr/bin/env python3
"""CLI журнала и quality-gates Academic-work.

  python3 scripts/journal_cli.py init
  python3 scripts/journal_cli.py append --type note --payload '{"text":"..."}' --actor human
  python3 scripts/journal_cli.py search --query "дизайн выборки"
  python3 scripts/journal_cli.py verify
  python3 scripts/journal_cli.py manifest
  python3 scripts/journal_cli.py resume --project thesis
  python3 scripts/journal_cli.py run-gate G0 --file draft.tex
  python3 scripts/journal_cli.py run-gate G1 --file draft.tex
  python3 scripts/journal_cli.py run-gate G3 --tex main.tex --bib refs.bib [--online]
  python3 scripts/journal_cli.py run-gate G5 --file draft.tex --values analysis/results.json
  python3 scripts/journal_cli.py hygiene --file draft.tex [--fix]
  python3 scripts/journal_cli.py claim --file claim.json
  python3 scripts/journal_cli.py evidence --file source.json
  python3 scripts/journal_cli.py decision --file decision.json
  python3 scripts/journal_cli.py gate-record --file gate.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from academic_journal import db, gates as gates_mod, latex, resume as resume_mod  # noqa: E402
from academic_journal.journal import Journal, ulid  # noqa: E402

DEFAULT_DB = os.path.expanduser("~/.academic/journal.db")
DEFAULT_DIR = os.path.expanduser("~/.academic/journal")


def _journal_and_conn(args) -> tuple:
    conn = db.connect(getattr(args, "db", DEFAULT_DB))
    journal = Journal(getattr(args, "journal_dir", DEFAULT_DIR),
                      session=getattr(args, "session", "") or ("s-" + time.strftime("%Y%m%d")),
                      project=getattr(args, "project", "") or "default",
                      indexer=lambda rec: db.index_event(conn, rec))
    return journal, conn


def _load_json_arg(value: str) -> dict:
    if value.strip().startswith("{"):
        return json.loads(value)
    with open(os.path.expanduser(value), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2) if not isinstance(obj, str) else obj)


def cmd_init(args) -> int:
    conn = db.connect(args.db)
    journal, _ = _journal_and_conn(args)
    print("db:      %s" % args.db)
    print("journal: %s" % journal.root)
    print("project: %s" % args.project)
    print("session: %s" % (args.session or "s-" + time.strftime("%Y%m%d")))
    return 0


def cmd_append(args) -> int:
    journal, _ = _journal_and_conn(args)
    project = args.project or os.environ.get("ACADEMIC_PROJECT") or "default"
    rec = journal.append(type=args.type, payload=_load_json_arg(args.payload), actor=args.actor,
                         session=args.session or None, project=project,
                         refs=_load_json_arg(args.refs) if args.refs else None)
    _print(rec)
    return 0


def cmd_search(args) -> int:
    journal, conn = _journal_and_conn(args)
    rows = db.search_events(conn, args.query, args.limit, args.type, args.project) if args.indexed \
        else journal.search(args.query, args.limit, args.type)
    _print(rows)
    return 0


def cmd_verify(args) -> int:
    journal, _ = _journal_and_conn(args)
    result = journal.verify()
    _print(result)
    return 0 if result["ok"] else 1


def cmd_manifest(args) -> int:
    journal, _ = _journal_and_conn(args)
    _print(journal.manifest())
    return 0


def cmd_tail(args) -> int:
    journal, _ = _journal_and_conn(args)
    _print(journal.tail(args.limit))
    return 0


def cmd_resume(args) -> int:
    conn = db.connect(args.db)
    text = resume_mod.build(conn, args.project)
    if args.out:
        with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("written: %s" % args.out)
    else:
        print(text)
    return 0


def cmd_run_gate(args) -> int:
    conn = db.connect(args.db)
    args.project = args.project or os.environ.get("ACADEMIC_PROJECT") or "default"
    gate = args.gate.upper()
    if gate == "G0":
        text = open(os.path.expanduser(args.file), encoding="utf-8").read()
        result = gates_mod.check_hygiene(text)
    elif gate == "G1":
        text = open(os.path.expanduser(args.file), encoding="utf-8").read()
        result = gates_mod.check_style(text, gates_mod.load_rules(args.rules), gates_mod.load_policy(args.policy))
    elif gate == "G3":
        result = gates_mod.check_bibliography(os.path.expanduser(args.tex), args.bib, offline=not args.online)
    elif gate == "G5":
        text = open(os.path.expanduser(args.file), encoding="utf-8").read()
        values = latex.load_values(args.values) if args.values else None
        kind = "tex" if (args.file or "").endswith(".tex") else "md"
        result = gates_mod.check_numbers(text, values, gates_mod.load_policy(args.policy), kind=kind)
    else:
        print("Гейт %s не детерминированный: G2/G4/G6/G7 выполняются агентом или человеком, "
              "результат фиксируется через `gate-record`" % gate, file=sys.stderr)
        return 2

    if args.report:
        os.makedirs(os.path.dirname(os.path.expanduser(args.report)) or ".", exist_ok=True)
        with open(os.path.expanduser(args.report), "w", encoding="utf-8") as fh:
            fh.write(result.to_json())
    if args.record:
        db.record_gate(conn, {
            "run_id": ulid(), "project": args.project, "gate": result.gate,
            "verdict": result.verdict, "blocking": result.blocking,
            "inputs_sha": result.inputs_sha, "checks": result.stats,
            "cost_usd": result.cost_usd, "duration_ms": result.duration_ms,
            "artifact": args.report or "",
        })
    if args.json:
        print(result.to_json())
    else:
        print("%s: %s (blocking=%s) %s" % (result.gate, result.verdict, result.blocking, result.stats))
        for f in result.findings[: args.show]:
            print("  [%-7s] %-18s %s" % (f.severity, f.code, f.message))
    return 1 if result.blocking else 0


def cmd_hygiene(args) -> int:
    path = os.path.expanduser(args.file)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    result = gates_mod.check_hygiene(text)
    if args.fix:
        fixed = gates_mod.fix_hygiene(text)
        if fixed != text and not args.in_place:
            backup = path + ".bak"
            with open(backup, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("backup: %s" % backup)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fixed)
        print("fixed: %s (%d ошибок до исправления)" % (path, result.stats.get("errors", 0)))
        return 0
    print(result.to_json())
    return 1 if result.blocking else 0


def _cmd_upsert(args, fn) -> int:
    conn = db.connect(args.db)
    data = _load_json_arg(args.file)
    data.setdefault("project", args.project or os.environ.get("ACADEMIC_PROJECT") or "default")
    _print(fn(conn, data))
    return 0


def cmd_claim(args) -> int:
    return _cmd_upsert(args, db.upsert_claim)


def cmd_evidence(args) -> int:
    return _cmd_upsert(args, db.upsert_evidence)


def cmd_decision(args) -> int:
    return _cmd_upsert(args, db.upsert_decision)


def cmd_gate_record(args) -> int:
    conn = db.connect(args.db)
    data = _load_json_arg(args.file)
    data.setdefault("project", args.project or os.environ.get("ACADEMIC_PROJECT") or "default")
    data.setdefault("run_id", ulid())
    _print(db.record_gate(conn, data))
    return 0


def cmd_extract(args) -> int:
    text = latex.expand_inputs(args.tex)
    out = {
        "cite_keys": sorted(set(latex.extract_cite_keys(text))),
        "sections": latex.extract_sections(text),
        "numbers": latex.extract_numbers(latex.strip_tex_noise(text))[: args.limit],
    }
    if args.bib:
        out["bib_entries"] = sorted(latex.parse_bib(args.bib))
    _print(out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="journal_cli", description="Academic-work: журнал, реестры и гейты")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--journal-dir", default=DEFAULT_DIR)
    p.add_argument("--project", default=None, help="проект; если не указан — фильтр по проекту не применяется")
    p.add_argument("--session", default=os.environ.get("ACADEMIC_SESSION", ""))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    a = sub.add_parser("append", help="добавить событие в журнал")
    a.add_argument("--type", required=True)
    a.add_argument("--payload", required=True, help="JSON или путь к .json")
    a.add_argument("--actor", default="human")
    a.add_argument("--refs", help="JSON со ссылками")
    a.set_defaults(func=cmd_append)

    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--type")
    s.add_argument("--indexed", action="store_true", help="искать через SQLite FTS (быстрее)")
    s.set_defaults(func=cmd_search)

    t = sub.add_parser("tail")
    t.add_argument("--limit", type=int, default=20)
    t.set_defaults(func=cmd_tail)

    sub.add_parser("verify").set_defaults(func=cmd_verify)
    sub.add_parser("manifest").set_defaults(func=cmd_manifest)

    r = sub.add_parser("resume")
    r.add_argument("--out", help="сохранить контекст-пак в файл")
    r.set_defaults(func=cmd_resume)

    g = sub.add_parser("run-gate")
    g.add_argument("gate", choices=["G0", "G1", "G3", "G5"])
    g.add_argument("--file", help="файл рукописи (.tex/.md) для G0/G1/G5")
    g.add_argument("--tex", help="корневой .tex для G3")
    g.add_argument("--bib", help=".bib для G3")
    g.add_argument("--values", help="results.json для G5")
    g.add_argument("--online", action="store_true", help="G3: проверять DOI через Crossref")
    g.add_argument("--rules")
    g.add_argument("--policy")
    g.add_argument("--report", help="путь для JSON-отчёта")
    g.add_argument("--record", action="store_true", help="записать прогон в БД")
    g.add_argument("--json", action="store_true")
    g.add_argument("--show", type=int, default=20)
    g.set_defaults(func=cmd_run_gate)

    h = sub.add_parser("hygiene")
    h.add_argument("--file", required=True)
    h.add_argument("--fix", action="store_true")
    h.add_argument("--in-place", action="store_true", help="не создавать .bak")
    h.set_defaults(func=cmd_hygiene)

    for name, fn in (("claim", cmd_claim), ("evidence", cmd_evidence),
                     ("decision", cmd_decision), ("gate-record", cmd_gate_record)):
        c = sub.add_parser(name)
        c.add_argument("--file", required=True, help="JSON-объект или путь к .json")
        c.set_defaults(func=fn)

    e = sub.add_parser("extract", help="извлечь ключи цитирования, секции и числа из .tex")
    e.add_argument("--tex", required=True)
    e.add_argument("--bib")
    e.add_argument("--limit", type=int, default=200)
    e.set_defaults(func=cmd_extract)
    return p


GLOBAL_FLAGS = {
    "--db": "db",
    "--journal-dir": "journal_dir",
    "--project": "project",
    "--session": "session",
}


def main(argv=None) -> int:
    """Глобальные флаги принимаются и до, и после подкоманды (удобно в Makefile)."""
    raw = list(sys.argv[1:] if argv is None else argv)
    defaults: dict = {}
    rest: list = []
    i = 0
    while i < len(raw):
        token = raw[i]
        if token in GLOBAL_FLAGS and i + 1 < len(raw):
            defaults[GLOBAL_FLAGS[token]] = raw[i + 1]
            i += 2
            continue
        if token.startswith("--") and "=" in token:
            name, value = token.split("=", 1)
            if name in GLOBAL_FLAGS:
                defaults[GLOBAL_FLAGS[name]] = value
                i += 1
                continue
        rest.append(token)
        i += 1
    parser = build_parser()
    if defaults:
        parser.set_defaults(**defaults)
    args = parser.parse_args(rest)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
