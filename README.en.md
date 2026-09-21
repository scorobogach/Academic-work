# Academic-work (short English overview)

Infrastructure for doing academic work with AI assistants: an immutable journal,
verifiable claims, and session-to-session memory.

**Full documentation is in Russian** — see [README.md](README.md) and [docs/](docs/).

## The three requirements, honestly scoped

| Requirement | What is actually achievable | This repo |
|---|---|---|
| "Remove AI markers / traces from the text" | Technical artefacts (invisible Unicode, bidi controls) and formulaic filler — yes, as a **quality** measure. Beating an AI detector — no: it does not improve accuracy and conflicts with disclosure rules | Gates **G0**, **G1** + Russian rule sets in `config/style-rules.ru.json` |
| "Everything must be verifiable and real" | References and claims are verifiable. That an experiment **happened** is proven by raw data, code, environment and reproduction — not by prose | `evidence` / `claims` / `decisions` registries, gates **G3–G6**, data manifest |
| "Keep the whole dialogue in memory, never revisit settled things" | Yes: append-only full journal + separate searchable memory. Summaries never replace the journal | `journal/YYYY-MM.jsonl` with a hash chain, SQLite + FTS5, MCP server, `make resume` |

## Quick start

```bash
make test                                   # 71 tests, stdlib only, Python 3.9+
make init                                   # ~/.academic/journal.db + journal/
python3 scripts/install_mcp.py --agent all  # Claude Code | Cursor | Codex CLI
make gates MANUSCRIPT=examples/minimal/main.tex BIB=examples/minimal/refs.bib \
            VALUES=examples/minimal/analysis/results.json
make resume                                 # context pack for the next session
```

## Gates

| Gate | Checks | Runner | Blocking |
|---|---|---|---|
| G0 `hygiene` | invisible Unicode, bidi controls | script | yes |
| G1 `style` | filler phrases, unsourced claims, rhythm, em dashes (threshold 90/100) | script | yes |
| G2 `fidelity` | numbers, quotes, caveats, conclusions unchanged after editing | second agent | yes |
| G3 `bibliography` | every reference exists, has DOI/URL, complete metadata; optional Crossref check | script | yes |
| G4 `claim_alignment` | every claim has a source, a locator (page/paragraph) and a verbatim quote match | script + human | yes |
| G5 `numbers` | every number in prose **and in tables** traced back to `results.json` | script | configurable |
| G6 `reproducibility` | tables and figures rebuilt from raw data with one command | `make reproduce` | yes |
| G7 `disclosure` | AI-use log, disclosure section, conflict of interest, human sign-off | `make disclosure` + human | yes |
| GX `canary` | recall of the gate pipeline itself: injected false claims must be caught | script | yes if recall < 0.8 |

Plus a monthly **canary test** (`make canary-run`): 5 false claims, 3 fake references and
one untraced number are injected into a copy of the draft, then recall is computed.
Recall < 0.8 blocks: the green status of the gates stops counting.

Other commands: `make export` (registries to CSV/Markdown), `make disclosure`
(AI-use section generated from the journal log), `make gate-quote` (one-off quote check).

## Architecture in one table

| Layer | Holds | Lives in | Property |
|---|---|---|---|
| Journal (truth) | messages, tool calls, files, gate runs | `~/.academic/journal/YYYY-MM.jsonl` | append-only, hash-chained, verified by `make journal-verify` |
| Registries (structure) | evidence, claims, decisions, gates | `~/.academic/journal.db` (SQLite + FTS5) | editable, indexed |
| Memory (search) | decisions, constraints, lessons | Engram / agentmemory | derived, always links back to `journal_id` |

## Deliberately excluded

- Watermark / provenance removers: stripping C2PA or SynthID markers from academic
  work conflicts with AI-use disclosure rules. This project does the opposite — it
  preserves provenance.
- Paid hosted citation checkers: sending unpublished manuscripts to a third party.
- `chirino/memory-service`: documented by its author as a proof of concept.

## Limits

No pipeline can prove an experiment took place. Gates verify text, references,
artefact consistency and recomputation. Raw data, protocols, logs and independent
reproduction remain a human responsibility. The style gate is a quality filter,
not a detector-evasion tool.

## License

Code and docs: MIT. Third-party projects keep their own licences — see
[NOTICE.md](NOTICE.md); `academic-research-skills` is **CC BY-NC 4.0** and is
referenced as an external plugin, never vendored.
