"""
WarehouseGrid -- Reusable TUI warehouse location manager.

CurvedBoxUI-based matrix showing warehouse locations (R{row}C{col}L{level}).
Ported from pystock for dbnocode — all DB access via callbacks.
"""
import curses
from .curved_box import CurvedBoxUI, EVT_CLICK, EVT_DBLCLICK, _CP_OFFSET


class WarehouseGrid:
    """Reusable warehouse location matrix with data entry."""

    # Default layout
    BOX_H = 4
    BOX_W = 14
    GAP_X = 0
    GAP_Y = 0
    GRID_START_Y = 8
    GRID_START_X = 6
    ROW_LABEL_X = 2
    MAX_LEVELS = 8

    def __init__(self, stdscr, *,
                 warehouses,
                 levels=None,
                 locations,
                 zones=None,
                 rows=8,
                 cols=10,
                 default_cbm=150.0,
                 title="Warehouse Location Manager",
                 # --- Callbacks ---
                 item_picker=None,
                 customer_picker=None,
                 on_receive=None,
                 on_issue=None,
                 on_assign=None,
                 on_unassign=None,
                 on_clear=None,
                 on_set_cbm=None,
                 on_add_layer=None,
                 on_remove_layer=None,
                 on_save=None,
                 on_select=None,
                 on_batch_assign=None,
                 customer_display=None,
                 zone_fn=None,
                 db_query=None):
        self.stdscr = stdscr
        self.ui = CurvedBoxUI(stdscr)

        # Data
        self.warehouses = list(warehouses)
        self.levels = dict(levels) if levels else {wh: 2 for wh in self.warehouses}
        self.locations = locations
        self.rows = rows
        self.cols = cols
        self.default_cbm = default_cbm
        self.title = title
        self.zone_fn = zone_fn
        self.db_query = db_query  # callable(table, filters, limit) -> list[dict]
        if zones:
            self.zones = zones
        elif zone_fn:
            zone_set = sorted({zone_fn(c) for c in range(1, cols + 1)})
            self.zones = ["ALL"] + zone_set
        else:
            self.zones = ["ALL", "A", "B", "C"]

        # Callbacks
        self.item_picker = item_picker
        self.customer_picker = customer_picker
        self.on_receive = on_receive
        self.on_issue = on_issue
        self.on_assign = on_assign
        self.on_unassign = on_unassign
        self.on_clear = on_clear
        self.on_set_cbm = on_set_cbm
        self.on_add_layer = on_add_layer
        self.on_remove_layer = on_remove_layer
        self.on_save = on_save
        self.on_select = on_select
        self.on_batch_assign = on_batch_assign
        self.customer_display = customer_display

        # State
        self._wh_idx = 0
        self._level = 1
        self._zone = self.zones[0] if self.zones else "ALL"
        self._selected = None
        self._msg = ""
        self._cur_row = 1
        self._cur_col = 1

        # SelectMode state
        self._select_mode = False
        self._marked = set()
        self._select_customer = ""
        self._select_cust_name = ""

        # Performance
        self._generated_levels = set()
        self._colors_dirty = False

        self._init_colors()

    # Color pairs for warehouse grid (offset from CurvedBoxUI base)
    CLR_GREEN   = 8
    CLR_YELLOW  = 9
    CLR_RED     = 10
    CLR_MAGENTA = 11
    CLR_WHITE   = 12
    CLR_YELLOW2 = 13
    CLR_MARKED  = 14

    def _init_colors(self):
        try:
            curses.init_pair(_CP_OFFSET + 8, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + 9, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + 10, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + 11, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + 12, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + 13, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(_CP_OFFSET + self.CLR_MARKED, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
        except curses.error:
            pass

    # ==================== LOCATION HELPERS ====================

    @staticmethod
    def make_key(warehouse, row, col, level):
        return f"{warehouse}:R{row}C{col}L{level}"

    @staticmethod
    def loc_key(loc):
        return f"{loc['warehouse']}:{loc['loc_id']}"

    @staticmethod
    def utilization(loc):
        if loc["max_cbm"] <= 0:
            return 0
        return min(100, int(loc["used_cbm"] / loc["max_cbm"] * 100))

    @staticmethod
    def generate_locations(warehouses, rows, cols, levels, default_cbm=150.0,
                           zone_map=None):
        """Generate empty location dicts. Returns {key: loc_dict}."""
        if zone_map is None:
            def zone_map(c):
                if c <= 5:
                    return "A"
                elif c <= 8:
                    return "B"
                return "C"

        locs = {}
        for wh in warehouses:
            max_lv = levels.get(wh, 2) if isinstance(levels, dict) else levels
            for lv in range(1, max_lv + 1):
                for r in range(1, rows + 1):
                    for c in range(1, cols + 1):
                        key = WarehouseGrid.make_key(wh, r, c, lv)
                        locs[key] = {
                            "warehouse": wh,
                            "row": r,
                            "col": c,
                            "level": lv,
                            "loc_id": f"R{r}C{c}L{lv}",
                            "zone": zone_map(c),
                            "max_cbm": default_cbm,
                            "used_cbm": 0.0,
                            "customer": "",
                            "customer_name": "",
                            "items": [],
                        }
        return locs

    def _generate_level(self, warehouse, level):
        """Generate empty locations for a single warehouse+level if none exist."""
        pair = (warehouse, level)
        if pair in self._generated_levels:
            return
        key_sample = self.make_key(warehouse, 1, 1, level)
        if key_sample in self.locations:
            self._generated_levels.add(pair)
            return

        zfn = self.zone_fn
        if zfn is None:
            def zfn(c):
                if c <= 5:
                    return "A"
                elif c <= 8:
                    return "B"
                return "C"

        for r in range(1, self.rows + 1):
            for c in range(1, self.cols + 1):
                key = self.make_key(warehouse, r, c, level)
                if key not in self.locations:
                    self.locations[key] = {
                        "warehouse": warehouse,
                        "row": r,
                        "col": c,
                        "level": level,
                        "loc_id": f"R{r}C{c}L{level}",
                        "zone": zfn(c),
                        "max_cbm": self.default_cbm,
                        "used_cbm": 0.0,
                        "customer": "",
                        "customer_name": "",
                        "items": [],
                    }
        self._generated_levels.add(pair)

    def _ensure_current_level(self):
        wh = self.warehouses[self._wh_idx]
        self._generate_level(wh, self._level)

    def _get_view_locs(self):
        wh = self.warehouses[self._wh_idx]
        result = []
        for r in range(1, self.rows + 1):
            for c in range(1, self.cols + 1):
                key = self.make_key(wh, r, c, self._level)
                loc = self.locations.get(key)
                if loc and (self._zone == "ALL" or loc["zone"] == self._zone):
                    result.append(loc)
        return result

    def _select_at_cursor(self):
        wh = self.warehouses[self._wh_idx]
        key = self.make_key(wh, self._cur_row, self._cur_col, self._level)
        loc = self.locations.get(key)
        if loc and (self._zone == "ALL" or loc["zone"] == self._zone):
            self._selected = loc
            self._msg = f"Selected {loc['loc_id']}"
            if self.on_select:
                self.on_select(loc)
        else:
            self._selected = None

    def _get_active_rows_cols(self):
        view_locs = self._get_view_locs()
        active_rows = sorted(set(loc["row"] for loc in view_locs))
        active_cols = sorted(set(loc["col"] for loc in view_locs))
        return active_rows, active_cols

    def _move_cursor(self, dr, dc):
        active_rows, active_cols = self._get_active_rows_cols()
        if not active_rows or not active_cols:
            return

        if self._cur_row in active_rows:
            ri = active_rows.index(self._cur_row)
        else:
            ri = 0
        if self._cur_col in active_cols:
            ci = active_cols.index(self._cur_col)
        else:
            ci = 0

        ri = max(0, min(len(active_rows) - 1, ri + dr))
        ci = max(0, min(len(active_cols) - 1, ci + dc))

        self._cur_row = active_rows[ri]
        self._cur_col = active_cols[ci]
        self._select_at_cursor()

    def _customer_name(self, code, loc=None):
        if not code:
            return "(none)"
        if loc and loc.get("customer_name"):
            return loc["customer_name"]
        if self.customer_display:
            return self.customer_display(code)
        return code

    # ==================== FILL / BORDER COLORS ====================

    @staticmethod
    def _fill_color(util):
        if util <= 0:
            return 7
        elif util <= 30:
            return 8
        elif util <= 60:
            return 9
        elif util <= 85:
            return 13
        else:
            return 10

    def _is_marked(self, loc):
        return self.loc_key(loc) in self._marked

    def _border_color(self, loc):
        if self._selected and loc["loc_id"] == self._selected["loc_id"] \
                and loc["warehouse"] == self._selected["warehouse"] \
                and loc["level"] == self._selected["level"]:
            return 6
        if self._is_marked(loc):
            return self.CLR_MARKED
        if loc.get("selected"):
            return self.CLR_MARKED
        if loc["customer"]:
            return 3
        return 1

    # ==================== DRAWING ====================

    def _draw_grid(self, view_locs):
        ui = self.ui
        start_y = self.GRID_START_Y
        start_x = self.GRID_START_X
        sel_id = self._selected["loc_id"] if self._selected else ""

        if not view_locs:
            ui.write_text(start_y, start_x, "No locations")
            return 0

        active_rows = sorted(set(loc["row"] for loc in view_locs))
        active_cols = sorted(set(loc["col"] for loc in view_locs))

        for ci, c in enumerate(active_cols):
            hx = start_x + ci * (self.BOX_W + self.GAP_X) + self.BOX_W // 2 - 1
            ui.write_text(start_y - 1, hx, f"C{c}", bold=True)

        loc_map = {(loc["row"], loc["col"]): loc for loc in view_locs}

        for ri, r in enumerate(active_rows):
            y = start_y + ri * (self.BOX_H + self.GAP_Y)
            ui.write_text(y + 1, self.ROW_LABEL_X, f"R{r}", bold=True)

            for ci, c in enumerate(active_cols):
                loc = loc_map.get((r, c))
                if not loc:
                    continue

                x = start_x + ci * (self.BOX_W + self.GAP_X)
                util = self.utilization(loc)
                is_marked = self._is_marked(loc) or loc.get("selected")
                fill_pct = util if util > 0 else None
                fc = self._fill_color(util)
                bc = self._border_color(loc)
                is_sel = (loc["loc_id"] == sel_id)

                if is_sel:
                    fc = 6
                    fill_pct = 100
                elif is_marked:
                    fc = self.CLR_MARKED
                    fill_pct = 100

                ui.draw_box(y, x, self.BOX_H, self.BOX_W,
                            color_pair=bc,
                            attr=curses.A_BOLD if (is_sel or is_marked) else curses.A_NORMAL,
                            fill_pct=fill_pct, fill_color=fc,
                            zone_id=f"loc:{loc['loc_id']}", zone_data=loc)

                sfc = fc if fill_pct else None
                if sfc is not None:
                    for row_i in range(self.BOX_H - 2):
                        ui.write_inside(y, x, self.BOX_H, self.BOX_W,
                                        row=row_i, col=0, text="", surface=sfc)

                ui.write_on_border(y, x, self.BOX_H, self.BOX_W, "top", 1,
                                   f" {loc['loc_id']} ", bold=True)
                ui.write_on_border(y, x, self.BOX_H, self.BOX_W, "top",
                                   self.BOX_W - 4,
                                   f" {loc['zone']} ", color_pair=3, reverse=True)

                if util == 0:
                    ut = "Free"
                elif util >= 100:
                    ut = "FULL"
                else:
                    ut = f"{util}%"
                ui.write_inside(y, x, self.BOX_H, self.BOX_W, row=0, col=1,
                                text=ut, bold=(util >= 75), surface=sfc)

                if loc["customer"]:
                    cn = self._customer_name(loc["customer"], loc)
                    ui.write_inside(y, x, self.BOX_H, self.BOX_W, row=1, col=1,
                                    text=cn[:self.BOX_W - 4], surface=sfc)
                elif loc["items"]:
                    ui.write_inside(y, x, self.BOX_H, self.BOX_W, row=1, col=1,
                                    text=loc["items"][0]["part_no"][:self.BOX_W - 4],
                                    surface=sfc)

        return len(active_rows)

    def _draw_detail(self, y, x, width):
        loc = self._selected
        if not loc:
            return

        ui = self.ui
        detail_h = 12
        util = self.utilization(loc)

        ui.draw_box(y, x, detail_h, width, color_pair=6, attr=curses.A_BOLD)
        ui.write_on_border(y, x, detail_h, width, "top", 1,
                           f" {loc['loc_id']} \u2014 {loc['warehouse']} L{loc['level']} ",
                           bold=True, color_pair=6)
        ui.write_on_border(y, x, detail_h, width, "top", width - 8,
                           f" {util}% ", color_pair=self._fill_color(util), reverse=True)

        cn = self._customer_name(loc["customer"], loc)
        ui.write_inside(y, x, detail_h, width, row=0, col=1,
                        text=f"Zone: {loc['zone']}    CBM: {loc['used_cbm']:.1f} / {loc['max_cbm']:.0f}",
                        bold=True)
        ui.write_inside(y, x, detail_h, width, row=1, col=1,
                        text=f"Customer: {cn}")

        ui.write_inside(y, x, detail_h, width, row=3, col=1,
                        text="Stored Items:", bold=True)
        if loc["items"]:
            for i, item in enumerate(loc["items"][:4]):
                line = f"  {item['part_no']}  {item['name'][:20]}  qty:{item['qty']}  cbm:{item['cbm']:.2f}"
                ui.write_inside(y, x, detail_h, width, row=4 + i, col=1,
                                text=line[:width - 4])
        else:
            ui.write_inside(y, x, detail_h, width, row=4, col=1,
                            text="  (empty)")

        ui.write_on_border(y, x, detail_h, width, "bottom", 1,
                           " F9:Actions  A/B/C:Zone  [+][-]:Layer  F10:Save",
                           color_pair=4, reverse=True)

    def _draw_legend(self, y, x):
        ui = self.ui
        ui.write_text(y + 1, x, "Fill:", bold=True)
        levels = [("Free", None, 7), ("~30%", 30, 8), ("~60%", 60, 9),
                  ("~85%", 85, 13), ("Full", 100, 10)]
        lx = x + 7
        for label, fill, fc in levels:
            ui.draw_box(y, lx, 3, 8, fill_pct=fill, fill_color=fc)
            sfc = fc if fill else None
            ui.write_inside(y, lx, 3, 8, row=0, col=0, text=label, surface=sfc)
            lx += 9
        lx += 2
        ui.write_text(y + 1, lx, "Cyan border = Customer assigned",
                      color_pair=3, bold=True)

    def _draw_level_bar(self, y, x, max_lv):
        ui = self.ui
        ui.write_text(y, x, "Layer: ", bold=True)
        lx = x + 7
        for lv in range(1, max_lv + 1):
            if lv == self._level:
                ui.write_text(y, lx, f"[{lv}]", bold=True, color_pair=2)
            else:
                ui.write_text(y, lx, f" {lv} ")
            lx += 4

    def _draw_zone_bar(self, y, x):
        ui = self.ui
        ui.write_text(y, x, "Zone: ", bold=True)
        zx = x + 6
        for z in self.zones:
            if z == self._zone:
                ui.write_text(y, zx, f"[{z}]", bold=True, color_pair=2)
            else:
                ui.write_text(y, zx, f" {z} ")
            zx += len(z) + 3

    # ==================== DIALOGS ====================

    def input_dialog(self, prompt, max_len=30):
        """Simple text input. Returns string or None on ESC."""
        max_y, max_x = self.stdscr.getmaxyx()
        w = max(len(prompt) + max_len + 6, 40)
        h = 5
        dy = max_y // 2 - 2
        dx = max_x // 2 - w // 2
        ui = self.ui

        buf = ""
        while True:
            ui.draw_box(dy, dx, h, w, color_pair=6, attr=curses.A_BOLD)
            for ri in range(h - 2):
                ui.write_inside(dy, dx, h, w, row=ri, col=0, text=" " * (w - 2))
            ui.write_inside(dy, dx, h, w, row=0, col=1, text=prompt, bold=True)
            ui.write_inside(dy, dx, h, w, row=1, col=1, text=buf + "_")
            ui.write_inside(dy, dx, h, w, row=2, col=1, text="Enter: OK  ESC: Cancel")
            ui.refresh()

            key = self.stdscr.getch()
            if key == curses.KEY_MOUSE:
                try:
                    curses.getmouse()
                except curses.error:
                    pass
                continue
            if key == 27:
                return None
            elif key in (10, 13):
                return buf
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif 32 <= key <= 126 and len(buf) < max_len:
                buf += chr(key)

    def pick_dialog(self, title, options):
        """Pick from [(key, display)] list. Returns key or None."""
        if not options:
            return None
        max_y, max_x = self.stdscr.getmaxyx()
        max_w = max(len(title) + 4,
                    max(len(f"{k} - {d}") for k, d in options) + 6)
        h = min(len(options) + 4, max_y - 4)
        w = min(max_w, max_x - 4)
        dy = max_y // 2 - h // 2
        dx = max_x // 2 - w // 2
        sel = 0
        scroll = 0
        visible = h - 4
        ui = self.ui

        while True:
            ui.draw_box(dy, dx, h, w, color_pair=6, attr=curses.A_BOLD)
            for ri in range(h - 2):
                ui.write_inside(dy, dx, h, w, row=ri, col=0, text=" " * (w - 2))
            ui.write_on_border(dy, dx, h, w, "top", 1,
                               f" {title} ", bold=True, color_pair=6)
            for i in range(scroll, min(scroll + visible, len(options))):
                k, d = options[i]
                text = f" {k} - {d}"[:w - 4]
                if i == sel:
                    ui.write_inside(dy, dx, h, w, row=1 + i - scroll, col=1,
                                    text=text.ljust(w - 4), reverse=True)
                else:
                    ui.write_inside(dy, dx, h, w, row=1 + i - scroll, col=1,
                                    text=text)
            ui.write_on_border(dy, dx, h, w, "bottom", 1,
                               " Enter: Select  ESC: Cancel ",
                               color_pair=4, reverse=True)
            ui.refresh()

            key = self.stdscr.getch()
            if key == curses.KEY_MOUSE:
                try:
                    curses.getmouse()
                except curses.error:
                    pass
                continue
            if key == 27:
                return None
            elif key in (10, 13):
                return options[sel][0]
            elif key == curses.KEY_DOWN and sel < len(options) - 1:
                sel += 1
                if sel >= scroll + visible:
                    scroll += 1
            elif key == curses.KEY_UP and sel > 0:
                sel -= 1
                if sel < scroll:
                    scroll -= 1

    def _popup_menu(self, title, items):
        """Simple popup menu. items = list of strings (None = separator).
        Returns chosen string or None."""
        choices = [(s, s) for s in items if s is not None]
        if not choices:
            return None
        return self.pick_dialog(title, choices)

    # ==================== ACTIONS ====================

    def _action_assign(self):
        loc = self._selected
        if not loc:
            return
        if loc["customer"]:
            owner = self._customer_name(loc["customer"], loc)
            self._msg = f"{loc['loc_id']} is reserved for {owner} — unassign first"
            return
        if self.customer_picker:
            result = self.customer_picker(self.stdscr)
            self._colors_dirty = True
            if result is None:
                return
            code, name = result
        else:
            self._msg = "No customer_picker provided"
            return

        if self.on_assign:
            if not self.on_assign(loc, code, name):
                return
        loc["customer"] = code
        loc["customer_name"] = name
        self._msg = f"{loc['loc_id']} assigned to {name}"
        self._action_save()

    def _action_unassign(self):
        loc = self._selected
        if not loc:
            return
        if not loc["customer"]:
            self._msg = "No customer assigned"
            return
        old = self._customer_name(loc["customer"], loc)
        if self.on_unassign:
            if not self.on_unassign(loc):
                return
        loc["customer"] = ""
        loc["customer_name"] = ""
        self._msg = f"{loc['loc_id']} unassigned from {old}"
        self._action_save()

    def _action_receive(self):
        loc = self._selected
        if not loc:
            return
        if loc["customer"] and not loc["items"]:
            owner = self._customer_name(loc["customer"], loc)
            self._msg = f"{loc['loc_id']} reserved for {owner}"
            return

        if loc["items"]:
            existing = loc["items"][0]
            self._msg = f"Has {existing['part_no']} \u2014 adding same SKU only"
            qty_str = self.input_dialog(f"Add qty of {existing['part_no']}:")
            if not qty_str or not qty_str.isdigit():
                return
            qty = int(qty_str)
            if self.on_receive:
                if not self.on_receive(loc, existing["part_no"], existing["name"], qty, 0):
                    return
            elif existing.get("cbm") and existing.get("qty"):
                cbm_per = existing["cbm"] / existing["qty"] if existing["qty"] else 0
                add_cbm = round(qty * cbm_per, 2)
                if loc["used_cbm"] + add_cbm > loc["max_cbm"]:
                    self._msg = "Exceeds CBM capacity"
                    return
                existing["qty"] += qty
                existing["cbm"] = round(existing["cbm"] + add_cbm, 2)
                loc["used_cbm"] = round(loc["used_cbm"] + add_cbm, 2)
                self._msg = f"Received {qty} of {existing['part_no']}"
        else:
            if self.item_picker:
                result = self.item_picker(self.stdscr)
                self._colors_dirty = True
                if result is None:
                    return
                part_no, name, cbm_per_unit = result
            else:
                self._msg = "No item_picker provided"
                return

            qty_str = self.input_dialog(f"Qty of {part_no}:")
            if not qty_str or not qty_str.isdigit():
                return
            qty = int(qty_str)
            cbm = round(qty * cbm_per_unit, 2)

            if cbm > loc["max_cbm"]:
                self._msg = f"Exceeds CBM! {cbm:.2f} > {loc['max_cbm']:.0f}"
                return

            if self.on_receive:
                if not self.on_receive(loc, part_no, name, qty, cbm):
                    return
            loc["items"] = [{"part_no": part_no, "name": name, "qty": qty, "cbm": cbm}]
            loc["used_cbm"] = cbm
            self._msg = f"Received {qty} x {part_no} ({cbm:.2f} CBM)"

    def _action_issue(self):
        loc = self._selected
        if not loc or not loc["items"]:
            self._msg = "Location is empty"
            return

        item = loc["items"][0]
        qty_str = self.input_dialog(f"Issue qty of {item['part_no']} (have {item['qty']}):")
        if not qty_str or not qty_str.isdigit():
            return
        qty = int(qty_str)

        if qty > item["qty"]:
            self._msg = f"Only {item['qty']} available"
            return

        if self.on_issue:
            if not self.on_issue(loc, item["part_no"], qty):
                return

        if qty == item["qty"]:
            loc["items"] = []
            loc["used_cbm"] = 0.0
            self._msg = f"Issued all {qty} \u2014 location cleared"
        else:
            cbm_per = item["cbm"] / item["qty"] if item["qty"] else 0
            cbm_out = round(qty * cbm_per, 2)
            item["qty"] -= qty
            item["cbm"] = round(item["cbm"] - cbm_out, 2)
            loc["used_cbm"] = round(loc["used_cbm"] - cbm_out, 2)
            self._msg = f"Issued {qty} of {item['part_no']}"

    def _action_clear(self):
        loc = self._selected
        if not loc:
            return
        if not loc["items"]:
            self._msg = "Already empty"
            return
        if self.on_clear:
            if not self.on_clear(loc):
                return
        loc["items"] = []
        loc["used_cbm"] = 0.0
        self._msg = f"{loc['loc_id']} cleared"

    def _action_set_cbm(self):
        loc = self._selected
        if not loc:
            return
        val = self.input_dialog(f"Max CBM (current: {loc['max_cbm']:.0f}):")
        if not val:
            return
        try:
            new_cbm = float(val)
        except ValueError:
            self._msg = "Invalid number"
            return
        if new_cbm < loc["used_cbm"]:
            self._msg = f"Cannot set below used ({loc['used_cbm']:.1f})"
            return
        if self.on_set_cbm:
            if not self.on_set_cbm(loc, new_cbm):
                return
        loc["max_cbm"] = new_cbm
        self._msg = f"{loc['loc_id']} CBM set to {new_cbm:.0f}"

    def _action_set_zone(self):
        loc = self._selected
        if not loc:
            return
        zone_opts = [(z, z) for z in self.zones if z != "ALL"]
        chosen = self.pick_dialog("Set Zone", zone_opts)
        if chosen is None:
            return
        loc["zone"] = chosen
        self._msg = f"{loc['loc_id']} zone set to {chosen}"

    def _action_save(self):
        if self.on_save:
            max_y, max_x = self.stdscr.getmaxyx()
            save_msg = " Saving... "
            sx = max_x // 2 - len(save_msg) // 2
            sy = max_y // 2
            self.ui._safe_addstr(sy, sx, save_msg,
                                 curses.color_pair(_CP_OFFSET + 2) | curses.A_BOLD)
            self.ui.refresh()
            try:
                self.on_save(self.locations, self.levels)
                self._msg = "\u2713 Saved OK"
            except Exception as e:
                self._msg = f"Save error: {e}"
        else:
            self._msg = "No on_save handler"

    # ==================== SELECT MODE ====================

    def _enter_select_mode(self):
        if self.customer_picker:
            result = self.customer_picker(self.stdscr)
            self._colors_dirty = True
            if result is None:
                return
            code, name = result
        else:
            self._msg = "No customer_picker provided"
            return

        self._select_mode = True
        self._select_customer = code
        self._select_cust_name = name
        self._marked = set()

        for k, loc in self.locations.items():
            if loc["customer"] == code:
                self._marked.add(k)

        self._select_at_cursor()
        self._msg = f"SELECT MODE: {name} \u2014 Space:toggle  Enter:confirm  ESC:cancel"

    def _toggle_mark(self):
        if not self._selected:
            self._select_at_cursor()
        if not self._selected:
            self._msg = "No location at cursor"
            return

        loc = self._selected
        key = self.loc_key(loc)

        if loc["customer"] and loc["customer"] != self._select_customer:
            other = self._customer_name(loc["customer"], loc)
            self._msg = f"Already assigned to {other}"
            return

        if key in self._marked:
            self._marked.discard(key)
            self._msg = f"{loc['loc_id']} unmarked"
        else:
            self._marked.add(key)
            self._msg = f"{loc['loc_id']} marked ({len(self._marked)} total)"

    def _confirm_select_mode(self):
        code = self._select_customer
        name = self._select_cust_name

        marked_locs = [self.locations[k] for k in self._marked if k in self.locations]

        if self.on_batch_assign:
            if not self.on_batch_assign(marked_locs, code, name):
                self._msg = "Batch assign rejected"
                return

        for k, loc in self.locations.items():
            if loc["customer"] == code and k not in self._marked:
                loc["customer"] = ""
                loc["customer_name"] = ""
                loc["selected"] = False

        for loc in marked_locs:
            loc["customer"] = code
            loc["customer_name"] = name
            loc["selected"] = True

        count = len(marked_locs)
        self._select_mode = False
        self._marked = set()
        self._select_customer = ""
        self._select_cust_name = ""
        self._msg = f"Assigned {count} locations to {name}"
        self._action_save()

    def _cancel_select_mode(self):
        self._select_mode = False
        self._marked = set()
        self._select_customer = ""
        self._select_cust_name = ""
        self._msg = "SelectMode cancelled"

    def get_customer_locations(self, customer_code):
        return [loc for loc in self.locations.values()
                if loc["customer"] == customer_code]

    # ==================== RUN ====================

    def run(self):
        """Main loop. Returns on ESC."""
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

        self.ui.on("*", EVT_CLICK, self._on_any_click)
        self.ui.on("*", EVT_DBLCLICK, self._on_any_dblclick)

        while True:
            if self._colors_dirty:
                self.ui._init_colors()
                self._init_colors()
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
                self._colors_dirty = False

            self.ui.stdscr.clear()
            self.ui.clear_zones()

            wh = self.warehouses[self._wh_idx]
            max_lv = self.levels.get(wh, 2)
            max_y, max_x = self.stdscr.getmaxyx()

            self._ensure_current_level()
            view_locs = self._get_view_locs()

            wh_prefix = f"{wh}:"
            total = 0
            used = 0
            for k, loc in self.locations.items():
                if k[:len(wh_prefix)] == wh_prefix and loc["level"] <= max_lv:
                    total += 1
                    if loc["items"]:
                        used += 1
            if self._select_mode:
                self.ui.write_text(0, 2,
                                   f"SELECT MODE \u2014 Assigning to: {self._select_cust_name}  "
                                   f"({len(self._marked)} marked)",
                                   bold=True, color_pair=self.CLR_MARKED)
            else:
                self.ui.write_text(0, 2, f"{self.title} \u2014 {wh}", bold=True)
            self.ui.write_text(0, max_x - 30, f"Locations: {used}/{total} used",
                               color_pair=3)

            self.ui.draw_tabs(1, 2, self.warehouses, self._wh_idx)
            self._draw_level_bar(4, 2, max_lv)
            self._draw_zone_bar(4, 45)

            if self._select_mode:
                self.ui.write_text(5, 2,
                                   "Click location then Space to toggle  |  "
                                   "Enter: confirm assign  |  ESC: cancel",
                                   color_pair=self.CLR_MARKED)
            else:
                self.ui.write_text(5, 2,
                                   f"Grid: {self.rows}R x {self.cols}C x {max_lv}L  |  "
                                   f"CBM: {self.default_cbm:.0f}  |  "
                                   "M:Mark  [+][-]:Layer  F3:SelectMode",
                                   color_pair=12)

            rows_drawn = self._draw_grid(view_locs)

            legend_y = self.GRID_START_Y + max(rows_drawn, 1) * (self.BOX_H + self.GAP_Y) + 1
            self._draw_legend(legend_y, 2)

            if self._selected:
                self._draw_detail(legend_y + 4, 2, 80)

            if self._select_mode:
                bar = f" SELECT: {self._select_cust_name}  |  "
                bar += f"Marked: {len(self._marked)}  |  "
                bar += "Arrows:Move  Space:Toggle  Enter:Confirm  ESC:Cancel  |  PgUp/Dn:Layer"
            else:
                bar = f" {wh} L{self._level}/{max_lv}  Zone:{self._zone}"
                bar += "  |  Arrows:Move  Space/M:Mark  Enter:Actions  F9:Menu  F3:SelectMode  F10:Save  ESC:Exit"
            if self._msg:
                bar += f"  |  {self._msg}"
            self.ui.write_text(max_y - 1, 0, bar.ljust(max_x - 1), color_pair=4)

            self.ui.refresh()

            key = self.stdscr.getch()
            if key == 27:
                if self._select_mode:
                    self._cancel_select_mode()
                    continue
                return

            result = self._handle_key(key, wh, max_lv)
            if result == "exit":
                return

    # ==================== KEY HANDLING ====================

    def _handle_key(self, key, wh, max_lv):
        if self._select_mode:
            if key == ord(' '):
                self._toggle_mark()
                return
            elif key in (10, 13, 459):
                self._confirm_select_mode()
                return
            elif key == curses.KEY_UP:
                self._move_cursor(-1, 0)
                return
            elif key == curses.KEY_DOWN:
                self._move_cursor(1, 0)
                return
            elif key == curses.KEY_LEFT:
                self._move_cursor(0, -1)
                return
            elif key == curses.KEY_RIGHT:
                self._move_cursor(0, 1)
                return
            elif key == curses.KEY_NPAGE and self._level < max_lv:
                self._level += 1
                self._selected = None
                return
            elif key == curses.KEY_PPAGE and self._level > 1:
                self._level -= 1
                self._selected = None
                return
            elif ord('1') <= key <= ord('8'):
                lv = key - ord('0')
                if lv <= max_lv:
                    self._level = lv
                    self._selected = None
                return
            elif key == curses.KEY_MOUSE:
                _, mx, my, _, bstate = curses.getmouse()
                self.ui.handle_mouse(bstate, mx, my)
                return
            return

        if key == curses.KEY_F3:
            self._enter_select_mode()
            return

        if key == curses.KEY_UP:
            self._move_cursor(-1, 0)
            return
        elif key == curses.KEY_DOWN:
            self._move_cursor(1, 0)
            return
        elif key == curses.KEY_LEFT:
            self._move_cursor(0, -1)
            return
        elif key == curses.KEY_RIGHT:
            self._move_cursor(0, 1)
            return

        if key == 9:  # Tab
            self._wh_idx = (self._wh_idx + 1) % len(self.warehouses)
            self._selected = None
            self._level = 1
            self._msg = f"Warehouse: {self.warehouses[self._wh_idx]}"
        elif key == curses.KEY_BTAB:
            self._wh_idx = (self._wh_idx - 1) % len(self.warehouses)
            self._selected = None
            self._level = 1
            self._msg = f"Warehouse: {self.warehouses[self._wh_idx]}"

        elif key == curses.KEY_NPAGE and self._level < max_lv:
            self._level += 1
            self._selected = None
            self._msg = f"Layer {self._level}"
        elif key == curses.KEY_PPAGE and self._level > 1:
            self._level -= 1
            self._selected = None
            self._msg = f"Layer {self._level}"
        elif ord('1') <= key <= ord('8'):
            lv = key - ord('0')
            if lv <= max_lv:
                self._level = lv
                self._selected = None
                self._msg = f"Layer {lv}"

        elif key in (10, 13, 459):
            # Enter: open context menu on selected location
            self._select_at_cursor()
            if self._selected:
                self._action_loc_menu()
            return

        elif key == ord(' ') or key in (ord('m'), ord('M')):
            # Space/M: toggle mark on current cursor location
            self._select_at_cursor()
            if self._selected:
                key_str = self.loc_key(self._selected)
                if key_str in self._marked:
                    self._marked.discard(key_str)
                    self._msg = f"{self._selected['loc_id']} unmarked ({len(self._marked)} marked)"
                else:
                    self._marked.add(key_str)
                    self._msg = f"{self._selected['loc_id']} marked ({len(self._marked)} marked)"
            return

        elif key in (ord('a'), ord('A')) and not self._selected:
            self._zone = "A" if self._zone != "A" else "ALL"
        elif key in (ord('b'), ord('B')) and not self._selected:
            self._zone = "B" if self._zone != "B" else "ALL"
        elif key in (ord('c'), ord('C')) and not self._selected:
            self._zone = "C" if self._zone != "C" else "ALL"
        elif key == ord('0'):
            self._zone = "ALL"

        elif key in (ord('+'), ord('=')):
            if max_lv < self.MAX_LEVELS:
                new_lv = max_lv + 1
                if self.on_add_layer:
                    if not self.on_add_layer(wh, new_lv):
                        return
                self.levels[wh] = new_lv
                self._generate_level(wh, new_lv)
                self._msg = f"Added layer {new_lv} to {wh}"
        elif key == ord('-') and max_lv > 1:
            top_used = any(self.locations[k]["items"]
                           for k in self.locations
                           if k.startswith(f"{wh}:") and self.locations[k]["level"] == max_lv)
            if top_used:
                self._msg = f"Layer {max_lv} has items \u2014 cannot remove"
            else:
                if self.on_remove_layer:
                    if not self.on_remove_layer(wh, max_lv):
                        return
                self.levels[wh] = max_lv - 1
                if self._level > max_lv - 1:
                    self._level = max_lv - 1
                self._msg = f"Removed layer {max_lv} from {wh}"

        elif key == curses.KEY_F9:
            self._action_loc_menu()

        elif key in (ord('s'), ord('S'), curses.KEY_F10):
            self._action_save()

        elif key == curses.KEY_MOUSE:
            _, mx, my, _, bstate = curses.getmouse()
            is_ctrl = bool(bstate & getattr(curses, 'BUTTON_CTRL', 0x8000000))
            is_click = bool(bstate & (getattr(curses, 'BUTTON1_CLICKED', 0) |
                                      getattr(curses, 'BUTTON1_PRESSED', 0)))
            if is_ctrl and is_click:
                zone_id, data = self.ui._find_zone(my, mx)
                if zone_id and zone_id.startswith("loc:"):
                    self._selected = data
                    self._cur_row = data["row"]
                    self._cur_col = data["col"]
                    key_str = self.loc_key(data)
                    if key_str in self._marked:
                        self._marked.discard(key_str)
                        self._msg = f"{data['loc_id']} unmarked ({len(self._marked)} marked)"
                    else:
                        self._marked.add(key_str)
                        self._msg = f"{data['loc_id']} marked ({len(self._marked)} marked)"
                return
            result = self.ui.handle_mouse(bstate, mx, my)
            if result:
                if result["event_type"] == "scroll_up" and self._level < max_lv:
                    self._level += 1
                    self._selected = None
                elif result["event_type"] == "scroll_down" and self._level > 1:
                    self._level -= 1
                    self._selected = None

    # --- Mouse event handlers ---

    def _on_any_click(self, zone_id, data, event_type):
        if zone_id.startswith("tab:"):
            self._wh_idx = data
            self._selected = None
            self._level = 1
            self._msg = f"Warehouse: {self.warehouses[self._wh_idx]}"
        elif zone_id.startswith("loc:"):
            self._selected = data
            self._cur_row = data["row"]
            self._cur_col = data["col"]
            if self._select_mode:
                self._toggle_mark()
                return
            self._msg = f"Selected {data['loc_id']}"
            if self.on_select:
                self.on_select(data)

    def _on_any_dblclick(self, zone_id, data, event_type):
        if zone_id.startswith("loc:"):
            self._selected = data
            self._cur_row = data["row"]
            self._cur_col = data["col"]
            self._action_loc_menu()

    def _action_loc_menu(self):
        """Context menu via F9 or double-click."""
        marked_locs = [self.locations[k] for k in self._marked if k in self.locations]

        if marked_locs:
            title = f'{len(marked_locs)} locations'
            choice = self._popup_menu(title, [
                'Assign Customer',
                'Set Max CBM',
                'Set Zone',
                'Clear Marks',
            ])
            if choice == 'Assign Customer':
                self._action_batch_assign_marked(marked_locs)
            elif choice == 'Set Max CBM':
                self._action_batch_set_cbm(marked_locs)
            elif choice == 'Set Zone':
                self._action_batch_set_zone(marked_locs)
            elif choice == 'Clear Marks':
                self._marked = set()
                self._msg = "Marks cleared"
        else:
            loc = self._selected
            if not loc:
                return
            choice = self._popup_menu(loc['loc_id'], [
                'Assign Customer',
                'Unassign Customer',
                'Receive Items',
                'Issue Items',
                'Set Zone',
                'Set Max CBM',
                'Clear Location',
            ])
            if choice == 'Assign Customer':
                self._action_assign()
            elif choice == 'Unassign Customer':
                self._action_unassign()
            elif choice == 'Receive Items':
                self._action_receive()
            elif choice == 'Issue Items':
                self._action_issue()
            elif choice == 'Set Zone':
                self._action_set_zone()
            elif choice == 'Set Max CBM':
                self._action_set_cbm()
            elif choice == 'Clear Location':
                self._action_clear()

    def _action_batch_assign_marked(self, marked_locs):
        if not self.customer_picker:
            self._msg = "No customer picker configured"
            return
        result = self.customer_picker(self.stdscr)
        self._colors_dirty = True
        if result is None:
            self._msg = "Cancelled"
            return
        code, name = result
        for loc in marked_locs:
            loc['customer'] = code
            loc['customer_name'] = name
        count = len(marked_locs)
        self._marked = set()
        self._msg = f"Assigned {count} locations to {name}"
        self._action_save()

    def _action_batch_set_cbm(self, marked_locs):
        val = self.input_dialog(f"Max CBM for {len(marked_locs)} locations:")
        if not val:
            return
        try:
            new_cbm = float(val)
        except ValueError:
            self._msg = "Invalid number"
            return
        count = 0
        for loc in marked_locs:
            if new_cbm < loc.get("used_cbm", 0):
                continue
            loc["max_cbm"] = new_cbm
            count += 1
        self._marked = set()
        self._msg = f"Set Max CBM to {new_cbm:.0f} on {count}/{len(marked_locs)} locations"
        self._action_save()

    def _action_batch_set_zone(self, marked_locs):
        zone_opts = [(z, z) for z in self.zones if z != "ALL"]
        chosen = self.pick_dialog("Set Zone", zone_opts)
        if chosen is None:
            return
        for loc in marked_locs:
            loc["zone"] = chosen
        self._marked = set()
        self._msg = f"Set zone to {chosen} on {len(marked_locs)} locations"
        self._action_save()
