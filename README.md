# dbnocode

A "no-code" framework: author `.dsl` scripts and run them as a full
Python/**curses** terminal UI bound to SQLite, libSQL/sqld, PostgreSQL, or
Firebird. The DSL is parsed and validated, then rendered by a built-in TUI
runtime — no application code required.

## Quick start

```bash
pip install -r requirements.txt

# Run the bundled demo app (scripts/main.dsl)
python main.py

# Run a specific DSL script
python main.py scripts/02_membership_compact.dsl

# Validate a script without launching the TUI
python main.py --validate-only scripts/04_inventory_compact.dsl
```

By default the app uses a local SQLite file (standalone, no server needed).
A remote/shared backend is optional (see `server.py` / `FBServer.py`).

## Project layout

| Path | Purpose |
|------|---------|
| `main.py` | Entry point: include resolution → validate → parse → run |
| `dsl_lib/` | DSL validator, parser (verbose + compact), runner, script engine, DB adapters, report engine |
| `tui/` | Curses widgets: grid, layout, menus, themes, reports, warehouse map |
| `scripts/` | Example DSL apps (`main.dsl`, `02_membership_compact.dsl`, `04_inventory_compact.dsl`, …) |
| `docs/` | DSL reference and tutorials |

## Documentation

- **`docs/dsl_tutorial.md`** — beginner-to-intermediate tutorial for authoring the
  compact DSL (forms, fields, details, scripts, stock operations, menus, reports, users).
- **`docs/canonical_dsl.md`** — canonical compact-DSL reference.
- **`docs/grammar.ebnf`** — compact DSL grammar.
- **`docs/tutorial.txt`** — long-form worked tutorial.
- **`REVIEW.md`** — architecture overview, known issues, and a prioritized
  remediation roadmap.

## Authoring a script

```text
APP "My App" VERSION "1.0"
DATASOURCE "myapp_db" ADAPTER "sqlite"

FORM customer TITLE "Customers" COLS 2
  name | 40 | req span:2
  kind | 12 | opts:Retail,Wholesale
  LIST name:30 kind:12
END
```

See `docs/dsl_tutorial.md` for the full language.

## Notes

- Run your terminal in a UTF-8 locale so box-drawing glyphs render correctly.
- Known limitations and planned fixes are tracked in `REVIEW.md`.
