# REVIEW.md — `dbnocode`: DSL-driven Curses TUI framework

## 1. What this is
A "no-code" framework: users author `.dsl` scripts → `main.py` resolves `INCLUDE`s,
runs a text-level validator, parses to an `app` dict (compact `compact_parser.py`
is canonical; verbose `parser.py` is a secondary path), runs semantic validation,
then `ScriptRunner` renders a Python-`curses` TUI bound to SQLite/libSQL/Postgres/
Firebird. All screens are generated from the DSL — no application code required.

## 2. Architecture / pipeline (`main.py`)
1. `_load_dsl_with_includes` — recursive inline of `INCLUDE`, cycle detection, `//` comment strip.
2. `_is_compact(text)` — heuristic on the first `FORM` line.
3. `DSLValidator.validate` — block-balance + declared-ID + hotkey + undeclared-target checks.
4. `CompactDSLParser` / `DSLParser` → `app` dict.
5. `validate_app_definition` — dict-level semantic checks.
6. `ScriptRunner(app_def).run()` → `curses.wrapper(self._main_loop)`.

## 3. Fixed since the last review
These were previously listed as open defects and have since been resolved
(verified in the current tree):

- **Unicode/terminal crash** — `locale.setlocale(locale.LC_ALL, "")` is now set
  in `ScriptRunner.run()` before `curses.wrapper` (`dsl_lib/runner.py`), so
  box/arrow glyphs no longer raise `UnicodeEncodeError` on stock locales.
- **Color-pair collision** — `curved_box.py` uses `_CP_OFFSET = 70`; warehouse
  pairs derive from it (78–84), leaving themes (1–65) untouched. No module
  re-inits another's pair IDs.
- **Nested-commit atomicity** — `ScriptExecutor.run` now tracks `started_txn`
  and only commits when it began the transaction, honoring `_in_txn`.
- **Remote DB write crash** — `business_settings` save is wrapped in
  `try/except`, logs via `_log`, and reports a friendly message instead of
  killing the session.
- **Validator B2B false-positive** — `B2B` (and `.INBOX`/`.OUTBOX` via base
  split) is in the builtin target set.
- **Silent Hrana batch failures** — `RemoteSQLAdapter.execute_many` /
  `query_many` now raise on `response_error` (or unanswered requests) instead of
  discarding results; both callers already fall back per-statement.
- **Whole-table `_rowid` scans** — FETCH/UPDATE/DELETE with a single
  `_rowid = expr` predicate now query `WHERE rowid = ?` instead of loading and
  filtering the entire table.
- **Server duplication** — `FBServer.py` is now a thin entry point over
  `server.py`; the near-duplicate implementation is gone.
- **Repo hygiene** — `.claude/`, prompt files, binaries, per-user JSON, logs and
  throwaway `_test_*.bat`/`_debug.bat` scripts are removed/gitignored; build
  scripts consolidated to `build_all.bat` + `run.bat`; README added.

## 4. Remaining findings

### 🟠 Maintainability / duplication
- **Three god-objects (~11.4k lines):** `runner.py` (~6.0k: entry + all screens +
  DSL engine + DB), `cxgrid.py` (~3.0k), `layout.py` (~2.5k). DSL runtime is
  co-located with UI.
- **Parallel verbose/compact parsers** share no base and re-implement field/flag
  logic → drift risk. Compact is canonical; the verbose dialect is lightly used
  and effectively untested.
- **`_safe_addstr` duplicated** in ~5 modules (`curved_box`, `cxgrid`, `cxreport`,
  `menu`, `tree_grid`, `report_designer`) — extract one helper.
- **No `delwin` anywhere** — long-running draw loops allocate windows per
  iteration and rely on GC (`report_designer.py`, `runner.py`).

### 🟡 Robustness gaps
- **Resize handling is partial:** `cxreport`, `menu`, `report_designer` and the
  runner handle `KEY_RESIZE`; `cxgrid`, `pos_screen`, `warehouse_grid`,
  `widgets`, `layout`, `curved_box` do not. `cxgrid` caches dimensions at
  construction.
- **No minimum-terminal-size guard** — small terminals can overflow on direct
  `addstr` paths.
- **`eval()` formula/filter sandboxes** (`runner.py`, `layout.py`,
  `report_engine.py`) restrict builtins but are not a real security boundary —
  acceptable only because DSL is developer-authored.
- **`_ensure_rowid_gen` swallows all exceptions** when creating the Firebird
  sequence/trigger (`adapters.py`); a genuine failure (e.g. permissions) is
  silent.
- **Load-then-filter remains for non-`_rowid` predicates** — WHERE clauses on
  other fields still read the whole table; only `_rowid` equality is pushed down.

### 🟡 Testing / CI / build hygiene
- **No automated test suite or CI.** There is no `tests/`, `conftest.py`,
  `.github/`, or pytest config; the largest/riskiest modules (`runner.py`,
  `cxgrid.py`, `layout.py`) have no behavioral coverage.
- **No LICENSE or CHANGELOG** (README added).
- **`errors.log` has no rotation** and grows unbounded (gitignored, local only).
- **Secrets in `remote_connections.json`** (plaintext passwords + production IPs)
  — now gitignored and absent from history; keep it that way and rotate any
  credentials ever shared.

### 🟢 Strengths to preserve
- Script engine is a real tokenizer→AST interpreter (no `eval` of user logic).
- Parameterized SQL throughout; no end-user injection surface.
- Four backends; standalone local SQLite by default, server optional.
- Atomic multi-statement postings, centralized Hrana error propagation.
- DSL docs are excellent: `docs/dsl_tutorial.md`, `canonical_dsl.md`,
  `grammar.ebnf`, `tutorial.txt`, `bnf_gramma.txt`.

## 5. Prioritized remediation roadmap
1. **Testability:** add `pytest` + a curses smoke test for `ScriptRunner.run()`;
   cover `cxgrid`/`layout`/`adapters` and the compact parser; wire a GitHub
   Action.
2. **Cross-cutting TUI concerns:** finish `KEY_RESIZE` handling and add a
   minimum-size guard; add `delwin` to per-frame window loops.
3. **Extract shared utilities:** one `_safe_addstr`; centralize cursor policy +
   theme-color init.
4. **Architecture:** decompose `runner.py` (DSL engine vs UI controller);
   extend predicate pushdown beyond `_rowid`; unify or formally deprecate the
   verbose dialect.
5. **Hygiene:** add LICENSE + CHANGELOG; log rotation; keep secrets out of the
   repo and rotate anything ever exposed.

## 6. Verdict
A functional, well-documented framework whose DSL layer, docs and multi-backend
data engine are the real assets. The crash-class defects have been cleared; the
remaining work is structural — decomposing the god-objects, finishing
cross-cutting TUI concerns (resize, window cleanup), and adding a real test/CI
safety net.
