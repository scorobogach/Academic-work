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
  python3 scripts/journal_cli.py gate-quote --source literature/smith2020.pdf --quote-file q.txt --locator "p.7"
  python3 scripts/journal_cli.py run-gate G4 --db ~/.academic/journal.db --project thesis
  python3 scripts/journal_cli.py export --out export --what all --format both
  python3 scripts/journal_cli.py canary --file draft.md --seed 42
  python3 scripts/journal_cli.py canary-check --manifest draft.canary.json --g1 r/G1.json --g3 r/G3.json --g5 r/G5.json
  python3 scripts/journal_cli.py disclosure --out ai-disclosure.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from academic_journal import (  # noqa: E402
    canary as canary_mod,
    db,
    disclosure as disclosure_mod,
    export as export_mod,
    gates as gates_mod,
    latex,
    quote as quote_mod,
    resume as resume_mod,
)
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
    elif gate == "G4":
        result = quote_mod.check_claims(conn, args.project if args.project else None,
                                        policy=gates_mod.load_policy(args.policy))
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


def cmd_gate_quote(args) -> int:
    """Разовая verbatim-проверка цитаты (без реестра)."""
    quote = args.quote
    if args.quote_file:
        with open(os.path.expanduser(args.quote_file), encoding="utf-8") as fh:
            quote = fh.read()
    if not quote:
        print("нужна цитата: --quote TEXT или --quote-file FILE", file=sys.stderr)
        return 2
    check = quote_mod.check_quote(quote, args.source, args.locator)
    if args.json:
        print(json.dumps(check.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("совпадение: %s (ratio %.2f)" % (check.match, check.ratio))
        print("источник:   %s [%s]" % (args.source, check.method))
        if check.page:
            print("страница:   %s%s" % (check.page,
                                        " (в локаторе указано %s)" % check.expected_page
                                        if check.expected_page else ""))
        if check.window:
            print("фрагмент:   %s" % check.window[:300])
        if check.note:
            print("примечание: %s" % check.note)
        if args.record:
            conn = db.connect(args.db)
            db.record_gate(conn, {
                "run_id": ulid(),
                "project": args.project or os.environ.get("ACADEMIC_PROJECT") or "default",
                "gate": "G4_claim_alignment",
                "verdict": "pass" if check.match in ("exact", "normalized") else "fail",
                "blocking": check.match in ("mismatch", "unverifiable"),
                "checks": [{"name": "verbatim_match", "result": check.match,
                            "threshold": "exact|normalized", "blocking": True}],
                "artifact": args.source,
            })
    return 0 if check.match in ("exact", "normalized") else 1


def cmd_export(args) -> int:
    conn = db.connect(args.db)
    created = export_mod.export(conn, args.out, args.what, args.format, args.project)
    for path in created:
        print(path)
    return 0


def cmd_canary(args) -> int:
    with open(os.path.expanduser(args.file), encoding="utf-8") as fh:
        text = fh.read()
    kind = "tex" if (args.file or "").endswith(".tex") else "md"
    counts = {"unsourced": args.unsourced, "references": args.references,
              "numbers": args.numbers}
    injected, manifest = canary_mod.inject(text, kind=kind, seed=args.seed, counts=counts)
    text_path = args.out or _default_canary_path(args.file)
    manifest_path = args.manifest or (os.path.splitext(os.path.expanduser(args.file))[0] + ".canary.json")
    with open(text_path, "w", encoding="utf-8") as fh:
        fh.write(injected)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print("подмешано элементов: %d" % len(manifest["items"]))
    print("текст с канарками:   %s" % text_path)
    print("манифест:            %s" % manifest_path)
    print("")
    print("дальше прогоните гейты по этому файлу и запустите canary-check:")
    print("  python3 scripts/journal_cli.py canary-check --manifest %s \\" % manifest_path)
    print("      --g1 reports/gates/G1-style.json --g3 reports/gates/G3-bibliography.json \\")
    print("      --g5 reports/gates/G5-numbers.json")
    return 0


def _default_canary_path(path: str) -> str:
    base, ext = os.path.splitext(path)
    return base + ".canary" + ext


def cmd_canary_check(args) -> int:
    with open(os.path.expanduser(args.manifest), encoding="utf-8") as fh:
        manifest = json.load(fh)
    reports = {}
    for gate, path in (("G1", args.g1), ("G3", args.g3), ("G5", args.g5)):
        if path:
            reports[gate] = path
    if not reports:
        print("нужен хотя бы один отчёт: --g1/--g3/--g5", file=sys.stderr)
        return 2
    report = canary_mod.evaluate(manifest, reports, threshold=args.threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(canary_mod.render_markdown(report))
    if args.record:
        conn = db.connect(args.db)
        db.record_gate(conn, {
            "run_id": ulid(),
            "project": args.project or os.environ.get("ACADEMIC_PROJECT") or "default",
            "gate": "GX_canary",
            "verdict": report["verdict"],
            "blocking": report["verdict"] == "fail",
            "checks": [{"name": "canary_recall", "result": report["recall"],
                        "threshold": args.threshold, "blocking": True}],
            "artifact": args.manifest,
        })
    return 0 if report["verdict"] == "pass" else 1


def cmd_disclosure(args) -> int:
    journal, conn = _journal_and_conn(args)
    result = disclosure_mod.build(journal)
    text = result["markdown"]
    if args.out:
        with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
            fh.write(text)
        print("written: %s" % args.out)
    else:
        print(text)
    if not result["ok"]:
        print("Замечания: %d — disclosure не готов к сдаче" % len(result["warnings"]),
              file=sys.stderr)
    return 0 if result["ok"] else 1


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

    q = sub.add_parser("gate-quote", help="G4: verbatim-сверка цитаты с источником")
    q.add_argument("--source", required=True, help="PDF/TXT/MD источника")
    q.add_argument("--quote", help="текст цитаты")
    q.add_argument("--quote-file", help="файл с цитатой")
    q.add_argument("--locator", default="", help="локатор: p.7, с. 7, 7")
    q.add_argument("--record", action="store_true", help="записать прогон в БД")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_gate_quote)

    x = sub.add_parser("export", help="экспорт реестров в CSV/Markdown")
    x.add_argument("--out", default="export", help="каталог")
    x.add_argument("--what", default="all", choices=["all", "evidence", "claims", "decisions", "gates"])
    x.add_argument("--format", default="both", choices=["csv", "md", "both"], dest="format")
    x.set_defaults(func=cmd_export)

    cn = sub.add_parser("canary", help="подмешать ложные элементы в копию черновика")
    cn.add_argument("--file", required=True)
    cn.add_argument("--out", help="куда записать текст с канарками")
    cn.add_argument("--manifest", help="куда записать манифест")
    cn.add_argument("--seed", type=int, default=42)
    cn.add_argument("--unsourced", type=int, default=5)
    cn.add_argument("--references", type=int, default=3)
    cn.add_argument("--numbers", type=int, default=1)
    cn.set_defaults(func=cmd_canary)

    cc = sub.add_parser("canary-check", help="посчитать recall контура по отчётам гейтов")
    cc.add_argument("--manifest", required=True)
    cc.add_argument("--g1", help="отчёт G1_style")
    cc.add_argument("--g3", help="отчёт G3_bibliography")
    cc.add_argument("--g5", help="отчёт G5_numbers")
    cc.add_argument("--threshold", type=float, default=0.8)
    cc.add_argument("--record", action="store_true", help="записать прогон в БД")
    cc.add_argument("--json", action="store_true")
    cc.set_defaults(func=cmd_canary_check)

    d = sub.add_parser("disclosure", help="собрать раздел «Использование ИИ» из журнала")
    d.add_argument("--out", help="файл для готового раздела")
    d.set_defaults(func=cmd_disclosure)

    g = sub.add_parser("run-gate")
    g.add_argument("gate", choices=["G0", "G1", "G3", "G4", "G5"])
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
