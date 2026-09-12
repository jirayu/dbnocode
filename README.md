# dbnocode — Build full business apps without writing code

dbnocode is a **no-code application platform** that turns a short text script
into a complete, production-style business application — data entry forms,
browse grids, menus, workflows, reports and an inventory engine — with zero
application code.

Describe your screens in a `.dsl` script, run it, and dbnocode validates,
parses and renders a full terminal (TUI) application bound to your database.
No Python modules to write. No web stack to deploy. **Edit a text file and
re-run — that's the entire development loop.**

```text
FORM customer TITLE "Customers" COLS 2
  name | 40 | req span:2
  kind | 12 | opts:Retail,Wholesale
  LIST name:30 kind:12
END
```

That is a complete app: a customer table, an entry form, and a browsable list.

## Why dbnocode

- **Go from idea to working app in minutes** — screens, actions and menu wiring
  are auto-generated from your form declarations.
- **Runs anywhere** — a fast, keyboard-driven terminal UI that runs on a stock
  terminal with no browser, no server, and no runtime install needed.
- **Zero-config database** — SQLite by default; the database file is created
  on first run. PostgreSQL, Firebird and libSQL/sqld are one-line changes.
- **Ship as a single EXE** — one build script produces a distributable runtime
  and a project initializer your end users can run without Python.
- **Built for real business workflows** — document headers with detail grids,
  automatic postings, stock movements, multi-level user permissions, and
  PDF reports.

## What you can build

| Capability | What it gives you |
|---|---|
| **Forms & grids** | Multi-column entry forms, sections, child detail grids, lookups, dropdowns, computed fields, auto-number prefixes |
| **Inventory engine** | RECEIVE / ISSUE / ALLOCATE / TRANSFER with lot tracking, pack-unit conversion, and stock-card postings in one transaction |
| **Postings & workflows** | `ON SAVE` auto-posting, draft→posted→void status flows, and a small safe scripting language |
| **Reports** | Listing, summary and crosstab reports with totals, live filter prompts, and PDF export |
| **Users & permissions** | Login context with numeric levels and field-level lockout that fails safe to read-only |
| **Menus & navigation** | Pulldown top-bar menus or a center launcher, hotkeys, module `INCLUDE`s |
| **Multi-user backends** | Optional shared server for PostgreSQL / Firebird / libSQL — run with a local file, or go remote |

## Quick start

```bash
pip install -r requirements.txt

# Run the bundled ERP demo app
python main.py

# Run a specific script
python main.py scripts/04_inventory_compact.dsl

# Validate a script without launching the UI
python main.py --validate-only scripts/04_inventory_compact.dsl
```

By default the app uses a local SQLite file — fully standalone, no server
required. A remote/shared backend is optional (`server.py` / `FBServer.py`).

## Choose your database

One line in your script switches backends:

```text
DATASOURCE "myapp_db" ADAPTER "sqlite"      # zero setup, local file
DATASOURCE "myapp_db" ADAPTER "postgres"    # PostgreSQL
DATASOURCE "myapp_db" ADAPTER "firebird"    # Firebird
```

## Distribute to end users

`build_all.bat` produces ready-to-run EXEs:

- `dsl_tui_app.exe` — the runtime (bundles engine + scripts, no Python needed)
- `dbnocode_init.exe` — project initializer your end users can run
- `dbnocode-server.exe` / `dbnocode-fbserver.exe` — optional shared backends

## Learning & reference

- **[`docs/dsl_tutorial.md`](docs/dsl_tutorial.md)** — step-by-step tutorial:
  forms, fields, details, scripts, stock, menus, reports, users.
- **[`docs/canonical_dsl.md`](docs/canonical_dsl.md)** — the canonical DSL reference.
- **[`docs/grammar.ebnf`](docs/grammar.ebnf)** — formal grammar.
- **[`docs/tutorial.txt`](docs/tutorial.txt)** — long-form worked tutorial.
- **[`REVIEW.md`](REVIEW.md)** — architecture notes, known limitations and the
  remediation roadmap.

## Project layout

| Path | Purpose |
|------|---------|
| `main.py` | Entry point: include resolution → validate → parse → run |
| `dsl_lib/` | Validator, parsers, runner, script engine, DB adapters, report engine |
| `tui/` | Terminal widgets: grids, layout, menus, themes, reports |
| `scripts/` | Example DSL apps, from a menu-only starter to a full ERP |
| `docs/` | DSL reference and tutorials |
| `init_app.py` | Scaffolds a new project folder (TUI + GUI wizards) |

## Notes

- Run your terminal in a UTF-8 locale so box-drawing glyphs render correctly.
- The core loop is *edit the script, rerun* — see `REVIEW.md` for known
  limitations and the roadmap.