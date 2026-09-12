import curses
import json
import os
import sqlite3
from datetime import date
from typing import Dict, Any, List
from tui import LayoutRenderer
from tui.layout import eval_computed_formula
from tui.themes import init_colors as _init_theme_colors
from dsl_lib.adapters import SQLiteAdapter, PostgreSQLAdapter, RemoteSQLAdapter, FirebirdAdapter
from dsl_lib.script_engine import ScriptExecutor, ScriptError


class ScriptRunner:
    def __init__(self, app_def: Dict[str, Any]):
        self.app = app_def
        self.current_user = self._load_user_context()
        self.current_layout = None
        self.db = None
        self.db_connect_error = None  # set if the initial DB connect failed
        self.is_fallback = False     # True when running on local fallback DB
        self.lookup_data = {}
        self.edit_context = None  # {"form_id": str, "rowid": int, "data": dict} or None
        self.column_configs = {}  # grid_id -> list of (name, visible, width) tuples
        self.sort_configs = {}    # grid_id -> (col_name, ascending) — session only
        self._list_view_idx = {}  # grid_id -> current LISTVIEW index
        self.company_name = ""    # current company display name
        self._lookups_dirty = False  # set True after save to trigger lookup reload
        self._tables_ensured = set()  # DB URLs/paths where tables already created
        self._init_database()
        self._ensure_tables()
        self._load_column_configs()
        self._load_active_company()

    @staticmethod
    def _load_user_context() -> Dict[str, Any]:
        """Load the initial UI permission context from the process environment."""
        name = os.environ.get("DBNOCODE_USER", "guest").strip() or "guest"
        try:
            level = max(0, int(os.environ.get("DBNOCODE_USER_LEVEL", "0")))
        except (TypeError, ValueError):
            level = 0
        return {"name": name, "level": level}

    def set_current_user(self, name: str, level: int):
        """Set the trusted session identity used by DSL permission rules."""
        try:
            normalized_level = max(0, int(level))
        except (TypeError, ValueError):
            normalized_level = 0
        self.current_user = {
            "name": str(name or "guest").strip() or "guest",
            "level": normalized_level,
        }

    @staticmethod
    def _log(exc, context: str = ""):
        """Append a timestamped traceback for a swallowed exception.

        Many DB / data ops in this codebase catch Exception and continue
        (to keep the TUI alive). Previously they did so silently, which
        hid data-corruption and partial-failure bugs. This helper makes
        those failures observable in errors.log without changing control
        flow. Best-effort: never raises.
        """
        import datetime
        import traceback as _tb
        try:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            header = f"[{stamp}] {context}: {exc!r}\n"
            body = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            with open("errors.log", "a", encoding="utf-8") as fh:
                fh.write(header + body + "\n")
        except Exception:
            pass

    def _default_local_db(self) -> str:
        """Derive local SQLite filename from DSL datasource or app meta."""
        ds = self.app.get("datasource")
        if ds:
            return ds.get("name", "app") + ".db"
        meta = self.app.get("meta", {})
        return meta.get("name", "app_data").replace(" ", "_").lower() + ".db"

    def _fallback_local(self, reason: str):
        """Fall back to local SQLite when remote is unavailable."""
        db_name = self._default_local_db()
        self._log(Exception(reason), context=f"fallback to local {db_name}")
        self.db = SQLiteAdapter(db_name)
        self.is_fallback = True
        try:
            self.db.connect()
            self.db_connect_error = f"LOCAL FALLBACK: {db_name}"
        except Exception as e:
            self.db_connect_error = str(e)
            self._log(e, context="local fallback connect")

    @staticmethod
    def _should_fallback_remote(company: Dict[str, Any] = None, source: str = "") -> bool:
        """Whether a failed websocket connection may switch to local SQLite.

        Saved company profiles should not silently drift into another database
        because users expect Switch Company / offline menu recovery instead.
        Keep local fallback only for ad-hoc DB_URL launches where there is no
        persisted company selection to recover from.
        """
        if source == "env":
            return True
        company = company or {}
        return not bool(company.get("db_url"))

    def _init_database(self):
        """Initialize database adapter.

        Priority: active company profile > DB_URL env var > DSL datasource.
        Remote SQLite can fall back to local SQLite. Direct Firebird does not
        fall back because writing to a different local database could corrupt
        the user's company data workflow.
        """
        # 1. Check active company profile first
        companies = self._load_companies()
        active = next((c for c in companies if c.get("active")), None)
        if active:
            db_url = active.get("db_url", "")
            if active.get("adapter") == "firebird" and not db_url:
                self.db = FirebirdAdapter(
                    host=active.get("host", "localhost"),
                    port=int(active.get("port", 3050)),
                    db=active.get("database", ""),
                    user=active.get("user", "sysdba"),
                    pwd=active.get("password", "masterkey"))
            elif db_url:
                auth_token = active.get("auth_token", "")
                self.db = RemoteSQLAdapter(db_url, auth_token)
            else:
                self.db = SQLiteAdapter(active.get("db_file", "app.db"))
            self.db_connect_error = None
            try:
                self.db.connect()
            except Exception as e:
                self.db_connect_error = str(e)
                self._log(e, context="db.connect")
                if (isinstance(self.db, RemoteSQLAdapter)
                        and self._should_fallback_remote(active, source="active")):
                    self._fallback_local(f"connection failed: {e}")
            self._ensure_tables()
            self.lookup_data = (self._load_lookups()
                                if not self.db_connect_error or self.is_fallback
                                else {})
            return

        # 2. Fall back to DB_URL env var
        db_url = os.environ.get("DB_URL")
        if db_url:
            auth_token = os.environ.get("DB_AUTH_TOKEN", "")
            self.db = RemoteSQLAdapter(db_url, auth_token)
            self.db_connect_error = None
            try:
                self.db.connect()
            except Exception as e:
                self.db_connect_error = str(e)
                self._log(e, context="db.connect")
                self._fallback_local(f"remote unavailable: {e}")
            self._ensure_tables()
            self.lookup_data = (self._load_lookups()
                                if not self.db_connect_error or self.is_fallback
                                else {})
            return

        # 3. Fall back to DSL datasource definition
        ds = self.app.get("datasource")
        if ds:
            adapter = ds.get("adapter", "sqlite")
            if adapter == "sqlite":
                db_name = os.environ.get("DB_NAME", ds.get("name", "app") + ".db")
                self.db = SQLiteAdapter(db_name)
            elif adapter == "postgres":
                self.db = PostgreSQLAdapter(
                    host=os.environ.get("DB_HOST", "localhost"),
                    port=int(os.environ.get("DB_PORT", "5432")),
                    db=os.environ.get("DB_NAME", ""),
                    user=os.environ.get("DB_USER", ""),
                    pwd=os.environ.get("DB_PASS", "")
                )
            elif adapter == "firebird":
                self.db = FirebirdAdapter(
                    host=os.environ.get("DB_HOST", "localhost"),
                    port=int(os.environ.get("DB_PORT", "3050")),
                    db=os.environ.get("DB_NAME", ""),
                    user=os.environ.get("DB_USER", "sysdba"),
                    pwd=os.environ.get("DB_PASS", "masterkey")
                )
        else:
            self.db = SQLiteAdapter(self._default_local_db())

        self.db_connect_error = None
        if self.db:
            try:
                self.db.connect()
            except Exception as e:
                self.db_connect_error = str(e)
                self._log(e, context="db.connect")

        self._ensure_tables()
        self.lookup_data = (self._load_lookups()
                            if not self.db_connect_error else {})

    def _ensure_tables(self):
        """Auto-create tables with JSON memo storage: rowid + data TEXT."""
        if not self.db:
            return
        # Do not retry a failed direct Firebird connection while starting up.
        # The main loop will show db_connect_error to the user.
        if self.db_connect_error and not self.is_fallback:
            return
        db_key = getattr(self.db, 'db_path', '') or getattr(self.db, 'db_url', '')
        if db_key in self._tables_ensured:
            return
        self._tables_ensured.add(db_key)
        # Collect required table names
        needed = set()
        for form_id, fields in self.app.get("forms", {}).items():
            if not isinstance(fields, list):
                continue
            needed.add(form_id.replace("_form", ""))
        for grid_id in self.app.get("grids", {}):
            needed.add(grid_id.replace("_grid", ""))
        # Quick check: if first table exists, likely all do — skip DDL
        if needed:
            try:
                existing = set(self.db.get_table_names())
                needed -= existing
            except Exception:
                pass  # can't check — create all
        # Always ensure system tables exist
        needed.add("business_settings")
        needed.add("wh_locations")
        needed.add("b2b_inbox")
        needed.add("b2b_outbox")
        if not needed:
            return
        stmts = [(f'CREATE TABLE IF NOT EXISTS "{t}" (data TEXT)', ())
                  for t in sorted(needed)]
        # Batch all CREATE TABLE statements in single round-trip if supported
        if hasattr(self.db, 'execute_many'):
            try:
                self.db.execute_many(stmts)
                return
            except Exception as e:
                self._log(e, context="batch CREATE TABLE")
        # Fallback: one-by-one
        for sql, params in stmts:
            try:
                self.db.execute(sql, params)
            except Exception as e:
                self._log(e, context=f"CREATE TABLE {sql}")

    def _has_fatal_db_error(self) -> bool:
        """True when startup failed and the app has no usable fallback DB."""
        return bool(self.db_connect_error and not self.is_fallback)

    def _startup_db_message(self) -> str:
        """Human-readable startup DB status message."""
        if self._has_fatal_db_error():
            return (
                "Database connection failed.\n\n"
                f"{self.db_connect_error}\n\n"
                "The app will open in offline menu mode.\n"
                "Use Switch Company from the main menu to connect to another "
                "database, or press Esc to exit.\n"
                "See errors.log for technical details."
            )
        if self.db_connect_error:
            return (
                "Database connection failed.\n\n"
                f"{self.db_connect_error}\n\n"
                "The app switched to a local fallback database and will run "
                "in degraded mode."
            )
        return ""

    def _offline_safe_action(self, act_def: Dict[str, Any]) -> bool:
        """Allow only recovery/navigation actions when no DB is connected."""
        return act_def.get("type") in {"switch", "exit"}

    def _console_startup_recovery(self) -> bool:
        """Offer a plain-console company switch flow before curses starts.

        Returns True when a working database connection is available and the
        TUI may proceed, or False when the user chooses to exit.
        """
        if not self._has_fatal_db_error():
            return True
        companies = self._load_companies()
        if not companies:
            print(self._startup_db_message())
            return False

        while self._has_fatal_db_error():
            print()
            print(self._startup_db_message())
            print()
            print("Available companies:")
            for idx, company in enumerate(companies, start=1):
                name = company.get("company", f"Company {idx}")
                marker = " (active)" if company.get("active") else ""
                db_type = company.get("adapter") or ("remote" if company.get("db_url") else "sqlite")
                print(f"  {idx}. {name} [{db_type}]{marker}")
            print("  0. Exit")
            choice = input("Select company number: ").strip()
            if choice in ("", "0", "q", "Q", "x", "X"):
                return False
            try:
                selected_index = int(choice) - 1
                selected = companies[selected_index]
            except (ValueError, IndexError):
                print("Invalid selection.")
                continue
            self._switch_db(selected)
            self.company_name = selected.get("company", self.company_name)
            if not self._has_fatal_db_error():
                self._set_active_company(companies, selected_index)
                print(f"Connected to {self.company_name}.")
                return True
            print()
            print(f"Connection failed for {selected.get('company', 'selected company')}.")
        return True

    def _col_config_path(self) -> str:
        """Path for persisted column configs, next to the DB file."""
        meta = self.app.get("meta", {})
        app_name = meta.get("name", "app").replace(" ", "_").lower()
        return app_name + "_colcfg.json"

    def _load_column_configs(self):
        path = self._col_config_path()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            # Convert lists back to list of tuples
            self.column_configs = {
                k: [tuple(item) for item in v] for k, v in raw.items()
            }
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self.column_configs = {}

    def _save_column_configs(self):
        path = self._col_config_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.column_configs, f)
        except OSError:
            pass

    # ── Company / database switching ──────────────────────────────

    def _companies_path(self) -> str:
        """Path for remote connections JSON file."""
        return "remote_connections.json"

    def _load_companies(self) -> list:
        """Load company profiles from JSON file."""
        path = self._companies_path()
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_companies(self, companies: list):
        """Save company profiles to JSON file."""
        path = self._companies_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(companies, f, indent=2)
        except OSError:
            pass

    def _load_active_company(self):
        """Set company_name from the active company profile."""
        companies = self._load_companies()
        if not companies:
            # No companies file yet — use default db name as company
            if self.db:
                self.company_name = getattr(
                    self.db, "name",
                    os.path.splitext(
                        os.path.basename(getattr(self.db, "db_path", "app")))[0])
            return
        active = next((c for c in companies if c.get("active")), None)
        if active:
            self.company_name = active.get("company", "")

    def _set_active_company(self, companies: list, selected_index: int):
        """Persist the last successfully connected company as active."""
        for idx, company in enumerate(companies):
            company["active"] = (idx == selected_index)
        self._save_companies(companies)

    def _switch_db(self, company: dict):
        """Disconnect current database and connect to a new one.

        Accepts a company profile dict with either:
          - ``db_url`` (+optional ``auth_token``) for remote libSQL/sqld
          - ``db_file`` for local SQLite
        For backward compat, a plain string is treated as db_file.
        Saved remote company profiles stay in offline mode if unreachable so
        the user can choose another company instead of silently switching DBs.
        """
        if isinstance(company, str):
            company = {"db_file": company}
        if self.db:
            try:
                self.db.disconnect()
            except Exception as e:
                self._log(e, context="company-switch disconnect")
            self.db = None
        db_url = company.get("db_url", "")
        if company.get("adapter") == "firebird" and not db_url:
            # Direct Firebird connection (no FBServer proxy)
            self.db = FirebirdAdapter(
                host=company.get("host", "localhost"),
                port=int(company.get("port", 3050)),
                db=company.get("database", ""),
                user=company.get("user", "sysdba"),
                pwd=company.get("password", "masterkey"))
        elif db_url:
            # Remote via WebSocket — works for both SQLite server and FBServer
            auth_token = company.get("auth_token", "")
            self.db = RemoteSQLAdapter(db_url, auth_token)
        else:
            self.db = SQLiteAdapter(company.get("db_file", "app.db"))
        self.db_connect_error = None
        self.is_fallback = False
        try:
            self.db.connect()
        except Exception as e:
            self.db_connect_error = str(e)
            self._log(e, context=f"company-switch connect {company}")
            if (isinstance(self.db, RemoteSQLAdapter)
                    and self._should_fallback_remote(company, source="switch")):
                self._fallback_local(f"remote unavailable: {e}")
        self._ensure_tables()
        # Verify tables were actually created (Firebird DDL can fail silently)
        if self.db and not self.db_connect_error:
            try:
                tables = self.db.get_table_names()
                if not tables:
                    self.db_connect_error = "Tables could not be created"
            except Exception:
                pass
        self.lookup_data = (self._load_lookups()
                            if not self.db_connect_error or self.is_fallback
                            else {})
        self._load_column_configs()
        self._bs_cache = None  # reload business settings from new DB

    @staticmethod
    def _probe_server(db_url: str) -> bool:
        """Quick TCP probe to check if a server is reachable."""
        if not db_url:
            return False
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        host = parsed.hostname or "localhost"
        if db_url.startswith("ws"):
            port = parsed.port or 8081
        elif db_url.startswith("fb://"):
            port = parsed.port or 3050
        else:
            return False
        try:
            s = socket.create_connection((host, port), timeout=0.3)
            s.close()
            return True
        except (OSError, socket.timeout):
            return False

    @staticmethod
    def _parse_ws_port(db_url: str) -> int:
        """Extract port number from ws:// URL."""
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        return parsed.port or 8081

    @staticmethod
    def _start_sqld_server(db_url: str, company_name: str) -> str:
        """Start a sqld instance in WSL for the given ws:// URL.

        Returns empty string on success, error message on failure.
        """
        import subprocess
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        hrana_port = parsed.port or 8081
        # HTTP port = hrana_port - 1 (convention: 8080/8081, 8082/8083, ...)
        http_port = hrana_port - 1
        # Sanitize company name for directory
        safe_name = "".join(c if c.isalnum() else "_" for c in company_name)
        data_dir = f"$HOME/.sqld-data/{safe_name}"
        sqld_bin = "$HOME/.local/bin/sqld"
        try:
            # Check sqld binary exists
            r = subprocess.run(
                ["wsl", "sh", "-c", f"test -x {sqld_bin}"],
                capture_output=True, timeout=5)
            if r.returncode != 0:
                return "sqld not installed in WSL"
            # Create data dir
            subprocess.run(
                ["wsl", "sh", "-c", f"mkdir -p {data_dir}"],
                capture_output=True, timeout=5)
            # Start sqld
            cmd = (f"setsid -f {sqld_bin} -d {data_dir}"
                   f" --http-listen-addr 0.0.0.0:{http_port}"
                   f" --hrana-listen-addr 0.0.0.0:{hrana_port}"
                   f" --no-welcome >/dev/null 2>&1")
            subprocess.run(
                ["wsl", "bash", "-c", cmd],
                capture_output=True, timeout=5)
            # Wait and verify
            import time
            time.sleep(2)
            r = subprocess.run(
                ["wsl", "sh", "-c",
                 f"ss -tlnp | grep -q ':{hrana_port} '"],
                capture_output=True, timeout=5)
            if r.returncode != 0:
                return f"sqld did not start on port {hrana_port}"
            return ""
        except subprocess.TimeoutExpired:
            return "WSL command timed out"
        except FileNotFoundError:
            return "WSL not available"
        except Exception as e:
            return str(e)

    @staticmethod
    def _stop_sqld_server(db_url: str) -> str:
        """Stop sqld instance listening on the given port. Returns error or empty."""
        import subprocess
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        hrana_port = parsed.port or 8081
        try:
            # Find and kill the sqld process on this port
            subprocess.run(
                ["wsl", "bash", "-c",
                 f"kill $(lsof -t -i :{hrana_port}) 2>/dev/null"
                 f" || fuser -k {hrana_port}/tcp 2>/dev/null"
                 f" || true"],
                capture_output=True, timeout=5)
            return ""
        except Exception as e:
            return str(e)

    def _switch_company(self, stdscr):
        """TUI screen to select, add, edit, or delete company profiles."""
        curses.curs_set(0)
        companies = self._load_companies()

        # If no companies file, seed with current db as default
        if not companies:
            seed = {"company": self.company_name or "Default Company",
                    "active": True}
            if isinstance(self.db, RemoteSQLAdapter):
                seed["db_url"] = self.db.db_url
            else:
                seed["db_file"] = getattr(self.db, "db_path", "app.db")
            companies = [seed]
            self._save_companies(companies)

        sel = 0
        for i, c in enumerate(companies):
            if c.get("active"):
                sel = i
                break

        # Cache server status to avoid probing every redraw
        server_status = {}
        status_msg = ""  # message shown in status bar area
        import threading

        def refresh_status(index=None):
            targets = [index] if index is not None else range(len(companies))
            for i in targets:
                comp = companies[i]
                url = comp.get("db_url", "")
                if not url and comp.get("adapter") == "firebird":
                    fb_host = comp.get("host", "localhost")
                    fb_port = comp.get("port", 3050)
                    url = f"fb://{fb_host}:{fb_port}"
                if url:
                    server_status[i] = self._probe_server(url)
                else:
                    server_status[i] = None  # local file, always OK

        # Probe servers in background so company list shows instantly
        threading.Thread(target=refresh_status, daemon=True).start()

        while True:
            max_y, max_x = stdscr.getmaxyx()
            stdscr.erase()

            # Title bar
            title = " Switch Company "
            stdscr.addstr(0, 0, " " * min(max_x, max_x - 1), curses.A_REVERSE)
            stdscr.addstr(0, 1, title, curses.A_REVERSE | curses.A_BOLD)

            # Column widths
            status_w = 14
            name_w = min(30, (max_x - status_w - 6) // 3)
            db_w = max_x - name_w - status_w - 6
            hdr = (f"  {'Company':<{name_w}} {'Database':<{db_w}}"
                   f" {'Status':<{status_w}}")
            try:
                stdscr.addstr(2, 1, hdr[:max_x - 2],
                              curses.A_BOLD | curses.A_UNDERLINE)
            except curses.error:
                pass

            # List companies
            for i, comp in enumerate(companies):
                y = 4 + i
                if y >= max_y - 3:
                    break
                name = comp.get("company", "")[:name_w]
                is_firebird = comp.get("adapter") == "firebird"
                has_url = bool(comp.get("db_url"))
                if is_firebird and has_url:
                    # Remote Firebird via FBServer — show WS URL + DB path
                    fb_db = comp.get("database", "")
                    db_f = f"{comp['db_url']} [{fb_db}]"[:db_w]
                elif is_firebird:
                    # Direct Firebird connection
                    fb_host = comp.get("host", "localhost")
                    fb_port = comp.get("port", 3050)
                    fb_db = comp.get("database", "")
                    db_f = f"fb://{fb_host}:{fb_port}/{fb_db}"[:db_w]
                else:
                    db_label = comp.get("db_url") or comp.get("db_file", "")
                    remote_name = comp.get("db_name", "")
                    if comp.get("db_url") and remote_name:
                        db_label = f"{db_label} [{remote_name}]"
                    db_f = db_label[:db_w]
                # Build status string
                is_active = comp.get("active", False)
                srv = server_status.get(i)
                is_direct_fb = is_firebird and not has_url
                is_remote = has_url  # both remote SQLite and remote-fb
                if is_active:
                    if srv is True:
                        status = "* ACTIVE"
                    elif is_direct_fb and srv is False:
                        status = "* EMBEDDED"
                    elif srv is False:
                        status = "* OFFLINE"
                    elif is_remote and i not in server_status:
                        status = "* ..."
                    else:
                        status = "* ACTIVE"
                elif srv is True:
                    status = "ONLINE"
                elif is_direct_fb and srv is False:
                    status = "EMBEDDED"
                elif srv is False:
                    status = "OFFLINE"
                elif is_remote and i not in server_status:
                    status = "..."
                elif is_direct_fb:
                    status = "FIREBIRD"
                else:
                    status = "LOCAL"
                line = (f"  {name:<{name_w}} {db_f:<{db_w}}"
                        f" {status:<{status_w}}")
                attr = curses.A_REVERSE if i == sel else 0
                # Color status
                try:
                    stdscr.addstr(y, 1, line[:max_x - 2], attr)
                    # Overlay colored status text
                    sx = 1 + 2 + name_w + 1 + db_w + 1
                    if sx + len(status) < max_x - 1:
                        if status == "ONLINE":
                            sattr = (curses.A_BOLD |
                                     (curses.A_REVERSE if i == sel else 0))
                        elif "OFFLINE" in status:
                            sattr = (curses.color_pair(1) |
                                     (curses.A_REVERSE if i == sel else 0))
                        elif "ACTIVE" in status:
                            sattr = (curses.A_BOLD |
                                     (curses.A_REVERSE if i == sel else 0))
                        else:
                            sattr = attr
                        stdscr.addstr(y, sx, status, sattr)
                except curses.error:
                    pass

            # Message line (above status bar)
            if status_msg:
                try:
                    stdscr.addstr(max_y - 2, 1,
                                  status_msg[:max_x - 2], curses.A_DIM)
                except curses.error:
                    pass

            # Status bar
            is_ws = bool(companies[sel].get("db_url", "").startswith("ws")
                         ) if companies else False
            bar_parts = ["Enter=Switch", "A=Add", "E=Edit", "D=Delete"]
            if is_ws:
                srv_up = server_status.get(sel)
                if srv_up:
                    bar_parts.append("X=Stop")
                else:
                    bar_parts.append("S=Start")
                bar_parts.append("R=Refresh")
            bar_parts.append("ESC=Cancel")
            status_bar = "  " + "  ".join(bar_parts) + " "
            try:
                stdscr.addstr(max_y - 1, 0, status_bar.ljust(max_x - 1),
                              curses.A_REVERSE)
            except curses.error:
                pass

            stdscr.refresh()
            # Use short timeout so display refreshes as background probes complete
            stdscr.timeout(300)
            key = stdscr.getch()
            stdscr.timeout(-1)
            if key == -1:
                continue  # timeout — just redraw with updated status
            status_msg = ""  # clear previous message

            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(companies) - 1:
                sel += 1
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                if companies:
                    c = companies[sel]
                    if c.get("adapter") == "firebird" and c.get("db_url"):
                        db_label = f"{c['db_url']} [{c.get('database', '')}]"
                    elif c.get("adapter") == "firebird":
                        db_label = (f"fb://{c.get('host', 'localhost')}:"
                                    f"{c.get('port', 3050)}/{c.get('database', '')}")
                    elif c.get("db_url") and c.get("db_name"):
                        db_label = f"{c.get('db_url')} [{c.get('db_name')}]"
                    else:
                        db_label = c.get("db_url") or c.get("db_file", "")
                    msg = f" Connecting to {db_label} ... "
                    try:
                        stdscr.addstr(max_y - 1, 0,
                                      msg[:max_x - 1], curses.A_BOLD)
                        stdscr.clrtoeol()
                        stdscr.refresh()
                    except curses.error:
                        pass
                    self.company_name = companies[sel].get("company", "")
                    self._switch_db(companies[sel])
                    if self._has_fatal_db_error():
                        err = f" Connect FAILED: {self.db_connect_error} "
                        try:
                            stdscr.addstr(max_y - 1, 0,
                                          err[:max_x - 1],
                                          curses.A_BOLD | curses.color_pair(1))
                            stdscr.clrtoeol()
                            stdscr.refresh()
                            stdscr.getch()
                        except curses.error:
                            pass
                    else:
                        self._set_active_company(companies, sel)
                return
            elif key in (curses.KEY_F3, curses.KEY_IC, ord('a'), ord('A')):
                result = self._edit_company_dialog(stdscr, None)
                if result:
                    companies.append(result)
                    self._save_companies(companies)
                    refresh_status(len(companies) - 1)
            elif key in (curses.KEY_F4, ord('e'), ord('E')):
                if companies:
                    result = self._edit_company_dialog(
                        stdscr, companies[sel])
                    if result:
                        companies[sel].update(result)
                        self._save_companies(companies)
                        refresh_status(sel)
            elif key in (curses.KEY_DC, 330, ord('d'), ord('D')):
                if companies and len(companies) > 1:
                    company_name = companies[sel].get("company", "this company")
                    if self._confirm_action(
                            stdscr,
                            f"Delete company '{company_name}'? (Y/N)",
                            title="Confirm Delete"):
                        was_active = companies[sel].get("active")
                        companies.pop(sel)
                        if sel >= len(companies):
                            sel = len(companies) - 1
                        if was_active and companies:
                            companies[0]["active"] = True
                        self._save_companies(companies)
                        server_status.clear()
                        refresh_status()
            elif key in (ord('s'), ord('S')):
                # Start sqld server for selected company
                if companies and companies[sel].get("db_url", "").startswith("ws"):
                    url = companies[sel]["db_url"]
                    name = companies[sel].get("company", "server")
                    try:
                        stdscr.addstr(max_y - 1, 0,
                                      f" Starting sqld for {name} ...".ljust(
                                          max_x - 1),
                                      curses.A_REVERSE | curses.A_BOLD)
                        stdscr.refresh()
                    except curses.error:
                        pass
                    err = self._start_sqld_server(url, name)
                    if err:
                        status_msg = f"Start FAILED: {err}"
                    else:
                        status_msg = f"Started sqld on port {self._parse_ws_port(url)}"
                    refresh_status(sel)
            elif key in (ord('x'), ord('X')):
                # Stop sqld server for selected company
                if companies and companies[sel].get("db_url", "").startswith("ws"):
                    url = companies[sel]["db_url"]
                    try:
                        stdscr.addstr(max_y - 1, 0,
                                      " Stopping sqld ...".ljust(max_x - 1),
                                      curses.A_REVERSE | curses.A_BOLD)
                        stdscr.refresh()
                    except curses.error:
                        pass
                    err = self._stop_sqld_server(url)
                    if err:
                        status_msg = f"Stop FAILED: {err}"
                    else:
                        import time
                        time.sleep(1)
                        status_msg = "Server stopped"
                    refresh_status(sel)
            elif key in (ord('r'), ord('R')):
                # Refresh server status
                try:
                    stdscr.addstr(max_y - 1, 0,
                                  " Checking servers ...".ljust(max_x - 1),
                                  curses.A_REVERSE)
                    stdscr.refresh()
                except curses.error:
                    pass
                server_status.clear()
                threading.Thread(target=refresh_status, daemon=True).start()
                status_msg = "Refreshing..."
            elif key == 27:  # ESC
                return

    def _edit_company_dialog(self, stdscr, existing: dict) -> dict:
        """Popup to add/edit a company profile. Returns dict or None.

        Fields: Type (sqlite/remote/firebird), Company Name, Host/IP,
        Port, Database Name, User, Password.
        Type field cycles with Space key.
        """
        from urllib.parse import urlparse
        max_y, max_x = stdscr.getmaxyx()
        adapter_types = ["sqlite", "remote", "firebird", "remote-fb"]
        box_h = 20
        box_w = min(60, max_x - 4)
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)

        # Pre-fill from existing
        company = ""
        host = ""
        port = ""
        db_name = ""
        user = ""
        password = ""
        adapter_idx = 0  # sqlite
        if existing:
            company = existing.get("company", "")
            if existing.get("adapter") == "firebird" and existing.get("db_url"):
                # Remote Firebird via FBServer
                adapter_idx = 3  # remote-fb
                parsed = urlparse(existing["db_url"])
                host = parsed.hostname or "localhost"
                port = str(parsed.port or 8082)
                db_name = existing.get("database", "")
                user = existing.get("user", "sysdba")
                password = existing.get("password", "")
            elif existing.get("adapter") == "firebird":
                adapter_idx = 2  # direct firebird
                host = existing.get("host", "localhost")
                port = str(existing.get("port", 3050))
                db_name = existing.get("database", "")
                user = existing.get("user", "sysdba")
                password = existing.get("password", "")
            elif existing.get("db_url", ""):
                adapter_idx = 1  # remote SQLite
                parsed = urlparse(existing["db_url"])
                host = parsed.hostname or "localhost"
                port = str(parsed.port or 8081)
                db_name = (existing.get("db_name", "")
                           or existing.get("db_file", "")
                           or existing.get("database", ""))
            else:
                adapter_idx = 0  # local SQLite
                db_name = existing.get("db_file", "")

        base_labels = ["Type:        ", "Company Name:", "Host / IP:   ",
                       "Port:        ", "Database:    ", "User:        ",
                       "Password:    "]
        fields = [adapter_types[adapter_idx], company, host, port,
                  db_name, user, password]
        field_count = 7
        field_idx = 0

        hints = {
            "sqlite":    "Local .db file. Database = filename.",
            "remote":    "Unified Server.exe. Host + Port connect. Database is server-side label/path only.",
            "firebird":  "Direct Firebird. Database = path to .fdb",
            "remote-fb": "Legacy Firebird WebSocket profile. Prefer remote on unified Server.exe.",
        }

        curses.curs_set(1)
        while True:
            cur_type = fields[0]
            try:
                win = curses.newwin(box_h, box_w, y, x)
                win.border()
                title = " Edit Company " if existing else " Add Company "
                win.addstr(0, 2, title, curses.A_BOLD)
                fw = box_w - 20
                labels = list(base_labels)
                if cur_type == "remote":
                    labels[4] = "Server DB Label:"
                elif cur_type == "sqlite":
                    labels[4] = "Database File:"
                elif cur_type in ("firebird", "remote-fb"):
                    labels[4] = "Database Path:"
                for fi in range(field_count):
                    # Hide user/password for non-firebird types
                    if fi >= 5 and cur_type not in ("firebird", "remote-fb"):
                        continue
                    row_y = 2 + fi * 2
                    if row_y >= box_h - 3:
                        break
                    win.addstr(row_y, 2, labels[fi])
                    val = fields[fi]
                    if fi == 6:  # password — mask
                        val = "*" * len(val)
                    attr = curses.A_REVERSE if fi == field_idx else 0
                    if fi == 0:  # type — highlight
                        attr |= curses.A_BOLD
                    win.addstr(row_y, 17, val.ljust(fw)[:fw], attr)
                hint = hints.get(cur_type, "")
                if field_idx == 0:
                    hint = "Space = cycle type. " + hint
                win.addstr(box_h - 3, 2, hint[:box_w - 4], curses.A_DIM)
                win.addstr(box_h - 2, 2,
                           "Tab/Enter=Next  Shift+Tab=Prev  F10=Save  ESC=Cancel",
                           curses.A_DIM)
                # Position cursor at end of active field text
                cur_row = 2 + field_idx * 2
                cur_col = 17 + len(fields[field_idx])
                if cur_col >= box_w - 3:
                    cur_col = box_w - 4
                win.refresh()
                # Move cursor on stdscr (absolute coords) so blinking cursor shows
                try:
                    stdscr.move(y + cur_row, x + cur_col)
                    stdscr.refresh()
                except curses.error:
                    pass
            except curses.error:
                curses.curs_set(0)
                return None

            key = stdscr.getch()

            if key in (9, curses.KEY_ENTER, 10, 13, curses.PADENTER):  # Tab or Enter = next field
                field_idx = (field_idx + 1) % field_count
                # Skip user/password fields for non-firebird types
                while field_idx >= 5 and fields[0] not in ("firebird", "remote-fb"):
                    field_idx = (field_idx + 1) % field_count
            elif key == 353:  # Shift+Tab = previous field
                field_idx = (field_idx - 1) % field_count
                while field_idx >= 5 and fields[0] not in ("firebird", "remote-fb"):
                    field_idx = (field_idx - 1) % field_count
            elif key == 32 and field_idx == 0:  # Space on type field
                adapter_idx = (adapter_idx + 1) % len(adapter_types)
                fields[0] = adapter_types[adapter_idx]
                # Set default port for type
                cur = adapter_types[adapter_idx]
                if cur == "firebird":
                    if not fields[3] or fields[3] in ("8081", "8082"):
                        fields[3] = "3050"
                    if not fields[5]:
                        fields[5] = "sysdba"
                elif cur == "remote-fb":
                    if not fields[3] or fields[3] in ("3050", "8081"):
                        fields[3] = "8082"
                    if not fields[5]:
                        fields[5] = "sysdba"
                elif cur == "remote":
                    if not fields[3] or fields[3] in ("3050", "8082"):
                        fields[3] = "8081"
            elif key == curses.KEY_F10:  # F10 = Save
                curses.curs_set(0)
                cur_type = fields[0]
                company = fields[1].strip()
                host = fields[2].strip()
                port = fields[3].strip()
                db_name = fields[4].strip()
                user = fields[5].strip()
                password = fields[6].strip()
                if not company:
                    return None
                result = {"company": company}
                if cur_type == "remote-fb":
                    if not host:
                        host = "localhost"
                    if not port:
                        port = "8082"
                    result["adapter"] = "firebird"
                    result["db_url"] = f"ws://{host}:{port}"
                    # Preserve Firebird connection details for FBServer
                    if existing and existing.get("adapter") == "firebird":
                        result["host"] = existing.get("host", "localhost")
                        result["port"] = existing.get("port", 3050)
                    else:
                        result["host"] = "localhost"
                        result["port"] = 3050
                    result["database"] = db_name
                    result["user"] = user or "sysdba"
                    result["password"] = password
                elif cur_type == "firebird":
                    result["adapter"] = "firebird"
                    result["host"] = host or "localhost"
                    result["port"] = int(port) if port else 3050
                    result["database"] = db_name
                    result["user"] = user or "sysdba"
                    result["password"] = password
                elif cur_type == "remote":
                    if not host:
                        host = "localhost"
                    if not port:
                        port = "8081"
                    result["db_url"] = f"ws://{host}:{port}"
                else:
                    if not db_name:
                        db_name = company.replace(" ", "_").lower()
                    if not db_name.endswith(".db"):
                        db_name += ".db"
                    result["db_file"] = db_name
                if db_name and cur_type == "remote":
                    result["db_name"] = db_name
                return result
            elif key == 27:  # ESC
                curses.curs_set(0)
                return None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if field_idx != 0 and fields[field_idx]:
                    fields[field_idx] = fields[field_idx][:-1]
            elif 32 <= key <= 126:
                if field_idx == 0:
                    pass  # type field — use Space to cycle, not typing
                else:
                    fields[field_idx] += chr(key)

    # ── Warehouse Map ─────────────────────────────────────────────

    def _wh_query_table(self, table_name):
        """Query all docs from a JSON store table. Returns list of dicts."""
        if not self.db:
            return []
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
            result = []
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    doc["_rowid"] = row.get("rowid")
                    result.append(doc)
                except (json.JSONDecodeError, TypeError):
                    pass
            return result
        except Exception:
            return []

    def _wh_upsert_bulk(self, table_name, docs):
        """Upsert docs into JSON store by 'id' field."""
        if not self.db:
            return
        try:
            self.db.execute(
                f'CREATE TABLE IF NOT EXISTS "{table_name}" (data TEXT)', ())
        except Exception:
            pass
        existing = {}
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    existing[doc.get("id", "")] = row.get("rowid")
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
        for doc in docs:
            doc_id = doc.get("id", "")
            data_str = json.dumps(doc)
            if doc_id in existing:
                self.db.execute(
                    f'UPDATE "{table_name}" SET data = ? WHERE rowid = ?',
                    (data_str, existing[doc_id]))
            else:
                self.db.execute(
                    f'INSERT INTO "{table_name}" (data) VALUES (?)',
                    (data_str,))

    def _wh_filter_update(self, table_name, match, updates):
        """Update docs matching filter dict."""
        docs = self._wh_query_table(table_name)
        for doc in docs:
            if all(doc.get(k) == v for k, v in match.items()):
                doc.update(updates)
                rowid = doc.pop("_rowid", None)
                if rowid is not None:
                    self.db.execute(
                        f'UPDATE "{table_name}" SET data = ? WHERE rowid = ?',
                        (json.dumps(doc), rowid))

    # ── Business Settings ────────────────────────────────────────────

    # Default schema — matches pystock business_settings
    _SETTINGS_SCHEMA = [
        {"name": "company_name",      "label": "Company Name",      "type": "TEXT",   "width": 40},
        {"name": "address_line1",     "label": "Address Line 1",    "type": "TEXT",   "width": 40},
        {"name": "address_line2",     "label": "Address Line 2",    "type": "TEXT",   "width": 40},
        {"name": "telephone",         "label": "Telephone",         "type": "TEXT",   "width": 20},
        {"name": "email",             "label": "Email",             "type": "TEXT",   "width": 30},
        {"name": "tax_id",            "label": "Tax ID",            "type": "TEXT",   "width": 20},
        {"name": "reg_id",            "label": "Registration ID",   "type": "TEXT",   "width": 20},
        {"name": "tax_rate_pct",      "label": "Tax Rate %",        "type": "NUMBER", "width": 10},
        {"name": "tax_type",          "label": "Tax Type",          "type": "SELECT",
         "options": ["NONE", "SALES TAX", "VAT"]},
        {"name": "business_type",     "label": "Business Type",     "type": "SELECT",
         "options": ["LOGISTIC", "MANUFACTURING", "RETAIL", "SERVICE"]},
        {"name": "pos_id",            "label": "POS ID",            "type": "TEXT",   "width": 15},
        {"name": "default_currency",  "label": "Default Currency",  "type": "TEXT",   "width": 10},
        {"name": "default_warehouse", "label": "Default Warehouse", "type": "TEXT",   "width": 15},
        {"name": "theme",             "label": "Theme",             "type": "SELECT",
         "options": ["DEFAULT", "MONOCHROME", "CLASSIC", "HIGH_CONTRAST", "YELLOW"]},
        {"name": "online_id",         "label": "Online ID (B2B)",   "type": "TEXT",   "width": 20},
    ]

    @staticmethod
    def _gen_online_id(name: str) -> str:
        """Generate a deterministic 15-digit numeric Online ID from company name.

        Structure: [4 name-fingerprint] [10 hash-body] [1 Luhn check]
        Compatible with pystock's gen_online_id().
        """
        import hashlib
        name = '' if name is None else str(name)
        normalised = ''.join(ch for ch in name.strip().upper() if ch.isalnum()) or '0'

        # Phone-keypad map: A-Z -> 1-9 cycling, digits pass through (0->1)
        keypad = {
            **{chr(ord('A') + i): str((i % 9) + 1) for i in range(26)},
            **{str(d): str(d) if d != 0 else '1' for d in range(10)},
        }
        padded = (normalised + '1111')[:4]
        fingerprint = ''.join(keypad.get(ch, '1') for ch in padded)

        digest = hashlib.sha256(normalised.encode('utf-8')).digest()
        uint32 = int.from_bytes(digest[:4], 'big')
        hash_body = str(uint32).zfill(10)

        payload = fingerprint + hash_body  # 14 digits
        # Luhn check digit
        total = 0
        for i, ch in enumerate(reversed(payload)):
            d = int(ch)
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        check = str((10 - (total % 10)) % 10)

        return payload + check  # 15 digits

    def get_setting(self, key, default=""):
        """Get a single business setting value."""
        if not hasattr(self, '_bs_cache') or self._bs_cache is None:
            self._bs_cache = self._load_business_settings()
        return self._bs_cache.get(key, default)

    def _resolve_default(self, val):
        """Resolve default value — @setting.key pulls from business settings."""
        if isinstance(val, str) and val.startswith("@setting."):
            key = val[9:]  # strip "@setting."
            return self.get_setting(key, "")
        return val

    def _load_business_settings(self):
        """Load business_settings doc from DB."""
        if not self.db:
            return {}
        try:
            rows = self.db.query('SELECT rowid, data FROM "business_settings"')
            if rows:
                doc = json.loads(rows[0].get("data", "{}"))
                doc["_rowid"] = rows[0].get("rowid")
                return doc
        except Exception:
            pass
        return {}

    def _save_business_settings(self, doc):
        """Save business_settings doc to DB."""
        if not self.db:
            return False
        # Auto-generate online_id from company_name if blank
        if not str(doc.get("online_id") or "").strip():
            company = str(doc.get("company_name") or "").strip()
            if company:
                doc["online_id"] = self._gen_online_id(company)
        try:
            rowid = doc.pop("_rowid", None)
            if rowid:
                self.db.execute(
                    'UPDATE "business_settings" SET data = ? WHERE rowid = ?',
                    (json.dumps(doc), rowid))
            else:
                self.db.execute(
                    'INSERT INTO "business_settings" (data) VALUES (?)',
                    (json.dumps(doc),))
            return True
        except Exception:
            return False

    def _open_business_settings(self, stdscr):
        """Business settings editor screen."""
        from tui.widgets import TextInput, NumericInput, Combobox
        from tui.themes import CLR_FORM_BORDER, CLR_FORM_LABEL, CLR_FORM_INPUT, CLR_STATUS_BAR

        curses.curs_set(1)
        max_y, max_x = stdscr.getmaxyx()

        # Ensure table exists
        try:
            self.db.execute(
                'CREATE TABLE IF NOT EXISTS "business_settings" '
                '("rowid" INTEGER PRIMARY KEY, "data" TEXT)', ())
        except Exception:
            pass

        # Load custom schema from settings doc or use default
        settings = self._load_business_settings()
        # Populate theme from local file if not in DB settings
        if not settings.get("theme"):
            from tui.themes import load_settings as _load_theme_settings
            settings["theme"] = _load_theme_settings().get("theme", "DEFAULT")
        custom_schema = settings.pop("_custom_fields", None)
        schema = list(self._SETTINGS_SCHEMA)
        if custom_schema and isinstance(custom_schema, list):
            for cs in custom_schema:
                if isinstance(cs, dict) and cs.get("name"):
                    # Don't duplicate built-in fields
                    if not any(s["name"] == cs["name"] for s in schema):
                        schema.append(cs)

        # Build widgets — 2 column layout
        label_w = max(len(s["label"]) for s in schema) + 2
        label_w = min(label_w, 22)
        col_gap = 4
        col_w = min((max_x - col_gap - 4) // 2, 55)
        field_w = col_w - label_w - 2

        widgets = []
        field_ids = []
        rows_per_col = (len(schema) + 1) // 2

        for i, sf in enumerate(schema):
            col = i // rows_per_col
            row = i % rows_per_col
            wy = 4 + row * 2
            wx = 2 + col * (col_w + col_gap) + label_w + 2

            ftype = sf.get("type", "TEXT").upper()
            fw = min(sf.get("width", 30), field_w)

            if ftype == "NUMBER":
                w = NumericInput(wy, wx, fw)
            elif ftype == "SELECT":
                opts = sf.get("options", [""])
                w = Combobox(wy, wx, fw, opts,
                             field_name=f"bs_{sf['name']}")
            else:
                w = TextInput(wy, wx, fw)

            # Load value (allow zero/falsy — only skip missing keys)
            val = settings.get(sf["name"])
            if val is not None and val != "":
                w.load(val)

            widgets.append(w)
            field_ids.append(sf["name"])

        focus_idx = 0
        message = ""

        while True:
            stdscr.erase()
            max_y, max_x = stdscr.getmaxyx()

            border_attr = curses.color_pair(CLR_FORM_BORDER)
            label_attr = curses.color_pair(CLR_FORM_LABEL)
            bar_attr = curses.color_pair(CLR_STATUS_BAR)

            # Title
            try:
                stdscr.addstr(0, 0, "─" * max_x, border_attr)
                stdscr.addstr(0, 2, " Business Settings ",
                              border_attr | curses.A_BOLD)
                stdscr.addstr(2, 2, "─" * (max_x - 4), border_attr)
            except curses.error:
                pass

            # Draw fields
            for i, sf in enumerate(schema):
                col = i // rows_per_col
                row = i % rows_per_col
                wy = 4 + row * 2
                lx = 2 + col * (col_w + col_gap)

                try:
                    label = sf["label"][:label_w]
                    attr = label_attr | curses.A_BOLD if i == focus_idx else label_attr
                    stdscr.addstr(wy, lx, f"{label}:", attr)
                except curses.error:
                    pass

                widgets[i].focused = (i == focus_idx)
                widgets[i].draw(stdscr)

            # Status bar
            try:
                stdscr.addstr(max_y - 1, 0, " " * (max_x - 1), bar_attr)
                stdscr.addstr(max_y - 1, 1, " F7 ", bar_attr | curses.A_BOLD)
                stdscr.addstr(max_y - 1, 5, " Add Field", curses.A_NORMAL)
                stdscr.addstr(max_y - 1, 18, " F9 ", bar_attr | curses.A_BOLD)
                stdscr.addstr(max_y - 1, 22, " Theme", curses.A_NORMAL)
                stdscr.addstr(max_y - 1, 31, " F10 ", bar_attr | curses.A_BOLD)
                stdscr.addstr(max_y - 1, 36, " Save", curses.A_NORMAL)
                stdscr.addstr(max_y - 1, 44, " ESC ", bar_attr | curses.A_BOLD)
                stdscr.addstr(max_y - 1, 49, " Cancel", curses.A_NORMAL)
            except curses.error:
                pass

            if message:
                try:
                    stdscr.addstr(max_y - 2, 2, message, curses.A_DIM)
                except curses.error:
                    pass
                message = ""

            # Cursor
            w = widgets[focus_idx]
            try:
                cx = w.cursor_x()
                cy = w.cursor_y() if hasattr(w, 'cursor_y') else w.y
                stdscr.move(cy, cx)
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key == 27:
                break

            # F10 save
            elif key in (curses.KEY_F10, 19):
                doc = dict(settings)
                doc.pop("_rowid", None)
                for fid, w in zip(field_ids, widgets):
                    doc[fid] = w.value
                # Preserve custom fields schema
                extra = [s for s in schema if not any(
                    s["name"] == d["name"] for d in self._SETTINGS_SCHEMA)]
                if extra:
                    doc["_custom_fields"] = extra
                rowid = settings.get("_rowid")
                try:
                    if rowid:
                        doc_copy = dict(doc)
                        self.db.execute(
                            'UPDATE "business_settings" SET data = ? WHERE rowid = ?',
                            (json.dumps(doc_copy), rowid))
                    else:
                        self.db.execute(
                            'INSERT INTO "business_settings" (data) VALUES (?)',
                            (json.dumps(doc),))
                    # Invalidate cache and reload
                    self._bs_cache = None
                    settings = self._load_business_settings()
                except Exception as exc:
                    self._log(exc, context="business_settings save")
                    message = "Settings save failed — see errors.log"
                # Apply theme if changed
                new_theme = doc.get("theme", "").upper()
                if new_theme and new_theme in ("DEFAULT", "MONOCHROME", "CLASSIC", "HIGH_CONTRAST", "YELLOW"):
                    from tui.themes import init_colors as _apply_theme, save_settings as _save_theme
                    _apply_theme(new_theme)
                    _save_theme({"theme": new_theme})
                message = "Settings saved"

            # F7 add custom field
            elif key == curses.KEY_F7:
                from tui.udf_panel import UDF_TYPES
                new_field = self._settings_field_dialog(stdscr)
                if new_field:
                    schema.append(new_field)
                    # Rebuild widgets (restart loop with updated schema)
                    # Save current values first
                    current_vals = {}
                    for fid, w in zip(field_ids, widgets):
                        current_vals[fid] = w.value
                    settings.update(current_vals)
                    extra = [s for s in schema if not any(
                        s["name"] == d["name"] for d in self._SETTINGS_SCHEMA)]
                    if extra:
                        settings["_custom_fields"] = extra
                    self._save_business_settings(dict(settings))
                    settings = self._load_business_settings()
                    # Rebuild widgets
                    widgets = []
                    field_ids = []
                    rows_per_col = (len(schema) + 1) // 2
                    for i, sf in enumerate(schema):
                        col = i // rows_per_col
                        row = i % rows_per_col
                        wy = 4 + row * 2
                        wx = 2 + col * (col_w + col_gap) + label_w + 2
                        ftype = sf.get("type", "TEXT").upper()
                        fw = min(sf.get("width", 30), field_w)
                        if ftype == "NUMBER":
                            w = NumericInput(wy, wx, fw)
                        elif ftype == "SELECT":
                            opts = sf.get("options", [""])
                            w = Combobox(wy, wx, fw, opts,
                                         field_name=f"bs_{sf['name']}")
                        else:
                            w = TextInput(wy, wx, fw)
                        val = settings.get(sf["name"])
                        if val is not None and val != "":
                            w.load(val)
                        widgets.append(w)
                        field_ids.append(sf["name"])
                    message = f"Added field: {new_field['label']}"

            # F9 theme picker
            elif key == curses.KEY_F9:
                from tui.themes import theme_picker as _theme_picker
                _theme_picker(stdscr)

            # Navigation
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER,
                         curses.KEY_DOWN, 9):
                focus_idx = (focus_idx + 1) % len(widgets)
            elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
                focus_idx = (focus_idx - 1) % len(widgets)
            else:
                widgets[focus_idx].handle_key(key)

        curses.curs_set(0)

    def _settings_field_dialog(self, stdscr):
        """Add custom field dialog. Returns field dict or None."""
        from tui.udf_panel import UDF_TYPES
        max_y, max_x = stdscr.getmaxyx()
        dh, dw = 12, min(46, max_x - 6)
        dy = max(0, (max_y - dh) // 2)
        dx = max(0, (max_x - dw) // 2)

        try:
            win = curses.newwin(dh, dw, dy, dx)
        except curses.error:
            return None
        win.keypad(True)

        fields = ["", "", "0", ""]  # name, label, type_idx, options
        field_labels = ["Name:", "Label:", "Type:", "Options:"]
        ftype_idx = 0
        focus = 0

        while True:
            win.erase()
            win.border()
            win.addstr(0, 2, " Add Setting Field ", curses.A_BOLD)
            win.addstr(dh - 1, 2, " F10=Save  ESC=Cancel ", curses.A_DIM)

            for i, (fl, fv) in enumerate(zip(field_labels, fields)):
                fy = 2 + i * 2
                attr_l = curses.A_BOLD if i == focus else curses.A_NORMAL
                try:
                    win.addstr(fy, 3, fl, attr_l)
                except curses.error:
                    pass
                if i == 2:
                    disp = UDF_TYPES[ftype_idx]
                    attr_v = curses.A_REVERSE if i == focus else curses.A_NORMAL
                    try:
                        win.addstr(fy, 14, f" {disp} ", attr_v)
                    except curses.error:
                        pass
                else:
                    val = fv[:dw - 18]
                    attr_v = curses.A_REVERSE if i == focus else curses.A_UNDERLINE
                    try:
                        win.addstr(fy, 14, val.ljust(dw - 18), attr_v)
                    except curses.error:
                        pass

            # Position cursor on focused field
            cursor_y = 2 + focus * 2
            if focus == 2:
                cursor_x = 14
            else:
                cursor_x = 14 + len(fields[focus][:dw - 18])
            try:
                win.move(cursor_y, min(cursor_x, dw - 2))
            except curses.error:
                pass
            curses.curs_set(1)
            win.refresh()
            key = win.getch()

            if key == 27:
                curses.curs_set(0)
                return None
            elif key in (curses.KEY_F10, 19):
                if not fields[0].strip():
                    continue
                curses.curs_set(0)
                result = {
                    "name": fields[0].strip().lower().replace(" ", "_"),
                    "label": fields[1].strip() or fields[0].strip(),
                    "type": UDF_TYPES[ftype_idx],
                }
                if UDF_TYPES[ftype_idx] == "SELECT" and fields[3].strip():
                    result["options"] = [o.strip() for o in fields[3].split(",")
                                         if o.strip()]
                return result
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER,
                         curses.KEY_DOWN, 9):
                focus = (focus + 1) % 4
            elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
                focus = (focus - 1) % 4
            elif focus == 2:
                if key in (curses.KEY_RIGHT, ord(' ')):
                    ftype_idx = (ftype_idx + 1) % len(UDF_TYPES)
                elif key == curses.KEY_LEFT:
                    ftype_idx = (ftype_idx - 1) % len(UDF_TYPES)
            elif key == curses.KEY_BACKSPACE or key in (127, 8):
                if fields[focus]:
                    fields[focus] = fields[focus][:-1]
            elif 32 <= key <= 126:
                fields[focus] += chr(key)

    # ------------------------------------------------------------------
    # B2B Inbox / Outbox
    # ------------------------------------------------------------------

    def _b2b_load_all(self, table: str) -> list:
        """Load all B2B messages from a JSON memo table."""
        if not self.db:
            return []
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
        except Exception:
            return []
        out = []
        for r in rows:
            try:
                doc = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                doc["rowid"] = r["rowid"]
                out.append(doc)
            except Exception:
                continue
        return out

    def _b2b_get(self, table: str, msg_id: str) -> dict:
        """Get single B2B message by id field."""
        for doc in self._b2b_load_all(table):
            if doc.get("id") == msg_id:
                return doc
        return {}

    def _b2b_insert(self, table: str, doc: dict) -> str:
        """Insert a B2B message document."""
        import uuid
        from datetime import datetime
        if "id" not in doc:
            doc["id"] = uuid.uuid4().hex
        if "created_at" not in doc:
            doc["created_at"] = datetime.utcnow().isoformat()
        self.db.execute(
            f'INSERT INTO "{table}" (data) VALUES (?)',
            (json.dumps(doc, default=str),))
        return doc["id"]

    def _b2b_update(self, table: str, msg_id: str, updates: dict):
        """Update fields of a B2B message by id."""
        for doc in self._b2b_load_all(table):
            if doc.get("id") == msg_id:
                rowid = doc["rowid"]
                doc.pop("rowid", None)
                doc.update(updates)
                self.db.execute(
                    f'UPDATE "{table}" SET data = ? WHERE rowid = ?',
                    (json.dumps(doc, default=str), rowid))
                return
        raise ValueError(f"B2B message not found: {msg_id}")

    def _b2b_delete(self, table: str, msg_id: str):
        """Delete a B2B message by id."""
        for doc in self._b2b_load_all(table):
            if doc.get("id") == msg_id:
                self.db.execute(
                    f'DELETE FROM "{table}" WHERE rowid = ?',
                    (doc["rowid"],))
                return

    def _b2b_queue_outbox(self, party_type: str, party_id: str,
                          online_id: str, payload, msg_type: str = '',
                          remote: dict = None) -> str:
        """Queue a document for B2B sending."""
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        doc = {
            "party_type": (party_type or "").strip().upper(),
            "party_id": (party_id or "").strip(),
            "online_id": (online_id or "").strip(),
            "msg_type": (msg_type or "").strip(),
            "status": "PENDING",
            "payload": payload,
            "remote": remote or {},
            "attempts": 0,
            "error": "",
            "queued_at": now,
        }
        return self._b2b_insert("b2b_outbox", doc)

    def _b2b_send_outbox(self, msg_id: str):
        """Send an outbox message to remote server via Hrana 2 WebSocket."""
        from datetime import datetime

        msg = self._b2b_get("b2b_outbox", msg_id)
        if not msg:
            return False, "Message not found"

        status = (msg.get("status") or "").upper()
        if status == "SENT":
            return True, "Already sent"

        now = datetime.utcnow().isoformat()
        attempts = int(msg.get("attempts", 0)) + 1
        self._b2b_update("b2b_outbox", msg_id, {
            "status": "SENDING",
            "attempts": attempts,
            "last_attempt_at": now,
            "error": "",
        })

        remote = msg.get("remote") if isinstance(msg.get("remote"), dict) else {}
        host = str(remote.get("host") or "").strip()
        port = str(remote.get("port") or "8085").strip()
        if not host:
            self._b2b_update("b2b_outbox", msg_id, {
                "status": "FAILED",
                "failed_at": now,
                "error": "Missing remote host (set B2B Host on partner record)",
            })
            return False, "Missing remote host"

        ws_url = f"ws://{host}:{port}"

        # Get sender info from business settings
        sender_company = ""
        sender_oid = ""
        try:
            bs_rows = self.db.query('SELECT rowid, data FROM "business_settings"')
            if bs_rows:
                bs = json.loads(bs_rows[0]["data"]) if isinstance(bs_rows[0]["data"], str) else bs_rows[0]["data"]
                sender_company = bs.get("company_name", "")
                sender_oid = bs.get("online_id", "")
        except Exception:
            pass
        # Auto-generate online_id if still blank
        if not sender_oid and sender_company:
            sender_oid = self._gen_online_id(sender_company)

        pt = (msg.get("party_type") or "").upper()
        from_party_type = "CUSTOMER" if pt == "VENDOR" else "VENDOR"
        sender_meta = {
            "from_company": sender_company,
            "from_online_id": sender_oid,
            "from_party_type": from_party_type,
            "from_outbox_id": msg_id,
            "attempts": attempts,
        }

        import uuid
        inbox_doc = {
            "id": uuid.uuid4().hex,
            "party_type": pt,
            "party_id": (msg.get("party_id") or "").strip(),
            "online_id": (msg.get("online_id") or "").strip(),
            "msg_type": (msg.get("msg_type") or "").strip(),
            "status": "NEW",
            "payload": msg.get("payload"),
            "remote": sender_meta,
            "received_at": now,
            "created_at": now,
        }

        try:
            remote_db = RemoteSQLAdapter(ws_url)
            remote_db.connect()
            # Ensure b2b_inbox table exists on remote
            remote_db.execute(
                'CREATE TABLE IF NOT EXISTS "b2b_inbox" (data TEXT)', ())
            remote_db.execute(
                'INSERT INTO "b2b_inbox" (data) VALUES (?)',
                (json.dumps(inbox_doc, default=str),))
            remote_db.disconnect()

            self._b2b_update("b2b_outbox", msg_id, {
                "status": "SENT",
                "sent_at": now,
                "remote": {"host": host, "port": port},
                "remote_inbox_id": inbox_doc["id"],
                "error": "",
            })
            return True, f"Sent to {host}:{port}"
        except Exception as exc:
            err = str(exc)[:300]
            self._b2b_update("b2b_outbox", msg_id, {
                "status": "FAILED",
                "failed_at": now,
                "remote": {"host": host, "port": port},
                "error": err,
            })
            return False, err

    def _b2b_msg(self, stdscr, text: str):
        """Show a simple message popup, wait for any key."""
        h, w = stdscr.getmaxyx()
        lines = str(text).split("\n")
        pw = min(max(len(l) for l in lines) + 6, w - 4)
        ph = min(len(lines) + 4, h - 4)
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        try:
            pop = curses.newwin(ph, pw, py, px)
        except Exception:
            return
        pop.keypad(True)
        pop.erase()
        pop.border()
        for i, line in enumerate(lines[:ph - 2]):
            pop.addstr(1 + i, 2, line[:pw - 4])
        pop.addstr(ph - 2, 2, "[Press any key]", curses.A_DIM)
        pop.refresh()
        pop.getch()
        del pop
        stdscr.touchwin()
        stdscr.refresh()

    def _b2b_pretty(self, obj) -> str:
        try:
            return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        except Exception:
            return str(obj)

    def _b2b_view_msg(self, stdscr, title: str, table: str, rec: dict):
        """Show full message detail in a scrollable popup."""
        full = self._b2b_get(table, rec.get("id", ""))
        if not full:
            full = rec
        payload_txt = self._b2b_pretty(full.get("payload"))
        remote = full.get("remote") if isinstance(full.get("remote"), dict) else {}
        remote_txt = self._b2b_pretty(remote)
        meta = {}
        for k in ("id", "party_type", "party_id", "online_id", "msg_type",
                   "status", "received_at", "queued_at", "sent_at",
                   "failed_at", "attempts", "error", "created_at"):
            v = full.get(k)
            if v is not None and v != "":
                meta[k] = v
        meta_txt = self._b2b_pretty(meta)

        # Scrollable view
        lines = []
        lines.append(f"=== {title} ===")
        lines.append("")
        for l in meta_txt.split("\n"):
            lines.append(l)
        lines.append("")
        lines.append("--- Remote ---")
        for l in remote_txt.split("\n"):
            lines.append(l)
        lines.append("")
        lines.append("--- Payload ---")
        for l in payload_txt.split("\n"):
            lines.append(l)

        h, w = stdscr.getmaxyx()
        pw = min(w - 4, 80)
        ph = min(h - 4, 30)
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        try:
            pop = curses.newwin(ph, pw, py, px)
        except Exception:
            return
        pop.keypad(True)
        scroll = 0
        visible = ph - 2

        while True:
            pop.erase()
            pop.border()
            for vi in range(visible):
                idx = scroll + vi
                if idx < len(lines):
                    pop.addstr(1 + vi, 1, lines[idx][:pw - 3])
            pop.refresh()
            k = pop.getch()
            if k in (27, ord('q'), ord('Q')):
                break
            elif k == curses.KEY_DOWN:
                if scroll < max(0, len(lines) - visible):
                    scroll += 1
            elif k == curses.KEY_UP:
                if scroll > 0:
                    scroll -= 1
            elif k == curses.KEY_NPAGE:
                scroll = min(scroll + visible, max(0, len(lines) - visible))
            elif k == curses.KEY_PPAGE:
                scroll = max(0, scroll - visible)
            elif k == curses.KEY_HOME:
                scroll = 0
            elif k == curses.KEY_END:
                scroll = max(0, len(lines) - visible)
        del pop
        stdscr.touchwin()
        stdscr.refresh()

    def _open_b2b_inbox(self, stdscr):
        """B2B Inbox screen — view received messages."""
        COLS = [
            ("received_at", "Received", 19),
            ("from_party_type", "From", 8),
            ("from_company", "Sender", 20),
            ("msg_type", "Type", 14),
            ("status", "Status", 10),
        ]

        while True:
            # Load data
            raw = self._b2b_load_all("b2b_inbox")
            raw.sort(key=lambda r: str(r.get("received_at", "")), reverse=True)
            data = []
            for rec in raw:
                remote = rec.get("remote") if isinstance(rec.get("remote"), dict) else {}
                fpt = str(remote.get("from_party_type") or "").upper()
                if not fpt:
                    pt = str(rec.get("party_type") or "").upper()
                    fpt = "CUSTOMER" if pt == "VENDOR" else "VENDOR"
                data.append({
                    **rec,
                    "from_party_type": fpt,
                    "from_company": str(remote.get("from_company") or "").strip(),
                })

            # Draw screen
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            title = f" B2B InBox ({len(data)} messages) "
            stdscr.addstr(0, 0, " " * w, curses.A_REVERSE)
            stdscr.addstr(0, max(0, (w - len(title)) // 2), title, curses.A_REVERSE | curses.A_BOLD)

            # Status bar
            hints = "F2/Enter View  F5 Mark Processed  Del Delete  ESC Back"
            stdscr.addstr(h - 1, 0, " " * (w - 1), curses.A_REVERSE)
            stdscr.addstr(h - 1, 1, hints[:w - 2], curses.A_REVERSE)

            # Column headers
            header_y = 2
            hx = 1
            for _, lbl, cw in COLS:
                stdscr.addstr(header_y, hx, lbl[:cw].ljust(cw), curses.A_BOLD)
                hx += cw + 1

            # Data rows
            visible = h - 5
            sel = 0
            scroll = 0

            while True:
                # Draw rows
                for vi in range(visible):
                    idx = scroll + vi
                    ry = header_y + 1 + vi
                    if ry >= h - 1:
                        break
                    if idx < len(data):
                        row = data[idx]
                        attr = curses.A_REVERSE if idx == sel else 0
                        rx = 1
                        line = ""
                        for cid, _, cw in COLS:
                            val = str(row.get(cid, ""))[:cw]
                            line += val.ljust(cw) + " "
                        stdscr.addstr(ry, 0, " " * (w - 1), attr)
                        stdscr.addstr(ry, 1, line[:w - 2], attr)
                    else:
                        stdscr.addstr(ry, 0, " " * (w - 1))

                stdscr.refresh()
                key = stdscr.getch()

                if key == 27:  # ESC
                    return
                elif key == curses.KEY_UP:
                    if sel > 0:
                        sel -= 1
                        if sel < scroll:
                            scroll = sel
                elif key == curses.KEY_DOWN:
                    if sel < len(data) - 1:
                        sel += 1
                        if sel >= scroll + visible:
                            scroll = sel - visible + 1
                elif key == curses.KEY_HOME:
                    sel = 0
                    scroll = 0
                elif key == curses.KEY_END:
                    sel = max(0, len(data) - 1)
                    scroll = max(0, sel - visible + 1)
                elif key in (curses.KEY_F2, 10, 13):
                    if data and 0 <= sel < len(data):
                        self._b2b_view_msg(stdscr, "B2B InBox", "b2b_inbox", data[sel])
                        break  # redraw
                elif key == curses.KEY_F5:
                    if data and 0 <= sel < len(data):
                        rec = data[sel]
                        if rec.get("status") == "PROCESSED":
                            self._b2b_msg(stdscr, "Already processed.")
                        else:
                            from datetime import datetime
                            self._b2b_update("b2b_inbox", rec.get("id", ""), {
                                "status": "PROCESSED",
                                "processed_at": datetime.utcnow().isoformat(),
                            })
                            self._b2b_msg(stdscr, "Marked as Processed.")
                        break  # redraw
                elif key in (curses.KEY_DC, 330):  # Delete
                    if data and 0 <= sel < len(data):
                        self._b2b_delete("b2b_inbox", data[sel].get("id", ""))
                        break  # redraw
                elif key == curses.KEY_RESIZE:
                    break  # redraw

    def _open_b2b_outbox(self, stdscr):
        """B2B Outbox screen — view queued/sent messages."""
        COLS = [
            ("queued_at", "Queued", 19),
            ("party_type", "To", 8),
            ("party_id", "ID", 14),
            ("msg_type", "Type", 14),
            ("status", "Status", 10),
            ("error", "Error", 20),
        ]

        while True:
            # Load data
            data = self._b2b_load_all("b2b_outbox")
            data.sort(key=lambda r: str(r.get("queued_at", "")), reverse=True)

            # Draw screen
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            title = f" B2B OutBox ({len(data)} messages) "
            stdscr.addstr(0, 0, " " * w, curses.A_REVERSE)
            stdscr.addstr(0, max(0, (w - len(title)) // 2), title, curses.A_REVERSE | curses.A_BOLD)

            # Status bar
            hints = "F2/Enter View  F5 Send  Del Delete  ESC Back"
            stdscr.addstr(h - 1, 0, " " * (w - 1), curses.A_REVERSE)
            stdscr.addstr(h - 1, 1, hints[:w - 2], curses.A_REVERSE)

            # Column headers
            header_y = 2
            hx = 1
            for _, lbl, cw in COLS:
                stdscr.addstr(header_y, hx, lbl[:cw].ljust(cw), curses.A_BOLD)
                hx += cw + 1

            # Data rows
            visible = h - 5
            sel = 0
            scroll = 0

            while True:
                for vi in range(visible):
                    idx = scroll + vi
                    ry = header_y + 1 + vi
                    if ry >= h - 1:
                        break
                    if idx < len(data):
                        row = data[idx]
                        attr = curses.A_REVERSE if idx == sel else 0
                        rx = 1
                        line = ""
                        for cid, _, cw in COLS:
                            val = str(row.get(cid, ""))[:cw]
                            line += val.ljust(cw) + " "
                        stdscr.addstr(ry, 0, " " * (w - 1), attr)
                        stdscr.addstr(ry, 1, line[:w - 2], attr)
                    else:
                        stdscr.addstr(ry, 0, " " * (w - 1))

                stdscr.refresh()
                key = stdscr.getch()

                if key == 27:  # ESC
                    return
                elif key == curses.KEY_UP:
                    if sel > 0:
                        sel -= 1
                        if sel < scroll:
                            scroll = sel
                elif key == curses.KEY_DOWN:
                    if sel < len(data) - 1:
                        sel += 1
                        if sel >= scroll + visible:
                            scroll = sel - visible + 1
                elif key == curses.KEY_HOME:
                    sel = 0
                    scroll = 0
                elif key == curses.KEY_END:
                    sel = max(0, len(data) - 1)
                    scroll = max(0, sel - visible + 1)
                elif key in (curses.KEY_F2, 10, 13):
                    if data and 0 <= sel < len(data):
                        self._b2b_view_msg(stdscr, "B2B OutBox", "b2b_outbox", data[sel])
                        break  # redraw
                elif key == curses.KEY_F5:
                    if data and 0 <= sel < len(data):
                        rec = data[sel]
                        status = (rec.get("status") or "").upper()
                        if status == "SENT":
                            self._b2b_msg(stdscr, "Already sent.")
                        elif status == "SENDING":
                            self._b2b_msg(stdscr, "Send in progress.")
                        else:
                            ok, result_msg = self._b2b_send_outbox(rec.get("id", ""))
                            if ok:
                                self._b2b_msg(stdscr, result_msg)
                            else:
                                self._b2b_msg(stdscr, f"Failed: {result_msg}")
                        break  # redraw
                elif key in (curses.KEY_DC, 330):  # Delete
                    if data and 0 <= sel < len(data):
                        self._b2b_delete("b2b_outbox", data[sel].get("id", ""))
                        break  # redraw
                elif key == curses.KEY_RESIZE:
                    break  # redraw

    def _b2b_partner_info(self, party_type: str, party_id: str,
                          party_name: str = "") -> dict:
        """Resolve a B2B partner by code/name and ensure its Online ID."""
        table_names = ("vendor", "vendors") if party_type == "VENDOR" else (
            "customer", "customers", "member", "members")
        id_fields = (
            ("vendor_code", "vendor_id", "code", "id")
            if party_type == "VENDOR" else
            ("cust_code", "customer_id", "customer_code", "member_id", "code", "id")
        )
        name_fields = ("vendor_name", "name") if party_type == "VENDOR" else (
            "customer_name", "cust_name", "full_name", "name")
        wanted_id = str(party_id or "").strip()
        wanted_name = str(party_name or "").strip().lower()

        for table_name in table_names:
            try:
                rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
            except Exception:
                continue
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                ids = {str(doc.get(field, "")).strip() for field in id_fields}
                names = {str(doc.get(field, "")).strip().lower() for field in name_fields}
                if wanted_id and wanted_id not in ids and wanted_name not in names:
                    continue
                name = next((str(doc.get(field, "")).strip()
                             for field in name_fields if doc.get(field)), "")
                online_id = str(doc.get("online_id", "")).strip()
                if not online_id and name:
                    online_id = self._make_unique_online_id(
                        name, table_name, own_rowid=row.get("rowid"))
                    doc["online_id"] = online_id
                    self.db.execute(
                        f'UPDATE "{table_name}" SET data=? WHERE rowid=?',
                        (json.dumps(doc), row.get("rowid")))
                return {
                    "table": table_name,
                    "party_id": wanted_id or next(
                        (str(doc.get(field, "")).strip()
                         for field in id_fields if doc.get(field)), ""),
                    "online_id": online_id,
                    "host": str(doc.get("b2b_host") or doc.get("b2b_ip") or "").strip(),
                    "port": str(doc.get("b2b_port") or "8085").strip(),
                }
        return {}

    def _b2b_queue_from_form(self, stdscr, renderer, layout_def):
        """Queue and send the current B2B document via Ctrl+B."""
        from datetime import datetime

        # Gather header data
        record = {}
        if self.edit_context and self.edit_context.get("data"):
            record.update(self.edit_context["data"])
        for w in renderer.widgets:
            fid = getattr(w, 'field_id', '') or getattr(w, '_field_id', '')
            if fid:
                record[fid] = w.value

        # Gather detail data
        detail_data = []
        if renderer.tabs_def and renderer.tab_grids:
            for ti, tg in enumerate(renderer.tab_grids):
                if tg:
                    from tui.cxgrid import Grid as CxGrid
                    if isinstance(tg, CxGrid):
                        detail_data.extend(list(tg.data))
                    elif hasattr(tg, 'data'):
                        detail_data.extend(list(tg.data))

        title = layout_def.get("title", "Document")
        form_id = next((ref for ref in layout_def.get("fields", {})
                        if ref in self.app.get("forms", {})), "")
        form_name = form_id.replace("_form", "").lower()
        doc_map = {
            "po": ("VENDOR", "PURCHASE_ORDER"),
            "purchase_order": ("VENDOR", "PURCHASE_ORDER"),
            "grn": ("VENDOR", "GOODS_RECEIPT"),
            "invoice": ("CUSTOMER", "INVOICE"),
            "delivery": ("CUSTOMER", "DELIVERY_ORDER"),
            "delivery_order": ("CUSTOMER", "DELIVERY_ORDER"),
            "so": ("CUSTOMER", "SALES_ORDER"),
            "pos_slip": ("CUSTOMER", "POS_SLIP"),
        }
        default_party_type, msg_type = doc_map.get(form_name, ("", form_name.upper()))

        payload = {"header": record, "lines": detail_data}

        party_type = default_party_type
        id_keys = (("vendor_code", "vendor_id", "vendor") if party_type == "VENDOR"
                   else ("cust_code", "customer_id", "customer_code", "customer"))
        name_keys = (("vendor_name",) if party_type == "VENDOR" else
                     ("customer_name", "cust_name", "member_name"))
        party_id = next((str(record.get(key, "")).strip()
                         for key in id_keys if record.get(key)), "")
        party_name = next((str(record.get(key, "")).strip()
                           for key in name_keys if record.get(key)), "")
        partner = self._b2b_partner_info(party_type, party_id, party_name) if party_type else {}
        party_id = partner.get("party_id", party_id)
        online_id = partner.get("online_id", "")
        b2b_host = partner.get("host", "")
        b2b_port = partner.get("port", "8085")

        if not party_type or not party_id:
            self._b2b_msg(stdscr, "B2B recipient not found on this document.")
            return
        if not online_id:
            self._b2b_msg(stdscr, "Partner has no Online ID. Save the partner record first.")
            return

        # Show confirmation popup
        h, w = stdscr.getmaxyx()
        info = [
            f"Queue to B2B OutBox?",
            f"",
            f"Document: {title}",
            f"To: {party_type} {party_id}",
            f"Online ID: {online_id or '(none)'}",
            f"Host: {b2b_host or '(not set)'}:{b2b_port}",
            f"",
            f"F10 = Confirm    ESC = Cancel",
        ]
        pw = min(50, w - 4)
        ph = len(info) + 2
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        try:
            pop = curses.newwin(ph, pw, py, px)
        except Exception:
            return
        pop.keypad(True)
        pop.erase()
        pop.border()
        for i, line in enumerate(info):
            pop.addstr(1 + i, 2, line[:pw - 4])
        pop.refresh()

        while True:
            k = pop.getch()
            if k == 27:
                del pop
                stdscr.touchwin()
                stdscr.refresh()
                return
            elif k == curses.KEY_F10:
                break

        del pop

        # Queue to outbox
        remote = {}
        if b2b_host:
            remote = {"host": b2b_host, "port": b2b_port}

        msg_id = self._b2b_queue_outbox(
            party_type=party_type,
            party_id=party_id,
            online_id=online_id,
            payload=payload,
            msg_type=msg_type,
            remote=remote,
        )

        if b2b_host:
            ok, result = self._b2b_send_outbox(msg_id)
            if ok:
                self._b2b_msg(stdscr, f"B2B sent.\nType: {msg_type}\n{result}")
            else:
                self._b2b_msg(stdscr, f"Queued, but send failed.\n{result}")
        else:
            self._b2b_msg(stdscr, f"Queued to B2B OutBox.\nType: {msg_type}\nSet partner B2B host to send.")

    def _open_pos_screen(self, stdscr):
        """Launch Point of Sale screen."""
        from tui.pos_screen import PosScreen
        screen = PosScreen(stdscr, self.db, self.app, self.lookup_data,
                           runner=self)
        screen.run()

    def _run_warehouse_map(self, stdscr):
        """Launch Warehouse Location Manager screen."""
        from tui.warehouse_grid import WarehouseGrid

        # Load warehouses
        wh_list = self._wh_query_table("warehouse")
        if not wh_list:
            self._show_message(stdscr, "No warehouses found. Create warehouses first.")
            return

        wh_names = []
        wh_levels = {}
        grid_rows = 8
        grid_cols = 10
        grid_cbm = 150.0
        zone_fn = None

        for wh in wh_list:
            wh_id = wh.get("whid", wh.get("id", ""))
            if not wh_id:
                continue
            wh_names.append(wh_id)
            wh_levels[wh_id] = int(wh.get("wh_levels", 0) or 3)

        if not wh_names:
            self._show_message(stdscr, "No valid warehouses found.")
            return

        first_wh = wh_list[0]
        grid_rows = int(first_wh.get("wh_rows", 0) or 8)
        grid_cols = int(first_wh.get("wh_cols", 0) or 10)
        grid_cbm = float(first_wh.get("default_cbm", 0) or 150.0)
        zone_str = str(first_wh.get("zone_map", "") or "")
        zone_fn = self._parse_zone_map(zone_str, grid_cols)

        # Load customer map for display
        cust_map = {}
        for doc in self._wh_query_table("customer"):
            cid = doc.get("customer_id") or doc.get("cust_code") or doc.get("code", "")
            cname = doc.get("customer_name") or doc.get("cust_name") or doc.get("name", "")
            if cid:
                cust_map[cid] = cname

        def customer_display(code):
            if not code:
                return "(none)"
            return cust_map.get(code, code)

        # Load existing locations
        locations = {}
        for doc in self._wh_query_table("wh_locations"):
            key = doc.get("id", "")
            if key:
                doc.pop("_rowid", None)
                doc.setdefault("items", [])
                doc.setdefault("customer", "")
                doc.setdefault("customer_name", "")
                doc.setdefault("used_cbm", 0.0)
                doc.setdefault("max_cbm", grid_cbm)
                doc.setdefault("zone", "")
                locations[key] = doc

        # Generate missing locations
        generated = WarehouseGrid.generate_locations(
            wh_names, rows=grid_rows, cols=grid_cols,
            levels=wh_levels, default_cbm=grid_cbm, zone_map=zone_fn,
        )
        for key, loc in generated.items():
            if key not in locations:
                locations[key] = loc

        # Populate items from warehouse_storage into locations
        for doc in self._wh_query_table("warehouse_storage"):
            wh = doc.get("warehouse", "")
            loc_name = doc.get("location", "")
            if not wh or not loc_name:
                continue
            loc_key = f"{wh}:{loc_name}"
            if loc_key in locations:
                part_no = doc.get("part_no", "")
                balance = float(doc.get("balance", 0) or 0)
                if balance > 0 and part_no:
                    part_name = doc.get("part_name", "")
                    cbm = float(doc.get("cbm_per_unit", 0)
                                or doc.get("cbm", 0.10) or 0.10)
                    locations[loc_key].setdefault("items", []).append({
                        "part_no": part_no,
                        "name": part_name,
                        "qty": balance,
                        "cbm": cbm,
                    })

        # Item picker callback
        def item_picker(scr):
            items = self._wh_query_table("item")
            if not items:
                items = self._wh_query_table("product")
            if not items:
                return None
            options = []
            for it in items:
                pn = it.get("part_no") or it.get("prod_code") or ""
                nm = it.get("part_name") or it.get("prod_name") or ""
                if pn:
                    options.append((pn, f"{nm}"))
            if not options:
                return None
            chosen = grid.pick_dialog("Select Item", options)
            if chosen is None:
                return None
            for it in items:
                pn = it.get("part_no") or it.get("prod_code") or ""
                if pn == chosen:
                    nm = it.get("part_name") or it.get("prod_name") or ""
                    cbm = float(it.get("cbm_per_unit") or it.get("cbm") or 0.01)
                    return (pn, nm, cbm)
            return None

        # Customer picker callback
        def customer_picker(scr):
            custs = self._wh_query_table("customer")
            if not custs:
                return None
            options = []
            for c in custs:
                cid = c.get("customer_id") or c.get("cust_code") or c.get("code", "")
                cname = c.get("customer_name") or c.get("cust_name") or c.get("name", "")
                if cid:
                    options.append((cid, cname))
            if not options:
                return None
            chosen = grid.pick_dialog("Select Customer", options)
            if chosen is None:
                return None
            for cid, cname in options:
                if cid == chosen:
                    return (cid, cname)
            return None

        # Save callback
        def on_save(locs, levels):
            docs = []
            for key, loc in locs.items():
                has_items = bool(loc.get("items"))
                has_customer = bool(loc.get("customer"))
                default = float(loc.get("_default_cbm", grid_cbm))
                has_custom_cbm = loc.get("max_cbm", default) != default
                has_used = loc.get("used_cbm", 0) > 0
                doc = {
                    "id": key,
                    "warehouse": loc["warehouse"],
                    "row": loc["row"],
                    "col": loc["col"],
                    "level": loc["level"],
                    "loc_id": loc["loc_id"],
                    "zone": loc.get("zone", ""),
                    "max_cbm": float(loc.get("max_cbm", default)),
                    "used_cbm": float(loc.get("used_cbm", 0.0)),
                    "customer": loc.get("customer", ""),
                    "customer_name": loc.get("customer_name", ""),
                    "items": loc.get("items", []),
                }
                if has_items or has_customer or has_custom_cbm or has_used:
                    docs.append(doc)
            if docs:
                self._wh_upsert_bulk("wh_locations", docs)
            for wh_id, lv_count in levels.items():
                self._wh_filter_update("warehouse", {"whid": wh_id},
                                       {"wh_levels": int(lv_count)})

        grid = WarehouseGrid(
            stdscr,
            warehouses=wh_names,
            levels=wh_levels,
            locations=locations,
            rows=grid_rows,
            cols=grid_cols,
            default_cbm=grid_cbm,
            title="Warehouse Locations",
            item_picker=item_picker,
            customer_picker=customer_picker,
            customer_display=customer_display,
            on_save=on_save,
            zone_fn=zone_fn,
        )
        grid.run()

    @staticmethod
    def _parse_zone_map(zone_str, cols):
        """Parse zone_map string like '1-5:A,6-8:B,9-10:C' into callable(col)."""
        zones = {}
        if zone_str:
            for part in str(zone_str).split(","):
                part = part.strip()
                if ":" not in part:
                    continue
                rng, label = part.rsplit(":", 1)
                rng = rng.strip()
                label = label.strip()
                if "-" in rng:
                    lo, hi = rng.split("-", 1)
                    try:
                        for c in range(int(lo), int(hi) + 1):
                            zones[c] = label
                    except ValueError:
                        pass
                else:
                    try:
                        zones[int(rng)] = label
                    except ValueError:
                        pass
        if not zones:
            for c in range(1, cols + 1):
                if c <= 5:
                    zones[c] = "A"
                elif c <= 8:
                    zones[c] = "B"
                else:
                    zones[c] = "C"
        return lambda c: zones.get(c, "C")

    def _load_lookups(self) -> Dict[str, list]:
        """Load lookup data from DB tables (JSON storage), with fallback samples."""
        lookups = {}
        lookup_tables = set()
        for form in self.app.get("forms", {}).values():
            if isinstance(form, list):
                for field in form:
                    if "lookup" in field:
                        lookup_tables.add(field["lookup"])
        # Also scan grid column lookups
        for grid in self.app.get("grids", {}).values():
            for col in grid.get("columns", []):
                if "lookup" in col:
                    lookup_tables.add(col["lookup"])
        if self.db:
            tables = sorted(lookup_tables)
            # Batch all lookup queries in single round-trip if adapter supports it
            def _parse_lookup_rows(rows):
                """Parse JSON rows, skip empty/detail records."""
                parsed = []
                for row in rows:
                    try:
                        doc = json.loads(row.get("data", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    doc["rowid"] = row.get("rowid")
                    # Skip detail rows and empty records
                    if doc.get("_parent_rowid") is not None:
                        continue
                    real_keys = [k for k in doc if k != "rowid" and not k.startswith("_")]
                    if not real_keys:
                        continue
                    parsed.append(doc)
                return parsed

            if hasattr(self.db, 'query_many') and tables:
                queries = [(f'SELECT rowid, data FROM "{t}"', ()) for t in tables]
                try:
                    results = self.db.query_many(queries)
                    for table, rows in zip(tables, results):
                        lookups[table] = _parse_lookup_rows(rows)
                except Exception as e:
                    self._log(e, context="batch lookup SELECT")
                    # Fall back to one-by-one
                    for table in tables:
                        try:
                            rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
                            lookups[table] = _parse_lookup_rows(rows)
                        except Exception as e2:
                            self._log(e2, context=f"lookup SELECT {table}")
                            lookups[table] = []
            else:
                for table in tables:
                    try:
                        rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
                        lookups[table] = _parse_lookup_rows(rows)
                    except Exception as e:
                        self._log(e, context=f"lookup SELECT {table}")
                        lookups[table] = []
        # Provide fallback sample data for common tables if empty
        if not lookups.get("customers"):
            lookups["customers"] = [
                {"code": "C001", "name": "Acme Supplies"},
                {"code": "C002", "name": "Beta Logistics"}
            ]
        if not lookups.get("items"):
            lookups["items"] = [
                {"code": "P001", "name": "Bolt M10", "price": 1.25},
                {"code": "P002", "name": "Nut M10", "price": 0.45}
            ]
        return lookups

    def _next_doc_no(self, form_id: str, field_id: str, prefix: str,
                     fmt: str = None) -> str:
        """Generate next running number.

        fmt=None:   PREFIX-NNNNN  (simple sequential, e.g. CUS-00001)
        fmt='YYMM': PREFIX-YYMMNNNNN (monthly sequence, e.g. SO-250700001)
        """
        if fmt == "YYMM":
            today = date.today()
            yymm = today.strftime("%y%m")
            doc_prefix = f"{prefix}-{yymm}"
        else:
            doc_prefix = f"{prefix}-"
        pad = 5
        if not self.db:
            return f"{doc_prefix}{1:0{pad}d}"
        table_name = form_id.replace("_form", "")
        max_seq = 0
        try:
            rows = self.db.query(f'SELECT data FROM "{table_name}"')
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    val = str(doc.get(field_id, ""))
                    if val.startswith(doc_prefix):
                        num_part = val[len(doc_prefix):]
                        seq = int(num_part)
                        if seq > max_seq:
                            max_seq = seq
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except Exception as e:
            self._log(e, context=f"next-doc-no {table_name}/{field_id}")
        return f"{doc_prefix}{max_seq + 1:0{pad}d}"

    def _get_auto_numbers(self, form_id: str) -> Dict[str, str]:
        """Get auto-generated numbers for all PREFIX fields in a form."""
        fields = self.app["forms"].get(form_id)
        if not fields:
            return {}
        result = {}
        for f in fields:
            if f.get("type") in ("SECTION", "SPACER") or not f.get("id"):
                continue
            if f.get("prefix"):
                fmt = f.get("prefix_format")
                result[f["id"]] = self._next_doc_no(form_id, f["id"], f["prefix"], fmt)
        return result

    def _prepare_values(self, form_id: str, values: Dict[str, Any]) -> Dict:
        """Clean form values into a JSON-safe dict."""
        fields = self.app["forms"].get(form_id)
        if not fields or not isinstance(fields, list):
            return {}
        doc = {}
        for f in fields:
            if f.get("type") in ("SECTION", "SPACER"):
                continue
            fid = f.get("id")
            if not fid:
                continue
            val = values.get(fid)
            if val is None:
                val = ""
            if f["type"] == "BOOL":
                val = 1 if val else 0
            doc[fid] = str(val) if not isinstance(val, (int, float, bool)) else val
        # Preserve UDF data if present
        if "udf" in values and isinstance(values["udf"], dict):
            doc["udf"] = values["udf"]
        return doc

    def _make_unique_online_id(self, name: str, table_name: str,
                               own_rowid=None) -> str:
        """Generate a deterministic online ID that is unique in a JSON table."""
        suffix = 0
        while suffix <= 999:
            seed = name if suffix == 0 else f"{name}#{suffix}"
            candidate = self._gen_online_id(seed)
            try:
                rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
                collision = False
                for row in rows:
                    doc = json.loads(row.get("data", "{}"))
                    if str(doc.get("online_id", "")).strip() != candidate:
                        continue
                    if own_rowid is None or str(row.get("rowid")) != str(own_rowid):
                        collision = True
                        break
                if not collision:
                    return candidate
            except Exception as e:
                self._log(e, context=f"online-id lookup {table_name}")
                # Preserve save behavior if the lookup is unavailable. The
                # deterministic candidate is still valid and format-correct.
                return candidate
            suffix += 1
        raise ValueError(f"Unable to generate unique online_id for {table_name}")

    def _auto_fill_online_id(self, form_id: str, doc: Dict[str, Any],
                            own_rowid=None) -> Dict[str, Any]:
        """Fill a declared online_id field when it is blank.

        This mirrors PyStock's behavior: only forms that declare an
        ``online_id`` field participate, and existing IDs are preserved.
        """
        fields = self.app.get("forms", {}).get(form_id) or []
        if not any(f.get("id") == "online_id" for f in fields):
            return doc
        if str(doc.get("online_id") or "").strip():
            return doc

        # On edit, keep an existing ID even if the form did not return it.
        table_name = form_id.replace("_form", "")
        if own_rowid is not None and self.db:
            try:
                rows = self.db.query(
                    f'SELECT data FROM "{table_name}" WHERE rowid=?',
                    (own_rowid,))
                if rows:
                    existing = json.loads(rows[0].get("data", "{}"))
                    if str(existing.get("online_id") or "").strip():
                        doc["online_id"] = existing["online_id"]
                        return doc
            except Exception as e:
                self._log(e, context=f"online-id existing lookup {table_name}")

        name = ""
        for field in fields:
            field_id = field.get("id", "")
            if field_id.endswith("_name"):
                name = str(doc.get(field_id) or "").strip()
                if name:
                    break
        if not name:
            return doc

        doc["online_id"] = self._make_unique_online_id(
            name, table_name, own_rowid=own_rowid)
        return doc

    def _save_form_data(self, form_id: str, values: Dict[str, Any]):
        """Insert a row as JSON blob. Returns new rowid or None."""
        if not self.db:
            return None
        table_name = form_id.replace("_form", "")
        doc = self._prepare_values(form_id, values)
        if not doc:
            return None
        doc = self._auto_fill_online_id(form_id, doc)
        sql = f'INSERT INTO "{table_name}" (data) VALUES (?)'
        try:
            _, lastrowid = self.db.execute(sql, (json.dumps(doc),))
            return lastrowid
        except Exception as e:
            self._log(e, context=f"INSERT {table_name}")
            return None

    def _update_form_data(self, form_id: str, values: Dict[str, Any], rowid: int):
        """Update an existing JSON row by rowid."""
        if not self.db:
            return False
        table_name = form_id.replace("_form", "")
        doc = self._prepare_values(form_id, values)
        if not doc:
            return False
        doc = self._auto_fill_online_id(form_id, doc, own_rowid=rowid)
        sql = f'UPDATE "{table_name}" SET data = ? WHERE rowid = ?'
        try:
            self.db.execute(sql, (json.dumps(doc), rowid))
            return True
        except Exception as e:
            self._log(e, context=f"UPDATE {table_name} rowid={rowid}")
            return False

    def _save_detail_grid_data(self, grid_id: str, rows: List[Dict],
                               parent_rowid: int = None):
        """Save detail grid rows for a specific parent.

        Deletes existing rows for this parent_rowid, then re-inserts.
        Each row gets '_parent_rowid' injected into its JSON.
        """
        if not self.db or parent_rowid is None:
            return
        grid_def = self.app["grids"].get(grid_id)
        if not grid_def:
            return
        parent_form = grid_def.get("parent", "")
        table_name = parent_form.replace("_form", "")
        # Delete only this parent's detail rows — collect rowids first,
        # then batch delete in a single statement to reduce round-trips
        try:
            existing = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
            del_ids = []
            for ex_row in existing:
                try:
                    ex_doc = json.loads(ex_row.get("data", "{}"))
                    if ex_doc.get("_parent_rowid") == parent_rowid:
                        del_ids.append(ex_row["rowid"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if del_ids:
                placeholders = ",".join("?" * len(del_ids))
                self.db.execute(
                    f'DELETE FROM "{table_name}" WHERE rowid IN ({placeholders})',
                    tuple(del_ids))
        except Exception as e:
            self._log(e, context="save-detail-grid query")
        # Re-insert with parent link
        for row in rows:
            doc = {}
            for k, v in row.items():
                if k == "rowid":
                    continue
                if isinstance(v, (date,)):
                    v = v.isoformat()
                elif v is not None and not isinstance(v, (int, float, bool, str)):
                    v = str(v)
                doc[k] = v
            doc["_parent_rowid"] = parent_rowid
            has_data = any(
                v for k, v in doc.items()
                if k != "_parent_rowid" and v not in ("", 0, 0.0, None)
            )
            if has_data:
                self.db.execute(
                    f'INSERT INTO "{table_name}" (data) VALUES (?)',
                    (json.dumps(doc),)
                )

    def _save_tree_detail_data(self, grid_id: str, tree_data: list,
                               parent_rowid: int = None):
        """Save tree detail data as a single JSON blob with nested structure."""
        if not self.db or parent_rowid is None:
            return
        grid_def = self.app["grids"].get(grid_id)
        if not grid_def:
            return
        parent_form = grid_def.get("parent", "")
        table_name = parent_form.replace("_form", "")
        # Delete existing tree row for this parent — batch delete
        try:
            existing = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
            del_ids = []
            for ex_row in existing:
                try:
                    ex_doc = json.loads(ex_row.get("data", "{}"))
                    if ex_doc.get("_parent_rowid") == parent_rowid:
                        del_ids.append(ex_row["rowid"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if del_ids:
                placeholders = ",".join("?" * len(del_ids))
                self.db.execute(
                    f'DELETE FROM "{table_name}" WHERE rowid IN ({placeholders})',
                    tuple(del_ids))
        except Exception as e:
            self._log(e, context="save-tree-detail query")
        # Save entire tree as one row
        doc = {
            "_parent_rowid": parent_rowid,
            "_tree_data": tree_data
        }
        self.db.execute(
            f'INSERT INTO "{table_name}" (data) VALUES (?)',
            (json.dumps(doc),)
        )

    def _load_tree_detail_data(self, grid_id: str, parent_rowid: int = None) -> list:
        """Load tree detail data (nested JSON) for a parent record."""
        if (not self.db or parent_rowid is None or
                (self.db_connect_error and not self.is_fallback)):
            return []
        grid_def = self.app["grids"].get(grid_id)
        if not grid_def:
            return []
        parent_form = grid_def.get("parent", "")
        table_name = parent_form.replace("_form", "")
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
        except Exception as e:
            self._log(e, context=f"grid data SELECT {table_name}")
            return []
        for row in rows:
            try:
                doc = json.loads(row.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            if doc.get("_parent_rowid") == parent_rowid and "_tree_data" in doc:
                return doc["_tree_data"]
        return []

    def _find_form_layout(self, form_id: str) -> str:
        """Find a layout that contains the given form in its fields."""
        for lid, ldef in self.app["layouts"].items():
            if form_id in ldef.get("fields", {}):
                # Make sure it's actually a form ref, not a grid ref
                if form_id in self.app["forms"]:
                    return lid
            # ENTRY mode layouts store the form in header_form, not fields
            if ldef.get("header_form") == form_id:
                return lid
        return ""

    def _find_grid_parent_form(self, layout_def: Dict) -> str:
        """Find the first grid's parent form in a layout."""
        for ref_id in layout_def.get("fields", {}):
            grid_def = self.app["grids"].get(ref_id)
            if grid_def:
                return grid_def.get("parent", "")
        return ""

    def _load_grid_data(self, grid_id: str, parent_rowid: int = None,
                        view_filter: str = None) -> List[Dict]:
        """Load data for a grid, extracting fields from JSON blob.

        If parent_rowid is given, only return rows whose JSON contains
        a matching '_parent_rowid' field (detail grid filtering).
        If view_filter is given (e.g. "balance > 0"), apply as row filter.
        """
        if not self.db:
            return []
        # A failed direct Firebird connection is reported by the startup
        # warning. Do not reconnect once per grid and make the UI appear hung.
        if self.db_connect_error and not self.is_fallback:
            return []
        grid_def = self.app["grids"].get(grid_id)
        if not grid_def:
            return []
        parent_form = grid_def.get("parent", "")
        table_name = parent_form.replace("_form", "")
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
        except Exception as e:
            self._log(e, context=f"grid data SELECT {table_name}")
            return []
        result = []
        for row in rows:
            try:
                doc = json.loads(row.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                doc = {}
            doc["rowid"] = row.get("rowid")
            # Filter by parent if requested
            if parent_rowid is not None:
                if doc.get("_parent_rowid") != parent_rowid:
                    continue
            else:
                # Listing grid: skip detail rows and empty records
                if doc.get("_parent_rowid") is not None:
                    continue
                # Skip empty records (only rowid, no real data)
                real_keys = [k for k in doc if k != "rowid" and not k.startswith("_")]
                if not real_keys:
                    continue
            # Apply LISTVIEW filter
            if view_filter and view_filter != "*":
                try:
                    ctx = {"__builtins__": {}, "abs": abs, "min": min, "max": max}
                    for k, v in doc.items():
                        if k.startswith("_"):
                            continue
                        if v is None or v == '':
                            ctx[k] = 0
                        else:
                            try:
                                ctx[k] = float(str(v).replace(",", ""))
                            except (ValueError, TypeError):
                                ctx[k] = str(v)
                    if not eval(view_filter, ctx):  # noqa: S307
                        continue
                except Exception:
                    pass
            result.append(doc)
        return result

    def _prefill_detail(self, grid_id: str, prefill: Dict) -> List[Dict]:
        """Load rows from a source table to pre-populate a detail grid.

        Maps source fields to detail columns by name. Fields in the detail
        that don't exist in the source get default values (0 for numeric, '').
        Applies optional filter expression.
        """
        if not self.db:
            return []
        source = prefill["source"]
        pf_filter = prefill.get("filter")

        grid_def = self.app["grids"].get(grid_id, {})
        detail_cols = grid_def.get("columns", [])
        col_ids = {c["id"] for c in detail_cols}
        col_types = {c["id"]: c.get("type", "STRING") for c in detail_cols}

        # Load source table data (the source is a form name, table = form name)
        table_name = source
        try:
            rows = self.db.query(f'SELECT rowid, data FROM "{table_name}"')
        except Exception as e:
            self._log(e, context=f"grid data SELECT {table_name}")
            return []

        result = []
        for row in rows:
            try:
                doc = json.loads(row.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            # Skip parent-link rows (detail rows of other forms)
            if "_parent_rowid" in doc:
                continue

            # Apply filter expression (e.g. "on_hand>0", "category=='Finished Good'")
            if pf_filter:
                try:
                    ctx = {"__builtins__": {}, "abs": abs, "min": min, "max": max}
                    for k, v in doc.items():
                        if k.startswith("_"):
                            continue
                        if v is None or v == '':
                            ctx[k] = 0
                        else:
                            try:
                                ctx[k] = float(str(v).replace(",", ""))
                            except (ValueError, TypeError):
                                ctx[k] = str(v)
                    if not eval(pf_filter, ctx):  # noqa: S307
                        continue
                except Exception:
                    continue

            # Build detail row: map matching fields, default the rest
            detail_row = {}
            for c in detail_cols:
                cid = c["id"]
                if cid in doc:
                    detail_row[cid] = doc[cid]
                elif col_types.get(cid) in ("INT", "FLOAT"):
                    detail_row[cid] = 0
                else:
                    detail_row[cid] = ""
            result.append(detail_row)
        return result

    def _aggregate_wh_balance(self, warehouse_id: str) -> Dict[str, float]:
        """Sum warehouse_storage balance by part_no for a given warehouse."""
        totals: Dict[str, float] = {}
        if not self.db:
            return totals
        try:
            rows = self.db.query('SELECT data FROM "warehouse_storage"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if str(doc.get("warehouse", "")).strip() != warehouse_id:
                    continue
                pn = str(doc.get("part_no", "")).strip()
                if not pn:
                    continue
                bal = 0.0
                try:
                    bal = float(doc.get("balance", 0) or 0)
                except (ValueError, TypeError):
                    pass
                totals[pn] = totals.get(pn, 0.0) + bal
        except Exception:
            pass
        return totals

    def _run_entry_mode(self, stdscr, layout_def):
        """Full-screen grid entry mode (ENTRY forms like Web Order).

        Flow:
        1. Show popup dialog to collect header fields (customer, date, etc.)
        2. Load PREFILL data into a full-screen grid
        3. CoEdit on the entry column
        4. F10 saves header + non-zero lines
        """
        from tui.cxgrid import Grid as CxGrid, GridColumn
        from tui.layout import eval_computed_formula, _safe_float

        title = layout_def.get("title", "Entry")
        header_form_id = layout_def.get("header_form", "")
        header_fields = layout_def.get("header_fields", [])
        entry_tab = layout_def.get("entry_tab", {})
        entry_grid_id = layout_def.get("entry_grid", "")
        grid_def = self.app["grids"].get(entry_grid_id, {})
        computed_defs = layout_def.get("computed", [])
        # coedit_col can be a string or list of strings
        _raw_coedit = entry_tab.get("coedit", "")
        if isinstance(_raw_coedit, list):
            coedit_cols = _raw_coedit
        elif _raw_coedit:
            coedit_cols = [_raw_coedit]
        else:
            coedit_cols = []
        # Keep single-string compat for _collect_save_lines
        coedit_col = coedit_cols[0] if coedit_cols else ""
        prefill = entry_tab.get("prefill")

        # ── Check for edit context (viewing/editing existing record) ──
        editing_existing = False
        existing_rowid = None
        if self.edit_context:
            editing_existing = True
            existing_rowid = self.edit_context.get("rowid")
            # Load existing header values
            header_values = dict(self.edit_context.get("data", {}))
            # Load existing detail lines
            data = self._load_grid_data(entry_grid_id, existing_rowid)
            if not data:
                data = []
            self.edit_context = None
        else:
            # ── Step 1: Popup dialog for header fields ──
            header_values = self._entry_header_dialog(stdscr, title, header_fields)
            if header_values is None:
                return "__back__"

            # ── Step 2: Load prefill data ──
            if prefill:
                data = self._prefill_detail(entry_grid_id, prefill)
            else:
                data = []

            # Enrich with warehouse_storage balance if grid has wh_balance col
            wh_bal_cols = [c for c in grid_def.get("columns", [])
                          if c["id"] == "wh_balance"]
            if wh_bal_cols and data and header_values.get("warehouse"):
                wh_id = str(header_values["warehouse"]).strip()
                wh_bal = self._aggregate_wh_balance(wh_id)
                for row in data:
                    pn = str(row.get("part_no", "")).strip()
                    row["wh_balance"] = wh_bal.get(pn, 0)
                # Filter out items with zero warehouse balance
                data = [r for r in data if r.get("wh_balance", 0) > 0]

            if not data:
                self._show_message(stdscr, "No items available")
                return "__back__"

        # ── Step 3: Build full-screen grid ──
        max_y, max_x = stdscr.getmaxyx()
        grid_y = 3  # row 0=title, 1=info, 2=hints, 3+=grid

        columns = []
        from tui.layout import LayoutRenderer
        for col in grid_def.get("columns", []):
            flags = LayoutRenderer._build_col_flags(
                col, readonly=False,
                user_level=self.current_user.get("level", 0))
            columns.append(GridColumn(
                name=col["id"],
                label=col["id"].replace("_", " ").title(),
                width=col.get("width", 12),
                flags=flags
            ))

        # Wire lookup list columns for multi-column picker
        for gc in columns:
            if gc.lookup:
                lookup_grid_id = gc.lookup + "_grid"
                lg_def = self.app.get("grids", {}).get(lookup_grid_id)
                if lg_def and lg_def.get("columns"):
                    gc.lookup_list_cols = [
                        {"id": c["id"],
                         "label": c["id"].replace("_", " ").title(),
                         "width": c.get("width", 12),
                         "numeric": c.get("type") in ("INT", "FLOAT")}
                        for c in lg_def["columns"]
                    ]

        grid = CxGrid(stdscr, columns, data,
                       start_y=grid_y, start_x=1,
                       show_totals=grid_def.get("show_totals", False))
        grid._grid_id = entry_grid_id
        grid.lookup_handler = self._make_entry_lookup_handler()

        # Apply saved column config (visibility + order, not width — stretch handles width)
        saved = self.column_configs.get(entry_grid_id)
        if saved:
            col_map = {c.name: c for c in grid.columns}
            reordered = []
            for name, visible, width in saved:
                if name in col_map:
                    c = col_map.pop(name)
                    c.visible = visible
                    reordered.append(c)
            for c in grid.columns:
                if c.name in col_map:
                    reordered.append(c)
            grid.columns = reordered
            grid._stretch_columns_to_fit()

        # ── Step 4: PyStock-style single-loop CoEdit ──
        # Instead of using grid.coedit() + blocking edit_loop(), we manage
        # the edit buffer directly in this loop — like pystock's order_screen.
        from tui.cxgrid import _numeric_char_allowed, _numeric_parse

        detail_col_names = [c["id"] for c in grid_def.get("columns", [])]

        # Find coedit column indices and make others readonly visually
        coedit_indices = []  # grid column indices that are editable
        for col_name in coedit_cols:
            for i, c in enumerate(grid.columns):
                if c.name == col_name and c.editable:
                    coedit_indices.append(i)
                    break
        active_ce = 0  # index into coedit_indices (which coedit col is active)
        ce_ci = coedit_indices[0] if coedit_indices else -1

        if coedit_indices:
            # Use grid's coedit for visual styling on first column
            grid.coedit(coedit_cols[0])
            grid._coedit_pending_start = False
            # Also mark additional coedit columns as editable
            for ci in coedit_indices[1:]:
                grid.columns[ci].editable = True

        # Edit state managed here, not in grid.edit_loop
        edit_buf = ""
        edit_pos = 0
        editing = False  # True when actively typing in a cell
        need_full_redraw = True

        def _save_cell():
            """Write edit_buf into the current row's coedit column."""
            nonlocal edit_buf, edit_pos, editing
            if not editing or ce_ci < 0:
                return
            row_data = grid._view_row(grid.row)
            if row_data is None:
                return
            col = grid.columns[ce_ci]
            if col.numeric:
                row_data[col.name] = _numeric_parse(edit_buf)
            else:
                row_data[col.name] = edit_buf
            grid.compute_row(row_data)

        def _start_edit(initial_ch=None):
            """Begin editing the current cell in the coedit column."""
            nonlocal edit_buf, edit_pos, editing
            if ce_ci < 0 or not grid._view_indices:
                return
            grid.col = ce_ci
            row_data = grid._view_row(grid.row)
            if row_data is None:
                return
            col = grid.columns[ce_ci]
            current = str(grid._field_value(col, row_data) or "")
            if col.numeric:
                try:
                    num = float(str(current).replace(",", ""))
                    if abs(num - round(num)) < 1e-9:
                        current = str(int(round(num)))
                    else:
                        current = f"{num:.10f}".rstrip("0").rstrip(".")
                except (ValueError, TypeError):
                    current = ""
                if current == "0":
                    current = ""
            edit_buf = current
            edit_pos = len(edit_buf)
            if initial_ch is not None and 32 <= initial_ch <= 126:
                ch = chr(initial_ch)
                if col.numeric:
                    if _numeric_char_allowed("", 0, ch):
                        edit_buf = ch
                        edit_pos = 1
                else:
                    edit_buf = ch
                    edit_pos = 1
            editing = True
            grid.edit_mode = True
            grid.edit_buffer = edit_buf
            grid.edit_pos = edit_pos
            curses.curs_set(1)

        def _draw_frame():
            """Draw title bar (row 0) and info line (row 1)."""
            info_parts = []
            for hf in header_fields:
                fid = hf.get("id")
                if not fid:
                    continue
                val = header_values.get(fid, "")
                if val:
                    info_parts.append(f"{fid}: {val}")
            info_line = "  ".join(info_parts)
            title_bar = f" {title} "
            if editing_existing:
                status = header_values.get("status", "")
                if status:
                    title_bar = f" {title} [{status}] "
            try:
                stdscr.addstr(0, 0, " " * max_x,
                              curses.color_pair(grid.CLR_HEADER))
                stdscr.addstr(0, max(0, (max_x - len(title_bar)) // 2),
                              title_bar,
                              curses.color_pair(grid.CLR_HEADER) | curses.A_BOLD)
            except curses.error:
                pass
            try:
                stdscr.addstr(1, 1, info_line[:max_x - 2], curses.A_BOLD)
            except curses.error:
                pass

        def _collect_save_lines():
            """Collect lines where any coedit column has a non-zero value."""
            lines = []
            for row in grid.data:
                if coedit_cols and not editing_existing:
                    has_value = False
                    for cc in coedit_cols:
                        v = row.get(cc)
                        if v not in (None, "", 0, 0.0, "0"):
                            has_value = True
                            break
                    if not has_value:
                        continue
                lines.append(dict(row))
            return lines

        # Start editing first coedit cell
        if coedit_indices and grid._view_indices:
            active_ce = 0
            ce_ci = coedit_indices[0]
            grid.row = 0
            grid.col = ce_ci
            grid.scroll_offset = 0
            _start_edit()

        while True:
            # ── Draw ──
            if need_full_redraw:
                stdscr.clear()
                need_full_redraw = False
                _draw_frame()

            # Row 2: hints + computed summaries
            comp_parts = []
            if computed_defs:
                merged = dict(header_values)
                for comp in computed_defs:
                    val = eval_computed_formula(
                        comp["expr"], merged, grid.data, detail_col_names)
                    merged[comp["name"]] = val
                    comp_parts.append(f"{comp['name']}: {val}")
            if editing_existing:
                hint = "F5 Workflow  Ctrl+D Del Row  F10 Save  ESC Back"
            else:
                hint = "Ctrl+D Del Row  F5 Undo  F7 Filter  F10 Save  ESC Cancel"
            comp_str = "  ".join(comp_parts)
            try:
                stdscr.move(2, 0)
                stdscr.clrtoeol()
                stdscr.addstr(2, 1, hint, curses.A_DIM)
                if comp_str:
                    cx = max(len(hint) + 3, max_x - len(comp_str) - 2)
                    stdscr.addstr(2, cx, comp_str[:max_x - cx - 1],
                                  curses.A_BOLD)
            except curses.error:
                pass

            # Sync edit state to grid for draw_edit_field
            grid.edit_buffer = edit_buf
            grid.edit_pos = edit_pos
            grid.edit_mode = editing

            grid.draw()

            if editing:
                grid.draw_edit_field()
            else:
                curses.curs_set(0)
                stdscr.refresh()

            # ── Input ──
            key = stdscr.getch()

            # ESC
            if key == 27:
                stdscr.nodelay(True)
                nk = stdscr.getch()
                stdscr.nodelay(False)
                if nk == -1:
                    # Real ESC
                    if editing:
                        _save_cell()
                        editing = False
                        grid.edit_mode = False
                        curses.curs_set(0)
                    else:
                        return "__back__"
                continue

            # Enter / Tab: save cell, advance to next coedit col or next row
            if key in (curses.KEY_ENTER, 10, 13, 9):
                _save_cell()
                editing = False
                grid.edit_mode = False
                if len(coedit_indices) > 1 and active_ce < len(coedit_indices) - 1:
                    # Move to next coedit column on same row
                    active_ce += 1
                    ce_ci = coedit_indices[active_ce]
                else:
                    # Move to first coedit column on next row
                    active_ce = 0
                    ce_ci = coedit_indices[0] if coedit_indices else -1
                    if grid.row < len(grid._view_indices) - 1:
                        grid.row += 1
                        grid.ensure_visible()
                _start_edit()
                continue

            # Down: save cell, move down (stay on same coedit col)
            if key == curses.KEY_DOWN:
                _save_cell()
                editing = False
                grid.edit_mode = False
                if grid.row < len(grid._view_indices) - 1:
                    grid.row += 1
                    grid.ensure_visible()
                _start_edit()
                continue

            # Up: save cell, move up (stay on same coedit col)
            if key == curses.KEY_UP:
                _save_cell()
                editing = False
                grid.edit_mode = False
                if grid.row > 0:
                    grid.row -= 1
                    grid.ensure_visible()
                _start_edit()
                continue

            # PgDn / PgUp
            if key == curses.KEY_NPAGE:
                _save_cell()
                editing = False
                grid.edit_mode = False
                grid.page_down()
                _start_edit()
                continue
            if key == curses.KEY_PPAGE:
                _save_cell()
                editing = False
                grid.edit_mode = False
                grid.page_up()
                _start_edit()
                continue

            # Ctrl+D: delete current detail row (deferred — not saved until F10)
            if key == 4:
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)
                if self._confirm_action(stdscr, "Delete selected row? (Y/N)",
                                        title="Confirm Delete"):
                    grid.delete_current_row()
                    need_full_redraw = True
                    if grid._view_indices:
                        _start_edit()
                continue

            # F5: undo last row deletion
            if key == curses.KEY_F5 and not editing_existing:
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)
                grid.undo_last_deletion()
                need_full_redraw = True
                _start_edit()
                continue

            # Del (non-editing): clear current cell value
            if key == curses.KEY_DC and not editing:
                if coedit_indices and grid._view_indices:
                    data_idx = grid._data_index(grid.row)
                    if data_idx >= 0 and ce_ci >= 0:
                        col = grid.columns[ce_ci]
                        grid.data[data_idx][col.name] = ""
                        grid.set_status(f"{col.name} cleared")
                        need_full_redraw = True
                continue

            # F10 or Ctrl+S: save
            if key in (curses.KEY_F10, 19):
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)

                # Sync computed fields into header_values so they persist
                if computed_defs:
                    merged = dict(header_values)
                    for comp in computed_defs:
                        val = eval_computed_formula(
                            comp["expr"], merged, grid.data, detail_col_names)
                        merged[comp["name"]] = val
                        header_values[comp["name"]] = val

                lines = _collect_save_lines()
                if not lines:
                    self._show_message(stdscr, "No quantities entered")
                    need_full_redraw = True
                    _start_edit()
                    continue

                if editing_existing and existing_rowid:
                    ok = self._update_form_data(header_form_id, header_values, existing_rowid)
                    parent_rid = existing_rowid if ok else None
                else:
                    parent_rid = self._save_form_data(header_form_id, header_values)
                if parent_rid:
                    self._save_detail_grid_data(entry_grid_id, lines, parent_rid)
                    self._show_save_ok(stdscr)
                else:
                    # Header save failed — do NOT silently drop detail rows
                    # and do NOT report success. Detail rows are only meaningful
                    # against a persisted parent. Return a distinct sentinel so
                    # the caller keeps the user on this screen to retry,
                    # instead of navigating away and losing entered values.
                    self._show_message(
                        stdscr,
                        "Save FAILED: header record could not be written. "
                        "Detail rows were not saved.")
                    return "__save_failed__"

                cfg = [(c.name, c.visible, c.width) for c in grid.columns]
                if cfg != self.column_configs.get(entry_grid_id):
                    self.column_configs[entry_grid_id] = cfg
                    self._save_column_configs()
                return "__saved__"

            # F5: workflow
            if key == curses.KEY_F5:
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)

                workflow = layout_def.get("workflow")
                if not workflow:
                    _start_edit()
                    continue
                lines = _collect_save_lines()
                if not lines:
                    self._show_message(stdscr, "No quantities entered")
                    need_full_redraw = True
                    _start_edit()
                    continue
                if editing_existing and existing_rowid:
                    self._update_form_data(header_form_id, header_values, existing_rowid)
                    parent_rid = existing_rowid
                else:
                    parent_rid = self._save_form_data(header_form_id, header_values)
                if parent_rid:
                    self._save_detail_grid_data(entry_grid_id, lines, parent_rid)
                    tab_data = {entry_grid_id: lines}
                    self.edit_context = {"rowid": parent_rid, "data": header_values}
                    self._handle_workflow(
                        stdscr, header_form_id, header_values,
                        tab_data, workflow, layout_def)
                    if self.edit_context:
                        header_values = self.edit_context.get("data", header_values)
                    self.edit_context = None
                    self._show_save_ok(stdscr)
                return "__saved__"

            # F7: filter
            if key == curses.KEY_F7:
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)
                grid._on_toggle_filter()
                need_full_redraw = True
                _start_edit()
                continue

            # F8: column config
            if key == curses.KEY_F8:
                _save_cell()
                editing = False
                grid.edit_mode = False
                curses.curs_set(0)
                grid._open_column_config()
                cfg = [(c.name, c.visible, c.width) for c in grid.columns]
                self.column_configs[entry_grid_id] = cfg
                self._save_column_configs()
                need_full_redraw = True
                _start_edit()
                continue

            # Resize
            if key == curses.KEY_RESIZE:
                max_y, max_x = stdscr.getmaxyx()
                need_full_redraw = True
                continue

            # ── Inline editing: digit/char input ──
            if editing and ce_ci >= 0:
                col = grid.columns[ce_ci]

                # Backspace
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    if edit_pos > 0:
                        edit_buf = edit_buf[:edit_pos - 1] + edit_buf[edit_pos:]
                        edit_pos -= 1
                    continue

                # Delete
                if key == curses.KEY_DC:
                    if edit_pos < len(edit_buf):
                        edit_buf = edit_buf[:edit_pos] + edit_buf[edit_pos + 1:]
                    continue

                # Home / End
                if key == curses.KEY_HOME:
                    edit_pos = 0
                    continue
                if key == curses.KEY_END:
                    edit_pos = len(edit_buf)
                    continue

                # Left / Right within cell
                if key == curses.KEY_LEFT:
                    edit_pos = max(0, edit_pos - 1)
                    continue
                if key == curses.KEY_RIGHT:
                    edit_pos = min(len(edit_buf), edit_pos + 1)
                    continue

                # Printable characters
                if 32 <= key <= 126:
                    ch = chr(key)
                    if col.numeric:
                        if not _numeric_char_allowed(edit_buf, edit_pos, ch):
                            continue
                    if len(edit_buf) < col.width * 2:
                        edit_buf = edit_buf[:edit_pos] + ch + edit_buf[edit_pos:]
                        edit_pos += 1
                    continue

            # If not editing and a printable char, start editing with it
            elif 32 <= key <= 126 and ce_ci >= 0:
                _start_edit(key)
                continue

    def _entry_header_dialog(self, stdscr, title, header_fields):
        """Show a popup dialog to collect header field values.

        Returns dict of {field_id: value} or None if cancelled.
        """
        from tui.widgets import TextInput, Combobox, LookupField, DateInput, NumericInput
        from curses.textpad import rectangle

        max_y, max_x = stdscr.getmaxyx()
        # Filter to editable header fields that need user input.
        # Skip readonly fields (auto-filled by lookup or default).
        header_fields = [f for f in header_fields
                         if f.get("type") not in ("SECTION", "SPACER") and f.get("id")]
        input_fields = [f for f in header_fields
                        if not LayoutRenderer._is_readonly_for_level(
                            f, self.current_user.get("level", 0))]
        if not input_fields:
            # All fields are readonly/auto — return defaults
            result = {}
            for f in header_fields:
                if f.get("default"):
                    result[f["id"]] = self._resolve_default(f["default"])
                elif f.get("prefix"):
                    result[f["id"]] = ""
                else:
                    result[f["id"]] = ""
            return result

        dlg_h = len(input_fields) + 6
        # Size dialog to fit widest field + extra for [F4] on lookup fields
        label_w = 16
        max_field = max((f.get("length", 20) for f in input_fields), default=20)
        has_lookup = any(f.get("lookup") for f in input_fields)
        extra = 6 if has_lookup else 0  # space for " [F4]"
        dlg_w = min(max(max_field + label_w + 6 + extra, 50), max_x - 4)
        dy = max(1, (max_y - dlg_h) // 2)
        dx = max(1, (max_x - dlg_w) // 2)

        # Build widgets
        widgets = []
        labels = []
        values = {}
        for i, f in enumerate(input_fields):
            fid = f["id"]
            label = fid.replace("_", " ").title()
            wy = dy + 2 + i
            lx = dx + 2
            wx = dx + label_w + 2
            fw = min(f.get("length", 20), dlg_w - label_w - 4)

            if f.get("type") in ("INT", "FLOAT"):
                w = NumericInput(wy, wx, fw)
            elif f.get("type") in ("DATE", "DATETIME"):
                w = DateInput(wy, wx, fw)
                from datetime import date as _date
                w.load(_date.today().isoformat())
            elif f.get("lookup"):
                ldata = self.lookup_data.get(f["lookup"], [])
                # Get LIST column defs for multi-column popup
                lookup_grid_id = f["lookup"] + "_grid"
                lcols = self.app.get("grids", {}).get(lookup_grid_id, {}).get("columns")
                # Find key/display fields — prefer LIST first column as key
                vf, df = fid, "name"
                if lcols:
                    vf = lcols[0]["id"]
                    # Display field: second column or first _name column
                    for lc in lcols[1:]:
                        if lc["id"].endswith("_name") or lc["id"] == "name":
                            df = lc["id"]
                            break
                    else:
                        if len(lcols) > 1:
                            df = lcols[1]["id"]
                elif ldata:
                    sample = ldata[0]
                    skip = {"rowid", "_parent_rowid"}
                    fields = [k for k in sample if k not in skip]
                    if fid in sample:
                        vf = fid
                    else:
                        for try_k in ("code", "id"):
                            if try_k in sample:
                                vf = try_k
                                break
                        else:
                            for sf in fields:
                                if sf.endswith(("_id", "_code", "_no")):
                                    vf = sf
                                    break
                            else:
                                if fields:
                                    vf = fields[0]
                    for try_n in ("name", "description", "full_name", "title"):
                        if try_n in sample:
                            df = try_n
                            break
                    else:
                        for sf in fields:
                            if sf.endswith("_name"):
                                df = sf
                                break
                        else:
                            if len(fields) > 1:
                                df = fields[1]
                w = LookupField(wy, wx, fw, ldata,
                                display_field=df, value_field=vf,
                                list_columns=lcols)
                if f.get("default"):
                    dval = self._resolve_default(f["default"])
                    if dval:
                        w.load(str(dval))
            elif f.get("enum_list"):
                w = Combobox(wy, wx, fw, options=f["enum_list"])
            else:
                w = TextInput(wy, wx, fw)
                if f.get("default"):
                    w.load(str(self._resolve_default(f["default"])))

            w._field_id = fid
            widgets.append(w)
            labels.append((label, lx, wy))

        focus = 0
        while True:
            stdscr.clear()
            try:
                rectangle(stdscr, dy, dx, dy + dlg_h - 1, dx + dlg_w - 1)
                stdscr.addstr(dy, dx + 2, f" {title} ", curses.A_BOLD)
                # Draw labels
                for label, lx, wy in labels:
                    stdscr.addstr(wy, lx, f"{label}:", curses.A_NORMAL)
                # Draw widgets
                for i, w in enumerate(widgets):
                    w.focused = (i == focus)
                    w.draw(stdscr)
                # Focus arrow
                if focus < len(widgets):
                    _, lx, wy = labels[focus]
                    stdscr.addstr(wy, lx - 1, ">", curses.A_BOLD)
                # Hint
                hint_y = dy + dlg_h - 2
                stdscr.addstr(hint_y, dx + 2,
                              "Tab/Enter: next  F10: OK  ESC: cancel",
                              curses.A_DIM)
            except curses.error:
                pass

            # Position cursor
            if focus < len(widgets):
                w = widgets[focus]
                try:
                    curses.curs_set(1)
                    stdscr.move(w.y, w.cursor_x())
                except curses.error:
                    pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == 27:
                stdscr.nodelay(True)
                nk = stdscr.getch()
                stdscr.nodelay(False)
                if nk == -1:
                    curses.curs_set(0)
                    return None
                continue
            elif key in (curses.KEY_F10, 19):
                # Collect values
                result = {}
                for w in widgets:
                    result[w._field_id] = w.value
                # Also fill auto-number, defaults, readonly fields
                for f in header_fields:
                    fid = f["id"]
                    if fid not in result:
                        if f.get("default"):
                            result[fid] = self._resolve_default(f["default"])
                        else:
                            result[fid] = ""
                # Auto-fill lookup companion fields
                for w in widgets:
                    fid = w._field_id
                    fdef = next((f for f in header_fields if f["id"] == fid), None)
                    if fdef and fdef.get("lookup") and fdef.get("lookupfill"):
                        lfill = fdef["lookupfill"]
                        if isinstance(lfill, str):
                            # Resolve from lookup data
                            ldata = self.lookup_data.get(fdef["lookup"], [])
                            for item in ldata:
                                vals = list(item.values())
                                if str(w.value) in [str(v) for v in vals]:
                                    if lfill in item:
                                        result[lfill] = item[lfill]
                                    break
                curses.curs_set(0)
                return result
            elif key in (9, curses.KEY_DOWN, curses.KEY_ENTER, 10, 13):
                focus = (focus + 1) % len(widgets)
            elif key in (curses.KEY_BTAB, 353, curses.KEY_UP):
                focus = (focus - 1) % len(widgets)
            elif key == curses.KEY_F4:
                if focus < len(widgets) and isinstance(widgets[focus], LookupField):
                    widgets[focus].handle_key(curses.KEY_F4)
            else:
                if focus < len(widgets):
                    widgets[focus].handle_key(key)

    def _make_entry_lookup_handler(self):
        """Create a lookup handler for entry mode grids."""
        lookup_data = self.lookup_data

        def handler(lookup_name, val, list_all=False, full_record=False):
            items = lookup_data.get(lookup_name, [])
            if list_all:
                if full_record:
                    # Return full record dicts for multi-column picker
                    return [dict(item) for item in items]
                result = []
                for item in items:
                    skip = {"rowid", "_parent_rowid"}
                    fields = [k for k in item if k not in skip]
                    key = None
                    for f in fields:
                        if f.endswith(("_id", "_code", "_no")):
                            key = str(item[f])
                            break
                    if key is None and fields:
                        key = str(item[fields[0]])
                    display = None
                    for f in fields:
                        if f.endswith("_name"):
                            display = str(item[f])
                            break
                    if display is None and len(fields) > 1:
                        display = str(item[fields[1]])
                    result.append((key or "", display or key or ""))
                return result
            else:
                if val is None:
                    return None
                val_str = str(val)
                for item in items:
                    skip = {"rowid", "_parent_rowid"}
                    fields = [k for k in item if k not in skip]
                    for f in fields:
                        if str(item[f]) == val_str:
                            if full_record:
                                return dict(item)
                            for df in fields:
                                if df.endswith("_name"):
                                    return str(item[df])
                            return val_str
                return None
        return handler

    @staticmethod
    def _show_message(stdscr, msg):
        """Show a centered message popup, dismiss on any key."""
        max_y, max_x = stdscr.getmaxyx()
        box_w = len(msg) + 4
        box_h = 5
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, y, x)
            win.attron(curses.A_BOLD)
            win.border()
            win.addstr(2, 2, msg, curses.A_NORMAL)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            stdscr.touchwin()
        except curses.error:
            pass

    @staticmethod
    def _show_save_ok(stdscr):
        """Show a centered 'Save OK' popup, dismiss on any key."""
        max_y, max_x = stdscr.getmaxyx()
        msg = " Save OK "
        box_w = len(msg) + 4
        box_h = 5
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, y, x)
            win.attron(curses.A_BOLD)
            win.border()
            win.addstr(2, 2, msg, curses.A_REVERSE)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()  # wait for any key
            stdscr.touchwin()
            stdscr.refresh()
        except curses.error:
            pass

    @staticmethod
    def _show_results_grid(stdscr, title: str, rows: list,
                           col_spec: list = None):
        """Show a full-screen read-only grid with result data. ESC to close.

        col_spec: optional list of (name, label, width) tuples to control
                  column order and display. If None, auto-detect from data.
        """
        if not rows:
            return
        from tui.cxgrid import Grid, GridColumn
        columns = []
        if col_spec:
            for name, label, width in col_spec:
                val = rows[0].get(name)
                flags = ["ro"]
                if isinstance(val, (int, float)):
                    flags.append("num")
                columns.append(GridColumn(name, label, width, flags))
        else:
            sample = rows[0]
            for key in sample:
                if key.startswith("_"):
                    continue
                val = sample[key]
                width = max(len(str(key)), 8)
                for r in rows[:20]:
                    width = max(width, len(str(r.get(key, ""))))
                width = min(width + 2, 40)
                flags = ["ro"]
                if isinstance(val, (int, float)):
                    flags.append("num")
                columns.append(GridColumn(key, key.replace("_", " ").title(),
                                          width, flags))
        grid = Grid(stdscr, columns, list(rows), start_y=3, start_x=1)
        max_y, max_x = stdscr.getmaxyx()
        while True:
            stdscr.clear()
            # Title bar
            try:
                stdscr.addstr(0, 1, f"\u2500 {title} \u2500",
                              curses.A_BOLD)
            except curses.error:
                stdscr.addstr(0, 1, f"- {title} -", curses.A_BOLD)
            # Toolbar
            toolbar = "/ Search  C Cols  Ctrl+F Filter"
            count_str = f"{len(rows)}/{len(rows)} records"
            try:
                stdscr.addstr(1, 1, toolbar, curses.A_DIM)
                stdscr.addstr(1, max_x - len(count_str) - 2,
                              count_str, curses.A_DIM)
            except curses.error:
                pass
            # Status bar
            status = " ESC=Close  \u2191\u2193=Navigate "
            try:
                stdscr.addstr(max_y - 1, 2, status, curses.A_DIM)
            except curses.error:
                pass
            grid.draw()
            key = stdscr.getch()
            if key == 27:
                break
            grid.handle_input(key)
        stdscr.touchwin()
        stdscr.refresh()

    @staticmethod
    def _prompt_number(stdscr, title: str, default: float = 1) -> float:
        """Show a small input dialog to prompt for a numeric value.
        Returns the value or None if cancelled."""
        max_y, max_x = stdscr.getmaxyx()
        box_w = max(len(title) + 10, 30)
        box_h = 5
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        buf = str(int(default)) if default == int(default) else str(default)
        cur = len(buf)
        try:
            while True:
                win = curses.newwin(box_h, box_w, y, x)
                win.attron(curses.A_BOLD)
                win.border()
                win.addstr(0, 2, f" {title} ", curses.A_BOLD)
                field_w = box_w - 6
                display = buf[:field_w].ljust(field_w)
                win.addstr(2, 3, display, curses.A_REVERSE)
                win.refresh()
                curses.curs_set(1)
                try:
                    stdscr.move(y + 2, x + 3 + min(cur, field_w - 1))
                except curses.error:
                    pass
                key = stdscr.getch()
                if key in (10, 13, curses.KEY_ENTER):
                    curses.curs_set(0)
                    stdscr.touchwin()
                    stdscr.refresh()
                    try:
                        return float(buf) if buf else default
                    except ValueError:
                        return default
                elif key == 27:
                    curses.curs_set(0)
                    stdscr.touchwin()
                    stdscr.refresh()
                    return None
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if cur > 0:
                        buf = buf[:cur - 1] + buf[cur:]
                        cur -= 1
                elif key == curses.KEY_LEFT and cur > 0:
                    cur -= 1
                elif key == curses.KEY_RIGHT and cur < len(buf):
                    cur += 1
                elif 32 <= key < 127:
                    ch = chr(key)
                    if ch.isdigit() or ch == '.':
                        buf = buf[:cur] + ch + buf[cur:]
                        cur += 1
        except curses.error:
            curses.curs_set(0)
            return default

    @staticmethod
    def _show_popup(stdscr, message: str):
        """Show a centered popup with a message, dismiss on any key."""
        max_y, max_x = stdscr.getmaxyx()
        msg = f" {message} "
        box_w = min(len(msg) + 4, max_x - 4)
        box_h = 5
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, y, x)
            win.attron(curses.A_BOLD)
            win.border()
            # Truncate message if too long
            display = msg[:box_w - 4]
            win.addstr(2, 2, display, curses.A_REVERSE)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            stdscr.touchwin()
            stdscr.refresh()
        except curses.error:
            pass

    @staticmethod
    def _confirm_action(stdscr, message: str, title: str = "Confirm") -> bool:
        """Show a centered Y/N confirmation dialog."""
        max_y, max_x = stdscr.getmaxyx()
        msg = f" {message} "
        box_w = min(max(len(msg) + 4, len(title) + 6, 20), max_x - 4)
        box_h = 5
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, y, x)
            win.attron(curses.A_BOLD)
            win.border()
            win.addstr(0, 2, f" {title[:box_w - 6]} ", curses.A_BOLD)
            display = msg[:box_w - 4]
            win.addstr(2, 2, display, curses.A_REVERSE)
            win.refresh()
            stdscr.nodelay(False)
            key = stdscr.getch()
            stdscr.touchwin()
            stdscr.refresh()
            return key in (ord('y'), ord('Y'))
        except curses.error:
            return False

    @staticmethod
    def _show_multiline_popup(stdscr, title: str, message: str):
        """Show a centered multi-line popup, dismiss on any key."""
        lines = [line.rstrip() for line in str(message).splitlines()]
        if not lines:
            lines = [""]
        max_y, max_x = stdscr.getmaxyx()
        content_w = max(len(title) + 2, max((len(line) for line in lines), default=0))
        box_w = min(max(content_w + 4, 36), max_x - 4)
        visible_lines = [line[:max(1, box_w - 4)] for line in lines]
        box_h = min(max(len(visible_lines) + 4, 7), max_y - 2)
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, y, x)
            win.attron(curses.A_BOLD)
            win.border()
            if title:
                win.addstr(0, 2, f" {title[:box_w - 6]} ", curses.A_BOLD)
            max_body = box_h - 3
            for i, line in enumerate(visible_lines[:max_body]):
                win.addstr(1 + i, 2, line)
            hint = "Press any key"
            win.addstr(box_h - 2, max(2, box_w - len(hint) - 3), hint, curses.A_DIM)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            stdscr.touchwin()
            stdscr.refresh()
        except curses.error:
            pass

    @staticmethod
    def _show_workflow_menu(stdscr, transitions: list,
                            form_title: str = "",
                            actions: list = None) -> dict:
        """Show a popup menu of workflow transitions + actions, return selected or None.

        Returns dict with either:
          - {"from", "to", "action", "script"} for transitions (changes status)
          - {"_action": True, "label", "script"} for actions (no status change)
          - None if cancelled
        """
        max_y, max_x = stdscr.getmaxyx()
        # Build menu items: transitions first, then separator + actions
        menu_items = []  # list of (label, entry_dict_or_None)
        suffix = f" {form_title}" if form_title else ""
        num = 1
        for t in transitions:
            menu_items.append((f"{num}. {t['action'].title()}{suffix}", t))
            num += 1
        # Add actions with optional separators
        if actions:
            for a in actions:
                if a.get("separator"):
                    menu_items.append(("---", None))
                else:
                    label = a.get("label", a.get("script", "Action"))
                    entry = {"_action": True, "label": label, "script": a["script"]}
                    menu_items.append((f"{num}. {label}", entry))
                    num += 1

        if not menu_items:
            return None

        # Compute selectable indices (skip separators)
        selectable = [i for i, (_, e) in enumerate(menu_items) if e is not None]
        if not selectable:
            return None

        labels = [lbl for lbl, _ in menu_items]
        max_item_w = max(len(s) for s in labels)
        box_w = max(max_item_w + 6, 20)
        box_h = len(labels) + 4
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)
        sel_pos = 0  # index into selectable[]
        try:
            while True:
                sel = selectable[sel_pos]
                win = curses.newwin(box_h, box_w, y, x)
                win.attron(curses.A_BOLD)
                win.border()
                win.addstr(0, 2, " Actions ", curses.A_BOLD)
                for i, label in enumerate(labels):
                    row_y = i + 2
                    if label == "---":
                        # Draw separator
                        try:
                            win.addstr(row_y, 1, "\u2500" * (box_w - 2), curses.A_DIM)
                        except curses.error:
                            win.addstr(row_y, 1, "-" * (box_w - 2), curses.A_DIM)
                    else:
                        attr = curses.A_REVERSE if i == sel else curses.A_NORMAL
                        win.addstr(row_y, 2, label.ljust(box_w - 4), attr)
                win.refresh()
                key = stdscr.getch()
                if key == curses.KEY_UP and sel_pos > 0:
                    sel_pos -= 1
                elif key == curses.KEY_DOWN and sel_pos < len(selectable) - 1:
                    sel_pos += 1
                elif key in (10, 13, curses.KEY_ENTER, curses.PADENTER):
                    stdscr.touchwin()
                    stdscr.refresh()
                    return menu_items[sel][1]
                elif key == 27:
                    stdscr.touchwin()
                    stdscr.refresh()
                    return None
                elif ord('1') <= key <= ord('9'):
                    idx = key - ord('1')
                    if 0 <= idx < len(selectable):
                        stdscr.touchwin()
                        stdscr.refresh()
                        return menu_items[selectable[idx]][1]
        except curses.error:
            return None

    # ── Report engine ─────────────────────────────────────────────

    def _report_browser(self, stdscr):
        """Full-screen report listing. Enter=Run, F2=Edit, F3=New, Del=Delete."""
        from dsl_lib.report_designer import (
            load_saved_reports, report_editor, delete_report
        )

        def _build_items():
            items = []
            # DSL-defined reports
            for name, rdef in self.app.get("reports", {}).items():
                items.append({
                    "name": name,
                    "title": rdef.get("title", name),
                    "source": rdef.get("source", ""),
                    "category": rdef.get("category", ""),
                    "tag": "DSL",
                    "def": rdef,
                })
            # DB-saved reports
            for name, rdef in load_saved_reports(self.db).items():
                if name not in self.app.get("reports", {}):
                    items.append({
                        "name": name,
                        "title": rdef.get("title", name),
                        "source": rdef.get("source", ""),
                        "category": rdef.get("category", ""),
                        "tag": "Custom",
                        "def": rdef,
                    })
            return items

        items = _build_items()
        sel = 0
        scroll = 0

        while True:
            stdscr.erase()
            max_y, max_x = stdscr.getmaxyx()
            vis = max_y - 5  # title(1) + header(1) + sep(1) + status(2)

            # ── Title bar ──
            title_bar = " Reports "
            try:
                stdscr.addstr(0, 0, " " * max_x,
                              curses.A_REVERSE | curses.A_BOLD)
                stdscr.addstr(0, 1, title_bar,
                              curses.A_REVERSE | curses.A_BOLD)
            except curses.error:
                pass

            # ── Column headers ──
            name_w = max(20, max_x - 50)
            hdr = f"  {'Report':<{name_w}} {'Category':<12} {'Source':<16} {'Type':<8}"
            try:
                stdscr.addstr(1, 0, hdr[:max_x],
                              curses.A_BOLD | curses.A_UNDERLINE)
            except curses.error:
                pass

            # ── Separator ──
            try:
                stdscr.addstr(2, 0, "\u2500" * max_x, curses.A_DIM)
            except curses.error:
                pass

            if items:
                if sel >= len(items):
                    sel = max(0, len(items) - 1)
                if sel < scroll:
                    scroll = sel
                if sel >= scroll + vis:
                    scroll = sel - vis + 1

                end = min(scroll + vis, len(items))
                for i in range(scroll, end):
                    ry = 3 + (i - scroll)
                    if ry >= max_y - 2:
                        break
                    item = items[i]
                    tag = f"[{item['tag']}]"
                    cat = item.get("category", "")
                    line = f"  {item['title']:<{name_w}} {cat:<12} {item['source']:<16} {tag:<8}"
                    line = line[:max_x]
                    attr = curses.A_REVERSE if i == sel else curses.A_NORMAL
                    try:
                        stdscr.addstr(ry, 0, line, attr)
                    except curses.error:
                        pass
            else:
                try:
                    stdscr.addstr(4, 3, "No reports. Press F3 to create one.",
                                  curses.A_DIM)
                except curses.error:
                    pass

            # ── Status bar ──
            sy = max_y - 2
            try:
                stdscr.addstr(sy, 0, "\u2500" * max_x, curses.A_DIM)
            except curses.error:
                pass
            cnt = f" {sel + 1}/{len(items)}" if items else " 0/0"
            hints = "Enter=Run  F2=Edit  F3=New  Del=Delete  ESC=Back"
            status = f"{cnt}  {hints}"
            try:
                stdscr.addstr(max_y - 1, 1, status[:max_x - 2], curses.A_DIM)
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and items and sel < len(items) - 1:
                sel += 1
            elif key == curses.KEY_PPAGE:
                sel = max(0, sel - vis)
            elif key == curses.KEY_NPAGE and items:
                sel = min(len(items) - 1, sel + vis)
            elif key == curses.KEY_HOME:
                sel = 0
            elif key == curses.KEY_END and items:
                sel = len(items) - 1

            # Enter — run selected report
            elif key in (10, 13, curses.KEY_ENTER) and items:
                chosen = items[sel]
                if chosen["tag"] == "Custom":
                    self.app.setdefault("reports", {})[chosen["name"]] = chosen["def"]
                self._run_report(stdscr, chosen["name"])
                if chosen["tag"] == "Custom":
                    self.app.get("reports", {}).pop(chosen["name"], None)
                items = _build_items()
                continue

            # F3 — new report
            elif key == curses.KEY_F3:
                result = report_editor(stdscr, self.db)
                if result:
                    items = _build_items()
                    sel = len(items) - 1
                continue

            # F2 — edit selected report
            elif key == curses.KEY_F2 and items:
                chosen = items[sel]
                if chosen["tag"] == "DSL":
                    self._show_popup(stdscr, "DSL reports cannot be edited")
                else:
                    result = report_editor(stdscr, self.db, chosen["def"])
                    if result:
                        items = _build_items()
                continue

            # Del — delete selected report
            elif key == curses.KEY_DC and items:
                chosen = items[sel]
                if chosen["tag"] == "DSL":
                    self._show_popup(stdscr, "DSL reports cannot be deleted")
                else:
                    # Confirm delete with Y/N popup
                    msg = f"Delete '{chosen['title']}'? (Y/N)"
                    my, mx = stdscr.getmaxyx()
                    bw = min(len(msg) + 6, mx - 4)
                    bh = 5
                    py = max(0, (my - bh) // 2)
                    px = max(0, (mx - bw) // 2)
                    try:
                        cwin = curses.newwin(bh, bw, py, px)
                        cwin.keypad(True)
                        cwin.border()
                        cwin.addstr(2, 2, msg[:bw - 4],
                                    curses.A_BOLD | curses.A_REVERSE)
                        cwin.refresh()
                        ck = cwin.getch()
                        if ck in (ord('y'), ord('Y')):
                            delete_report(self.db, chosen["name"])
                            items = _build_items()
                            if sel >= len(items):
                                sel = max(0, len(items) - 1)
                    except curses.error:
                        pass
                continue

            elif key == 27:
                break

            elif key == curses.KEY_RESIZE:
                continue

    def _run_report(self, stdscr, report_name: str):
        """Execute a report: PARAMS → load → filter → view loop."""
        from dsl_lib.report_engine import (
            load_report_data, apply_where, apply_runtime_filters, apply_formulas,
            apply_order, build_listing, build_summary, build_crosstab
        )
        from tui.cxreport import ReportGrid, SummaryGrid

        report_def = self.app.get("reports", {}).get(report_name)
        if not report_def:
            from dsl_lib.report_designer import load_saved_reports
            report_def = load_saved_reports(self.db).get(report_name)
        if not report_def:
            self._show_popup(stdscr, f"Report '{report_name}' not found")
            return

        while True:
            # 1. PARAMS dialog
            params = {}
            if report_def.get("params"):
                params = self._show_params_dialog(
                    stdscr, report_def["params"], report_def["title"])
                if params is None:
                    return  # user cancelled

            # 2. Load data (with optional limit and mode)
            dataset_mode = report_def.get("dataset_mode", "docs")
            rows = load_report_data(
                self.db, report_def["source"], mode=dataset_mode)
            limit = report_def.get("limit")
            if limit and isinstance(limit, int) and limit > 0:
                rows = rows[:limit]
            if not rows:
                self._show_popup(stdscr, "No data in table: " + report_def["source"])
                if not report_def.get("params"):
                    return
                continue

            # 3. Apply WHERE
            if report_def.get("where"):
                rows = apply_where(rows, report_def["where"], params)
                if not rows:
                    self._show_popup(stdscr, "No rows match filter criteria")
                    if not report_def.get("params"):
                        return
                    continue

            # 4. Apply formulas
            rows = apply_formulas(rows, report_def["columns"])

            # 5. Apply ORDER (explicit sort, then ensure group fields are sorted)
            if report_def.get("order"):
                rows = apply_order(rows, report_def["order"])

            # Auto-sort by group fields to ensure contiguous groups
            if report_def.get("groups"):
                gfields = report_def["groups"][0].get("fields", [])
                if gfields:
                    rows = sorted(rows,
                                  key=lambda r: tuple(
                                      str(r.get(g, "")) for g in gfields))

            # 6. Build sum_fields set
            sum_fields = {c["id"] for c in report_def["columns"] if c.get("sum")}

            # Collect all field names for setup dialogs
            all_col_ids = [c["id"] for c in report_def["columns"]]
            numeric_col_ids = [c["id"] for c in report_def["columns"]
                               if c.get("type") == "FLOAT" or c.get("sum")]
            # Auto-detect numeric fields from data if none flagged
            if not numeric_col_ids and rows:
                sample = rows[0]
                for cid in all_col_ids:
                    val = sample.get(cid)
                    if isinstance(val, (int, float)):
                        numeric_col_ids.append(cid)
                    elif isinstance(val, str):
                        try:
                            float(val.replace(",", ""))
                            numeric_col_ids.append(cid)
                        except (ValueError, TypeError):
                            pass

            # Persistent state for interactive setup
            ct_state = dict(report_def.get("crosstab") or {})
            summary_group = ""
            if report_def.get("groups"):
                summary_group = report_def["groups"][0]["fields"][0]
            runtime_filters = {}

            # 7. View loop
            view = "listing"
            while True:
                active_rows = apply_runtime_filters(
                    rows, runtime_filters, report_def["columns"])
                if view == "listing":
                    grid = ReportGrid(
                        stdscr, report_def["columns"], rows,
                        groups=report_def["groups"],
                        totals=report_def.get("totals"),
                        sum_fields=sum_fields,
                        title=report_def["title"],
                        subtitle=f"{len(active_rows)} rows",
                        has_crosstab=True,
                        filter_state=runtime_filters,
                        page_elements=report_def.get("page_elements"))
                    view = grid.run()
                    runtime_filters = grid.get_filter_state()
                    active_rows = grid.get_filtered_rows()

                elif view == "summary":
                    # Use pre-defined group or show setup dialog
                    group_key = summary_group
                    if not group_key:
                        result = self._summary_setup(
                            stdscr, all_col_ids, numeric_col_ids)
                        if result is None:
                            view = "listing"
                            continue
                        group_key, extra_sums = result
                        summary_group = group_key
                        if extra_sums:
                            sum_fields = sum_fields | extra_sums

                    if not sum_fields:
                        self._show_popup(stdscr, "No numeric columns for summary")
                        view = "listing"
                        continue

                    col_names, summary_rows = build_summary(
                        active_rows, group_key, sorted(sum_fields))
                    sgrid = SummaryGrid(
                        stdscr, col_names, summary_rows,
                        title=report_def["title"],
                        subtitle=f"[Summary by {group_key}]",
                        numeric_cols={"_count"} | sum_fields)
                    view = sgrid.run()

                elif view == "crosstab":
                    ct = ct_state if ct_state.get("row") else None
                    if not ct:
                        # Show interactive crosstab setup dialog
                        result = self._crosstab_setup(
                            stdscr, all_col_ids, numeric_col_ids)
                        if result is None:
                            view = "listing"
                            continue
                        ct = result
                        ct_state = ct

                    col_names, ct_rows = build_crosstab(
                        active_rows, ct["row"], ct["col"], ct["value"],
                        ct.get("agg", "sum"),
                        ct.get("row_total", True),
                        ct.get("col_total", True))
                    if not ct_rows:
                        self._show_popup(stdscr, "No crosstab data")
                        view = "listing"
                        continue
                    num_cols = set(col_names) - {"_row"}
                    sgrid = SummaryGrid(
                        stdscr, col_names, ct_rows,
                        title=report_def["title"],
                        subtitle=f"[Crosstab: {ct['row']} x {ct['col']}]",
                        numeric_cols=num_cols)
                    view = sgrid.run()

                elif view == "rerun":
                    break  # go back to PARAMS

                elif view == "exit":
                    return

    def _summary_setup(self, stdscr, all_fields, numeric_fields):
        """Interactive summary setup: pick group-by field and sum fields.

        Returns (group_key, sum_fields_set) or None if cancelled.
        """
        if not all_fields:
            return None
        fields = [
            {"label": "Group By", "value": "", "options": all_fields},
            {"label": "Sum Fields", "value": ", ".join(numeric_fields),
             "options": None},
        ]
        sel = 0
        cursors = [0, len(fields[1]["value"])]

        max_y, max_x = stdscr.getmaxyx()
        box_h = 10
        box_w = min(60, max_x - 4)
        by = max(0, (max_y - box_h) // 2)
        bx = max(0, (max_x - box_w) // 2)

        while True:
            try:
                win = curses.newwin(box_h, box_w, by, bx)
            except curses.error:
                return None
            win.keypad(True)
            win.erase()
            win.border()
            try:
                win.addstr(0, 2, " Summary Setup ", curses.A_BOLD)
            except curses.error:
                pass

            for i, f in enumerate(fields):
                y = 2 + i * 2
                is_cur = (i == sel)
                # Focus indicator
                try:
                    win.addstr(y, 1, "\u25ba" if is_cur else " ",
                               curses.A_BOLD if is_cur else curses.A_NORMAL)
                except curses.error:
                    pass
                lattr = curses.A_BOLD if is_cur else curses.A_NORMAL
                try:
                    win.addstr(y, 3, f"{f['label']:>12s}:", lattr)
                except curses.error:
                    pass
                vx = 17
                vw = box_w - vx - 2
                val = f["value"]
                if f["options"] and is_cur:
                    disp = (val or "(pick)") + "  [Space]"
                    try:
                        win.addstr(y, vx, disp[:vw],
                                   curses.A_REVERSE)
                    except curses.error:
                        pass
                elif is_cur and not f["options"]:
                    display = val[:vw]
                    cx = min(cursors[i], len(display))
                    try:
                        before = display[:cx]
                        after = display[cx:]
                        if before:
                            win.addstr(y, vx, before)
                        ch = after[0] if after else ' '
                        win.addstr(y, vx + cx, ch, curses.A_REVERSE)
                        if len(after) > 1:
                            win.addstr(y, vx + cx + 1, after[1:])
                    except curses.error:
                        pass
                else:
                    try:
                        win.addstr(y, vx, (val or "(empty)")[:vw],
                                   curses.A_DIM if not val else curses.A_NORMAL)
                    except curses.error:
                        pass

            try:
                win.addstr(box_h - 1, 2, " F10:Run  ESC:Cancel ",
                           curses.A_DIM)
            except curses.error:
                pass

            win.refresh()
            key = win.getch()

            # Enter/Down/Tab = next field
            if key in (curses.KEY_DOWN, 9, 10, 13, curses.KEY_ENTER):
                sel = (sel + 1) % len(fields)
            elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
                sel = (sel - 1) % len(fields)
            # Space = dropdown for pick fields
            elif key == ord(' ') and fields[sel]["options"]:
                f = fields[sel]
                from tui.widgets import OptionDropdown
                fy = by + 2 + sel * 2
                fx = bx + 17
                fw = box_w - 20
                picked = OptionDropdown.show(
                    stdscr, fy, fx, fw, f["value"], f["options"])
                if picked is not None:
                    f["value"] = picked
            elif key == curses.KEY_F10:
                group_key = fields[0]["value"].strip()
                if not group_key:
                    continue
                sum_str = fields[1]["value"].strip()
                sum_set = set()
                if sum_str:
                    sum_set = {s.strip() for s in sum_str.split(",")
                               if s.strip()}
                return group_key, sum_set
            elif key == 27:
                return None
            else:
                f = fields[sel]
                if f["options"]:
                    continue
                ci = cursors[sel]
                if key in (curses.KEY_BACKSPACE, 8, 127):
                    if ci > 0:
                        f["value"] = f["value"][:ci-1] + f["value"][ci:]
                        cursors[sel] -= 1
                elif key == curses.KEY_LEFT:
                    cursors[sel] = max(0, ci - 1)
                elif key == curses.KEY_RIGHT:
                    cursors[sel] = min(len(f["value"]), ci + 1)
                elif 32 <= key <= 126:
                    f["value"] = f["value"][:ci] + chr(key) + f["value"][ci:]
                    cursors[sel] += 1

    def _crosstab_setup(self, stdscr, all_fields, numeric_fields):
        """Interactive crosstab setup: pick row, col, value, agg fields.

        Returns crosstab config dict or None if cancelled.
        """
        if not all_fields or not numeric_fields:
            self._show_popup(stdscr, "Need numeric columns for crosstab")
            return None

        agg_options = ["sum", "count", "avg", "min", "max"]
        fields = [
            {"label": "Row Field", "value": "", "options": all_fields},
            {"label": "Col Field", "value": "", "options": all_fields},
            {"label": "Value", "value": numeric_fields[0] if numeric_fields else "",
             "options": numeric_fields},
            {"label": "Aggregate", "value": "sum", "options": agg_options},
        ]
        sel = 0
        status = ""

        max_y, max_x = stdscr.getmaxyx()
        box_h = 14
        box_w = min(60, max_x - 4)
        by = max(0, (max_y - box_h) // 2)
        bx = max(0, (max_x - box_w) // 2)

        while True:
            try:
                win = curses.newwin(box_h, box_w, by, bx)
            except curses.error:
                return None
            win.keypad(True)
            win.erase()
            win.border()
            try:
                win.addstr(0, 2, " Crosstab Setup ", curses.A_BOLD)
            except curses.error:
                pass

            for i, f in enumerate(fields):
                y = 2 + i * 2
                is_cur = (i == sel)
                # Focus indicator
                try:
                    win.addstr(y, 1, "\u25ba" if is_cur else " ",
                               curses.A_BOLD if is_cur else curses.A_NORMAL)
                except curses.error:
                    pass
                lattr = curses.A_BOLD if is_cur else curses.A_NORMAL
                try:
                    win.addstr(y, 3, f"{f['label']:>12s}:", lattr)
                except curses.error:
                    pass
                vx = 17
                vw = box_w - vx - 2
                val = f["value"]
                if is_cur:
                    disp = (val or "(pick)") + "  [Space]"
                    try:
                        win.addstr(y, vx, disp[:vw], curses.A_REVERSE)
                    except curses.error:
                        pass
                else:
                    attr = curses.A_DIM if not val else curses.A_NORMAL
                    try:
                        win.addstr(y, vx, (val or "(pick)")[:vw], attr)
                    except curses.error:
                        pass

            if status:
                try:
                    win.addstr(box_h - 3, 2, status[:box_w - 4],
                               curses.A_BOLD)
                except curses.error:
                    pass

            try:
                win.addstr(box_h - 1, 2,
                           " F10:Run Crosstab  ESC:Cancel ",
                           curses.A_DIM)
            except curses.error:
                pass

            win.refresh()
            key = win.getch()

            # Enter/Down/Tab = next field
            if key in (curses.KEY_DOWN, 9, 10, 13, curses.KEY_ENTER):
                sel = (sel + 1) % len(fields)
                status = ""
            elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
                sel = (sel - 1) % len(fields)
                status = ""
            # Space = dropdown
            elif key == ord(' '):
                f = fields[sel]
                if f["options"]:
                    from tui.widgets import OptionDropdown
                    fy = by + 2 + sel * 2
                    fx = bx + 17
                    fw = box_w - 20
                    picked = OptionDropdown.show(
                        stdscr, fy, fx, fw, f["value"], f["options"])
                    if picked is not None:
                        f["value"] = picked
            elif key == curses.KEY_F10:
                row_f = fields[0]["value"].strip()
                col_f = fields[1]["value"].strip()
                val_f = fields[2]["value"].strip()
                agg_f = fields[3]["value"].strip() or "sum"
                if not row_f or not col_f or not val_f:
                    status = "All fields required"
                    continue
                if row_f == col_f:
                    status = "Row and Col must differ"
                    continue
                return {
                    "row": row_f, "col": col_f,
                    "value": val_f, "agg": agg_f,
                    "row_total": True, "col_total": True,
                }
            elif key == 27:
                return None

    def _show_params_dialog(self, stdscr, param_fields: list,
                            title: str) -> dict:
        """Show parameter input form. Returns dict or None if cancelled."""
        max_y, max_x = stdscr.getmaxyx()
        # Calculate dialog size
        field_count = len(param_fields)
        box_h = min(field_count * 2 + 6, max_y - 4)
        box_w = min(60, max_x - 4)
        y = max(0, (max_y - box_h) // 2)
        x = max(0, (max_x - box_w) // 2)

        values = {}
        field_idx = 0

        while True:
            try:
                win = curses.newwin(box_h, box_w, y, x)
            except curses.error:
                return None
            win.keypad(True)
            win.border()
            win.addstr(0, 2, f" {title} - Parameters ", curses.A_BOLD)
            win.addstr(box_h - 1, 2, " F10=Run  ESC=Cancel ",
                       curses.A_DIM)

            # Draw fields
            for i, pf in enumerate(param_fields):
                fy = 2 + i * 2
                if fy >= box_h - 2:
                    break
                label = pf.get("label", pf["id"])
                is_current = (i == field_idx)
                # Focus indicator
                try:
                    win.addstr(fy, 1, "\u25ba" if is_current else " ",
                               curses.A_BOLD if is_current else curses.A_NORMAL)
                except curses.error:
                    pass
                # Label
                attr = curses.A_BOLD if is_current else curses.A_NORMAL
                try:
                    win.addstr(fy, 3, f"{label}:", attr)
                except curses.error:
                    pass
                # Value field
                val = str(values.get(pf["id"], ""))
                fw = box_w - 6 - len(label) - 2
                vx = 4 + len(label) + 1
                val_attr = curses.A_REVERSE if is_current else curses.A_UNDERLINE
                try:
                    win.addstr(fy, vx, val[:fw].ljust(fw), val_attr)
                except curses.error:
                    pass

            win.refresh()
            key = stdscr.getch()

            if key == 27:  # ESC
                stdscr.touchwin()
                stdscr.refresh()
                return None
            elif key == curses.KEY_F10:
                stdscr.touchwin()
                stdscr.refresh()
                return values
            elif key in (10, 13, curses.KEY_ENTER):
                # Enter navigates to next field
                if field_idx < field_count - 1:
                    field_idx += 1
                else:
                    # On last field, run
                    stdscr.touchwin()
                    stdscr.refresh()
                    return values
            elif key == curses.KEY_UP and field_idx > 0:
                field_idx -= 1
            elif key == curses.KEY_DOWN or key == 9:  # Tab
                if field_idx < field_count - 1:
                    field_idx += 1
            elif key == curses.KEY_BTAB:
                if field_idx > 0:
                    field_idx -= 1
            elif key == curses.KEY_BACKSPACE or key == 8 or key == 127:
                fid = param_fields[field_idx]["id"]
                val = values.get(fid, "")
                if val:
                    values[fid] = val[:-1]
            elif 32 <= key <= 126:
                fid = param_fields[field_idx]["id"]
                val = values.get(fid, "")
                values[fid] = val + chr(key)


    def _reset_all_data(self, stdscr):
        """Interactive table selection wipe — checkbox picker like PyStock."""
        if not self.db:
            return

        # ── Build wipe items from DSL RESET block or fallback ──
        reset_def = self.app.get("reset")
        items = []  # [(label, {"type": "clear"|"update", ...}), ...]
        if reset_def:
            for table in reset_def.get("clears", []):
                label = table.replace("_", " ").title()
                items.append((label, {"type": "clear", "table": table}))
            for upd in reset_def.get("updates", []):
                table = upd["table"]
                fields = ", ".join(f"{k}={v}" for k, v in upd["sets"].items())
                label = f"Zero {table.replace('_', ' ').title()} ({fields})"
                items.append((label, {"type": "update", "table": table,
                                      "sets": upd["sets"]}))
        else:
            # Fallback: discover tables from forms/grids
            tables = set()
            for form_id in self.app.get("forms", {}):
                tables.add(form_id.replace("_form", ""))
            for grid_id in self.app.get("grids", {}):
                tables.add(grid_id.replace("_grid", ""))
            for table in sorted(tables):
                label = table.replace("_", " ").title()
                items.append((label, {"type": "clear", "table": table}))

        if not items:
            self._show_popup(stdscr, "No tables to reset")
            return

        # ── Checkbox picker ──
        checked = [True] * len(items)
        cursor = 0
        max_y, max_x = stdscr.getmaxyx()
        title = "Wipe Test Data \u2014 Select Tables"

        while True:
            stdscr.erase()
            # Title bar
            try:
                from tui.themes import CLR_HEADER
                stdscr.addstr(0, 0, " " * max_x, curses.color_pair(CLR_HEADER))
                title_trunc = title[:max_x - 2]
                stdscr.addstr(0, max_x - len(title_trunc) - 2, title_trunc,
                              curses.color_pair(CLR_HEADER) | curses.A_BOLD)
            except (curses.error, ImportError):
                pass

            # Two-column layout
            col_w = max_x // 2
            mid = (len(items) + 1) // 2
            start_y = 2

            for i, (label, _op) in enumerate(items):
                if i < mid:
                    row = start_y + i
                    cx = 4
                else:
                    row = start_y + (i - mid)
                    cx = col_w + 4

                if row >= max_y - 3:
                    continue

                mark = "[x]" if checked[i] else "[ ]"
                text = f"{mark} {label}"
                text = text[:col_w - 5]

                attr = curses.A_NORMAL
                if i == cursor:
                    attr = curses.A_REVERSE | curses.A_BOLD
                try:
                    stdscr.addstr(row, cx, text, attr)
                except curses.error:
                    pass

            # Selection counter
            sel_count = sum(checked)
            counter = f"{sel_count}/{len(items)} selected"
            try:
                stdscr.addstr(max_y - 2, 2, counter,
                              curses.color_pair(3) | curses.A_BOLD)
            except curses.error:
                pass

            # Hotkey bar
            bar = "Space Toggle    F2 All    F3 None    F10 Proceed    ESC Cancel"
            try:
                stdscr.addstr(max_y - 1, 2, bar[:max_x - 4])
            except curses.error:
                pass

            stdscr.refresh()
            stdscr.nodelay(False)
            key = stdscr.getch()

            if key == 27:  # ESC
                stdscr.touchwin()
                stdscr.refresh()
                return
            elif key == curses.KEY_UP:
                cursor = (cursor - 1) % len(items)
            elif key == curses.KEY_DOWN:
                cursor = (cursor + 1) % len(items)
            elif key == curses.KEY_LEFT:
                if cursor >= mid:
                    cursor = min(cursor - mid, mid - 1)
            elif key == curses.KEY_RIGHT:
                if cursor < mid and cursor + mid < len(items):
                    cursor = cursor + mid
            elif key in (ord(' '), ord('\n'), curses.KEY_ENTER, 10, 13):
                checked[cursor] = not checked[cursor]
            elif key == curses.KEY_F2:
                checked = [True] * len(items)
            elif key == curses.KEY_F3:
                checked = [False] * len(items)
            elif key == curses.KEY_F10:
                if not any(checked):
                    continue
                # Double confirmation
                sel_count = sum(checked)
                msg1 = f"Wipe {sel_count} table(s)? Press Y to confirm"
                box_w = min(len(msg1) + 6, max_x - 4)
                box_h = 5
                by = max(0, (max_y - box_h) // 2)
                bx = max(0, (max_x - box_w) // 2)
                try:
                    win = curses.newwin(box_h, box_w, by, bx)
                    win.attron(curses.A_BOLD)
                    win.border()
                    win.addstr(2, 2, msg1[:box_w - 4], curses.A_REVERSE)
                    win.refresh()
                    ckey = stdscr.getch()
                except curses.error:
                    continue
                if ckey not in (ord('y'), ord('Y')):
                    continue

                # Second confirmation
                msg2 = "THIS CANNOT BE UNDONE! Press Y again"
                try:
                    win.erase()
                    win.border()
                    win.addstr(2, 2, msg2[:box_w - 4],
                               curses.A_BOLD | curses.A_REVERSE)
                    win.refresh()
                    ckey2 = stdscr.getch()
                except curses.error:
                    continue
                if ckey2 not in (ord('y'), ord('Y')):
                    continue

                # ── Execute wipe with progress ──
                self._execute_wipe(stdscr, items, checked)
                return

    def _execute_wipe(self, stdscr, items, checked):
        """Run selected wipe operations with progress display."""
        max_y, max_x = stdscr.getmaxyx()
        box_w = min(60, max_x - 4)
        selected = [(label, op) for (label, op), chk
                     in zip(items, checked) if chk]
        box_h = min(len(selected) + 6, max_y - 2)
        by = max(0, (max_y - box_h) // 2)
        bx = max(0, (max_x - box_w) // 2)
        try:
            win = curses.newwin(box_h, box_w, by, bx)
            win.border()
            win.addstr(1, 2, "Wiping data...", curses.A_BOLD)
            win.refresh()
        except curses.error:
            return

        row = 3
        for label, op in selected:
            status = "OK"
            try:
                if op["type"] == "clear":
                    self.db.execute(f'DELETE FROM "{op["table"]}"')
                elif op["type"] == "update":
                    table = op["table"]
                    sets = op["sets"]
                    rows = self.db.query(
                        f'SELECT rowid, data FROM "{table}"')
                    for r in rows:
                        doc = json.loads(r.get("data", "{}"))
                        for fld, val in sets.items():
                            try:
                                doc[fld] = (float(val) if '.' in str(val)
                                            else int(val))
                            except ValueError:
                                doc[fld] = val
                        self.db.execute(
                            f'UPDATE "{table}" SET data=? WHERE rowid=?',
                            (json.dumps(doc), r["rowid"]))
            except Exception as e:
                status = "FAIL"
                self._log(e, context=f"wipe {op['type']} {op.get('table')}")

            if row < box_h - 2:
                line = f"{status:4s} {label}"[:box_w - 4]
                attr = curses.A_NORMAL
                if status == "FAIL":
                    attr = curses.A_BOLD
                try:
                    win.addstr(row, 2, line, attr)
                    win.refresh()
                except curses.error:
                    pass
                row += 1

        try:
            win.addstr(min(row + 1, box_h - 2), 2, "Done. Press any key.",
                       curses.A_BOLD)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
        except curses.error:
            pass
        stdscr.touchwin()
        stdscr.refresh()
        # Reload lookup data
        self.lookup_data = self._load_lookups()

    def _handle_workflow(self, stdscr, form_id: str, values: dict,
                         tab_data: dict, workflow: dict,
                         layout_def: dict = None):
        """Execute a workflow transition: show menu, run handler, update status."""
        # Guard: this handler reads self.edit_context["rowid"] below, but
        # _main_loop sets self.edit_context = None in several navigation
        # branches. A workflow/post fired without an active edit context
        # would TypeError on that subscript. Bail cleanly instead.
        if self.edit_context is None:
            self._show_popup(stdscr, "No active record to post (edit context is empty).")
            return
        status_field = workflow["status_field"]
        current_status = values.get(status_field, "")

        # Find valid transitions from current status
        valid = [t for t in workflow["transitions"]
                 if t["from"].lower() == current_status.lower()]
        wf_actions = workflow.get("actions", [])
        if not valid and not wf_actions:
            self._show_popup(stdscr, f"No action for status '{current_status}'")
            return

        # Pick transition — always show popup so user can confirm or ESC
        form_name = form_id.replace("_form", "")
        form_title = (layout_def or {}).get("title", form_name.upper())
        chosen = self._show_workflow_menu(stdscr, valid, form_title,
                                          actions=wf_actions)
        if not chosen:
            return

        # Check if this is an action (no status change) vs transition
        is_action_only = chosen.get("_action", False)
        action_name = chosen.get("action", "")
        script_name = chosen.get("script")
        scripts = dict((layout_def or {}).get("scripts", {}))
        # Merge global subroutines so CALL can find them
        for sname, sdef in self.app.get("subroutines", {}).items():
            if sname not in scripts:
                scripts[sname] = sdef

        script_ok_msg = ""
        if script_name and script_name in scripts:
            # New script engine path
            try:
                detail_rows = []
                for grid_id, grid_rows in tab_data.items():
                    for row in grid_rows:
                        has_data = any(
                            v for k, v in row.items()
                            if k not in ("rowid", "_parent_rowid")
                            and v not in ("", 0, 0.0, None))
                        if has_data:
                            detail_rows.append(row)

                # Prompt for compute qty if script uses _compute_qty
                script_text = str(scripts.get(script_name, []))
                compute_qty = None
                if "_compute_qty" in script_text:
                    base = float(values.get("base_qty", 1) or 1)
                    compute_qty = self._prompt_number(
                        stdscr, "Compute Qty", default=base)
                    if compute_qty is None:
                        return  # user cancelled

                ctx = {
                    "id": self.edit_context["rowid"],
                    "table": form_name,
                    "fields": values,
                    "lines": detail_rows,
                    "vars": {},
                    "scripts": scripts,
                }
                if compute_qty is not None:
                    ctx["vars"]["_compute_qty"] = compute_qty
                executor = ScriptExecutor(self.db, scripts,
                                         settings_fn=self.get_setting)
                script_ok_msg = executor.run(scripts[script_name], ctx)

                # Show BOM Explosion results grid
                result_data = ctx.get("vars", {}).get("result")
                if isinstance(result_data, list) and result_data and isinstance(result_data[0], dict):
                    explode_cols = [
                        ("part_no", "Part No", 12),
                        ("vendor_id", "Vendor Id", 10),
                        ("part_name", "Part Name", 35),
                        ("uom", "Uom", 6),
                        ("part_level", "Part Level", 10),
                        ("total_usage", "Usage", 12),
                        ("path", "Path", 40),
                    ]
                    self._show_results_grid(stdscr, "BOM Explosion",
                                            result_data, explode_cols)

                # Show Roll-up Summary grid
                rollup_data = ctx.get("vars", {}).get("rollup")
                if isinstance(rollup_data, list) and rollup_data and isinstance(rollup_data[0], dict):
                    rollup_cols = [
                        ("part_no", "Part No", 12),
                        ("part_name", "Part Name", 40),
                        ("uom", "Uom", 6),
                        ("part_level", "Part Level", 10),
                        ("total_usage", "Total Usage", 12),
                    ]
                    self._show_results_grid(stdscr, "Roll-up Summary",
                                            rollup_data, rollup_cols)

                # Show Put-Away allocation results
                pa_data = ctx.get("vars", {}).get("pa_lines")
                if isinstance(pa_data, list) and pa_data and isinstance(pa_data[0], dict):
                    pa_cols = [
                        ("part_no", "Part No", 14),
                        ("part_name", "Part Name", 24),
                        ("qty", "Qty", 8),
                        ("total_cbm", "CBM", 10),
                        ("loc_id", "Location", 12),
                        ("level", "Level", 6),
                        ("lot_no", "Lot No", 12),
                    ]
                    self._show_results_grid(stdscr, "Put-Away Allocation",
                                            pa_data, pa_cols)

                # Show Picking allocation results
                pick_data = ctx.get("vars", {}).get("pick_lines")
                if isinstance(pick_data, list) and pick_data and isinstance(pick_data[0], dict):
                    pk_cols = [
                        ("part_no", "Part No", 14),
                        ("part_name", "Part Name", 24),
                        ("pick_qty", "Pick Qty", 8),
                        ("lot_no", "Lot No", 14),
                        ("grn_no", "GRN No", 14),
                        ("loc_id", "Location", 12),
                    ]
                    self._show_results_grid(stdscr, "Picking Allocation",
                                            pick_data, pk_cols)
            except ScriptError as e:
                self._show_popup(stdscr, str(e))
                return
            except Exception as e:
                self._show_popup(stdscr, f"Error: {e}")
                return
        else:
            # Old posting rules engine (backward compat)
            rules = chosen.get("rules", [])
            if rules:
                # Wrap the posting in a transaction so a mid-loop failure
                # (e.g. one detail row's UPDATE/INSERT fails) rolls back all
                # prior rows in the same posting, instead of leaving partial
                # state committed.
                has_txn = hasattr(self.db, "begin")
                try:
                    if has_txn: self.db.begin()
                    self._execute_posting_rules(rules, values, tab_data)
                    if has_txn: self.db.commit()
                except Exception as e:
                    if has_txn:
                        try: self.db.rollback()
                        except Exception: pass
                    self._show_popup(stdscr, f"Error: {e}")
                    return
            else:
                handler_name = f"_{action_name}_{form_name}"
                handler = getattr(self, handler_name, None)
                if handler:
                    try:
                        handler(values, tab_data)
                    except Exception as e:
                        self._show_popup(stdscr, f"Error: {e}")
                        return

        # Update status (skip for action-only items)
        if not is_action_only:
            values[status_field] = chosen["to"]
            rowid = self.edit_context["rowid"]
            self._update_form_data(form_id, values, rowid)
            self.edit_context["data"] = values
            self._lookups_dirty = True
            msg = script_ok_msg or f"{action_name.title()} OK"
            self._show_popup(stdscr, f"{msg} - Status: {chosen['to']}")
        else:
            self._lookups_dirty = True
            if script_ok_msg:
                self._show_popup(stdscr, script_ok_msg)
            else:
                label = chosen.get("label", script_name or "Action")
                self._show_popup(stdscr, f"{label} completed")

    # ── Generic DSL posting engine ───────────────────────────────

    def _execute_on_save(self, rules: list, form_name: str,
                         parent_rid: int, values: dict, is_new: bool):
        """Execute ON SAVE rules after saving a form record.

        For new records: execute directly.
        For updates: only re-run if child table has exactly 1 INIT row
        (delete it first, then re-create).
        """
        if not is_new:
            # Check child tables — find WRITE targets in rules
            write_tables = []
            for step in rules:
                if step.get("type") == "write":
                    write_tables.append(step["table"])
                elif step.get("type") == "if":
                    for s in step.get("steps", []):
                        if s.get("type") == "write":
                            write_tables.append(s["table"])
            for tbl in write_tables:
                try:
                    rows = self.db.query(f'SELECT rowid, data FROM "{tbl}"')
                    child_rows = []
                    for r in rows:
                        doc = json.loads(r.get("data", "{}"))
                        if doc.get("_parent_rowid") == parent_rid:
                            child_rows.append((r["rowid"], doc))
                    # Only re-run if 0 or 1 INIT row (no real transactions)
                    if len(child_rows) > 1:
                        return
                    if len(child_rows) == 1:
                        _, cdoc = child_rows[0]
                        if cdoc.get("trans_type") != "INIT":
                            return
                        # Delete the single INIT row
                        self.db.execute(
                            f'DELETE FROM "{tbl}" WHERE rowid=?',
                            (child_rows[0][0],))
                except Exception as e:
                    self._log(e, context=f"on_save child-table check {tbl}")

        context = {
            "header": values,
            "line": {},
            "found": {form_name: (parent_rid, values)}
        }
        self._exec_steps(rules, context)

    def _execute_posting_rules(self, rules: list, header_values: dict,
                               tab_data: dict):
        """Execute DSL-defined posting rules."""
        for rule in rules:
            if rule["type"] == "for_each":
                self._exec_for_each(rule, header_values, tab_data)

    def _exec_for_each(self, rule: dict, header_values: dict,
                       tab_data: dict):
        """Iterate detail grid rows and execute steps for each."""
        detail_name = rule["detail"]
        rows = None
        for grid_id, grid_rows in tab_data.items():
            if detail_name in grid_id:
                rows = grid_rows
                break
        if not rows:
            raise ValueError(f"No detail lines for '{detail_name}'")
        for row in rows:
            # Skip empty rows
            has_data = any(v for k, v in row.items()
                          if k not in ("rowid", "_parent_rowid")
                          and v not in ("", 0, 0.0, None))
            if not has_data:
                continue
            context = {
                "header": header_values,
                "line": row,
                "found": {}
            }
            self._exec_steps(rule["steps"], context)

    def _exec_steps(self, steps: list, context: dict):
        """Execute a sequence of posting steps."""
        for step in steps:
            stype = step["type"]
            if stype == "find":
                self._exec_find(step, context)
            elif stype == "update":
                self._exec_update(step, context)
            elif stype == "if":
                self._exec_if(step, context)
            elif stype == "write":
                self._exec_write(step, context)

    def _exec_find(self, step: dict, context: dict):
        """FIND <table> BY <field> [OF <parent> USING <link>]."""
        table = step["table"]
        by_field = step["by"]
        match_value = context["line"].get(by_field, "")
        if not match_value:
            return

        parent_table = step.get("parent_table")
        if parent_table:
            # Detail-row lookup: FIND po_line BY part_no OF po USING po_number
            # 1. Resolve parent record via link_field from header
            link_field = step["link_field"]
            link_value = context["header"].get(link_field, "")
            if not link_value:
                return
            # 2. Find parent rowid
            parent_rowid = None
            try:
                prows = self.db.query(f'SELECT rowid, data FROM "{parent_table}"')
                for pr in prows:
                    pdoc = json.loads(pr.get("data", "{}"))
                    if pdoc.get(link_field) == link_value:
                        parent_rowid = pr["rowid"]
                        break
            except Exception:
                return
            if parent_rowid is None:
                return
            # 3. Find detail row matching by_field AND _parent_rowid
            try:
                rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
                for row in rows:
                    doc = json.loads(row.get("data", "{}"))
                    if (doc.get("_parent_rowid") == parent_rowid
                            and doc.get(by_field) == match_value):
                        context["found"][table] = (row["rowid"], doc)
                        return
            except Exception as e:
                self._log(e, context=f"legacy FIND {table} (detail)")
        else:
            # Simple top-level lookup: FIND item BY part_no
            try:
                rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
                for row in rows:
                    doc = json.loads(row.get("data", "{}"))
                    if doc.get(by_field) == match_value:
                        context["found"][table] = (row["rowid"], doc)
                        return
            except Exception as e:
                self._log(e, context=f"legacy FIND {table}")

    def _resolve_expr(self, expr: str, context: dict):
        """Resolve an expression: scope.field, string literal, or numeric."""
        expr = expr.strip()
        # String literal: "RECV"
        if expr.startswith('"') and expr.endswith('"'):
            return expr[1:-1]
        # Built-in constants
        if expr == "TODAY":
            return date.today().isoformat()
        # Numeric literal
        try:
            return float(expr)
        except ValueError:
            pass
        # Scope.field reference
        if "." in expr:
            scope, field = expr.split(".", 1)
            if scope == "header":
                val = context["header"].get(field, "")
                # Convert date objects to string
                if isinstance(val, date):
                    return val.isoformat()
                return val
            elif scope == "line":
                return context["line"].get(field, "")
            elif scope in context["found"]:
                _, doc = context["found"][scope]
                return doc.get(field, "")
        return ""

    def _exec_update(self, step: dict, context: dict):
        """UPDATE <table>.<field> <op> <expr> — arithmetic update."""
        target = step["target"]
        table, field = target.split(".", 1)
        op = step["op"]
        value = self._resolve_expr(step["expr"], context)
        try:
            value = float(value)
        except (ValueError, TypeError):
            value = 0.0

        if table not in context["found"]:
            return
        rowid, doc = context["found"][table]
        current = float(doc.get(field) or 0)
        if op == "+=":
            doc[field] = current + value
        elif op == "-=":
            doc[field] = current - value
        elif op == "=":
            doc[field] = value
        self.db.execute(f'UPDATE "{table}" SET data = ? WHERE rowid = ?',
                        (json.dumps(doc), rowid))
        # Update context so subsequent refs see new values
        context["found"][table] = (rowid, doc)

    def _exec_if(self, step: dict, context: dict):
        """IF <cond> — execute steps only if condition is truthy."""
        cond_val = self._resolve_expr(step["cond"], context)
        if cond_val and cond_val != "" and cond_val != 0 and cond_val != 0.0:
            self._exec_steps(step["steps"], context)

    def _exec_write(self, step: dict, context: dict):
        """WRITE <table> — insert a child record with field assignments."""
        table = step["table"]
        doc = {}
        for field_name, expr in step["fields"].items():
            val = self._resolve_expr(expr, context)
            # Convert date objects to string for JSON
            if isinstance(val, date):
                val = val.isoformat()
            doc[field_name] = val
        # Auto-set _parent_rowid from the most recent FIND target
        for found_table, (rowid, _) in context["found"].items():
            doc["_parent_rowid"] = rowid
            break  # use first found table as parent
        self.db.execute(f'INSERT INTO "{table}" (data) VALUES (?)',
                        (json.dumps(doc),))

    def run(self):
        import locale
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        try:
            if not self._console_startup_recovery():
                return
            curses.wrapper(self._main_loop)
        except Exception:
            import traceback
            with open("crash.log", "w") as f:
                traceback.print_exc(file=f)
            traceback.print_exc()
        finally:
            if self.db:
                self.db.disconnect()

    def _main_loop(self, stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        _init_theme_colors()  # centralized color init from saved theme
        # Surface startup DB status before entering the normal UI flow.
        if self.db_connect_error:
            self._show_multiline_popup(
                stdscr,
                "Startup Warning" if self._has_fatal_db_error() else "Database Warning",
                self._startup_db_message())
        if not self.app["layouts"]:
            return
        first_layout = "main_menu" if "main_menu" in self.app["layouts"] else next(iter(self.app["layouts"]))
        current_layout = first_layout
        history = []  # navigation stack for ESC/back
        while current_layout:
            layout_def = self.app["layouts"].get(current_layout)
            if not layout_def:
                break

            # Refresh lookup data only when flagged dirty (after save)
            if self._lookups_dirty:
                self.lookup_data = self._load_lookups()
                self._lookups_dirty = False

            # ENTRY mode: full-screen grid entry (new or edit existing)
            if layout_def.get("entry"):
                result = self._run_entry_mode(stdscr, layout_def)
                if result == "__back__":
                    if history:
                        current_layout = history.pop()
                    else:
                        break
                elif result == "__save_failed__":
                    # Header save failed — stay on this entry screen so the
                    # user can correct and retry. Do not navigate away.
                    self.edit_context = None
                else:
                    self.edit_context = None
                    if history:
                        current_layout = history.pop()
                    else:
                        current_layout = first_layout
                continue

            # Load grid data for this layout
            grid_data = {}
            for ref_id in layout_def.get("fields", {}):
                if ref_id in self.app["grids"]:
                    # Apply LISTVIEW filter if defined
                    gdef = self.app["grids"][ref_id]
                    views = gdef.get("list_views", [])
                    vf = None
                    if views:
                        vi = self._list_view_idx.get(ref_id, 0)
                        vf = views[vi]["filter"] if vi < len(views) else None
                    grid_data[ref_id] = self._load_grid_data(ref_id, view_filter=vf)
            # Also load data for tab grids (filtered by parent rowid)
            tabs = layout_def.get("tabs")
            if tabs:
                parent_rid = (self.edit_context or {}).get("rowid")
                for tab_item in tabs.get("items", []):
                    grid_id = tab_item.get("grid")
                    if grid_id and grid_id in self.app["grids"]:
                        grid_def = self.app["grids"][grid_id]
                        is_tree = grid_def.get("tree_detail", False)
                        if parent_rid is not None:
                            if is_tree:
                                grid_data[grid_id] = self._load_tree_detail_data(
                                    grid_id, parent_rowid=parent_rid)
                            else:
                                grid_data[grid_id] = self._load_grid_data(
                                    grid_id, parent_rowid=parent_rid)
                        else:
                            # New record — check for PREFILL source
                            prefill = tab_item.get("prefill")
                            if prefill:
                                grid_data[grid_id] = self._prefill_detail(
                                    grid_id, prefill)
                            else:
                                grid_data[grid_id] = []

            # Auto-generate running numbers for new records
            eff_edit_context = self.edit_context
            if not eff_edit_context:
                for ref_id in layout_def.get("fields", {}):
                    if ref_id in self.app["forms"]:
                        auto_nums = self._get_auto_numbers(ref_id)
                        if auto_nums:
                            eff_edit_context = {
                                "form_id": ref_id, "rowid": None,
                                "data": auto_nums
                            }
                        break

            # Inject company name and fallback warning into menu title
            if current_layout == first_layout:
                base_title = self.app["layouts"][current_layout].get(
                    "_base_title", layout_def.get("title", ""))
                if "_base_title" not in self.app["layouts"][current_layout]:
                    self.app["layouts"][current_layout]["_base_title"] = base_title
                parts = []
                if base_title:
                    parts.append(base_title)
                if self.company_name:
                    parts.append(f"[{self.company_name}]")
                if self.db_connect_error and not self.is_fallback:
                    parts.append("[DB OFFLINE - SWITCH COMPANY]")
                elif self.is_fallback:
                    parts.append("[LOCAL - SERVER OFFLINE]")
                layout_def["title"] = "  ".join(parts) if parts else ""

            renderer = LayoutRenderer(
                stdscr, self.app["forms"], self.app["grids"],
                layout_def, self.lookup_data, grid_data,
                edit_context=eff_edit_context,
                column_configs=self.column_configs,
                sort_configs=self.sort_configs,
                db=self.db,
                app_grids=self.app["grids"],
                default_resolver=self._resolve_default,
                user_context=self.current_user
            )
            # Pass LISTVIEW index to grids for label display
            for g in renderer.cxgrids:
                gid = getattr(g, '_grid_id', '')
                if gid:
                    g._view_idx = self._list_view_idx.get(gid, 0)
            action = renderer.run_menu()
            # Persist any column config changes made via F8
            self._save_column_configs()
            # Capture sort state for session persistence
            self.sort_configs.update(renderer.get_sort_configs())

            # ── Handle special actions from F-keys ──
            if action == "__save__":
                # F10 save: find which form this layout contains
                form_id = None
                for ref_id in layout_def.get("fields", {}):
                    if ref_id in self.app["forms"]:
                        form_id = ref_id
                        break
                parent_rid = None
                save_failed = False
                is_new = not (self.edit_context and self.edit_context.get("rowid") is not None)
                # Wrap entire save (header + details + ON SAVE) in one
                # transaction so network drops don't leave partial state.
                has_txn = hasattr(self.db, "begin")
                max_retries = 2 if has_txn else 1
                for _attempt in range(max_retries):
                    try:
                        if has_txn:
                            self.db.begin()
                        parent_rid = None
                        save_failed = False
                        if form_id:
                            values = renderer.get_values()
                            # Generate the ID before persisting and push it
                            # back into the live form so it is visible now,
                            # rather than only after reopening the record.
                            current_rowid = (
                                self.edit_context.get("rowid")
                                if not is_new and self.edit_context else None)
                            values = self._auto_fill_online_id(
                                form_id, values, own_rowid=current_rowid)
                            renderer.load_values(values)
                            # Recompute COMPUTED fields so they persist
                            std_computed = layout_def.get("computed", [])
                            if std_computed:
                                tab_data_for_comp = renderer.get_tab_grid_data()
                                all_lines = []
                                for glines in tab_data_for_comp.values():
                                    all_lines.extend(glines)
                                detail_cols = []
                                tabs_def_c = layout_def.get("tabs", {})
                                for ti in tabs_def_c.get("items", []):
                                    gid = ti.get("grid", "")
                                    gdef = self.app["grids"].get(gid, {})
                                    for col in gdef.get("columns", []):
                                        cid = col.get("id") or col.get("name", "")
                                        if cid and cid not in detail_cols:
                                            detail_cols.append(cid)
                                merged_c = dict(values)
                                for comp in std_computed:
                                    val = eval_computed_formula(
                                        comp["expr"], merged_c, all_lines, detail_cols)
                                    merged_c[comp["name"]] = val
                                    values[comp["name"]] = val
                            if not is_new:
                                parent_rid = self.edit_context["rowid"]
                                if not self._update_form_data(form_id, values, parent_rid):
                                    parent_rid = None
                                    save_failed = True
                            else:
                                parent_rid = self._save_form_data(form_id, values)
                                if parent_rid is None:
                                    save_failed = True
                        # Save detail grid (tab) data linked to parent
                        if parent_rid is not None:
                            pf_filter_cols = {}
                            tabs_def = layout_def.get("tabs", {})
                            for ti in tabs_def.get("items", []):
                                if ti.get("prefill") and ti.get("coedit"):
                                    pf_filter_cols[ti["grid"]] = ti["coedit"]
                            tab_data = renderer.get_tab_grid_data()
                            for grid_id, rows in tab_data.items():
                                grid_def = self.app["grids"].get(grid_id, {})
                                if grid_def.get("tree_detail"):
                                    self._save_tree_detail_data(grid_id, rows, parent_rid)
                                else:
                                    fcol = pf_filter_cols.get(grid_id)
                                    if fcol:
                                        rows = [r for r in rows
                                                if r.get(fcol) not in (None, "", 0, 0.0, "0")]
                                    self._save_detail_grid_data(grid_id, rows, parent_rid)
                        # Execute ON SAVE rules (DSL-driven)
                        on_save = layout_def.get("on_save")
                        if on_save and form_id and parent_rid is not None:
                            values = renderer.get_values()
                            form_name = form_id.replace("_form", "")
                            self._execute_on_save(on_save, form_name, parent_rid,
                                                  values, is_new)
                        if has_txn:
                            self.db.commit()
                        break  # success
                    except Exception as e:
                        if has_txn:
                            try:
                                self.db.rollback()
                            except Exception:
                                pass
                        if _attempt < max_retries - 1:
                            self._log(e, context="save transaction retry")
                            continue  # retry once
                        save_failed = True
                        self._log(e, context="save transaction failed")
                # Stay on form — set edit_context to saved record
                if form_id and parent_rid is not None:
                    values = renderer.get_values()
                    self.edit_context = {
                        "form_id": form_id,
                        "rowid": parent_rid,
                        "data": values
                    }
                if save_failed:
                    # Header save failed: detail rows were skipped (parent_rid
                    # is None) and ON SAVE rules were skipped. Report the
                    # failure honestly instead of showing "Save OK".
                    self._show_message(
                        stdscr,
                        "Save FAILED: header record could not be written. "
                        "Detail rows and ON-SAVE rules were not executed.")
                else:
                    self._lookups_dirty = True
                    self._show_save_ok(stdscr)
                continue

            elif action == "__post__":
                workflow = layout_def.get("workflow")
                if not workflow:
                    continue
                # Find form_id
                form_id = None
                for ref_id in layout_def.get("fields", {}):
                    if ref_id in self.app["forms"]:
                        form_id = ref_id
                        break
                if not form_id:
                    continue
                values = renderer.get_values()
                # Auto-save (new or existing)
                if self.edit_context and self.edit_context.get("rowid") is not None:
                    parent_rid = self.edit_context["rowid"]
                    self._update_form_data(form_id, values, parent_rid)
                else:
                    parent_rid = self._save_form_data(form_id, values)
                    self.edit_context = {
                        "form_id": form_id,
                        "rowid": parent_rid,
                        "data": values
                    }
                # Save detail grids
                tab_data = renderer.get_tab_grid_data()
                if parent_rid is not None:
                    for grid_id, rows in tab_data.items():
                        grid_def = self.app["grids"].get(grid_id, {})
                        if grid_def.get("tree_detail"):
                            self._save_tree_detail_data(grid_id, rows, parent_rid)
                        else:
                            self._save_detail_grid_data(grid_id, rows, parent_rid)
                # Run workflow transition
                self._handle_workflow(stdscr, form_id, values, tab_data, workflow, layout_def)
                self.edit_context["data"] = values
                continue

            elif action == "__edit__":
                # Enter on grid row: load selected row into form for editing
                row = renderer.get_selected_row()
                if row:
                    parent_form = self._find_grid_parent_form(layout_def)
                    if parent_form:
                        form_layout = self._find_form_layout(parent_form)
                        if form_layout:
                            self.edit_context = {
                                "form_id": parent_form,
                                "rowid": row.get("rowid"),
                                "data": row
                            }
                            history.append(current_layout)
                            current_layout = form_layout
                            continue
                continue

            elif isinstance(action, tuple) and action[0] == "__delete__":
                # Ctrl+D: delete row from DB
                rowid = action[1]
                parent_form = self._find_grid_parent_form(layout_def)
                if parent_form and self.db:
                    table_name = parent_form.replace("_form", "")
                    try:
                        self.db.execute(
                            f'DELETE FROM "{table_name}" WHERE rowid = ?',
                            (rowid,))
                        self._lookups_dirty = True
                    except Exception as e:
                        self._log(e, context=f"delete-detail {table_name} rowid={rowid}")
                continue

            elif action == "__b2b_send__":
                # Ctrl+B: queue current document to B2B outbox
                self._b2b_queue_from_form(stdscr, renderer, layout_def)
                continue

            elif action == "__cycle_view__":
                # F2: cycle LISTVIEW filter
                for ref_id in layout_def.get("fields", {}):
                    if ref_id in self.app["grids"]:
                        views = self.app["grids"][ref_id].get("list_views", [])
                        if views:
                            cur = self._list_view_idx.get(ref_id, 0)
                            self._list_view_idx[ref_id] = (cur + 1) % len(views)
                        break
                continue

            elif action == "__new__":
                # F3: new blank record
                parent_form = self._find_grid_parent_form(layout_def)
                if parent_form:
                    form_layout = self._find_form_layout(parent_form)
                    if form_layout:
                        self.edit_context = None
                        history.append(current_layout)
                        current_layout = form_layout
                        continue
                continue

            # ── Handle regular actions ──
            elif action in self.app["actions"]:
                act_def = self.app["actions"][action]
                if self._has_fatal_db_error() and not self._offline_safe_action(act_def):
                    self._show_multiline_popup(
                        stdscr,
                        "Database Offline",
                        "No database is connected.\n\n"
                        "Use Switch Company to select a working database "
                        "before opening forms, reports, or other data screens."
                    )
                    continue
                # Save form data if a Save button was pressed
                if renderer.widgets and action:
                    for btn in renderer.buttons:
                        if btn["action"] == action and btn["label"].lower() == "save":
                            parent_rid = None
                            for ref_id in layout_def.get("fields", {}):
                                if ref_id in self.app["forms"]:
                                    values = renderer.get_values()
                                    if self.edit_context and self.edit_context.get("rowid") is not None:
                                        parent_rid = self.edit_context["rowid"]
                                        self._update_form_data(ref_id, values, parent_rid)
                                    else:
                                        parent_rid = self._save_form_data(ref_id, values)
                                    self.edit_context = None
                                    break
                            # Save detail grid (tab) data linked to parent
                            if parent_rid is not None:
                                tab_data = renderer.get_tab_grid_data()
                                for grid_id, rows in tab_data.items():
                                    grid_def = self.app["grids"].get(grid_id, {})
                                    if grid_def.get("tree_detail"):
                                        self._save_tree_detail_data(grid_id, rows, parent_rid)
                                    else:
                                        self._save_detail_grid_data(grid_id, rows, parent_rid)
                            break

                if act_def["type"] == "goto":
                    history.append(current_layout)
                    current_layout = act_def["target"]
                elif act_def["type"] == "report":
                    self._run_report(stdscr, act_def["target"])
                    continue
                elif act_def["type"] == "report_browser":
                    self._report_browser(stdscr)
                    continue
                elif act_def["type"] == "reset":
                    self._reset_all_data(stdscr)
                    continue
                elif act_def["type"] == "switch":
                    self._switch_company(stdscr)
                    continue
                elif act_def["type"] == "whmap":
                    self._run_warehouse_map(stdscr)
                    from tui.themes import init_colors as _reinit_colors
                    _reinit_colors()
                    continue
                elif act_def["type"] == "pos_screen":
                    self._open_pos_screen(stdscr)
                    continue
                elif act_def["type"] == "settings":
                    self._open_business_settings(stdscr)
                    continue
                elif act_def["type"] == "b2b_inbox":
                    self._open_b2b_inbox(stdscr)
                    continue
                elif act_def["type"] == "b2b_outbox":
                    self._open_b2b_outbox(stdscr)
                    continue
                elif act_def["type"] == "exit":
                    break
            elif action == "":
                self.edit_context = None
                if history:
                    current_layout = history.pop()
                else:
                    break
            else:
                continue
