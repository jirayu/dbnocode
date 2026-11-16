"""FBServerDocStore.py — Firebird DocStore-only entry point for dbnocode server.

The Hrana 2 server implementation (SQLite + PostgreSQL + Firebird + DocStore)
lives in `server.py`. This module restricts the shared server to companies
whose adapter is "firebird-docstore", so you can run a dedicated server that
only serves nested master/detail/sub-detail document databases.

Usage:
    python FBServerDocStore.py                  Interactive menu
    python FBServerDocStore.py start MyCo       Start just this company
    python FBServerDocStore.py list             Show docstore companies

Packaged as dbnocode-fbserver-docstore.exe via build_all.bat.
"""

import server

# Restrict the shared server to Firebird DocStore companies only.
server._ADAPTER_FILTER = "firebird-docstore"


if __name__ == "__main__":
    server.main()
