import json
import logging
import os
import sqlite3
import time
from typing import List, Dict, Any, Optional

_perf_log = logging.getLogger("perf")
_perf_log.setLevel(logging.DEBUG)
if not _perf_log.handlers:
    _h = logging.FileHandler("errors.log", encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s PERF %(message)s"))
    _perf_log.addHandler(_h)

class SQLiteAdapter:
    def __init__(self, db_path: str = "sample_data.db"):
        self.db_path = db_path
        self.conn = None
        self._in_txn = False  # True between begin() and commit()/rollback()

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def disconnect(self):
        if self.conn: self.conn.close()

    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        if not self.conn: self.connect()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

    def execute(self, sql: str, params: tuple = ()):
        if not self.conn: self.connect()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            # Auto-commit only when not inside an explicit transaction;
            # the caller (begin/commit/rollback) owns commit in that case.
            if not self._in_txn:
                self.conn.commit()
            return cur.rowcount, cur.lastrowid
        finally:
            cur.close()

    # ── Explicit transaction control ────────────────────────────────────
    # Used by the posting/script engines to make multi-statement postings
    # atomic: begin() suspends per-execute auto-commit, commit()/rollback()
    # resume it. Nested begins are ignored (the outermost owns the txn).
    def begin(self):
        if not self.conn: self.connect()
        if not self._in_txn:
            self._in_txn = True
            # sqlite3 starts a transaction implicitly on the next DML; we
            # just suppress the per-execute commit until commit()/rollback().

    def commit(self):
        if self.conn:
            self.conn.commit()
        self._in_txn = False

    def rollback(self):
        if self.conn:
            self.conn.rollback()
        self._in_txn = False

    def get_table_names(self) -> List[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY name")
        return [r["name"] for r in rows]

    def discover_fields(self, table_name: str, limit: int = 50) -> List[str]:
        rows = self.query(
            f'SELECT data FROM "{table_name}" LIMIT ?', (limit,))
        fields = set()
        for r in rows:
            try:
                obj = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                if isinstance(obj, dict):
                    fields.update(obj.keys())
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(f for f in fields if not f.startswith("_"))


class RemoteSQLAdapter:
    """Adapter for a remote libSQL/sqld server via Hrana 2 WebSocket.

    Activated by setting the ``DB_URL`` environment variable to a
    ``ws://`` or ``wss://`` URL pointing at sqld's Hrana port.

    Requires the ``websocket-client`` package.
    """

    def __init__(self, db_url: str, auth_token: str = ""):
        self.db_url = db_url.rstrip("/")
        self.db_path = self.db_url
        self.auth_token = auth_token
        self._ws: Optional[Any] = None
        self._next_req_id = 1
        self._next_stream_id = 1
        self._stream_id: Optional[int] = None
        self._in_txn = False

    # -- display name for company / status UI --------------------------------
    @property
    def name(self) -> str:
        return self.db_url

    # -- Hrana 2 low-level helpers --------------------------------------------
    def _send(self, msg: dict):
        self._ws.send(json.dumps(msg))

    def _recv(self) -> dict:
        raw = self._ws.recv()
        if raw in (None, ""):
            raise RuntimeError("Hrana server closed the connection before sending a response")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Hrana server returned invalid JSON: {raw!r}") from exc

    def _send_request(self, req: dict) -> int:
        rid = self._next_req_id
        self._next_req_id += 1
        self._send({"type": "request", "request_id": rid, "request": req})
        return rid

    def _recv_response(self, expected_rid: int) -> dict:
        for _ in range(200):
            raw = self._recv()
            if raw["type"] == "response_ok":
                if raw["request_id"] == expected_rid:
                    return raw["response"]
            elif raw["type"] == "response_error":
                if raw["request_id"] == expected_rid:
                    err = raw.get("error", {})
                    raise RuntimeError(
                        f"Hrana error (req {raw['request_id']}): {err.get('message', err)}")
            elif raw["type"] == "hello_ok":
                continue
        raise RuntimeError(f"Hrana: no response for request {expected_rid}")

    @staticmethod
    def _to_hrana_value(v: Any) -> dict:
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": str(v)}

    @staticmethod
    def _from_hrana_value(val: dict) -> Any:
        t = val["type"]
        if t == "null":
            return None
        if t == "integer":
            return int(val["value"])
        if t == "float":
            return val["value"]
        if t == "text":
            return val["value"]
        if t == "blob":
            return val["value"]
        return val.get("value")

    def _execute_stmt(self, sql: str, params: tuple = (), want_rows: bool = True) -> dict:
        t0 = time.perf_counter()
        # Auto-reconnect if websocket dropped
        if self._ws is None:
            self.connect()
        stmt: dict = {"sql": sql, "want_rows": want_rows}
        if params:
            stmt["args"] = [self._to_hrana_value(p) for p in params]
        try:
            rid = self._send_request({
                "type": "execute",
                "stream_id": self._stream_id,
                "stmt": stmt,
            })
            response = self._recv_response(rid)
        except RuntimeError:
            raise  # SQL/Hrana errors — don't reconnect
        except Exception:
            if self._in_txn:
                # Inside transaction — reconnect would lose tx state.
                # Raise so caller can retry the whole transaction.
                self._ws = None
                self._stream_id = None
                self._in_txn = False
                raise
            # Connection/transport failure — reconnect and retry once.
            # Catches OSError, AttributeError, WebSocketConnectionClosedException, etc.
            self.connect()
            rid = self._send_request({
                "type": "execute",
                "stream_id": self._stream_id,
                "stmt": stmt,
            })
            response = self._recv_response(rid)
        elapsed = time.perf_counter() - t0
        if elapsed > 0.5:
            _perf_log.debug("SLOW query %.3fs: %s", elapsed, sql[:120])
        return response.get("result", response)

    # -- public interface (mirrors SQLiteAdapter) ----------------------------
    def connect(self):
        t0 = time.perf_counter()
        try:
            from websocket import create_connection
        except ImportError:
            raise RuntimeError(
                "RemoteSQLAdapter requires the 'websocket-client' package. "
                "Install it with: pip install websocket-client")

        try:
            self._ws = create_connection(
                self.db_url,
                subprotocols=["hrana2"],
                timeout=3,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"WebSocket server unreachable: {self.db_url} timed out. "
                "Check the server is running and that the host/port are open "
                "through firewall/router."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"WebSocket connect failed: {self.db_url} ({exc}). "
                "Check the host/IP, port, and firewall."
            ) from exc
        self._send({"type": "hello", "jwt": self.auth_token or None})
        resp = self._recv()
        if resp["type"] != "hello_ok":
            raise RuntimeError(f"Hrana authentication failed: {resp.get('error', resp)}")

        stream_id = self._next_stream_id
        self._next_stream_id += 1
        rid = self._send_request({"type": "open_stream", "stream_id": stream_id})
        self._recv_response(rid)
        self._stream_id = stream_id
        _perf_log.debug("connect %.3fs %s", time.perf_counter() - t0, self.db_url)

    def disconnect(self):
        if self._ws:
            try:
                if self._stream_id is not None:
                    self._send_request({"type": "close_stream", "stream_id": self._stream_id})
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._stream_id = None

    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        result = self._execute_stmt(sql, params, want_rows=True)
        cols = [c.get("name", "") for c in result.get("cols", [])]
        rows = result.get("rows", [])
        return [
            dict(zip(cols, [self._from_hrana_value(v) for v in row]))
            for row in rows
        ]

    def query_many(self, queries: List[tuple]) -> List[List[Dict]]:
        """Execute multiple SELECT queries in a single Hrana batch request.

        Each entry in *queries* is (sql, params).
        Returns a list of result sets, one per query.
        """
        t0 = time.perf_counter()
        if self._ws is None:
            self.connect()
        steps = []
        for sql, params in queries:
            stmt: dict = {"sql": sql, "want_rows": True}
            if params:
                stmt["args"] = [self._to_hrana_value(p) for p in params]
            steps.append({
                "type": "execute",
                "stream_id": self._stream_id,
                "stmt": stmt,
            })
        # Send all as individual requests (Hrana 2 multiplexes on same stream)
        rids = []
        for step in steps:
            rids.append(self._send_request(step))
        # Collect all responses
        results = {}
        pending = set(rids)
        for _ in range(len(rids) * 200):
            if not pending:
                break
            raw = self._recv()
            if raw["type"] == "response_ok" and raw["request_id"] in pending:
                pending.discard(raw["request_id"])
                results[raw["request_id"]] = raw["response"].get("result", raw["response"])
            elif raw["type"] == "response_error" and raw["request_id"] in pending:
                pending.discard(raw["request_id"])
                results[raw["request_id"]] = {"cols": [], "rows": []}
            elif raw["type"] == "hello_ok":
                continue
        out = []
        for rid in rids:
            result = results.get(rid, {"cols": [], "rows": []})
            cols = [c.get("name", "") for c in result.get("cols", [])]
            rows = result.get("rows", [])
            out.append([
                dict(zip(cols, [self._from_hrana_value(v) for v in row]))
                for row in rows
            ])
        _perf_log.debug("query_many %d queries %.3fs", len(queries), time.perf_counter() - t0)
        return out

    def execute_many(self, statements: List[tuple]):
        """Execute multiple non-SELECT statements in parallel.

        Each entry in *statements* is (sql, params).
        Errors on individual statements are silently ignored.
        """
        if not statements:
            return
        t0 = time.perf_counter()
        if self._ws is None:
            self.connect()
        rids = []
        for sql, params in statements:
            stmt: dict = {"sql": sql, "want_rows": False}
            if params:
                stmt["args"] = [self._to_hrana_value(p) for p in params]
            rids.append(self._send_request({
                "type": "execute",
                "stream_id": self._stream_id,
                "stmt": stmt,
            }))
        pending = set(rids)
        for _ in range(len(rids) * 200):
            if not pending:
                break
            raw = self._recv()
            rid = raw.get("request_id")
            if rid in pending:
                pending.discard(rid)
        _perf_log.debug("execute_many %d stmts %.3fs", len(statements), time.perf_counter() - t0)

    def execute(self, sql: str, params: tuple = ()):
        result = self._execute_stmt(sql, params, want_rows=False)
        rowcount = result.get("affected_row_count", 0)
        lastid = result.get("last_insert_rowid")
        if lastid is not None:
            lastid = int(lastid)
        return (rowcount, lastid)

    def begin(self):
        self._execute_stmt("BEGIN", (), want_rows=False)
        self._in_txn = True

    def commit(self):
        if not self._in_txn:
            return
        self._in_txn = False
        self._execute_stmt("COMMIT", (), want_rows=False)

    def rollback(self):
        if not self._in_txn:
            self._in_txn = False
            return
        self._in_txn = False
        try:
            self._execute_stmt("ROLLBACK", (), want_rows=False)
        except Exception:
            pass

    def get_table_names(self) -> List[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY name")
        return [r["name"] for r in rows]

    def discover_fields(self, table_name: str, limit: int = 50) -> List[str]:
        rows = self.query(
            f'SELECT data FROM "{table_name}" LIMIT ?', (limit,))
        fields = set()
        for r in rows:
            try:
                obj = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                if isinstance(obj, dict):
                    fields.update(obj.keys())
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(f for f in fields if not f.startswith("_"))

class FirebirdAdapter:
    """Adapter for Firebird 3.0+ via the fdb driver.

    Tables are created with an explicit ``rowid`` identity column so that
    existing SQL (``SELECT rowid, data FROM ...``, ``WHERE rowid = ?``)
    works without modification.
    """

    def __init__(self, host: str = "localhost", port: int = 3050,
                 db: str = "", user: str = "sysdba", pwd: str = "masterkey"):
        self.host = host
        self.port = port
        self.db = db
        self.db_path = db          # used by runner for db_key
        self.user = user
        self.pwd = pwd
        self.conn = None
        self._in_txn = False
        self._fb_major = 5  # default; updated on connect
        self._use_identity = True  # try IDENTITY syntax first

    @staticmethod
    def _find_fbclient() -> Optional[str]:
        """Locate a 64-bit fbclient.dll from Firebird install directories."""
        import struct
        if struct.calcsize("P") * 8 != 64:
            return None  # only needed for 64-bit Python
        base = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                            "Firebird")
        if not os.path.isdir(base):
            return None
        # Prefer newest version
        for subdir in sorted(os.listdir(base), reverse=True):
            candidate = os.path.join(base, subdir, "fbclient.dll")
            if os.path.isfile(candidate):
                return candidate
        return None

    def connect(self):
        try:
            import fdb
        except ImportError:
            raise RuntimeError(
                "fdb package required for Firebird. Install with: pip install fdb")
        # Avoid the full driver timeout when the remote host is unreachable.
        is_local_host = not self.host or self.host in ("localhost", "127.0.0.1")
        if not is_local_host:
            import socket
            try:
                with socket.create_connection((self.host, self.port), timeout=0.75):
                    pass
            except (OSError, TimeoutError) as exc:
                raise ConnectionError(
                    f"Firebird server unreachable ({self.host}:{self.port})"
                ) from exc
        fb_lib = os.environ.get("FIREBIRD_CLIENT") or self._find_fbclient()
        if fb_lib and not getattr(fdb, '_fbnocode_loaded', False):
            fdb.load_api(fb_lib)
            fdb._fbnocode_loaded = True
        # Resolve relative paths to prevent .fdb landing in System32
        # (Firebird service cwd). For local connections, expand to absolute.
        is_local = is_local_host
        if is_local and self.db and not os.path.isabs(self.db):
            default_dir = os.path.join(os.path.expanduser("~"),
                                       ".dbnocode-data")
            os.makedirs(default_dir, exist_ok=True)
            self.db = os.path.join(default_dir, self.db)
            self.db_path = self.db
        import threading
        result = [None, None]  # [conn, error]

        def _try_connect():
            try:
                result[0] = fdb.connect(
                    host=self.host, port=self.port, database=self.db,
                    user=self.user, password=self.pwd, charset="UTF8")
            except fdb.fbcore.DatabaseError as e:
                if "-902" in str(e):
                    try:
                        if is_local:
                            db_dir = os.path.dirname(self.db)
                            if db_dir:
                                os.makedirs(db_dir, exist_ok=True)
                        result[0] = fdb.create_database(
                            host=self.host, port=self.port, database=self.db,
                            user=self.user, password=self.pwd, charset="UTF8",
                            page_size=16384)
                    except Exception as e2:
                        result[1] = e2
                else:
                    result[1] = e
            except Exception as e:
                result[1] = e

        t = threading.Thread(target=_try_connect, daemon=True)
        t.start()
        t.join(timeout=10)
        if t.is_alive():
            raise ConnectionError(
                f"Firebird connection timed out after 10s ({self.host}:{self.port})")
        if result[1]:
            raise result[1]
        self.conn = result[0]
        # Detect server major version for DDL compatibility
        try:
            ver = getattr(self.conn, 'server_version', '') or ''
            # server_version format: "WI-V5.0.1.1234 Firebird 5.0" or similar
            import re
            vm = re.search(r'(\d+)\.', ver)
            if vm:
                self._fb_major = int(vm.group(1))
        except Exception:
            pass

    def disconnect(self):
        if self.conn:
            try:
                if self._in_txn:
                    self.conn.rollback()
                    self._in_txn = False
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def _rewrite_sql(self, sql: str) -> str:
        """Translate SQLite-isms to Firebird dialect."""
        import re
        stripped = sql.strip()
        upper = stripped.upper()

        # CREATE TABLE IF NOT EXISTS "name" (data TEXT)
        # → check existence, create with rowid identity column
        if upper.startswith("CREATE TABLE IF NOT EXISTS"):
            return self._rewrite_create_table(stripped)

        # Quote column names: unquoted data/rowid → "data"/"rowid"
        # so they match the lowercase quoted columns in the table.
        # Use word-boundary matching to avoid mangling table names or
        # JSON content.  Skip if already quoted.
        sql = re.sub(r'(?<!")(?<!\w)rowid(?!"|\w)', '"rowid"', sql,
                     flags=re.IGNORECASE)
        sql = re.sub(r'(?<!")(?<!\w)data(?!"|\w)', '"data"', sql,
                     flags=re.IGNORECASE)

        return sql

    def _rewrite_create_table(self, sql: str) -> str:
        """Rewrite CREATE TABLE for Firebird column types.

        The fdb driver uses legacy wire protocol that doesn't support
        IF NOT EXISTS or newer DDL syntax. Use plain CREATE TABLE;
        caller catches -607 (table exists).
        """
        import re
        m = re.match(
            r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"([^"]+)"\s*\((.+)\)',
            sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return sql
        table_name = m.group(1)
        # Try IDENTITY first (Firebird 3+); fallback column def stored
        # for retry on syntax error (fdb may not support IDENTITY syntax)
        if self._use_identity:
            cols = ('"rowid" INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, '
                    '"data" BLOB SUB_TYPE TEXT CHARACTER SET UTF8')
        else:
            cols = ('"rowid" INTEGER NOT NULL PRIMARY KEY, '
                    '"data" BLOB SUB_TYPE TEXT CHARACTER SET UTF8')
        return f'CREATE TABLE "{table_name}" ({cols})'

    def _is_conn_dead(self, exc) -> bool:
        """Check if exception indicates a dead connection."""
        s = str(exc)
        return any(k in s for k in ("-902", "connection", "writing data",
                                     "reading data", "shutdown"))

    def _reconnect(self):
        """Close stale connection and reconnect."""
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self.connect()

    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        if not self.conn:
            self.connect()
        sql = self._rewrite_sql(sql)
        for attempt in range(2):
            cur = self.conn.cursor()
            try:
                cur.execute(sql, params)
                if not cur.description:
                    cur.close()
                    return []
                cols = [d[0].lower() for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    decoded = []
                    for v in row:
                        if hasattr(v, 'read'):
                            v = v.read()
                        if isinstance(v, bytes):
                            v = v.decode('utf-8', errors='replace')
                        decoded.append(v)
                    rows.append(dict(zip(cols, decoded)))
                cur.close()
                return rows
            except Exception as e:
                try:
                    cur.close()
                except Exception:
                    pass
                if attempt == 0 and not self._in_txn and self._is_conn_dead(e):
                    self._reconnect()
                    continue
                raise
        return []

    def query_many(self, queries) -> List[List[Dict]]:
        """Execute multiple SELECT queries in a single round-trip.

        For queries with identical column structure (e.g. all 'SELECT rowid,
        data FROM ...'), uses UNION ALL to batch into one query. Otherwise
        falls back to sequential execution with cursor reuse.
        """
        if not self.conn:
            self.connect()
        try:
            return self._query_many_inner(queries)
        except Exception as e:
            if not self._in_txn and self._is_conn_dead(e):
                self._reconnect()
                return self._query_many_inner(queries)
            raise

    def _query_many_inner(self, queries) -> List[List[Dict]]:
        # Optimization: if all queries select same columns (rowid, data),
        # batch via UNION ALL with a _src tag column — single round-trip
        can_union = True
        table_names = []
        for sql, params in queries:
            if params:
                can_union = False
                break
            import re
            m = re.match(
                r'SELECT\s+"?rowid"?\s*,\s*"?data"?\s+FROM\s+"([^"]+)"',
                sql.strip(), re.IGNORECASE)
            if not m:
                can_union = False
                break
            table_names.append(m.group(1))

        if can_union and table_names:
            parts = []
            for t in table_names:
                parts.append(
                    f"SELECT '{t}' AS \"_src\", \"rowid\", \"data\" "
                    f"FROM \"{t}\"")
            union_sql = " UNION ALL ".join(parts)
            cur = self.conn.cursor()
            try:
                cur.execute(union_sql)
                # Split results by _src
                by_table = {t: [] for t in table_names}
                for row in cur.fetchall():
                    src = row[0]
                    rid = row[1]
                    data = row[2]
                    if hasattr(data, 'read'):
                        data = data.read()
                    if isinstance(src, bytes):
                        src = src.decode('utf-8', errors='replace')
                    if isinstance(data, bytes):
                        data = data.decode('utf-8', errors='replace')
                    if src in by_table:
                        by_table[src].append({"rowid": rid, "data": data})
                return [by_table[t] for t in table_names]
            except Exception:
                pass  # fall through to sequential
            finally:
                cur.close()

        # Fallback: sequential with cursor reuse
        results = []
        cur = self.conn.cursor()
        try:
            for sql, params in queries:
                sql = self._rewrite_sql(sql)
                try:
                    cur.execute(sql, params)
                    if not cur.description:
                        results.append([])
                        continue
                    cols = [d[0].lower() for d in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        decoded = []
                        for v in row:
                            if hasattr(v, 'read'):
                                v = v.read()
                            if isinstance(v, bytes):
                                v = v.decode('utf-8', errors='replace')
                            decoded.append(v)
                        rows.append(dict(zip(cols, decoded)))
                    results.append(rows)
                except Exception:
                    results.append([])
        finally:
            cur.close()
        return results

    def _ensure_rowid_gen(self, table_name: str):
        """Create sequence + trigger for auto-increment rowid if missing."""
        gen_name = f"gen_{table_name}_id"
        cur = self.conn.cursor()
        try:
            cur.execute(f'CREATE SEQUENCE "{gen_name}"')
            if not self._in_txn:
                self.conn.commit()
        except Exception:
            pass  # already exists
        try:
            cur.execute(
                f'CREATE TRIGGER "trg_{table_name}_bi" '
                f'FOR "{table_name}" ACTIVE BEFORE INSERT '
                f'AS BEGIN '
                f'IF (NEW."rowid" IS NULL) THEN '
                f'NEW."rowid" = NEXT VALUE FOR "{gen_name}"; '
                f'END')
            if not self._in_txn:
                self.conn.commit()
        except Exception:
            pass  # already exists
        finally:
            cur.close()

    def execute(self, sql: str, params: tuple = ()):
        if not self.conn:
            self.connect()
        sql = self._rewrite_sql(sql)
        upper = sql.strip().upper()

        for attempt in range(2):
            cur = self.conn.cursor()
            try:
                lastrowid = None
                if upper.startswith("CREATE TABLE"):
                    try:
                        cur.execute(sql, params)
                    except Exception as e:
                        err_str = str(e)
                        if "-607" in err_str:
                            import re
                            tm = re.search(r'"([^"]+)"', sql)
                            tname = tm.group(1) if tm else "unknown"
                            self._ensure_rowid_gen(tname)
                            cur.close()
                            return 0, None
                        if "-104" in err_str and self._use_identity:
                            self._use_identity = False
                            import re
                            tm = re.search(r'"([^"]+)"', sql)
                            tname = tm.group(1) if tm else "unknown"
                            fallback = sql.replace(
                                "GENERATED BY DEFAULT AS IDENTITY ", "")
                            cur.close()
                            cur = self.conn.cursor()
                            cur.execute(fallback, params)
                            if not self._in_txn:
                                self.conn.commit()
                            self._ensure_rowid_gen(tname)
                        else:
                            raise
                elif upper.startswith("INSERT") and "RETURNING" not in upper:
                    sql_ret = sql.rstrip().rstrip(";") + ' RETURNING "rowid"'
                    cur.execute(sql_ret, params)
                    row = cur.fetchone()
                    if row:
                        lastrowid = row[0]
                else:
                    cur.execute(sql, params)
                if not self._in_txn:
                    self.conn.commit()
                rc = cur.rowcount
                cur.close()
                return rc, lastrowid
            except Exception as e:
                try:
                    cur.close()
                except Exception:
                    pass
                if attempt == 0 and not self._in_txn and self._is_conn_dead(e):
                    self._reconnect()
                    continue
                raise
        return 0, None

    def execute_many(self, stmts):
        """Execute multiple statements in a single transaction."""
        if not self.conn:
            self.connect()
        was_in_txn = self._in_txn
        self._in_txn = True  # suppress per-statement commits
        try:
            for sql, params in stmts:
                self.execute(sql, params)
            if not was_in_txn:
                self.conn.commit()
        except Exception:
            if not was_in_txn:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            self._in_txn = was_in_txn

    def begin(self):
        if not self.conn:
            self.connect()
        if not self._in_txn:
            self._in_txn = True
            # fdb auto-starts transactions; we just suppress per-execute commit

    def commit(self):
        if self.conn:
            self.conn.commit()
        self._in_txn = False

    def rollback(self):
        if self.conn:
            self.conn.rollback()
        self._in_txn = False

    def get_table_names(self) -> List[str]:
        rows = self.query(
            "SELECT TRIM(RDB$RELATION_NAME) AS name FROM RDB$RELATIONS "
            "WHERE RDB$SYSTEM_FLAG = 0 AND RDB$RELATION_TYPE = 0 "
            "AND RDB$RELATION_NAME NOT STARTING WITH '_' "
            "ORDER BY RDB$RELATION_NAME")
        return [r["name"] for r in rows]

    def discover_fields(self, table_name: str, limit: int = 50) -> List[str]:
        rows = self.query(
            f'SELECT FIRST {int(limit)} "data" FROM "{table_name}"')
        fields = set()
        for r in rows:
            try:
                raw = r.get("data", "")
                if hasattr(raw, "read"):
                    raw = raw.read()
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', errors='replace')
                obj = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(obj, dict):
                    fields.update(obj.keys())
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(f for f in fields if not f.startswith("_"))


class PostgreSQLAdapter:
    def __init__(self, host: str, port: int, db: str, user: str, pwd: str):
        self.host = host; self.port = port; self.db = db; self.user = user; self.pwd = pwd
        self.conn = None
        self._in_txn = False

    def connect(self):
        try: import psycopg2
        except ImportError: raise RuntimeError("psycopg2 required for PostgreSQL")
        self.conn = psycopg2.connect(host=self.host, port=self.port, dbname=self.db, user=self.user, password=self.pwd)

    def disconnect(self):
        if self.conn: self.conn.close()

    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        if not self.conn: self.connect()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def execute(self, sql: str, params: tuple = ()):
        if not self.conn: self.connect()
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            if not self._in_txn:
                self.conn.commit()
            return cur.rowcount, getattr(cur, 'lastrowid', None)
        finally:
            cur.close()

    def begin(self):
        if not self.conn: self.connect()
        if not self._in_txn:
            self._in_txn = True
            # psycopg2 is implicitly in a transaction after connect; we
            # just suppress per-execute commit until commit()/rollback().

    def commit(self):
        if self.conn:
            self.conn.commit()
        self._in_txn = False

    def rollback(self):
        if self.conn:
            self.conn.rollback()
        self._in_txn = False

    def get_table_names(self) -> List[str]:
        rows = self.query(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT LIKE '\\_%' ORDER BY tablename")
        return [r["tablename"] for r in rows]

    def discover_fields(self, table_name: str, limit: int = 50) -> List[str]:
        rows = self.query(
            f'SELECT data FROM "{table_name}" LIMIT %s', (limit,))
        fields = set()
        for r in rows:
            try:
                obj = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                if isinstance(obj, dict):
                    fields.update(obj.keys())
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(f for f in fields if not f.startswith("_"))
