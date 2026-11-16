"""Headless regression harness for the detail-grid F4 lookup path.

Fakes just enough of curses to build the real Grid / SearchDialog and drive F4
with a scripted key sequence, so the lookup -> pick -> fill chain is exercised
without a terminal. Guards the regression where F4 on PO/GRN/SO/DO lines
silently did nothing.

Run:  python tests/test_f4_lookup_smoke.py
"""
import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeWin:
    keys = []          # shared keyboard queue for every fake window

    def __init__(self, h=40, w=180):
        self.h, self.w = h, w
        self.written = []

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.written.clear()

    def clear(self):
        self.written.clear()

    def box(self):
        pass

    def border(self):
        pass

    def attron(self, *_a):
        pass

    def attroff(self, *_a):
        pass

    def attrset(self, *_a):
        pass

    def bkgd(self, *_a):
        pass

    def keypad(self, _v):
        pass

    def nodelay(self, _v):
        pass

    def timeout(self, _v):
        pass

    def touchwin(self):
        pass

    def addstr(self, y, x, text, *_attr):
        self.written.append((y, x, text))

    def move(self, y, x):
        pass

    def refresh(self):
        pass

    def getch(self):
        return FakeWin.keys.pop(0) if FakeWin.keys else 27


def patch_curses():
    curses.newwin = lambda h, w, y, x: FakeWin(h, w)
    curses.color_pair = lambda _n: 0
    curses.curs_set = lambda _n: 0
    curses.mousemask = lambda _m: 0
    curses.init_pair = lambda *_a: 0
    curses.start_color = lambda: None
    curses.use_default_colors = lambda: None


patch_curses()

from tui.cxgrid import Grid, GridColumn            # noqa: E402
from tui.layout import LayoutRenderer              # noqa: E402
from tui.widgets import SearchDialog               # noqa: E402

ITEMS = [
    {"rowid": 1, "part_no": "ITM-00001", "part_name": "Cocobase Water Soluble",
     "uom": "KG", "unit_cost": 14, "pack_uom": "BAG", "pack_qty": 25,
     "recv_uom": "KG", "pack_cost": 350, "base_unit_cost": 14, "on_hand": 0},
    {"rowid": 2, "part_no": "ITM-00002", "part_name": "Butter Pecan Pallette",
     "uom": "KG", "unit_cost": 14, "pack_uom": "BAG", "pack_qty": 25,
     "recv_uom": "KG", "pack_cost": 350, "base_unit_cost": 14, "on_hand": 0},
    {"rowid": 3, "part_no": "ITM-00003", "part_name": "Vanilla Cream Basemix",
     "uom": "KG", "unit_cost": 14.5, "pack_uom": "BAG", "pack_qty": 25,
     "recv_uom": "KG", "pack_cost": 362.5, "base_unit_cost": 14.5, "on_hand": 0},
]
LOOKUPS = {"item": ITEMS}


class Stub:
    """Minimal host for LayoutRenderer._make_lookup_handler."""
    lookup_data = LOOKUPS
    lookup_loader = None
    _lookup_reload_at = 0.0


def make_handler():
    return LayoutRenderer._make_lookup_handler(Stub())


def test_fields():
    col = GridColumn("part_no", "Part No", 12, ["lookup:item"])
    col.lookup_list_cols = [{"id": "part_no"}, {"id": "part_name"}]
    assert Grid._lookup_key_field(col, ITEMS[0]) == "part_no", "key field"
    assert Grid._lookup_display_field(ITEMS[0], "part_no") == "part_name", "display"
    print("ok  key/display field detection: part_no -> part_name")


def test_search_dialog_direct():
    win = FakeWin()
    FakeWin.keys = [ord('v'), ord('a'), ord('n'), 10]
    row = SearchDialog.show(win, 5, 5, 20, ITEMS, "part_name", "part_no",
                            title=" Item Lookup ", list_columns=None,
                            current_value="", initial_search="")
    assert row is not None, "dialog returned nothing"
    assert row["part_no"] == "ITM-00003", row
    print("ok  SearchDialog: typed 'van' + Enter ->", row["part_no"])


def test_search_dialog_initial_search():
    win = FakeWin()
    FakeWin.keys = [10]
    row = SearchDialog.show(win, 5, 5, 20, ITEMS, "part_name", "part_no",
                            title=" Item Lookup ", list_columns=None,
                            current_value="", initial_search="butter")
    assert row is not None, "dialog returned nothing"
    assert row["part_no"] == "ITM-00002", row
    print("ok  SearchDialog: initial_search='butter' ->", row["part_no"])


def make_grid(cols, rows, handler, list_cols=None):
    for c in cols:
        c.lookup_list_cols = list_cols
    g = Grid(FakeWin(), cols, rows, lookup_handler=handler,
             start_y=20, start_x=1, show_totals=True, max_height=18)
    g._grid_id = "grn_line_grid"
    return g


def build_renderer(rows):
    """Build the real GRN form renderer from scripts/main.dsl."""
    import main as M
    text = M._load_dsl_with_includes("scripts/main.dsl")
    from dsl_lib import CompactDSLParser
    app = CompactDSLParser(text).parse()
    layout = app["layouts"]["add_grn"]
    grid_id = layout["tabs"]["items"][0]["grid"]
    renderer = LayoutRenderer(FakeWin(), app["forms"], app["grids"], layout,
                              LOOKUPS, {grid_id: rows},
                              edit_context=None, db=None,
                              app_grids=app["grids"],
                              lookup_loader=lambda: LOOKUPS)
    return renderer, grid_id


def test_grid_f4_fills_row():
    """End-to-end: real tab grid + real flags/callbacks + scripted keypresses."""
    empty = {"part_no": "", "part_name": "", "uom": "", "order_qty": 0,
             "received_qty": 25, "lot_no": "", "unit_cost": 0, "amount": 0}
    renderer, _grid_id = build_renderer([dict(empty)])
    grid = renderer.tab_grids[0]
    row = grid.data[0]
    grid.col = next(i for i, c in enumerate(grid.columns) if c.name == "part_no")
    FakeWin.keys = [ord('v'), ord('a'), ord('n'), 10]
    grid._open_lookup()
    assert row["part_no"] == "ITM-00003", row
    assert row["part_name"] == "Vanilla Cream Basemix", row
    assert str(row["uom"]).upper() == "KG", row
    assert float(row["unit_cost"] or 0) == 14.5, row
    print("ok  real GRN line F4 ->", row["part_no"], "|", row["part_name"],
          "| uom", row["uom"], "| cost", row["unit_cost"])


def test_grid_f4_cancel_keeps_row():
    handler = make_handler()
    col = GridColumn("part_no", "Part No", 12, ["lookup:item"])
    raw = {"part_no": "", "part_name": ""}
    grid = make_grid([col], [raw], handler)
    FakeWin.keys = [27]  # ESC
    grid._open_lookup()
    assert raw["part_no"] == "", raw
    print("ok  Grid F4: ESC cancels without touching the row")


def test_grid_f4_no_lookup_column():
    col = GridColumn("notes", "Notes", 20, [])
    grid = make_grid([col], [{"notes": ""}], make_handler())
    grid._open_lookup()
    assert grid.get_status() == "Column 'Notes' has no lookup", grid.get_status()
    print("ok  Grid F4 on plain column reports:", grid.get_status())


def test_grid_f4_empty_lookup():
    class EmptyHost:
        lookup_data = {}
        lookup_loader = None
        _lookup_reload_at = 0.0

        def _refresh_lookup_data(self, name):
            return []

    col = GridColumn("part_no", "Part No", 12, ["lookup:item"])
    grid = Grid(FakeWin(), [col], [{"part_no": ""}],
                lookup_handler=LayoutRenderer._make_lookup_handler(EmptyHost()),
                start_y=20, start_x=1, max_height=18)
    grid._open_lookup()
    assert "records" in grid.get_status(), grid.get_status()
    print("ok  Grid F4 with empty lookup reports:", grid.get_status())


def test_lookup_cache_heals():
    """A lookup cache built while the DB was unavailable must recover on F4."""
    _renderer, grid_id = build_renderer([dict(ITEMS[0])])
    app = _renderer.form_defs, _renderer.grid_defs
    layout = _renderer.layout_def
    healed = LayoutRenderer(FakeWin(), app[0], app[1], layout,
                            {}, {grid_id: [dict(ITEMS[0])]},
                            edit_context=None, db=None,
                            app_grids=app[1],
                            lookup_loader=lambda: dict(LOOKUPS))
    grid = healed.tab_grids[0]
    rows = grid.lookup_handler("item", None, list_all=True, full_record=True)
    assert rows and rows[0]["part_no"] == "ITM-00001", rows
    print("ok  empty lookup cache healed on demand ->", len(rows), "rows")


def test_renderer_tab_grids_wired():
    """The real parser output must give the detail grid a lookup handler."""
    renderer, grid_id = build_renderer([dict(ITEMS[0])])
    grid = renderer.tab_grids[0]
    assert grid is not None, "no tab grid"
    assert grid.lookup_handler is not None, "lookup_handler not wired"
    assert grid.on_cell_edit is not None, "on_cell_edit not wired"
    part_col = next(c for c in grid.columns if c.name == "part_no")
    assert part_col.lookup == "item", part_col.lookup
    assert part_col.lookup_list_cols, "lookup_list_cols not wired"
    assert [c["id"] for c in part_col.lookup_list_cols][:2] == ["part_no", "part_name"]
    assert part_col.lookup_fill_map, "DSL fill map not parsed"
    print("ok  renderer: tab grid F4 wired for", grid_id,
          "| list cols:", len(part_col.lookup_list_cols),
          "| fill:", part_col.lookup_fill_map)


def test_firebird_union_padded_src_tags():
    """Firebird returns UNION literals CHAR-padded ('item     ').

    Regression: the batched lookup loader dropped every padded table, so the
    lookup cache was empty for item/customer/po/vendor and F4 found nothing.
    """
    from dsl_lib.adapters import FirebirdAdapter

    class FakeCursor:
        description = [("_src",), ("rowid",), ("data",)]

        def execute(self, _sql):
            pass

        def fetchall(self):
            return [
                ("item     ", 1, '{"part_no": "ITM-00001"}'),
                ("item     ", 2, '{"part_no": "ITM-00002"}'),
                ("warehouse", 7, '{"whid": "MAIN"}'),
            ]

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    adapter = FirebirdAdapter.__new__(FirebirdAdapter)
    adapter.conn = FakeConn()
    adapter._in_txn = False
    out = FirebirdAdapter._query_many_inner(adapter, [
        ('SELECT rowid, data FROM "item"', ()),
        ('SELECT rowid, data FROM "warehouse"', ()),
    ])
    assert [len(x) for x in out] == [2, 1], [len(x) for x in out]
    assert out[0][0]["rowid"] == 1, out[0]
    print("ok  Firebird UNION: padded 'item     ' rows kept ->", [len(x) for x in out])


if __name__ == "__main__":
    for fn in (test_fields, test_search_dialog_direct,
               test_search_dialog_initial_search, test_grid_f4_fills_row,
               test_grid_f4_cancel_keeps_row, test_grid_f4_no_lookup_column,
               test_grid_f4_empty_lookup, test_lookup_cache_heals,
               test_firebird_union_padded_src_tags,
               test_renderer_tab_grids_wired):
        try:
            fn()
        except Exception as exc:
            print("FAIL", fn.__name__, "->", type(exc).__name__, exc)
            sys.exit(1)
    print("ALL F4 SMOKE TESTS PASSED")
