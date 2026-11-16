"""pytest suite for docstore.py — Firebird JSON document store.

Covers: save/load, same-line vs different-line matches, 3-level nesting,
sub-line scoped to its own line (ARR linking), optimistic concurrency error,
reindex_all after REGISTRY changes, delete, and header + numeric queries.

Run:  pytest tests/test_docstore.py -q

Requires firebird-driver and a reachable Firebird instance (local embedded
or TCP).  A temporary .fdb file is created under pytest's tmp_path for each
test.  If Firebird cannot be reached, DB-backed tests are skipped.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docstore import (  # noqa: E402
    ConcurrencyError,
    DocStore,
    REGISTRY,
    flatten,
    scope_of,
)

# ---------------------------------------------------------------------------
# Pure-Python unit tests (no database needed)
# ---------------------------------------------------------------------------


def test_flatten_root_scalars():
    doc = {"customer": "Somchai", "date": "2026-11-07", "total": 1500}
    leaves = flatten(doc)
    assert ("customer", "", "Somchai") in leaves
    assert ("date", "", "2026-11-07") in leaves
    assert ("total", "", 1500) in leaves


def test_flatten_lines_with_subs():
    doc = {
        "customer": "Somchai",
        "lines": [
            {"sku": "A1", "qty": 2, "subs": [{"code": "X", "qty": 5}]},
            {"sku": "B7", "qty": 9, "subs": [{"code": "Y", "qty": 1}, {"code": "X", "qty": 8}]},
        ],
    }
    leaves = flatten(doc)
    # Line level: arr identifies the line instance
    assert ("lines[].sku", "lines.0", "A1") in leaves
    assert ("lines[].qty", "lines.0", 2) in leaves
    assert ("lines[].sku", "lines.1", "B7") in leaves
    assert ("lines[].qty", "lines.1", 9) in leaves
    # Sub level: arr identifies the sub-line instance within its line
    assert ("lines[].subs[].code", "lines.0.subs.0", "X") in leaves
    assert ("lines[].subs[].qty", "lines.0.subs.0", 5) in leaves
    assert ("lines[].subs[].code", "lines.1.subs.0", "Y") in leaves
    assert ("lines[].subs[].code", "lines.1.subs.1", "X") in leaves
    assert ("lines[].subs[].qty", "lines.1.subs.1", 8) in leaves


def test_flatten_wanted_subset():
    doc = {"customer": "Somchai", "lines": [{"sku": "A1", "qty": 2}]}
    leaves = flatten(doc, wanted={"lines[].sku", "customer"})
    paths = {p for p, _, _ in leaves}
    assert paths == {"customer", "lines[].sku"}


def test_scope_of():
    assert scope_of("customer") == ""
    assert scope_of("lines[].sku") == "lines[]"
    assert scope_of("lines[].subs[].code") == "lines[].subs[]"
    assert scope_of("lines[].subs[].qty") == "lines[].subs[]"


# ---------------------------------------------------------------------------
# Migration tests (no database needed — uses a fake adapter)
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Stand-in for an adapter with query() returning [{"rowid", "data"}].

    Simulates dsl_lib.adapters.FirebirdAdapter.query() output without a DB.
    """

    def __init__(self, rows):
        self.rows = rows
        self.called_with = None

    def query(self, sql, params=()):
        self.called_with = sql
        return self.rows


class _FakeCon:
    """Stand-in for a firebird-driver connection with commit()."""

    def commit(self):
        pass


def test_migrate_from_flat_basic():
    """Rows from a flat table become docstore documents under one entity."""
    store = DocStore.__new__(DocStore)
    store._con = _FakeCon()
    store.dsn = ""
    store.user = "sysdba"
    store.password = "masterkey"

    captured = {}

    def fake_save(entity, doc, doc_id=None, ver=None, commit=True):
        captured.setdefault("saves", []).append((entity, doc, doc_id, ver, commit))
        captured["next_id"] = captured.get("next_id", 0) + 1
        return captured["next_id"], 1

    store.save = fake_save

    adapter = _FakeAdapter([
        {"rowid": 1, "data": json.dumps({"customer": "A", "qty": 10})},
        {"rowid": 2, "data": json.dumps({"customer": "B", "qty": 20})},
        {"rowid": 3, "data": '{"customer": "C", "qty": 30}'},  # string, not bytes
    ])

    migrated, errors = store.migrate_from_flat(adapter, "po", "po")

    assert migrated == 3
    assert errors == 0
    assert adapter.called_with == 'SELECT rowid, data FROM "po"'

    saves = captured["saves"]
    assert len(saves) == 3
    # legacy rowid preserved under _legacy_rowid
    assert saves[0][1]["_legacy_rowid"] == 1
    assert saves[0][1]["customer"] == "A"
    assert saves[1][1]["_legacy_rowid"] == 2
    assert saves[2][1]["_legacy_rowid"] == 3


def test_migrate_from_flat_skips_bad_rows():
    """Non-dict JSON and missing data are counted as errors, not crashes."""
    store = DocStore.__new__(DocStore)
    store._con = _FakeCon()
    store.dsn = ""
    store.user = "sysdba"
    store.password = "masterkey"

    store.save = lambda *a, **k: (1, 1)

    adapter = _FakeAdapter([
        {"rowid": 1, "data": json.dumps({"customer": "A"})},
        {"rowid": 2, "data": "[1,2,3]"},           # not a dict -> error
        {"rowid": 3, "data": "not json at all"},   # bad json -> error
        {"rowid": 4, "data": None},                 # None -> error
    ])

    migrated, errors = store.migrate_from_flat(adapter, "item", "item")

    assert migrated == 1
    assert errors == 3


def test_migrate_from_flat_no_legacy_field():
    """legacy_field=None skips storing the original rowid."""
    store = DocStore.__new__(DocStore)
    store._con = _FakeCon()
    store.dsn = ""
    store.user = "sysdba"
    store.password = "masterkey"

    saves = []
    store.save = lambda entity, doc, **k: saves.append(doc) or (1, 1)

    adapter = _FakeAdapter([
        {"rowid": 42, "data": json.dumps({"customer": "X"})},
    ])

    migrated, errors = store.migrate_from_flat(adapter, "t", "t",
                                               legacy_field=None)

    assert migrated == 1
    assert errors == 0
    assert "_legacy_rowid" not in saves[0]


# ---------------------------------------------------------------------------
# Database-backed tests (Firebird required)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Temporary Firebird document store with schema created."""
    fdb_path = str(tmp_path / "test.fdb")
    store = DocStore(fdb_path, user="SYSDBA", password="masterkey")
    try:
        store.create_schema()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Cannot connect to Firebird at {fdb_path}: {exc}")
    yield store
    store.close()


def _backup_registry():
    return {k: set(v) if v is not None else None for k, v in REGISTRY.items()}


def _restore_registry(backup):
    REGISTRY.clear()
    REGISTRY.update(backup)


def test_save_and_load(db):
    doc_id, ver = db.save("order", {
        "customer": "Somchai",
        "date": "2026-11-07",
        "total": 1500,
    })
    assert ver == 1

    loaded = db.load(doc_id)
    assert loaded is not None
    assert loaded["id"] == doc_id
    assert loaded["entity"] == "order"
    assert loaded["ver"] == 1
    assert loaded["data"]["customer"] == "Somchai"
    assert loaded["data"]["date"] == "2026-11-07"
    assert loaded["data"]["total"] == 1500

    assert db.load(999999) is None


def test_same_line_vs_different_line(db):
    """Same-scope conditions must match the same array element (equal ARR)."""
    doc_id, _ = db.save("order", {
        "customer": "Somchai",
        "lines": [
            {"sku": "A1", "qty": 2},
            {"sku": "B7", "qty": 9},
        ],
    })

    # Same line: sku B7 AND qty > 5 -> line 1 matches both
    ids = db.find("order", [
        ("lines[].sku", "=", "B7"),
        ("lines[].qty", ">", 5),
    ])
    assert doc_id in ids

    # Different line: sku A1 AND qty > 5 -> no single line has both
    ids = db.find("order", [
        ("lines[].sku", "=", "A1"),
        ("lines[].qty", ">", 5),
    ])
    assert doc_id not in ids


def test_three_level_nesting(db):
    """lines[].subs[].code and lines[].subs[].qty at depth 3."""
    doc_id, _ = db.save("order", {
        "customer": "Somchai",
        "lines": [
            {"sku": "A1", "subs": [{"code": "X", "qty": 5}]},
            {"sku": "B7", "subs": [{"code": "Y", "qty": 1}, {"code": "X", "qty": 8}]},
        ],
    })

    ids = db.find("order", [("lines[].subs[].code", "=", "X")])
    assert doc_id in ids

    ids = db.find("order", [("lines[].subs[].qty", ">=", 8)])
    assert doc_id in ids

    ids = db.find("order", [("lines[].subs[].qty", "<", 2)])
    assert doc_id in ids  # line 1 subs[0].qty = 1


def test_sub_line_linked_to_own_line(db):
    """Sub-line must belong to the matched line (ARR STARTING WITH parent)."""
    # Doc 1: line B7 has sub X -> matches "B7 + sub X"
    doc1_id, _ = db.save("order", {
        "customer": "C1",
        "lines": [{"sku": "B7", "subs": [{"code": "X", "qty": 1}]}],
    })
    # Doc 2: line B7 has NO sub X, but line A1 has sub X -> should NOT match
    doc2_id, _ = db.save("order", {
        "customer": "C2",
        "lines": [
            {"sku": "B7", "subs": [{"code": "Y", "qty": 1}]},
            {"sku": "A1", "subs": [{"code": "X", "qty": 5}]},
        ],
    })

    ids = db.find("order", [
        ("lines[].sku", "=", "B7"),
        ("lines[].subs[].code", "=", "X"),
    ])
    assert doc1_id in ids
    assert doc2_id not in ids


def test_header_and_deep_value(db):
    """Mix of root-level and deep conditions."""
    doc_id, _ = db.save("order", {
        "customer": "Somchai",
        "date": "2026-11-07",
        "lines": [
            {"sku": "A1", "qty": 2},
            {"sku": "B7", "qty": 9},
        ],
    })

    ids = db.find("order", [
        ("customer", "=", "Somchai"),
        ("lines[].subs[].qty", ">=", 8),
    ])
    # No subs in this doc, so no match
    assert doc_id not in ids

    ids = db.find("order", [("customer", "=", "Somchai")])
    assert doc_id in ids

    ids = db.find("order", [("customer", "=", "Unknown")])
    assert doc_id not in ids


def test_concurrency_error(db):
    """Optimistic locking: stale VER raises ConcurrencyError."""
    doc_id, ver = db.save("order", {"customer": "A"})
    assert ver == 1

    # Same ver succeeds, ver increments
    new_id, new_ver = db.save("order", {"customer": "B"}, doc_id=doc_id, ver=ver)
    assert new_id == doc_id
    assert new_ver == 2

    # Stale ver fails
    with pytest.raises(ConcurrencyError):
        db.save("order", {"customer": "C"}, doc_id=doc_id, ver=1)

    # Reload confirms ver is now 2
    loaded = db.load(doc_id)
    assert loaded["ver"] == 2
    assert loaded["data"]["customer"] == "B"


def test_delete(db):
    """Delete removes the document and its index rows (cascade)."""
    doc_id, _ = db.save("order", {"customer": "Temp", "lines": [{"sku": "X1"}]})
    assert db.load(doc_id) is not None

    db.delete(doc_id)
    assert db.load(doc_id) is None

    # Index rows should be gone too: no doc matches sku X1 anymore
    ids = db.find("order", [("lines[].sku", "=", "X1")])
    assert doc_id not in ids


def test_reindex_all_picks_up_new_path():
    """reindex_all indexes existing docs with a REGISTRY path added later."""
    backup = _backup_registry()
    try:
        REGISTRY["order"] = {"customer", "lines[].sku", "lines[].priority"}

        fdb_path = os.path.join(os.path.dirname(__file__), "_reindex_test.fdb")
        store = DocStore(fdb_path, user="SYSDBA", password="masterkey")
        try:
            store.create_schema()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Cannot connect to Firebird at {fdb_path}: {exc}")
        try:
            # Save with priority NOT yet indexed (REGISTRY already has it here,
            # so save WILL index it — instead we test the scenario where a doc
            # was saved before the path existed, then reindex_all is called).
            doc_id, _ = store.save("order", {
                "customer": "Somchai",
                "lines": [{"sku": "A1", "priority": "urgent"}],
            })

            # Now simulate adding an entirely NEW path after the fact
            REGISTRY["order"] = {"customer", "lines[].sku", "lines[].priority", "lines[].notes"}

            # Before reindex: notes path not indexed for existing docs
            ids = store.find("order", [("lines[].notes", "=", "urgent")])
            assert doc_id not in ids

            # After reindex: notes path indexed (but no doc has notes, so still empty)
            store.reindex_all("order")
            ids = store.find("order", [("lines[].notes", "=", "urgent")])
            assert doc_id not in ids  # no doc actually has notes="urgent"

            # But priority (which was indexed at save) still works
            ids = store.find("order", [("lines[].priority", "=", "urgent")])
            assert doc_id in ids
        finally:
            store.close()
            if os.path.exists(fdb_path):
                os.remove(fdb_path)
    finally:
        _restore_registry(backup)


def test_reindex_all_after_save_without_path():
    """Doc saved before path in REGISTRY becomes findable after reindex_all."""
    backup = _backup_registry()
    try:
        REGISTRY["order"] = {"customer", "lines[].sku"}

        fdb_path = os.path.join(os.path.dirname(__file__), "_reindex_test2.fdb")
        store = DocStore(fdb_path, user="SYSDBA", password="masterkey")
        try:
            store.create_schema()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Cannot connect to Firebird at {fdb_path}: {exc}")
        try:
            doc_id, _ = store.save("order", {
                "customer": "Somchai",
                "lines": [{"sku": "A1", "priority": "urgent"}],
            })
            # priority was NOT in REGISTRY at save time -> not indexed
            ids = store.find("order", [("lines[].priority", "=", "urgent")])
            assert doc_id not in ids

            # Add path and reindex
            REGISTRY["order"] = {"customer", "lines[].sku", "lines[].priority"}
            store.reindex_all("order")

            ids = store.find("order", [("lines[].priority", "=", "urgent")])
            assert doc_id in ids
        finally:
            store.close()
            if os.path.exists(fdb_path):
                os.remove(fdb_path)
    finally:
        _restore_registry(backup)


def test_find_empty_conds_returns_all(db):
    """find with no conditions returns all docs of the entity (up to limit)."""
    id1, _ = db.save("order", {"customer": "A"})
    id2, _ = db.save("order", {"customer": "B"})
    id3, _ = db.save("grn", {"po_id": id1})

    ids = db.find("order", [])
    assert id1 in ids
    assert id2 in ids
    assert id3 not in ids  # different entity