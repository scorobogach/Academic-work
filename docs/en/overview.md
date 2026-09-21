# Academic-work — architecture overview (English, short)

Full documentation is in Russian: [README.md](../../README.md), [architecture.md](../architecture.md),
[gate-reference.md](../gate-reference.md).

## Core idea

One source of truth — an append-only, hash-chained journal. Everything else
(SQLite registries, semantic memory, summaries) is a derived index that can be
rebuilt. The journal cannot be rebuilt from them.

| Layer | Holds | Where | Property |
|---|---|---|---|
| Journal (truth) | messages, tool calls, files, gate runs | `~/.academic/journal/YYYY-MM.jsonl` | append-only, hash chain, verified by `make journal-verify` |
| Registries (structure) | evidence, claims, decisions, gates | `~/.academic/journal.db` (SQLite + FTS5) | editable, indexed, exportable to CSV/Markdown |
| Memory (search) | decisions, constraints, lessons | Engram / agentmemory | derived; every entry must cite a `journal_id` |

## Journal record

```json
{"v":1,"id":"01J9Z8...","ts":"2026-09-21T10:15:03.221Z","session":"s-2026-09-21-01",
 "project":"thesis-ch4","actor":"agent:claude-code","type":"gate_run",
 "refs":{"parent":"01J9Z8...","artifact":"sha256:9f2c..."},
 "payload":{"gate":"G3_bibliography","verdict":"fail","blocking":true},
 "prev":"sha256:1a4e...","hash":"sha256:7bd0..."}
```

`hash = sha256(canonical_json(record without hash))`, `prev = hash of the previous
record`. Tampering or deleting a line breaks the chain and is detected by
`make journal-verify`. Canonical form: sorted keys, `,` / `:` separators, UTF-8.

## Gates

| Gate | Checks | Runner | Blocking |
|---|---|---|---|
| G0 `hygiene` | invisible Unicode, bidi controls | script | yes |
| G1 `style` | filler phrases, unsourced claims, rhythm, em dashes (threshold 90/100) | script | yes |
| G2 `fidelity` | numbers, quotes, caveats, conclusions unchanged after editing | second agent | yes |
| G3 `bibliography` | every reference exists, has DOI/URL, metadata complete; optional Crossref | script | yes |
| G4 `claim_alignment` | claim ↔ source: locator (page/paragraph), verbatim quote match | agent + human | yes |
| G5 `numbers` | every number traced to `results.json` | script | configurable |
| G6 `reproducibility` | tables and figures rebuilt from raw data with one command | `make reproduce` | yes |
| G7 `disclosure` | AI-use log, disclosure section, conflicts, human sign-off | human | yes |

Monthly canary test: 5 false claims + 3 fake references are injected; if the gates
miss them (recall < 0.8), their green status stops counting.

## MCP tools

`journal.append`, `journal.search`, `journal.verify`, `claims.upsert / list / summary`,
`evidence.upsert / list`, `decisions.upsert / list`, `gates.record / latest`,
`resume.build`, `text.check`.

Install: `python3 scripts/install_mcp.py --agent all` (Claude Code, Cursor, Codex CLI).

## Storage decision: SQLite

One file, offline, FTS5 handles Cyrillic out of the box, backup = copy. Move to
PostgreSQL only when several people write simultaneously or a managed server with
backups already exists. The journal itself is plain files and never changes.

## Deliberate exclusions

Watermark / provenance removers, paid hosted citation checkers (unpublished text
leaves your machine), and `chirino/memory-service` (documented by its author as a
proof of concept).

## Hard limits

No pipeline proves an experiment took place. Gates verify text, references,
artefact consistency and recomputation. Raw data, protocols, logs and independent
reproduction remain a human responsibility. The style gate is a quality filter,
not a detector-evasion tool.
