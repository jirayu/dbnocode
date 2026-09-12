# REVIEW.md — `dbnocode-oc`: DSL-driven Curses TUI framework

## 1. What this is
A "no-code" framework: users author `.dsl` scripts → `main.py` resolves `INCLUDE`s, runs a text-level validator, parses to an `app` dict (verbose `parser.py` or compact `compact_parser.py`), runs semantic validation, then `ScriptRunner` renders a Python‑`curses` TUI bound to SQLite/libSQL/Postgres/Firebird. Two dialects exist; **compact is the real workhorse** and the runner is built around it.

## 2. Architecture / pipeline (`main.py:97`)
1. `_load_dsl_with_includes` — recursive inline of `INCLUDE`, cycle detection (`main.py:50-94`), `//` comment strip.
2. `_is_compact(text)` — heuristic on first `FORM` line (`main.py:14-27`).
3. `DSLValidator.validate` — block-balance + declared-ID + hotkey + undeclared-action checks.
4. `CompactDSLParser` / `DSLParser` → `app` dict.
5. `validate_app_definition` — dict-level semantic checks (reported at **line 0**, losing source location).
6. `ScriptRunner(app_def).run()` → `curses.wrapper(self._main_loop)` (`runner.py:5559`).

## 3. Severity-ordered findings

### 🔴 Critical defects
- **Unicode crash kills the app.** No `locale.setlocale(locale.LC_ALL, '')` anywhere. `addstr` of box/arrow glyphs (`curved_box.py:33-38`, `statusline.py:79/85`) raises `UnicodeEncodeError` (not `curses.error`) on stock-locale terminals, uncaught in per-screen loops → propagates to `runner.run()`'s broad `except` (`runner.py:5560`) → `crash.log` + exit.
- **Color-pair collision corrupts theming.** `themes.py` owns pairs 1–65 (`CLR_HEADER=20`…`CLR_HL_DIM=35`, report 40–49, search 60–65). `curved_box.py` (`_CP_OFFSET=20`, 21–27) and `warehouse_grid.py` (28–34) re-`init_pair` the same band, clobbering `cxgrid`'s `CLR_SELECTED/CLR_ALT_ROW`. Contradictory comments (`cxgrid.py:440`, `curved_box.py:16`) hide it. After visiting the warehouse screen, grid colors are wrong until theme re-pick.
- **Nested-commit breaks atomicity.** `ScriptExecutor.run` calls `self.db.commit()` unconditionally (`script_engine.py:660`) instead of honoring `_in_txn`, violating the adapter's own contract (`adapters.py:53-57`) → inner commit prematurely commits outer save.
- **One remote DB write crashes the TUI.** `crash.log` shows `runner.py:1406` (`_open_business_settings` INSERT) throwing `Hrana server closed…` with no `try/except`, killing the session (`runner.py:5987→5559`).
- **Validator false-positive (B2B).** `validator.py:71` omits `B2B`/`B2B.INBOX`/`B2B.OUTBOX` from builtins, yet `compact_parser.py:1173` maps them to built-in actions → valid compact scripts wrongly rejected as `UNDECLARED_TARGET`.
- **Verbose dialect is a trap.** `parser.py` emits a schema the runner doesn't support (no auto layouts/actions) and has **zero tests**; `--validate-only` passes but it can't drive the TUI. The "produces the same app dict" docstring (`compact_parser.py:1`) is false.

### 🟠 Maintainability / duplication
- **Three god-objects ~10.5k lines:** `runner.py` (~5.6k: entry + all screens + DSL engine + DB), `cxgrid.py` (~2.6k), `layout.py` (~2.3k). DSL runtime awkwardly co-located with UI.
- **Parallel verbose/compact parsers** share no base and re-implement field/flag logic (identical `opts:`/`formula:` regexes at `compact_parser.py:591` & `796`) → drift.
- **`_safe_addstr` duplicated ~6×** (`curved_box.py:140`, `cxgrid.py:573`, `cxreport.py:38`, `menu.py:233`, `tree_grid.py:70`, `report_designer.py:63`).
- **`server.py` ↔ `FBServer.py`** are near-duplicate Hrana 2 servers (Firebird logic twice).
- **No `delwin` anywhere** — `while True` loops allocate windows per iteration relying on GC (`report_designer.py:379`, `runner.py:3990+`).

### 🟡 Robustness gaps
- **Incomplete resize handling:** `cxgrid`, `pos_screen`, `warehouse_grid`, `widgets`, `layout`, `curved_box`, `themes.theme_picker` ignore `KEY_RESIZE`; `cxgrid` caches dims from `__init__`.
- **No min-terminal-size guard**; some paths `addstr` directly and overflow on small terminals (`full_grid.py:28`, `pos_screen.py:784`).
- **`eval()` formula/filter sandboxes** (`runner.py:2930`, `layout.py:66`, `report_engine.py:313`) use `__builtins__={}` — not a real security boundary; acceptable only because DSL is developer-authored.
- **Silent partial failures remain:** `RemoteSQLAdapter.execute_many`/`query_many` and `_ensure_rowid_gen` still swallow errors (T2.1 didn't cover them).
- **Whole-table load-then-filter** (`SELECT rowid, data FROM "{t}"`, `script_engine.py:734+`) — perf cliff; `errors.log` already shows `PERF SLOW 1.2s` on a small `item` table.

### 🟡 Testing / CI / build hygiene
- **Orphaned tests:** `tests/` never run by any `*.bat`/`.ps1` or CI (no `conftest.py`/`.github`/pytest config). Largest riskiest modules (`runner.py`, `cxgrid.py`, `layout.py`) have almost no behavioral coverage. Tests bypass constructors (`object.__new__`) — validate internals, not behavior.
- **16 `.bat` + 5 `.spec`** inconsistent: `build_all.bat:22` deletes all `*.spec` then runs inline `pyinstaller`, so committed specs are stale (onefile vs onedir, two specs for `server.py`). Throwaway `_test_*.bat`/`_debug.bat` committed.
- **`remote_connections.json` leaks secrets:** plaintext passwords (`masterkey`) + hardcoded prod IPs (`203.151.66.173`, `143.14.9.158`) committed.
- **No README/LICENSE/CHANGELOG**; `*_colcfg.json`/configs have no schema validation.
- `errors.log` is 1.5 MB, no rotation, mostly environmental connection failures (no backoff on unavailable remotes).

### 🟢 Strengths to preserve
- Script engine is a real tokenizer→AST interpreter (no `eval` of user logic) — strong.
- Parameterized SQL everywhere; no real end-user injection surface.
- Four backends; defaults to standalone local SQLite (server optional).
- Recent commits (T2.x) improved atomicity + error logging.
- DSL docs are excellent: `docs/tutorial.txt`, `canonical_dsl.md`, `grammar.ebnf`, `bnf_gramma.txt`.

## 4. Prioritized remediation roadmap
1. **Fix crash-level bugs:** add `locale.setlocale`; unify color-pair namespace via single allocator in `themes.py`; guard nested commit (`if self.db._in_txn`); wrap remote DB writes in UI handlers; fix B2B builtin set; decide verbose dialect fate (unify or formally deprecate + test).
2. **Extract shared utilities:** `_safe_addstr` → one module; centralize cursor policy + theme-color init; consolidate `server.py`+`FBServer.py`.
3. **Testing/CI:** wire `pytest` into a single runner + GitHub Action; add a curses smoke test for `runner.run()`; cover `cxgrid`/`layout`/`adapters` (Postgres/Firebird) and the verbose parser.
4. **Hygiene:** remove throwaway `_test_*`/`_debug` scripts; reconcile `.spec` vs batch build; move secrets out of `remote_connections.json` (gitignore + example); add README + JSON schema validation; add log rotation.
5. **Architecture:** decompose `runner.py` (split DSL engine vs UI controller); add resize + min-size handling across screens; replace whole-table scans with indexed queries / `lastrowid`.

## 5. Verdict
A functional, well-documented demo held together by large, tightly-coupled modules and a few latent crash bugs. The DSL layer + docs are the real assets; the weakest areas are TUI cross-cutting concerns (locale, color namespace, resize, window cleanup) and build/test hygiene.
