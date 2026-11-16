"""Thai memo store — 'secret memo' side data linked to DB records.

Thai typed via Alt-L is stored here, keyed by (table, record rowid, field),
NEVER in the visible field value — so on-screen data entry stays pure
English/ASCII and Thai typing cannot clobber the record. The memo is used
only at print time (tui/form_pdf.py renders it under the English value).

Storage: a small `thai_memo` table created on demand in the app database
through the adapter layer (works on SQLite / libSQL / Postgres / Firebird),
with a JSON side file as fallback mirror when the DB is unavailable.
"""
import datetime
import json
import os

_SIDE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "thai_memo_side.json")

_TABLE_OK = {}       # id(db) -> True/False (DDL attempted and succeeded?)
_MEMO_KEY_LEN = 150
_THAI_LEN = 2000


def memo_key(table: str, rid, field: str) -> str:
    """Stable key for one Thai memo: table:rowid:field."""
    return f"{table}:{rid}:{field}"


def _side_load() -> dict:
    try:
        with open(_SIDE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _side_save(data: dict) -> None:
    try:
        with open(_SIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
    except Exception:
        pass  # fallback mirror is best-effort


def _side_set(key: str, text: str) -> None:
    data = _side_load()
    if text:
        data[key] = text
    else:
        data.pop(key, None)
    _side_save(data)


def _ensure_table(db) -> bool:
    """Create thai_memo once per adapter instance. Returns True when the
    table is usable (False → caller falls back to the JSON mirror)."""
    if db is None:
        return False
    tag = id(db)
    if tag in _TABLE_OK:
        return _TABLE_OK[tag]
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS thai_memo ("
            "memo_key VARCHAR(150) PRIMARY KEY, "
            "thai_text VARCHAR(2000), "
            "updated_at VARCHAR(32))")
        _TABLE_OK[tag] = True
    except Exception:
        # Firebird reports "already exists" as an error too — treat as OK
        try:
            db.query("SELECT memo_key FROM thai_memo WHERE 1=0")
            _TABLE_OK[tag] = True
        except Exception:
            _TABLE_OK[tag] = False
    return _TABLE_OK[tag]


def set_thai(db, table: str, rid, field: str, text: str) -> bool:
    """Store one Thai memo (empty text deletes it). Returns True when it was
    written to the DB; False means only the JSON fallback holds it."""
    key = memo_key(table, rid, field)
    text = (text or "").strip()
    _side_set(key, text)  # mirror always — DB failure never loses data
    if not _ensure_table(db):
        return False
    try:
        db.execute("DELETE FROM thai_memo WHERE memo_key = ?", (key,))
        if text:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO thai_memo (memo_key, thai_text, updated_at) "
                "VALUES (?, ?, ?)", (key, text[:_THAI_LEN], now))
        return True
    except Exception:
        return False


def get_thai(db, table: str, rid, field: str) -> str:
    key = memo_key(table, rid, field)
    if _ensure_table(db):
        try:
            rows = db.query(
                "SELECT thai_text FROM thai_memo WHERE memo_key = ?", (key,))
            if rows:
                return str(rows[0].get("thai_text") or "")
        except Exception:
            pass
    return _side_load().get(key, "")


def get_all(db) -> dict:
    """All memos as {memo_key: thai_text}. DB first, JSON fallback merged
    underneath (DB wins — it is the authoritative store)."""
    merged = _side_load()
    if _ensure_table(db):
        try:
            for row in db.query("SELECT memo_key, thai_text FROM thai_memo"):
                k = str(row.get("memo_key") or "")
                if k:
                    merged[k] = str(row.get("thai_text") or "")
        except Exception:
            pass
    return merged
