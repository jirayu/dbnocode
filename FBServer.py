"""FBServer.py — Firebird-only entry point for the unified dbnocode server.

The Hrana 2 server implementation (SQLite + PostgreSQL + Firebird) now lives
in `server.py`. This module is kept only so the dedicated `dbnocode-fbserver`
executable preserves its original Firebird-only behaviour: it launches the
shared server restricted to companies whose adapter is "firebird".

There is no duplicated server logic here any more; all protocol handling,
SQL rewriting, transaction management and the Tk GUI are owned by `server.py`.
If you want to serve every backend from one process, run `server.py` directly.
"""

import server

# Restrict the shared server to Firebird companies only.
server._ADAPTER_FILTER = "firebird"


if __name__ == "__main__":
    server.main()
