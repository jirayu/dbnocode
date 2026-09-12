"""dbnocode SQL Server — Pure-Python Hrana 2 WebSocket server.

Replaces sqld for Windows environments (no WSL/Docker needed).
Speaks the same Hrana 2 protocol that RemoteSQLAdapter expects.
Supports SQLite, PostgreSQL, and Firebird backends behind a per-company
WebSocket endpoint.

Usage:
    python server.py                  Interactive menu
    python server.py list             Show all companies and server status
    python server.py start [name]     Start server (all or by company name)
    python server.py stop [name]      Stop server (all or by company name)
    python server.py status           Show running servers

Can be packaged with PyInstaller:
    pyinstaller --onefile server.py
"""

import asyncio
import json
import logging
import os
import platform
import queue
import re
import signal
import socket
import sqlite3
import struct

# Suppress noisy websockets handshake errors from TCP probes
logging.getLogger("websockets").setLevel(logging.CRITICAL)
import sys
import threading
import time
from urllib.parse import urlparse

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    HAS_TK = True
except ImportError:
    HAS_TK = False

# ── Configuration ──────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.expanduser("~"), ".dbnocode-data")
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import ctypes
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

# Track running server threads so we can stop them
_running_servers = {}  # port -> {"thread": Thread, "stop": Event, "name": str}
_quiet = False  # suppress console output in GUI mode
_activity_log = queue.Queue()  # thread-safe log for GUI activity tab

# Optional adapter filter (None = serve all adapters). The FBServer.py shim
# sets this to "firebird" to preserve the dedicated Firebird-only executable.
_ADAPTER_FILTER = None


def _filter_companies(companies):
    """Restrict served companies to a single adapter when _ADAPTER_FILTER is set."""
    if not _ADAPTER_FILTER:
        return companies
    wanted = _ADAPTER_FILTER.strip().lower()
    return [c for c in companies
            if str(c.get("adapter") or "").strip().lower() == wanted]


def _log(msg):
    """Print only when not in GUI (quiet) mode."""
    if not _quiet:
        print(msg)


def _log_activity(server_name, event, detail=""):
    """Non-blocking activity log entry for GUI consumption."""
    ts = time.strftime("%H:%M:%S")
    _activity_log.put_nowait((ts, server_name, event, detail))


# ── Hrana 2 Server ─────────────────────────────────────────────────────────

def _to_hrana_value(val):
    """Convert Python value to Hrana value dict."""
    if val is None:
        return {"type": "null"}
    if isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    if isinstance(val, float):
        return {"type": "float", "value": val}
    if isinstance(val, bytes):
        import base64
        return {"type": "blob", "base64": base64.b64encode(val).decode()}
    return {"type": "text", "value": str(val)}


def _from_hrana_value(val):
    """Convert Hrana value dict to Python value."""
    t = val.get("type", "text")
    if t == "null":
        return None
    if t == "integer":
        return int(val["value"])
    if t == "float":
        return float(val["value"])
    return val.get("value")


def _company_adapter(company):
    """Normalized backend adapter name for a company config."""
    return str((company or {}).get("adapter") or "sqlite").strip().lower()


def _resolve_sqlite_db_path(company):
    """Resolve the SQLite file path served for a company."""
    db_file = (company or {}).get("db_file", "") or (company or {}).get("db_name", "")
    if not db_file:
        return db_path_for((company or {}).get("company", "default"))
    if os.path.isabs(db_file):
        return db_file
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, db_file)


def _company_database_display(company):
    """Human-readable backend database label for status UIs."""
    adapter = _company_adapter(company)
    if adapter == "postgres":
        host = company.get("host", "localhost")
        port = company.get("port", 5432)
        db = company.get("database", "") or company.get("db_name", "")
        return f"postgres://{host}:{port}/{db}"
    if adapter == "firebird":
        fb = _fb_config_from_company(company)
        host = fb.get("host", "localhost")
        port = fb.get("port", 3050)
        db = fb.get("database", "")
        return f"firebird://{host}:{port}/{db}"
    return _resolve_sqlite_db_path(company)


def _find_fbclient():
    """Locate a 64-bit fbclient.dll from Firebird install directories."""
    if struct.calcsize("P") * 8 != 64:
        return None
    base = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                        "Firebird")
    if not os.path.isdir(base):
        return None
    for subdir in sorted(os.listdir(base), reverse=True):
        candidate = os.path.join(base, subdir, "fbclient.dll")
        if os.path.isfile(candidate):
            return candidate
    return None


def _fb_config_from_company(company):
    """Extract Firebird connection config from a company dict."""
    return {
        "host": company.get("fb_host", company.get("host", "localhost")),
        "port": int(company.get("fb_port", company.get("port", 3050))),
        "database": company.get("fb_database", company.get("database", "")),
        "user": company.get("fb_user", company.get("user", "sysdba")),
        "password": company.get("fb_password", company.get("password", "masterkey")),
    }


def _fb_connect(company):
    """Connect to Firebird database. Returns (conn, fb_major_version)."""
    try:
        import fdb
    except ImportError:
        raise RuntimeError("fdb required for Firebird. Install with: pip install fdb")

    fb_lib = os.environ.get("FIREBIRD_CLIENT") or _find_fbclient()
    if fb_lib and not getattr(fdb, "_fbserver_loaded", False):
        fdb.load_api(fb_lib)
        fdb._fbserver_loaded = True

    fb = _fb_config_from_company(company)
    host = fb["host"]
    port = fb["port"]
    database = fb["database"]
    user = fb["user"]
    password = fb["password"]
    is_local = not host or host in ("localhost", "127.0.0.1")

    if not is_local:
        try:
            with socket.create_connection((host, port), timeout=0.75):
                pass
        except (OSError, TimeoutError) as exc:
            raise ConnectionError(
                f"Firebird server unreachable ({host}:{port})"
            ) from exc

    if is_local and database and not os.path.isabs(database):
        os.makedirs(DATA_DIR, exist_ok=True)
        database = os.path.join(DATA_DIR, database)

    def _connect_with_credentials(user_name, user_password):
        return fdb.connect(
            host=host, port=port, database=database,
            user=user_name, password=user_password, charset="UTF8")

    try:
        conn = _connect_with_credentials(user, password)
    except fdb.fbcore.DatabaseError as e:
        err_text = str(e).lower()
        if ("user name and password are not defined" in err_text
                and isinstance(password, str)
                and password.lower() == "masterkey"
                and password != "masterkey"):
            conn = _connect_with_credentials(user, "masterkey")
            company["password"] = "masterkey"
            company["fb_password"] = "masterkey"
        elif is_local and database and not os.path.exists(database):
            db_dir = os.path.dirname(database)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            conn = fdb.create_database(
                host=host, port=port, database=database,
                user=user, password=password, charset="UTF8",
                page_size=16384)
        else:
            raise

    fb_major = 5
    try:
        ver = getattr(conn, "server_version", "") or ""
        vm = re.search(r'(\d+)\.', ver)
        if vm:
            fb_major = int(vm.group(1))
    except Exception:
        pass
    return conn, fb_major


def _decode_fb_value(value):
    """Decode Firebird blobs/bytes to Python strings."""
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value


def _rewrite_qmark_placeholders(sql):
    """Translate SQLite qmark placeholders to psycopg2 %s placeholders."""
    out = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _rewrite_postgres_sql(sql):
    """Rewrite SQLite-flavored SQL into PostgreSQL-compatible SQL."""
    stripped = sql.strip()
    upper = stripped.upper()
    needs_returning = False

    if "SQLITE_MASTER" in upper:
        return (
            "SELECT tablename AS name FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT LIKE 'sqlite_%' "
            "AND tablename NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY tablename",
            False,
        )

    if upper.startswith("CREATE TABLE IF NOT EXISTS"):
        match = re.match(
            r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"([^"]+)"\s*\((.+)\)',
            stripped, re.IGNORECASE | re.DOTALL)
        if match:
            table_name = match.group(1)
            cols = match.group(2).strip()
            cols = re.sub(r'(?<!")\bdata\b(?!")', '"data"', cols, flags=re.IGNORECASE)
            if "rowid" not in cols.lower():
                cols = '"rowid" BIGSERIAL PRIMARY KEY, ' + cols
            stripped = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({cols})'
            upper = stripped.upper()

    stripped = _rewrite_qmark_placeholders(stripped)

    if upper.startswith("INSERT ") and " RETURNING " not in upper:
        stripped += " RETURNING rowid"
        needs_returning = True

    return stripped, needs_returning


_use_firebird_identity = True


def _rewrite_firebird_create_table(sql):
    """Rewrite CREATE TABLE for Firebird column types."""
    global _use_firebird_identity
    match = re.match(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"([^"]+)"\s*\((.+)\)',
        sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return sql
    table_name = match.group(1)
    if _use_firebird_identity:
        cols = ('"rowid" INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, '
                '"data" BLOB SUB_TYPE TEXT CHARACTER SET UTF8')
    else:
        cols = ('"rowid" INTEGER NOT NULL PRIMARY KEY, '
                '"data" BLOB SUB_TYPE TEXT CHARACTER SET UTF8')
    return f'CREATE TABLE "{table_name}" ({cols})'


def _rewrite_firebird_sql(sql):
    """Translate SQLite-flavored SQL into Firebird-compatible SQL."""
    stripped = sql.strip()
    upper = stripped.upper()
    if upper.startswith("CREATE TABLE IF NOT EXISTS"):
        return _rewrite_firebird_create_table(stripped)
    sql = re.sub(r'(?<!")(?<!\w)rowid(?!"|\w)', '"rowid"', sql,
                 flags=re.IGNORECASE)
    sql = re.sub(r'(?<!")(?<!\w)data(?!"|\w)', '"data"', sql,
                 flags=re.IGNORECASE)
    return sql


def _ensure_firebird_rowid_gen(conn, table_name):
    """Create sequence + trigger for Firebird rowid auto-increment fallback."""
    gen_name = f"gen_{table_name}_id"
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE SEQUENCE "{gen_name}"')
        conn.commit()
    except Exception:
        pass
    try:
        cur.execute(
            f'CREATE TRIGGER "trg_{table_name}_bi" '
            f'FOR "{table_name}" ACTIVE BEFORE INSERT '
            f'AS BEGIN '
            f'IF (NEW."rowid" IS NULL) THEN '
            f'NEW."rowid" = NEXT VALUE FOR "{gen_name}"; '
            f'END')
        conn.commit()
    except Exception:
        pass
    finally:
        cur.close()


def _postgres_db_missing(exc, dbname):
    """True when a psycopg2 connection error indicates a missing database."""
    code = getattr(exc, "pgcode", None)
    if code == "3D000":
        return True
    msg = str(exc).lower()
    if not dbname:
        return False
    return f'database "{dbname.lower()}" does not exist' in msg


def _ensure_postgres_database(psycopg2_mod, company):
    """Create the target PostgreSQL database if it does not exist."""
    from psycopg2 import sql as pg_sql

    host = company.get("host", "localhost")
    port = int(company.get("port", 5432))
    dbname = company.get("database", "") or company.get("db_name", "")
    user = company.get("user", "")
    password = company.get("password", "")
    if not dbname:
        raise RuntimeError("Postgres database name is required")

    last_error = None
    for admin_db in ("postgres", "template1"):
        admin_conn = None
        try:
            admin_conn = psycopg2_mod.connect(
                host=host, port=port, dbname=admin_db,
                user=user, password=password)
            admin_conn.autocommit = True
            cur = admin_conn.cursor()
            try:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                exists = cur.fetchone()
                if not exists:
                    cur.execute(
                        pg_sql.SQL("CREATE DATABASE {}").format(
                            pg_sql.Identifier(dbname)))
            finally:
                cur.close()
            return
        except Exception as exc:
            last_error = exc
        finally:
            if admin_conn:
                try:
                    admin_conn.close()
                except Exception:
                    pass
    raise last_error or RuntimeError(f"Could not create PostgreSQL database {dbname}")


def _connect_postgres_backend(company):
    """Connect to PostgreSQL, creating the target database when missing."""
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError(
            "psycopg2 required for PostgreSQL. Install with: pip install psycopg2-binary")

    host = company.get("host", "localhost")
    port = int(company.get("port", 5432))
    dbname = company.get("database", "") or company.get("db_name", "")
    user = company.get("user", "")
    password = company.get("password", "")
    if not dbname:
        raise RuntimeError("Postgres database name is required")

    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password)
    except psycopg2.OperationalError as exc:
        if not _postgres_db_missing(exc, dbname):
            raise
        _ensure_postgres_database(psycopg2, company)
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password)
    conn.autocommit = True
    return conn


def _connect_backend(company):
    """Open a backend DB connection for a served company."""
    adapter = _company_adapter(company)
    if adapter == "postgres":
        conn = _connect_postgres_backend(company)
        return conn, {"adapter": adapter}
    if adapter == "firebird":
        conn, fb_major = _fb_connect(company)
        return conn, {"adapter": adapter, "fb_major": fb_major}
    db_path = _resolve_sqlite_db_path(company)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn, {"adapter": "sqlite"}


def _execute_stmt_sqlite(conn, stmt, in_txn):
    """Execute a Hrana statement on a SQLite connection.

    Returns (result_dict, new_in_txn).  *in_txn* tracks whether the
    client has issued an explicit BEGIN so that auto-commit is suppressed
    until COMMIT/ROLLBACK.
    """
    sql = stmt["sql"]
    args = [_from_hrana_value(a) for a in stmt.get("args", [])]
    want_rows = stmt.get("want_rows", True)

    cur = conn.cursor()
    try:
        cur.execute(sql, args)

        result = {
            "affected_row_count": cur.rowcount if cur.rowcount >= 0 else 0,
            "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None,
        }

        if want_rows and cur.description:
            cols = [{"name": d[0]} for d in cur.description]
            rows = [[_to_hrana_value(v) for v in row] for row in cur.fetchall()]
            result["cols"] = cols
            result["rows"] = rows
        else:
            result["cols"] = []
            result["rows"] = []

        # Track explicit transaction state
        upper = sql.strip().upper()
        if upper.startswith("BEGIN") or upper.startswith("SAVEPOINT"):
            in_txn = True
        elif upper.startswith("COMMIT") or upper.startswith("ROLLBACK") or upper.startswith("END"):
            in_txn = False
        elif not in_txn:
            # Auto-commit only when NOT inside explicit transaction
            if upper.startswith(("INSERT", "UPDATE", "DELETE", "CREATE",
                                 "DROP", "ALTER")):
                try:
                    conn.commit()
                except Exception:
                    pass

        return result, in_txn
    except Exception as e:
        # On error inside explicit txn, keep in_txn True so client
        # can still issue ROLLBACK
        raise RuntimeError(str(e))
    finally:
        cur.close()


def _execute_stmt_postgres(conn, stmt, in_txn):
    """Execute a Hrana statement on a PostgreSQL connection."""
    sql = stmt["sql"]
    args = [_from_hrana_value(a) for a in stmt.get("args", [])]
    want_rows = stmt.get("want_rows", True)
    upper = sql.strip().upper()

    cur = conn.cursor()
    try:
        if upper.startswith("BEGIN"):
            if conn.autocommit:
                conn.autocommit = False
            return {
                "affected_row_count": 0,
                "last_insert_rowid": None,
                "cols": [],
                "rows": [],
            }, True
        if upper.startswith("SAVEPOINT"):
            if conn.autocommit:
                conn.autocommit = False
            cur.execute(_rewrite_qmark_placeholders(sql), args)
            return {
                "affected_row_count": cur.rowcount if cur.rowcount >= 0 else 0,
                "last_insert_rowid": None,
                "cols": [],
                "rows": [],
            }, True
        if upper.startswith(("COMMIT", "END")):
            conn.commit()
            conn.autocommit = True
            return {
                "affected_row_count": 0,
                "last_insert_rowid": None,
                "cols": [],
                "rows": [],
            }, False
        if upper.startswith("ROLLBACK"):
            conn.rollback()
            conn.autocommit = True
            return {
                "affected_row_count": 0,
                "last_insert_rowid": None,
                "cols": [],
                "rows": [],
            }, False

        exec_sql, needs_returning = _rewrite_postgres_sql(sql)
        cur.execute(exec_sql, args)

        result = {
            "affected_row_count": cur.rowcount if cur.rowcount >= 0 else 0,
            "last_insert_rowid": None,
        }

        if cur.description:
            rows_raw = cur.fetchall()
            if needs_returning and rows_raw:
                result["last_insert_rowid"] = str(rows_raw[0][0])
            if want_rows:
                result["cols"] = [{"name": d[0]} for d in cur.description]
                result["rows"] = [[_to_hrana_value(v) for v in row] for row in rows_raw]
            else:
                result["cols"] = []
                result["rows"] = []
        else:
            result["cols"] = []
            result["rows"] = []

        return result, in_txn
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        cur.close()


def _execute_stmt_firebird(conn, stmt, in_txn, fb_major=5):
    """Execute a Hrana statement on a Firebird connection."""
    global _use_firebird_identity
    sql = stmt["sql"]
    args = [_from_hrana_value(a) for a in stmt.get("args", [])]
    want_rows = stmt.get("want_rows", True)
    upper_raw = sql.strip().upper()

    if upper_raw in ("BEGIN", "BEGIN TRANSACTION", "BEGIN IMMEDIATE",
                     "BEGIN DEFERRED", "BEGIN EXCLUSIVE"):
        return {
            "affected_row_count": 0,
            "last_insert_rowid": None,
            "cols": [],
            "rows": [],
        }, True
    if upper_raw in ("COMMIT", "COMMIT TRANSACTION"):
        conn.commit()
        return {
            "affected_row_count": 0,
            "last_insert_rowid": None,
            "cols": [],
            "rows": [],
        }, False
    if upper_raw in ("ROLLBACK", "ROLLBACK TRANSACTION"):
        conn.rollback()
        return {
            "affected_row_count": 0,
            "last_insert_rowid": None,
            "cols": [],
            "rows": [],
        }, False

    sql = _rewrite_firebird_sql(sql)
    upper = sql.strip().upper()
    cur = conn.cursor()
    try:
        lastrowid = None
        if upper.startswith("CREATE TABLE"):
            try:
                cur.execute(sql, args)
                if not in_txn:
                    conn.commit()
            except Exception as e:
                err_str = str(e)
                if "-607" in err_str:
                    tm = re.search(r'"([^"]+)"', sql)
                    tname = tm.group(1) if tm else "unknown"
                    _ensure_firebird_rowid_gen(conn, tname)
                    return {
                        "affected_row_count": 0,
                        "last_insert_rowid": None,
                        "cols": [],
                        "rows": [],
                    }, in_txn
                if "-104" in err_str and _use_firebird_identity:
                    _use_firebird_identity = False
                    tm = re.search(r'"([^"]+)"', sql)
                    tname = tm.group(1) if tm else "unknown"
                    fallback = sql.replace(
                        "GENERATED BY DEFAULT AS IDENTITY ", "")
                    cur.close()
                    cur = conn.cursor()
                    cur.execute(fallback, args)
                    if not in_txn:
                        conn.commit()
                    _ensure_firebird_rowid_gen(conn, tname)
                else:
                    raise
        elif upper.startswith("INSERT") and "RETURNING" not in upper:
            sql_ret = sql.rstrip().rstrip(";") + ' RETURNING "rowid"'
            cur.execute(sql_ret, args)
            row = cur.fetchone()
            if row:
                lastrowid = row[0]
            if not in_txn:
                conn.commit()
        else:
            cur.execute(sql, args)
            if not in_txn and upper.startswith(("INSERT", "UPDATE", "DELETE", "CREATE",
                                                "DROP", "ALTER")):
                try:
                    conn.commit()
                except Exception:
                    pass

        result = {
            "affected_row_count": cur.rowcount if cur.rowcount >= 0 else 0,
            "last_insert_rowid": str(lastrowid) if lastrowid else None,
        }
        if want_rows and cur.description:
            result["cols"] = [{"name": _decode_fb_value(d[0]).lower()}
                              for d in cur.description]
            rows = []
            for row in cur.fetchall():
                rows.append([_to_hrana_value(_decode_fb_value(v)) for v in row])
            result["rows"] = rows
        else:
            result["cols"] = []
            result["rows"] = []
        return result, in_txn
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _execute_stmt(conn, stmt, in_txn, backend=None):
    """Execute a Hrana statement on the selected backend connection."""
    adapter = (backend or {}).get("adapter", "sqlite")
    if adapter == "postgres":
        return _execute_stmt_postgres(conn, stmt, in_txn)
    if adapter == "firebird":
        return _execute_stmt_firebird(
            conn, stmt, in_txn, (backend or {}).get("fb_major", 5))
    return _execute_stmt_sqlite(conn, stmt, in_txn)


async def _handle_client(websocket, company, server_name=""):
    """Handle one Hrana 2 WebSocket client connection."""
    try:
        conn, adapter = _connect_backend(company)
    except Exception as e:
        _log_activity(server_name, "ERROR", f"DB connect failed: {e}")
        try:
            await websocket.send(json.dumps({
                "type": "hello_error",
                "error": {"message": str(e)},
            }))
        except Exception:
            pass
        await websocket.close(code=1011, reason=str(e)[:120])
        return
    streams = set()
    in_txn = False  # tracks explicit BEGIN/COMMIT state
    remote = ""
    try:
        remote = websocket.remote_address
        if isinstance(remote, tuple):
            remote = f"{remote[0]}:{remote[1]}"
    except Exception:
        remote = "unknown"
    _log_activity(server_name, "CONNECT", str(remote))

    try:
        async for raw_msg in websocket:
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "hello":
                # No auth check for local server
                await websocket.send(json.dumps({"type": "hello_ok"}))

            elif msg_type == "request":
                req_id = msg["request_id"]
                req = msg["request"]
                req_type = req["type"]

                try:
                    if req_type == "open_stream":
                        stream_id = req["stream_id"]
                        streams.add(stream_id)
                        await websocket.send(json.dumps({
                            "type": "response_ok",
                            "request_id": req_id,
                            "response": {},
                        }))

                    elif req_type == "close_stream":
                        stream_id = req["stream_id"]
                        streams.discard(stream_id)
                        await websocket.send(json.dumps({
                            "type": "response_ok",
                            "request_id": req_id,
                            "response": {},
                        }))

                    elif req_type == "execute":
                        sql = req["stmt"].get("sql", "")
                        result, in_txn = _execute_stmt(
                            conn, req["stmt"], in_txn, adapter)
                        rows = len(result.get("rows", []))
                        sql_short = sql[:80] + ("..." if len(sql) > 80 else "")
                        _log_activity(server_name, "SQL",
                                      f"{sql_short}  ({rows} rows)")
                        await websocket.send(json.dumps({
                            "type": "response_ok",
                            "request_id": req_id,
                            "response": {"result": result},
                        }))

                    else:
                        await websocket.send(json.dumps({
                            "type": "response_error",
                            "request_id": req_id,
                            "error": {"message": f"Unknown request type: {req_type}"},
                        }))

                except Exception as e:
                    _log_activity(server_name, "ERROR", str(e)[:120])
                    await websocket.send(json.dumps({
                        "type": "response_error",
                        "request_id": req_id,
                        "error": {"message": str(e)},
                    }))
    except Exception:
        pass
    finally:
        _log_activity(server_name, "DISCONNECT", str(remote))
        try:
            conn.close()
        except Exception:
            pass


async def _run_server(port, company, stop_event, name):
    """Run Hrana 2 WebSocket server on given port."""
    try:
        import websockets
        from websockets.asyncio.server import serve
    except ImportError:
        _log("  ERROR: 'websockets' package required. pip install websockets")
        return

    async def handler(websocket):
        await _handle_client(websocket, company, name)

    try:
        # Bind both IPv4 and IPv6 so "localhost" resolves fast on Windows
        # (Windows resolves localhost to ::1 first, then 127.0.0.1)
        async with serve(handler, "", port,
                         subprotocols=["hrana2"]) as server:
            _log(f"  {name}: listening on port {port} "
                 f"(db: {_company_database_display(company)})")
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
    except OSError as e:
        _log(f"  {name}: FAILED to start on port {port} - {e}")


def _server_thread(port, company, stop_event, name):
    """Thread entry point for running async server."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_server(port, company, stop_event, name))
    finally:
        loop.close()


# ── Company/Config Helpers ─────────────────────────────────────────────────

def find_companies_file():
    """Find remote_connections.json or *_companies.json in current directory."""
    if os.path.exists("remote_connections.json"):
        return "remote_connections.json"
    for f in sorted(os.listdir(".")):
        if f.endswith("_companies.json"):
            return f
    if os.path.exists("company_server.json"):
        return "company_server.json"
    return None


def load_companies(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_companies(path, companies):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(companies, f, indent=2)


def safe_name(company_name):
    return "".join(c if c.isalnum() else "_" for c in company_name)


def parse_port(db_url):
    parsed = urlparse(db_url)
    return parsed.port or 8081


def db_path_for(company_name):
    """Return .db file path for a company."""
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, safe_name(company_name) + ".db")


def probe_server(db_url):
    """Quick TCP check if server is reachable."""
    if not db_url or not db_url.startswith("ws"):
        return False
    parsed = urlparse(db_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8081
    try:
        s = socket.create_connection((host, port), timeout=1)
        s.close()
        return True
    except (OSError, socket.timeout):
        return False


def get_remote_companies(companies):
    return [(i, c) for i, c in enumerate(companies)
            if c.get("db_url", "").startswith("ws")]


# ── Start / Stop ───────────────────────────────────────────────────────────

def start_server(company_name, db_url=None):
    if isinstance(company_name, dict):
        company = dict(company_name)
        company_name = company.get("company", "")
        db_url = company.get("db_url", "")
    else:
        company = {"company": company_name, "db_url": db_url}
    port = parse_port(db_url)

    if port in _running_servers:
        _log(f"  {company_name}: already running on port {port}")
        return True

    if probe_server(db_url):
        _log(f"  {company_name}: port {port} already in use (external process?)")
        return False

    # Keep Firebird startup aligned with the original dedicated FBServer:
    # bring up the websocket first, then let client sessions surface
    # backend/auth issues through hello_error instead of blocking Start All.
    if _company_adapter(company) != "firebird":
        try:
            probe_conn, _ = _connect_backend(company)
            probe_conn.close()
        except Exception as e:
            _log(f"  {company_name}: backend connect FAILED - {e}")
            _log_activity(company_name, "ERROR", f"Backend connect failed: {e}")
            return False

    stop_event = threading.Event()
    t = threading.Thread(target=_server_thread,
                         args=(port, company, stop_event, company_name),
                         daemon=True)
    t.start()

    # Wait for server to come up
    max_waits = 20 if _company_adapter(company) == "firebird" else 10
    for _ in range(max_waits):
        time.sleep(0.5)
        if probe_server(db_url):
            _running_servers[port] = {
                "thread": t, "stop": stop_event, "name": company_name
            }
            return True

    _log(f"  {company_name}: failed to start on port {port}")
    stop_event.set()
    return False


def stop_server(company_name, db_url):
    if isinstance(company_name, dict):
        db_url = company_name.get("db_url", "")
        company_name = company_name.get("company", "")
    port = parse_port(db_url)
    info = _running_servers.pop(port, None)
    if info:
        info["stop"].set()
        info["thread"].join(timeout=3)
        _log(f"  {company_name}: stopped (port {port})")
        return True
    else:
        _log(f"  {company_name}: not managed by this process")
        return False


def stop_all():
    for port in list(_running_servers.keys()):
        info = _running_servers.pop(port)
        info["stop"].set()
        _log(f"  {info['name']}: stopped (port {port})")


# ── Display ────────────────────────────────────────────────────────────────

def show_status(companies):
    print()
    print(f"  {'Company':<25} {'Database':<30} {'Port':<8} {'Status':<10}")
    print(f"  {'-' * 25} {'-' * 30} {'-' * 8} {'-' * 10}")
    for c in companies:
        name = c.get("company", "")
        db = _company_database_display(c)
        if c.get("db_url", "").startswith("ws"):
            port = str(parse_port(c["db_url"]))
            up = probe_server(c["db_url"])
            managed = parse_port(c["db_url"]) in _running_servers
            if up and managed:
                status = "RUNNING"
            elif up:
                status = "ONLINE"
            else:
                status = "OFFLINE"
        else:
            port = "-"
            status = "LOCAL"
        active = " *" if c.get("active") else ""
        print(f"  {name:<25} {db:<30} {port:<8} {status}{active}")
    print()
    # Show data directory
    print(f"  Data directory: {DATA_DIR}")
    print()


# ── GUI Manager ───────────────────────────────────────────────────────────

class ServerManagerGUI:
    """Tkinter GUI for managing SQL servers."""

    def __init__(self, companies_file, companies):
        self.companies_file = companies_file
        self.companies = _filter_companies(companies)
        self.root = tk.Tk()
        self.root.title("dbnocode SQL Server Manager")
        self.root.geometry("850x520")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.minsize(700, 400)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = self.root

        title_frame = tk.Frame(root, bg="#2c3e50")
        title_frame.pack(fill="x")
        tk.Label(title_frame, text="dbnocode SQL Server Manager",
                 font=("Segoe UI", 14, "bold"), fg="white", bg="#2c3e50",
                 pady=10).pack()

        # Tabbed notebook
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=(5, 0))

        # ── Tab 1: Servers ──
        servers_tab = tk.Frame(self.notebook)
        self.notebook.add(servers_tab, text="  Servers  ")

        tree_frame = tk.Frame(servers_tab)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        columns = ("company", "database", "port", "status")
        self.tree = ttk.Treeview(tree_frame, columns=columns,
                                 show="headings", selectmode="browse")
        self.tree.heading("company", text="Company")
        self.tree.heading("database", text="Database")
        self.tree.heading("port", text="Port")
        self.tree.heading("status", text="Status")

        self.tree.column("company", width=200, minwidth=120)
        self.tree.column("database", width=300, minwidth=200)
        self.tree.column("port", width=80, minwidth=60, anchor="center")
        self.tree.column("status", width=100, minwidth=80, anchor="center")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btn_frame = tk.Frame(servers_tab)
        btn_frame.pack(fill="x", padx=10, pady=5)

        buttons = [
            ("Start All", self._start_all, "#27ae60"),
            ("Stop All", self._stop_all, "#e74c3c"),
            ("Start", self._start_selected, "#2ecc71"),
            ("Stop", self._stop_selected, "#c0392b"),
            ("Refresh", self._refresh, "#3498db"),
            ("Add", self._add_company, "#f39c12"),
            ("Edit", self._edit_company, "#9b59b6"),
            ("Delete", self._delete_company, "#e74c3c"),
        ]
        for text, cmd, color in buttons:
            btn = tk.Button(btn_frame, text=text, command=cmd,
                            bg=color, fg="white", width=8,
                            font=("Segoe UI", 9, "bold"))
            btn.pack(side="left", padx=3)

        # ── Tab 2: Activity Log ──
        log_tab = tk.Frame(self.notebook)
        self.notebook.add(log_tab, text="  Activity Log  ")

        log_toolbar = tk.Frame(log_tab)
        log_toolbar.pack(fill="x", padx=10, pady=(8, 3))

        tk.Label(log_toolbar, text="Filter:",
                 font=("Segoe UI", 9)).pack(side="left")
        self._log_filter_var = tk.StringVar()
        filter_entry = tk.Entry(log_toolbar, textvariable=self._log_filter_var,
                                width=25, font=("Segoe UI", 9))
        filter_entry.pack(side="left", padx=(5, 10))

        self._log_pause_var = tk.BooleanVar(value=False)
        tk.Checkbutton(log_toolbar, text="Pause", variable=self._log_pause_var,
                       font=("Segoe UI", 9)).pack(side="left", padx=5)

        tk.Button(log_toolbar, text="Clear", command=self._clear_log,
                  bg="#e74c3c", fg="white", width=6,
                  font=("Segoe UI", 9, "bold")).pack(side="right")

        log_frame = tk.Frame(log_tab)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(3, 8))

        self.log_text = tk.Text(log_frame, wrap="none", state="disabled",
                                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                                insertbackground="white")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical",
                                command=self.log_text.yview)
        log_hsb = ttk.Scrollbar(log_frame, orient="horizontal",
                                command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=log_vsb.set,
                                xscrollcommand=log_hsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")
        log_hsb.pack(side="bottom", fill="x")

        # Color tags for log events
        self.log_text.tag_configure("connect", foreground="#27ae60")
        self.log_text.tag_configure("disconnect", foreground="#e67e22")
        self.log_text.tag_configure("sql", foreground="#d4d4d4")
        self.log_text.tag_configure("error", foreground="#e74c3c")
        self.log_text.tag_configure("timestamp", foreground="#888888")

        self._log_line_count = 0
        self._log_max_lines = 2000
        self._poll_activity_log()

        self.root.bind("<F5>", lambda e: self._refresh())

        # Double-click on row → toggle start/stop
        self.tree.bind("<Double-1>", self._on_double_click)

        # Right-click context menu
        self._context_menu = tk.Menu(self.root, tearoff=0)
        self._context_menu.add_command(label="Start", command=self._start_selected)
        self._context_menu.add_command(label="Stop", command=self._stop_selected)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="Edit", command=self._edit_company)
        self._context_menu.add_command(label="Delete", command=self._delete_company)
        self.tree.bind("<Button-3>", self._on_context_menu)

        status_bar = tk.Frame(root, relief="sunken")
        status_bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar()
        tk.Label(status_bar, textvariable=self.status_var,
                 anchor="w", padx=10, pady=2).pack(side="left")

    def _refresh(self, event=None):
        self._refresh_gen = getattr(self, '_refresh_gen', 0) + 1
        my_gen = self._refresh_gen

        def probe_and_update():
            companies = _filter_companies(load_companies(self.companies_file))
            remote = get_remote_companies(companies)
            results = []
            running_count = 0
            for _, c in remote:
                name = c.get("company", "")
                ws_url = c.get("db_url", "")
                db = _company_database_display(c)
                port = str(parse_port(ws_url))
                up = probe_server(ws_url)
                managed = parse_port(ws_url) in _running_servers
                if up and managed:
                    status = "RUNNING"
                    running_count += 1
                elif up:
                    status = "ONLINE"
                else:
                    status = "OFFLINE"
                results.append((name, db, port, status, up, managed))

            def update_ui():
                if my_gen != self._refresh_gen:
                    return
                self.companies = companies
                for item in self.tree.get_children():
                    self.tree.delete(item)
                for name, db, port, status, up, managed in results:
                    tag = "running" if (up and managed) else (
                        "online" if up else "offline")
                    self.tree.insert("", "end",
                                     values=(name, db, port, status),
                                     tags=(tag,))
                self.tree.tag_configure("running", foreground="#27ae60")
                self.tree.tag_configure("online", foreground="#3498db")
                self.tree.tag_configure("offline", foreground="#999999")
                total = len(results)
                self.status_var.set(
                    f"  Config: {self.companies_file}  |  "
                    f"Servers: {running_count}/{total} running  |  "
                    f"Data: {DATA_DIR}")

            self.root.after(0, update_ui)

            if hasattr(self, '_refresh_timer'):
                try:
                    self.root.after_cancel(self._refresh_timer)
                except Exception:
                    pass
            self._refresh_timer = self.root.after(3000, self._refresh)

        threading.Thread(target=probe_and_update, daemon=True).start()

    def _on_double_click(self, event):
        info = self._get_selected()
        if not info:
            return
        port = parse_port(info["db_url"])
        managed = port in _running_servers
        up = probe_server(info["db_url"])
        def work():
            if up and managed:
                stop_server(info)
            else:
                start_server(info)
            self.root.after(0, self._refresh)
        threading.Thread(target=work, daemon=True).start()

    def _on_close(self):
        stop_all()
        if hasattr(self, '_refresh_timer'):
            try:
                self.root.after_cancel(self._refresh_timer)
            except Exception:
                pass
        self.root.destroy()

    def _poll_activity_log(self):
        """Drain activity queue into log text widget. Runs every 200ms."""
        if not self._log_pause_var.get():
            filt = self._log_filter_var.get().strip().lower()
            batch = []
            try:
                while True:
                    batch.append(_activity_log.get_nowait())
            except queue.Empty:
                pass
            if batch:
                self.log_text.configure(state="normal")
                for ts, server, event, detail in batch:
                    line = f"[{ts}] {server}: {event}  {detail}"
                    if filt and filt not in line.lower():
                        continue
                    tag = event.lower() if event.lower() in (
                        "connect", "disconnect", "sql", "error") else "sql"
                    self.log_text.insert("end", line + "\n", tag)
                    self._log_line_count += 1
                # Clear log when exceeding limit
                if self._log_line_count > self._log_max_lines:
                    self.log_text.delete("1.0", "end")
                    self.log_text.insert("end",
                        f"--- Log cleared ({self._log_max_lines} lines) ---\n",
                        "timestamp")
                    self._log_line_count = 1
                self.log_text.configure(state="disabled")
                self.log_text.see("end")
        self.root.after(200, self._poll_activity_log)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_line_count = 0

    def _start_all(self):
        self.status_var.set("  Starting servers...")
        def work():
            remote = get_remote_companies(self.companies)
            ok = 0
            total = len(remote)
            for i, (_, c) in enumerate(remote):
                self.root.after(0, lambda i=i, c=c: self.status_var.set(
                    f"  Starting {c['company']} ({i+1}/{total})..."))
                try:
                    if start_server(c):
                        ok += 1
                except Exception as e:
                    self.root.after(0, lambda e=e: self.status_var.set(
                        f"  Error: {e}"))
            self.root.after(0, lambda: self.status_var.set(
                f"  Started {ok}/{total} servers"))
            self.root.after(0, self._refresh)
        threading.Thread(target=work, daemon=True).start()

    def _stop_all(self):
        self.status_var.set("  Stopping servers...")
        def work():
            stop_all()
            self.root.after(0, self._refresh)
        threading.Thread(target=work, daemon=True).start()

    def _get_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection",
                                   "Please select a company first.",
                                   parent=self.root)
            return None
        item = self.tree.item(sel[0])
        values = item["values"]
        if not values:
            return None
        match = next((c for c in self.companies
                      if c.get("company") == values[0]
                      and parse_port(c.get("db_url", "")) == int(values[2])), None)
        if match:
            return dict(match)
        return {"company": values[0], "db_url": ""}

    def _start_selected(self):
        info = self._get_selected()
        if info:
            self.status_var.set(f"  Starting {info['company']}...")
            def work():
                try:
                    result = start_server(info)
                    self.root.after(0, lambda: self.status_var.set(
                        f"  {'Started' if result else 'Failed'} {info['company']}"))
                except Exception as e:
                    self.root.after(0, lambda: self.status_var.set(
                        f"  Error: {e}"))
                self.root.after(0, self._refresh)
            threading.Thread(target=work, daemon=True).start()

    def _stop_selected(self):
        info = self._get_selected()
        if info:
            self.status_var.set(f"  Stopping {info['company']}...")
            def work():
                stop_server(info)
                self.root.after(0, self._refresh)
            threading.Thread(target=work, daemon=True).start()

    def _company_dialog(self, title, company=None):
        company = dict(company or {})
        parsed = urlparse(company.get("db_url", "ws://localhost:8081"))
        ws_host_default = parsed.hostname or "localhost"
        ws_port_default = str(parsed.port or 8081)
        adapter_default = _company_adapter(company)

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("500x680")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.minsize(460, 540)

        pad = {"padx": 20, "pady": 3}
        entry_kw = {"width": 40, "font": ("Segoe UI", 10)}
        body = tk.Frame(dialog)
        body.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical",
                                  command=canvas.yview)
        form_frame = tk.Frame(canvas)
        form_window = canvas.create_window((0, 0), window=form_frame,
                                           anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_inner_width(event):
            canvas.itemconfigure(form_window, width=event.width)

        def _on_mousewheel(event):
            delta = getattr(event, "delta", 0)
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            return "break"

        form_frame.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_inner_width)
        for target in (dialog, canvas, form_frame):
            target.bind("<MouseWheel>", _on_mousewheel)
            target.bind("<Button-4>", _on_mousewheel)
            target.bind("<Button-5>", _on_mousewheel)

        tk.Label(form_frame, text="Company Name:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        name_var = tk.StringVar(value=company.get("company", ""))
        name_entry = tk.Entry(form_frame, textvariable=name_var, **entry_kw)
        name_entry.pack(**pad)
        name_entry.focus_set()

        tk.Label(form_frame, text="Server Adapter:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        adapter_var = tk.StringVar(value=adapter_default)
        adapter_box = ttk.Combobox(
            form_frame, textvariable=adapter_var,
            values=["sqlite", "postgres", "firebird"],
            state="readonly", width=37, font=("Segoe UI", 10))
        adapter_box.pack(**pad)

        tk.Label(form_frame, text="WebSocket Host / IP:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        host_var = tk.StringVar(value=ws_host_default)
        tk.Entry(form_frame, textvariable=host_var, **entry_kw).pack(**pad)

        tk.Label(form_frame, text="WebSocket Port (e.g. 8081):",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        port_var = tk.StringVar(value=ws_port_default)
        tk.Entry(form_frame, textvariable=port_var, **entry_kw).pack(**pad)

        tk.Label(form_frame, text="SQLite File / DB Name:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        dbname_var = tk.StringVar(value=company.get("db_file", "") or company.get("db_name", ""))
        dbname_entry = tk.Entry(form_frame, textvariable=dbname_var, **entry_kw)
        dbname_entry.pack(**pad)

        tk.Label(form_frame, text="Postgres Host / IP:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        pg_host_var = tk.StringVar(value=company.get("host", "localhost"))
        pg_host_entry = tk.Entry(form_frame, textvariable=pg_host_var, **entry_kw)
        pg_host_entry.pack(**pad)

        tk.Label(form_frame, text="Postgres Port:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        pg_port_var = tk.StringVar(value=str(company.get("port", 5432)))
        pg_port_entry = tk.Entry(form_frame, textvariable=pg_port_var, **entry_kw)
        pg_port_entry.pack(**pad)

        tk.Label(form_frame, text="Postgres Database:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        pg_db_var = tk.StringVar(value=company.get("database", "") or company.get("db_name", ""))
        pg_db_entry = tk.Entry(form_frame, textvariable=pg_db_var, **entry_kw)
        pg_db_entry.pack(**pad)

        tk.Label(form_frame, text="Postgres User:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        pg_user_var = tk.StringVar(value=company.get("user", ""))
        pg_user_entry = tk.Entry(form_frame, textvariable=pg_user_var, **entry_kw)
        pg_user_entry.pack(**pad)

        tk.Label(form_frame, text="Postgres Password:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        pg_pwd_var = tk.StringVar(value=company.get("password", ""))
        pg_pwd_entry = tk.Entry(form_frame, textvariable=pg_pwd_var, show="*", **entry_kw)
        pg_pwd_entry.pack(**pad)

        fb_defaults = _fb_config_from_company(company)

        tk.Label(form_frame, text="Firebird Host / IP:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        fb_host_var = tk.StringVar(value=fb_defaults.get("host", "localhost"))
        fb_host_entry = tk.Entry(form_frame, textvariable=fb_host_var, **entry_kw)
        fb_host_entry.pack(**pad)

        tk.Label(form_frame, text="Firebird Port:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        fb_port_var = tk.StringVar(value=str(fb_defaults.get("port", 3050)))
        fb_port_entry = tk.Entry(form_frame, textvariable=fb_port_var, **entry_kw)
        fb_port_entry.pack(**pad)

        tk.Label(form_frame, text="Firebird Database Path:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        fb_db_var = tk.StringVar(value=fb_defaults.get("database", ""))
        fb_db_entry = tk.Entry(form_frame, textvariable=fb_db_var, **entry_kw)
        fb_db_entry.pack(**pad)

        tk.Label(form_frame, text="Firebird User:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        fb_user_var = tk.StringVar(value=fb_defaults.get("user", "sysdba"))
        fb_user_entry = tk.Entry(form_frame, textvariable=fb_user_var, **entry_kw)
        fb_user_entry.pack(**pad)

        tk.Label(form_frame, text="Firebird Password:",
                 font=("Segoe UI", 10)).pack(fill="x", **pad)
        fb_pwd_var = tk.StringVar(value=fb_defaults.get("password", "masterkey"))
        fb_pwd_entry = tk.Entry(form_frame, textvariable=fb_pwd_var, show="*", **entry_kw)
        fb_pwd_entry.pack(**pad)

        active_var = tk.BooleanVar(value=company.get("active", False))
        tk.Checkbutton(form_frame, text="Active", variable=active_var,
                       font=("Segoe UI", 10)).pack(anchor="w", **pad)

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill="x", pady=10)

        def refresh_fields(*_args):
            mode = adapter_var.get().strip().lower()
            is_pg = mode == "postgres"
            is_fb = mode == "firebird"
            sqlite_state = "normal" if mode == "sqlite" else "disabled"
            pg_state = "normal" if is_pg else "disabled"
            fb_state = "normal" if is_fb else "disabled"
            dbname_entry.configure(state=sqlite_state)
            for widget in (pg_host_entry, pg_port_entry, pg_db_entry, pg_user_entry, pg_pwd_entry):
                widget.configure(state=pg_state)
            for widget in (fb_host_entry, fb_port_entry, fb_db_entry, fb_user_entry, fb_pwd_entry):
                widget.configure(state=fb_state)

        adapter_box.bind("<<ComboboxSelected>>", refresh_fields)
        refresh_fields()

        result = {"value": None}

        def do_save():
            name = name_var.get().strip()
            ws_host = host_var.get().strip() or "localhost"
            ws_port = port_var.get().strip()
            adapter = adapter_var.get().strip().lower() or "sqlite"
            if not name or not ws_port:
                messagebox.showwarning("Input Required",
                                       "Company name and port are required.",
                                       parent=dialog)
                return
            try:
                int(ws_port)
            except ValueError:
                messagebox.showwarning("Invalid Port",
                                       "WebSocket port must be a number.",
                                       parent=dialog)
                return
            entry = {
                "company": name,
                "db_url": f"ws://{ws_host}:{ws_port}",
                "active": active_var.get(),
            }
            if adapter == "postgres":
                pg_host = pg_host_var.get().strip() or "localhost"
                pg_port = pg_port_var.get().strip() or "5432"
                pg_db = pg_db_var.get().strip()
                pg_user = pg_user_var.get().strip()
                try:
                    int(pg_port)
                except ValueError:
                    messagebox.showwarning("Invalid Port",
                                           "Postgres port must be a number.",
                                           parent=dialog)
                    return
                if not pg_db:
                    messagebox.showwarning("Input Required",
                                           "Postgres database is required.",
                                           parent=dialog)
                    return
                entry.update({
                    "adapter": "postgres",
                    "host": pg_host,
                    "port": int(pg_port),
                    "database": pg_db,
                    "user": pg_user,
                    "password": pg_pwd_var.get(),
                })
            elif adapter == "firebird":
                fb_host = fb_host_var.get().strip() or "localhost"
                fb_port = fb_port_var.get().strip() or "3050"
                fb_db = fb_db_var.get().strip()
                fb_user = fb_user_var.get().strip() or "sysdba"
                try:
                    int(fb_port)
                except ValueError:
                    messagebox.showwarning("Invalid Port",
                                           "Firebird port must be a number.",
                                           parent=dialog)
                    return
                if not fb_db:
                    messagebox.showwarning("Input Required",
                                           "Firebird database path is required.",
                                           parent=dialog)
                    return
                entry.update({
                    "adapter": "firebird",
                    "host": fb_host,
                    "port": int(fb_port),
                    "database": fb_db,
                    "user": fb_user,
                    "password": fb_pwd_var.get() or "masterkey",
                })
            else:
                dbname = dbname_var.get().strip()
                if dbname:
                    entry["db_file"] = dbname
            result["value"] = entry
            dialog.destroy()

        tk.Button(btn_frame, text="Save", command=do_save,
                  bg="#27ae60", fg="white", width=10,
                  font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Cancel", command=dialog.destroy,
                  width=10,
                  font=("Segoe UI", 10)).pack(side="right", padx=5)
        dialog.after(0, _sync_scroll_region)
        self.root.wait_window(dialog)
        return result["value"]

    def _add_company(self):
        entry = self._company_dialog("Add New Company")
        if entry:
            self.companies.append(entry)
            save_companies(self.companies_file, self.companies)
            self._refresh()

    def _on_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._context_menu.tk_popup(event.x_root, event.y_root)

    def _edit_company(self):
        info = self._get_selected()
        if not info:
            return
        idx = next((i for i, c in enumerate(self.companies)
                     if c.get("company") == info["company"]
                     and c.get("db_url") == info["db_url"]), None)
        if idx is None:
            return
        entry = self._company_dialog("Edit Company", self.companies[idx])
        if entry:
            self.companies[idx] = entry
            save_companies(self.companies_file, self.companies)
            self._refresh()

    def _delete_company(self):
        info = self._get_selected()
        if not info:
            return
        if not messagebox.askyesno("Confirm Delete",
                                   f"Remove '{info['company']}' from the list?\n"
                                   "(This does not stop the server or delete data.)",
                                   parent=self.root):
            return
        self.companies = [c for c in self.companies
                          if not (c.get("company") == info["company"]
                                  and c.get("db_url") == info["db_url"])]
        save_companies(self.companies_file, self.companies)
        self._refresh()

    def run(self):
        self.root.mainloop()


# ── Interactive Menu ──────────────────────────────────────────────────────

def interactive_menu(companies_file, companies):
    while True:
        companies = load_companies(companies_file)
        companies = _filter_companies(companies)
        show_status(companies)
        remote = get_remote_companies(companies)

        print("  Commands:")
        print("    1  Start all servers")
        print("    2  Stop all servers")
        print("    3  Start one server")
        print("    4  Stop one server")
        print("    5  Add new company")
        print("    6  Refresh status")
        print("    q  Quit (stops all servers)")
        print()

        try:
            choice = input("  > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if choice == "q":
            stop_all()
            break

        elif choice == "1":
            for _, c in remote:
                start_server(c)

        elif choice == "2":
            stop_all()

        elif choice == "3":
            if not remote:
                print("  No remote companies configured")
                continue
            print("  Select company:")
            for j, (_, c) in enumerate(remote):
                print(f"    {j + 1}  {c['company']} ({c['db_url']})")
            try:
                pick = int(input("  > ").strip()) - 1
                if 0 <= pick < len(remote):
                    _, c = remote[pick]
                    start_server(c)
            except (ValueError, KeyboardInterrupt):
                pass

        elif choice == "4":
            if not remote:
                print("  No remote companies configured")
                continue
            print("  Select company:")
            for j, (_, c) in enumerate(remote):
                print(f"    {j + 1}  {c['company']} ({c['db_url']})")
            try:
                pick = int(input("  > ").strip()) - 1
                if 0 <= pick < len(remote):
                    _, c = remote[pick]
                    stop_server(c)
            except (ValueError, KeyboardInterrupt):
                pass

        elif choice == "5":
            try:
                name = input("  Company name: ").strip()
                adapter = input("  Adapter [sqlite/postgres/firebird] (default sqlite): ").strip().lower() or "sqlite"
                host = input("  WebSocket host (default localhost): ").strip() or "localhost"
                port = input("  WebSocket port (e.g. 8081): ").strip()
                if name and port:
                    db_url = f"ws://{host}:{port}"
                    entry = {"company": name, "db_url": db_url, "active": False}
                    if adapter == "postgres":
                        pg_host = input("  Postgres host (default localhost): ").strip() or "localhost"
                        pg_port = input("  Postgres port (default 5432): ").strip() or "5432"
                        pg_db = input("  Postgres database: ").strip()
                        pg_user = input("  Postgres user: ").strip()
                        pg_pwd = input("  Postgres password: ").strip()
                        entry.update({
                            "adapter": "postgres",
                            "host": pg_host,
                            "port": int(pg_port),
                            "database": pg_db,
                            "user": pg_user,
                            "password": pg_pwd,
                        })
                    elif adapter == "firebird":
                        fb_host = input("  Firebird host (default localhost): ").strip() or "localhost"
                        fb_port = input("  Firebird port (default 3050): ").strip() or "3050"
                        fb_db = input("  Firebird database path: ").strip()
                        fb_user = input("  Firebird user (default sysdba): ").strip() or "sysdba"
                        fb_pwd = input("  Firebird password (default masterkey): ").strip() or "masterkey"
                        entry.update({
                            "adapter": "firebird",
                            "host": fb_host,
                            "port": int(fb_port),
                            "database": fb_db,
                            "user": fb_user,
                            "password": fb_pwd,
                        })
                    companies.append(entry)
                    save_companies(companies_file, companies)
                    print(f"  Added: {name} on {db_url}")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "6":
            pass  # loop refreshes


# ── Foreground mode (run all and block) ────────────────────────────────────

def run_foreground(companies_file):
    """Start all servers and block until Ctrl+C."""
    companies = _filter_companies(load_companies(companies_file))
    remote = get_remote_companies(companies)

    if not remote:
        print("No remote companies to serve.")
        return

    print(f"Starting {len(remote)} server(s)...\n")
    for _, c in remote:
        start_server(c)

    show_status(companies)
    print("Press Ctrl+C to stop all servers.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        stop_all()


# ── CLI Entry Point ───────────────────────────────────────────────────────

def main():
    companies_file = find_companies_file()
    if not companies_file:
        companies_file = "remote_connections.json"
        companies = [
            {"company": "Default", "db_url": "ws://localhost:8081",
             "active": True, "adapter": _ADAPTER_FILTER or "sqlite"}
        ]
        save_companies(companies_file, companies)
        print(f"  Created default config: {companies_file}")
        print(f"  Company: Default  |  ws://localhost:8081")
        print()
    else:
        companies = load_companies(companies_file)
    companies = _filter_companies(companies)
    args = sys.argv[1:]

    # Parse args
    show_gui = True
    hide_console = False
    cmd_args = []
    for a in args:
        al = a.lower()
        if al == "--cli":
            show_gui = False
        elif al == "--hidden":
            hide_console = True
        elif al == "--gui":
            pass  # explicit, no-op since default
        else:
            cmd_args.append(a)

    if show_gui and not cmd_args:
        if not HAS_TK:
            print("Tkinter not available. Install python-tk or use --cli flag.")
            sys.exit(1)
        global _quiet
        _quiet = True
        if hide_console and IS_WINDOWS:
            _kernel32.FreeConsole()
        gui = ServerManagerGUI(companies_file, companies)
        gui.run()
        stop_all()
        return

    if not show_gui and not cmd_args:
        print(f"dbnocode SQL Server  (config: {companies_file})")
        print(f"Data: {DATA_DIR}")
        interactive_menu(companies_file, companies)
        return

    cmd = cmd_args[0].lower() if cmd_args else ""
    name_filter = " ".join(cmd_args[1:]) if len(cmd_args) > 1 else None

    if cmd == "list" or cmd == "status":
        show_status(companies)

    elif cmd == "start":
        remote = get_remote_companies(companies)
        started = 0
        for _, c in remote:
            if name_filter and name_filter.lower() not in c["company"].lower():
                continue
            if start_server(c):
                started += 1
        if started:
            show_status(companies)
            if not name_filter:
                # Start all = foreground mode, block until Ctrl+C
                print("Press Ctrl+C to stop all servers.\n")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nStopping...")
                    stop_all()
        elif name_filter:
            print(f"No company matching '{name_filter}' found")

    elif cmd == "stop":
        # Can only stop servers started by this process
        print("  Note: can only stop servers started by this process")
        stop_all()

    elif cmd == "serve":
        # Alias for start-all-and-block
        run_foreground(companies_file)

    else:
        print(f"Unknown command: {cmd}")
        print()
        print("Usage:")
        print("  python server.py                     GUI mode (default)")
        print("  python server.py --gui --hidden       GUI with hidden console")
        print("  python server.py --cli                Interactive menu (CLI)")
        print("  python server.py serve                Start all and run in foreground")
        print("  python server.py start                Start all servers")
        print("  python server.py start NAME           Start matching company")
        print("  python server.py stop                 Stop all servers")
        print("  python server.py list                 Show companies and status")
        sys.exit(1)


if __name__ == "__main__":
    main()
