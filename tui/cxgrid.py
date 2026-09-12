import curses
import json
import os
import time
import re

# ── Custom dropdown options persistence ──────────────────────────────
_CUSTOM_OPTS_FILE = "custom_options.json"
_custom_opts_cache = None


def _load_custom_options():
    global _custom_opts_cache
    if _custom_opts_cache is not None:
        return _custom_opts_cache
    try:
        with open(_CUSTOM_OPTS_FILE, "r", encoding="utf-8") as f:
            _custom_opts_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _custom_opts_cache = {}
    return _custom_opts_cache


def _save_custom_options(field_name, options):
    """Persist custom options added by user."""
    data = _load_custom_options()
    data[field_name] = list(options)
    try:
        with open(_CUSTOM_OPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    global _custom_opts_cache
    _custom_opts_cache = data


def merge_custom_options(field_name, base_options):
    """Merge persisted custom options into base list. Returns merged list."""
    data = _load_custom_options()
    saved = data.get(field_name, [])
    merged = list(base_options)
    for val in saved:
        if val not in merged:
            merged.append(val)
    return merged


# Footer aggregation types
AGG_NONE = "none"
AGG_SUM = "sum"
AGG_COUNT = "count"
AGG_AVG = "avg"
AGG_MIN = "min"
AGG_MAX = "max"
AGG_CYCLE = [AGG_NONE, AGG_SUM, AGG_COUNT, AGG_AVG, AGG_MIN, AGG_MAX]

# Sort directions
SORT_NONE = None
SORT_ASC = "asc"
SORT_DESC = "desc"


# ==================== NUMERIC FORMATTING ====================

NUMERIC_DECIMALS = 2  # formula totals, footer sums, stored numeric precision


def _numeric_round(val, decimals=NUMERIC_DECIMALS):
    try:
        num = round(float(str(val).replace(",", "")), decimals)
        if num == int(num):
            return int(num)
        return num
    except (ValueError, TypeError):
        return 0


def _numeric_digits_only(s):
    return "".join(c for c in s if c.isdigit())


def _numeric_format_raw(raw):
    """Format unformatted edit buffer for display (commas; decimals only when typed)."""
    if not raw:
        return ""
    if raw == "-":
        return "-"

    negative = raw.startswith("-")
    body = raw[1:] if negative else raw
    prefix = "-" if negative else ""

    if "." in body:
        int_part, dec_part = body.split(".", 1)
        int_digits = _numeric_digits_only(int_part) or "0"
        int_fmt = f"{int(int_digits):,}"
        dec_digits = _numeric_digits_only(dec_part)
        if body.endswith(".") and not dec_digits:
            return f"{prefix}{int_fmt}."
        if dec_digits:
            return f"{prefix}{int_fmt}.{dec_digits}"
        return f"{prefix}{int_fmt}"

    digits = _numeric_digits_only(body)
    if not digits:
        return prefix
    return f"{prefix}{int(digits):,}"


def _numeric_format_number(val, decimals=NUMERIC_DECIMALS):
    """Format a stored number for grid display (commas; max 2 decimal places)."""
    if val is None or val == "":
        return ""
    num = _numeric_round(val, decimals)
    if num != num:  # NaN
        return ""

    if abs(num - round(num)) < 10 ** (-decimals - 1):
        return f"{int(round(num)):,}"

    s = f"{abs(num):.{decimals}f}"
    if "." in s:
        int_part, dec_part = s.split(".", 1)
        dec_part = dec_part.rstrip("0")
        sign = "-" if num < 0 else ""
        if dec_part:
            return f"{sign}{int(int_part):,}.{dec_part}"
        return f"{sign}{int(int_part):,}"
    sign = "-" if num < 0 else ""
    return f"{sign}{int(s):,}"


def _numeric_raw_cursor_display(raw, pos):
    """Map cursor index in raw buffer to position in formatted display."""
    if pos <= 0:
        return 0
    return len(_numeric_format_raw(raw[:pos]))


def _numeric_char_allowed(raw, pos, ch):
    """Return True if ch may be inserted at pos in a numeric edit buffer."""
    if ch.isdigit():
        return True
    if ch == ".":
        return "." not in raw.lstrip("-")
    if ch == "-":
        return pos == 0 and not raw.startswith("-")
    return False


def _numeric_parse(raw):
    """Parse raw edit buffer to float."""
    cleaned = raw.replace(",", "").strip()
    if not cleaned or cleaned in ("-", "-.", "."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        digits = "".join(c for c in cleaned if c.isdigit() or c in ".-")
        try:
            return float(digits or "0")
        except ValueError:
            return 0.0


def _fmt_num(val):
    """Format numeric value: no trailing .0, blank for 0, with commas."""
    if val is None or val == "":
        return ""
    try:
        num = float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return str(val)
    if num == 0:
        return ""
    return _numeric_format_number(num)


class GridColumn:
    def __init__(self, name, label, width, flags=None):
        if flags is None:
            flags = []
        self.name = name
        self.label = label
        self.width = width
        self.flags = flags

        self.editable = "ro" not in flags and not any(f.startswith("formula:") for f in flags)
        self.numeric = "num" in flags
        self.date = "date" in flags

        self.lookup = None
        self.formula = None
        self.dropdown = None  # list of str choices for combo dropdown
        self.store_field = name          # row dict key for lookup value (may differ from col.name)
        self.lookup_fill = None          # row dict key to receive display text on pick
        self.lookup_fill_map = None      # dict {src: dest} for multi-field fill
        self.lookup_fill_resolve = True  # True: resolve clean text via lookup_handler
        self.pick_key_label = "Code"     # picker left-column header
        self.pick_desc_label = "Description"  # picker right-column header
        self.lookup_list_cols = None    # list of {id, label, width} for multi-column picker

        for f in flags:
            if f.startswith("lookup:"):
                self.lookup = f.split(":", 1)[1]
            if f.startswith("formula:"):
                self.formula = f.split(":", 1)[1]
            if f.startswith("dropdown:"):
                base = [s.strip() for s in f.split(":", 1)[1].split(",") if s.strip()]
                self.dropdown = merge_custom_options(name, base)
            if f.startswith("store:"):
                self.store_field = f.split(":", 1)[1]
            if f.startswith("lookupfill_map:"):
                # Multi-field fill: "lookupfill_map:src=>dest,src2=>dest2"
                map_str = f.split(":", 1)[1]
                self.lookup_fill_map = {}
                for pair in map_str.split(","):
                    if "=>" in pair:
                        src, dest = pair.split("=>", 1)
                        self.lookup_fill_map[src.strip()] = dest.strip()
                    else:
                        self.lookup_fill_map[pair.strip()] = pair.strip()
            elif f.startswith("lookupfill:"):
                parts = f.split(":", 2)
                self.lookup_fill = parts[1]
                if len(parts) > 2 and parts[2] == "picker":
                    self.lookup_fill_resolve = False
            if f.startswith("pickkey:"):
                self.pick_key_label = f.split(":", 1)[1]
            if f.startswith("pickdesc:"):
                self.pick_desc_label = f.split(":", 1)[1]

        self.type = "numeric" if self.numeric else "date" if self.date else "text"
        self.original_editable = self.editable

        # EhLib extensions
        self.sort_order = SORT_NONE
        self.footer_type = AGG_SUM if self.numeric else AGG_NONE
        self.filter_text = ""
        self.frozen = "frozen" in flags
        self.alignment = "right" if self.numeric else "left"
        self.min_width = 4
        self.visible = True
        self.validator = None


class ColumnConfigDialog:
    """Popup dialog for toggling column visibility and reordering columns.

    Keys: Space=toggle, +/-=reorder, Enter=apply, ESC=cancel.
    """

    CLR_CFG_BORDER = 50
    CLR_CFG_NORMAL = 51
    CLR_CFG_SELECTED = 52
    CLR_CFG_HEADER = 53
    CLR_CFG_DIM = 54
    _colors_ready = False

    @classmethod
    def _init_colors(cls):
        # Colors initialized centrally by tui.themes.init_colors()
        cls._colors_ready = True

    def __init__(self, stdscr, columns):
        """columns: list of GridColumn objects from the grid."""
        self.stdscr = stdscr
        # Work on a mutable copy of column order
        self.cols = list(columns)
        self.sel = 0
        self.reordered = False

    def run(self):
        """Show dialog. Returns True if changes applied, False if cancelled."""
        self._init_colors()
        max_y, max_x = self.stdscr.getmaxyx()

        # Sizing
        name_w = max(len(c.label) for c in self.cols) + 2
        w = min(max_x - 4, max(name_w + 14, 40))
        h = min(max_y - 4, len(self.cols) + 5)
        visible = h - 5  # border + title + header + hint + border
        y = max(0, (max_y - h) // 2)
        x = max(0, (max_x - w) // 2)

        try:
            popup = curses.newwin(h, w, y, x)
        except curses.error:
            return False
        popup.keypad(True)
        popup.bkgd(' ', curses.color_pair(self.CLR_CFG_NORMAL))

        scroll = 0

        while True:
            popup.erase()

            # Border
            try:
                popup.attron(curses.color_pair(self.CLR_CFG_BORDER))
                popup.box()
                popup.attroff(curses.color_pair(self.CLR_CFG_BORDER))
            except curses.error:
                pass

            # Title
            try:
                popup.addstr(0, 2, " Column Config ",
                             curses.color_pair(self.CLR_CFG_BORDER) | curses.A_BOLD)
            except curses.error:
                pass

            # Column headers
            try:
                hdr = "  Vis  Column" + " " * (w - 16) + "Width"
                popup.addstr(1, 2, hdr[:w - 4],
                             curses.color_pair(self.CLR_CFG_HEADER) | curses.A_BOLD)
                sep = "\u2500" * (w - 2)
                popup.addstr(2, 1, sep, curses.color_pair(self.CLR_CFG_DIM))
            except curses.error:
                pass

            # Column list
            for i in range(visible):
                idx = scroll + i
                ry = 3 + i
                if ry >= h - 2:
                    break
                if idx >= len(self.cols):
                    break
                col = self.cols[idx]
                is_sel = (idx == self.sel)
                attr = curses.color_pair(self.CLR_CFG_SELECTED) | curses.A_BOLD if is_sel \
                    else curses.color_pair(self.CLR_CFG_NORMAL)

                check = "\u2713" if col.visible else " "
                width_str = str(col.width)
                label = col.label
                line = f"  [{check}]  {label}"
                padded = line[:w - 8].ljust(w - 8) + width_str.rjust(4)

                try:
                    popup.addstr(ry, 1, " " * (w - 2), attr)
                    popup.addstr(ry, 2, padded[:w - 4], attr)
                except curses.error:
                    pass

            # Scrollbar
            if len(self.cols) > visible and visible > 0:
                sb_h = max(1, visible * visible // len(self.cols))
                sb_pos = scroll * visible // len(self.cols)
                for si in range(visible):
                    ry = 3 + si
                    if ry >= h - 2:
                        break
                    ch = "\u2588" if sb_pos <= si < sb_pos + sb_h else "\u2502"
                    try:
                        popup.addstr(ry, w - 1, ch, curses.color_pair(self.CLR_CFG_DIM))
                    except curses.error:
                        pass

            # Hint bar
            try:
                hint = " Spc:Toggle  +/-:Move  Enter:Apply  ESC:Cancel "
                popup.addstr(h - 1, 2, hint[:w - 4],
                             curses.color_pair(self.CLR_CFG_DIM))
            except curses.error:
                pass

            popup.refresh()
            key = popup.getch()

            if key == 27:  # ESC — cancel
                break
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):  # Enter — apply
                # Clean up and return True
                try:
                    popup.erase()
                    popup.refresh()
                    self.stdscr.touchwin()
                    self.stdscr.refresh()
                except curses.error:
                    pass
                return True
            elif key == curses.KEY_UP:
                if self.sel > 0:
                    self.sel -= 1
                    if self.sel < scroll:
                        scroll = self.sel
            elif key == curses.KEY_DOWN:
                if self.sel < len(self.cols) - 1:
                    self.sel += 1
                    if self.sel >= scroll + visible:
                        scroll = self.sel - visible + 1
            elif key == curses.KEY_PPAGE:
                self.sel = max(0, self.sel - visible)
                scroll = max(0, scroll - visible)
            elif key == curses.KEY_NPAGE:
                self.sel = min(len(self.cols) - 1, self.sel + visible)
                scroll = min(max(0, len(self.cols) - visible), scroll + visible)
            elif key == ord(' '):
                # Toggle visibility (keep at least 1 visible)
                col = self.cols[self.sel]
                vis_count = sum(1 for c in self.cols if c.visible)
                if col.visible and vis_count <= 1:
                    pass  # can't hide the last column
                else:
                    col.visible = not col.visible
            elif key in (ord('+'), ord('=')):
                # Move column down
                if self.sel < len(self.cols) - 1:
                    self.cols[self.sel], self.cols[self.sel + 1] = \
                        self.cols[self.sel + 1], self.cols[self.sel]
                    self.sel += 1
                    if self.sel >= scroll + visible:
                        scroll = self.sel - visible + 1
                    self.reordered = True
            elif key in (ord('-'), ord('_')):
                # Move column up
                if self.sel > 0:
                    self.cols[self.sel], self.cols[self.sel - 1] = \
                        self.cols[self.sel - 1], self.cols[self.sel]
                    self.sel -= 1
                    if self.sel < scroll:
                        scroll = self.sel
                    self.reordered = True

        # Cancelled — clean up
        try:
            popup.erase()
            popup.refresh()
            self.stdscr.touchwin()
            self.stdscr.refresh()
        except curses.error:
            pass
        return False


class Grid:
    # Color pair IDs share the grid band (20-35) reserved by tui/themes.py.
    CLR_HEADER = 20
    CLR_SELECTED = 21
    CLR_ALT_ROW = 22
    CLR_FOOTER = 23
    CLR_FILTER = 24
    CLR_INDICATOR = 25
    CLR_EDIT = 26
    CLR_BOOKMARK = 27
    CLR_BORDER = 28
    CLR_SORT_IND = 29
    CLR_COEDIT = 30  # column-edit mode (yellow entry column)
    CLR_HL_ERROR = 31
    CLR_HL_WARN = 32
    CLR_HL_HILITE = 33
    CLR_HL_SUCCESS = 34
    CLR_HL_DIM = 35

    # Indicator column width
    INDICATOR_W = 3

    def __init__(self, stdscr, columns, data, lookup_handler=None,
                 start_y=5, start_x=2, show_totals=True, max_height=0,
                 max_width=0):
        self.stdscr = stdscr
        self.columns = columns
        self.data = data
        self.lookup_handler = lookup_handler
        self.start_y = start_y
        self.start_x = start_x
        self.show_totals = show_totals
        self.max_height = max_height  # 0 = use full terminal height
        self.max_width = max_width    # 0 = use full terminal width
        self._skip_autofit = False

        # Cursor
        self.row = 0
        self.col = 0

        # Edit state
        self.edit_mode = False
        self.edit_buffer = ""
        self.edit_pos = 0

        # Scroll
        self.scroll_offset = 0
        self.visible_rows = 0
        self.h_scroll_offset = 0

        # Modes
        self.row_select_mode = all(not c.editable for c in columns)
        self.inline_edit_mode = False
        self.editable_column_index = 0
        self.coedit_mode = False
        self.save_requested = False
        self.coedit_col_idx = -1
        self.coedit_column_name = ""
        self._coedit_saved_editable = {}
        self._coedit_pending_start = False

        # Selection
        self.selected_rows = set()
        self.bookmarked_rows = set()

        # Deletion undo
        self.confirm_row_deletion = False
        self.deleted_rows_log = []

        # Status
        self.status_message = ""
        self.status_until = 0.0

        # Sort
        self.sort_column = None
        self.sort_ascending = True

        # Filter
        self.show_filter_row = False
        self.filter_mode = False
        self.filter_col = 0
        self.filter_edit_buffer = ""

        # View indirection
        self._view_indices = list(range(len(self.data)))

        # Mouse
        self._header_rects = []
        self._cell_rects = []
        self._indicator_rects = []

        # Callbacks
        self.on_cell_edit = None
        self.on_selection_change = None
        self.on_row_delete = None
        self.on_lookup_pick = None  # fn(col, lookup_name, items, current_value) -> picked_key or None
        self.on_need_new_row = None  # fn() -> called when Enter/Tab wraps past last col of last row
        self.on_uom_toggle = None  # fn() -> save current edit and toggle row UOM
        self.row_attr_fn = None     # fn(row_data) -> curses attr or None

        # Unicode border chars
        self._tl = "┌"
        self._tr = "┐"
        self._bl = "└"
        self._br = "┘"
        self._h = "─"
        self._v = "│"
        self._t_down = "┬"
        self._t_up = "┴"
        self._t_right = "├"
        self._t_left = "┤"
        self._cross = "┼"

        self._columns_stretched = False

        # Init
        self._init_colors()
        self.update_dimensions()  # Must run before _rebuild_view so visible_rows is set
        self._autofit_columns()
        self._stretch_columns_to_fit()
        for row in self.data:
            self.compute_row(row)
        self._rebuild_view()
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    # ==================== INIT ====================

    def _init_colors(self):
        # Colors are now initialized centrally by tui.themes.init_colors().
        # This method kept for backward compat — just a no-op.
        pass

    # ==================== SAFE RENDERING ====================

    def _safe_addstr(self, y, x, text, attr=curses.A_NORMAL):
        if y < 0 or y >= self.max_y or x < 0 or x >= self.max_x:
            return
        available = self.max_x - x
        if available <= 0:
            return
        # Strip newlines/tabs — they cause curses to wrap to next line
        text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        text = text[:available]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _field_value(self, col, row_data):
        """Read a column's value from a row, using store_field for lookup columns."""
        if col.lookup:
            return row_data.get(col.store_field, row_data.get(col.name))
        return row_data.get(col.name)

    def _set_field_value(self, col, row_data, value):
        """Write a column's value to a row, using store_field for lookup columns."""
        if col.lookup:
            row_data[col.store_field] = value
        else:
            row_data[col.name] = value

    # ==================== VIEW INDIRECTION ====================

    def _rebuild_view(self):
        """Rebuild the view index from filters and sort state."""
        indices = list(range(len(self.data)))

        # Apply per-column filters
        for col in self.columns:
            if col.filter_text:
                indices = [i for i in indices if self._matches_filter(col, self.data[i])]

        # Apply sort
        if self.sort_column is not None and 0 <= self.sort_column < len(self.columns):
            col = self.columns[self.sort_column]
            indices.sort(key=lambda i: self._sort_key(col, self.data[i]),
                         reverse=not self.sort_ascending)

        self._view_indices = indices

        # Clamp cursor
        if self._view_indices:
            self.row = max(0, min(self.row, len(self._view_indices) - 1))
        else:
            self.row = 0
        self.ensure_visible()

    def _matches_filter(self, col, row_data):
        """Check if a row matches a column's filter."""
        ft = col.filter_text.strip()
        if not ft:
            return True
        val = self._field_value(col, row_data)
        if val is None:
            val = ""

        if col.numeric:
            try:
                num_val = float(str(val).replace(",", ""))
            except (ValueError, TypeError):
                num_val = 0.0
            # Range filter: N-M
            if "-" in ft and not ft.startswith("-"):
                parts = ft.split("-", 1)
                try:
                    lo, hi = float(parts[0]), float(parts[1])
                    return lo <= num_val <= hi
                except (ValueError, IndexError):
                    pass
            # Comparison filters
            for op, fn in [(">=", lambda n, t: n >= t), ("<=", lambda n, t: n <= t),
                           (">", lambda n, t: n > t), ("<", lambda n, t: n < t),
                           ("=", lambda n, t: n == t)]:
                if ft.startswith(op):
                    try:
                        threshold = float(ft[len(op):])
                        return fn(num_val, threshold)
                    except ValueError:
                        pass
            # Plain number: exact match
            try:
                return num_val == float(ft)
            except ValueError:
                return True
        else:
            # Text: case-insensitive substring
            return ft.lower() in str(val).lower()

    def _sort_key(self, col, row_data):
        """Return a sort key for a row based on a column."""
        val = self._field_value(col, row_data)
        if val is None:
            return (1, "")  # Nulls last
        if col.numeric:
            try:
                return (0, float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                return (0, 0.0)
        return (0, str(val).lower())

    def _view_row(self, view_idx):
        """Get the actual data row for a view index."""
        if 0 <= view_idx < len(self._view_indices):
            return self.data[self._view_indices[view_idx]]
        return None

    def _data_index(self, view_idx):
        """Get the actual data index for a view index."""
        if 0 <= view_idx < len(self._view_indices):
            return self._view_indices[view_idx]
        return -1

    # ==================== DIMENSIONS ====================

    def _autofit_columns(self):
        """Set column widths based on actual data in first 10 rows.

        Uses max(header_len, max_data_len) as the base width, clamped to
        a minimum of 4 and maximum of 40. Only runs on listing grids
        (row_select_mode) to avoid resizing editable detail grids.
        """
        if not self.row_select_mode or not self.data or getattr(self, '_skip_autofit', False):
            return
        sample = self.data[:10]
        for c in self.columns:
            if not c.visible:
                continue
            best = len(c.label)
            for row in sample:
                val = row.get(c.name)
                if val is not None:
                    best = max(best, len(str(val)))
            c.width = max(4, min(best, 40))

    def _stretch_columns_to_fit(self):
        """Proportionally stretch columns to fill available terminal width.

        DSL widths are treated as relative weights. Each visible column is
        scaled up so the grid fills the terminal. On first call, stores
        original DSL widths so re-stretch on resize uses correct ratios.
        """
        visible = [c for c in self.columns if c.visible]
        if not visible:
            return

        # Save original DSL widths on first call (before any stretching)
        if not self._columns_stretched:
            self._columns_stretched = True
            for c in self.columns:
                c._dsl_width = c.width

        # Available width: terminal minus indicator, borders, and inter-column gaps
        # Last column also has a trailing gap, so subtract len(visible) not len-1
        avail = self.max_x - self.start_x - self.INDICATOR_W - 1
        avail -= len(visible)

        # Use original DSL widths as proportional weights
        total_dsl = sum(getattr(c, '_dsl_width', c.width) for c in visible)
        if total_dsl <= 0:
            return

        # Scale proportionally — up or down — to fill available width
        scale = avail / total_dsl
        new_widths = []
        for c in visible:
            dsl_w = getattr(c, '_dsl_width', c.width)
            new_widths.append(max(4, int(dsl_w * scale)))

        # Distribute leftover pixels to widest columns first
        used = sum(new_widths)
        remainder = avail - used
        if remainder > 0:
            indices = sorted(range(len(visible)), key=lambda i: -new_widths[i])
            for i in indices:
                if remainder <= 0:
                    break
                new_widths[i] += 1
                remainder -= 1

        for c, w in zip(visible, new_widths):
            c.width = w

    def update_dimensions(self):
        self.max_y, self.max_x = self.stdscr.getmaxyx()
        # Constrain right boundary if max_width is set
        if self.max_width > 0:
            self.max_x = min(self.max_x, self.start_x + self.max_width)
        # Constrain bottom boundary if max_height is set
        if self.max_height > 0:
            self.max_y = min(self.max_y, self.start_y + self.max_height)
        # Lines consumed above data rows:
        #   header(1) + filter(0 or 1) + header separator(1) = 2 or 3
        above = 2 + (1 if self.show_filter_row else 0)
        # Lines consumed below data rows:
        #   footer separator(1) + footer(1) + bottom border(1) + status bar(1)
        #   OR just bottom border(1) + status bar(1) if no totals
        #   Embedded grids (max_height>0) skip the status bar
        below = 1 if self.max_height > 0 else 2
        if self.show_totals:
            below += 2  # footer separator + footer row
        self.visible_rows = max(1, self.max_y - self.start_y - above - below)

    # ==================== STATUS ====================

    def set_status(self, message, duration=3):
        self.status_message = message
        self.status_until = time.monotonic() + duration

    def get_status(self):
        if self.status_message and time.monotonic() > self.status_until:
            self.status_message = ""
        return self.status_message

    # ==================== MODES ====================

    def set_inline_edit_mode(self, enabled, column_index=0):
        self.inline_edit_mode = enabled
        self.editable_column_index = column_index
        if enabled:
            self.col = column_index

    def _column_index(self, column_name):
        """Find column index by field name."""
        for i, col in enumerate(self.columns):
            if col.name == column_name:
                return i
        return -1

    def coedit(self, column_name):
        """Column-edit mode: one yellow vertical column for data entry.

        All other columns become read-only. Enter moves down the same column
        (web-order style). ESC exits column-edit mode.

        Args:
            column_name: GridColumn.name or store_field to edit (e.g. 'qty').

        Returns:
            True if mode started, False if column not found.
        """
        ci = self._column_index(column_name)
        if ci < 0:
            self.set_status(f"CoEdit: column '{column_name}' not found")
            return False

        if self.coedit_mode:
            self.exit_coedit()

        self.filter_mode = False
        self.inline_edit_mode = False

        self._coedit_saved_editable = {}
        for i, col in enumerate(self.columns):
            self._coedit_saved_editable[i] = col.editable
            col.editable = (i == ci)

        self.coedit_mode = True
        self.coedit_col_idx = ci
        self.coedit_column_name = column_name
        self.col = ci
        self.row = 0
        self.scroll_offset = 0
        self.ensure_visible()
        self._ensure_col_visible()
        self._coedit_pending_start = True
        self.set_status(f"CoEdit: {self.columns[ci].label} — type qty, Enter: next row")
        return True

    # Alias matching typical EhLib-style naming
    CoEdit = coedit

    def coedit_tick(self):
        """Call after draw() + refresh() to open row 1 for typing.

        Starts inline edit on the first row of the CoEdit column once the
        screen has been painted (so the yellow cell is visible).
        """
        if not self._coedit_pending_start or not self.coedit_mode:
            return
        self._coedit_pending_start = False
        if self.edit_mode:
            return
        if not self._view_indices:
            self.set_status("CoEdit: no rows to edit")
            return
        self.row = 0
        self.scroll_offset = 0
        self.ensure_visible()
        self._ensure_coedit_column()
        self._ensure_col_visible()
        self.start_edit()

    def exit_coedit(self):
        """Leave column-edit mode and restore column editability."""
        if not self.coedit_mode:
            return
        self._coedit_pending_start = False
        if self.edit_mode:
            self.edit_mode = False
            curses.curs_set(0)
        for i, col in enumerate(self.columns):
            if i in self._coedit_saved_editable:
                col.editable = self._coedit_saved_editable[i]
        self._coedit_saved_editable = {}
        self.coedit_mode = False
        self.coedit_col_idx = -1
        self.coedit_column_name = ""
        self.set_status("CoEdit off")

    def _ensure_coedit_column(self):
        if self.coedit_mode and 0 <= self.coedit_col_idx < len(self.columns):
            self.col = self.coedit_col_idx
            self._ensure_col_visible()

    def _coedit_advance(self, start_edit_next=True):
        """Save done — move down the coedit column; optionally open next cell."""
        self._ensure_coedit_column()
        if not self._view_indices:
            return
        if self.row < len(self._view_indices) - 1:
            self.row += 1
            self.ensure_visible()
            if start_edit_next:
                self.start_edit()
        else:
            self.set_status("End of column")

    def set_confirm_deletion(self, enabled):
        self.confirm_row_deletion = enabled

    # ==================== DATA OPS ====================

    def add_row(self, row_data):
        self.data.append(row_data)
        self.compute_row(row_data)
        self._rebuild_view()
        # Move to the new row, first column
        new_data_idx = len(self.data) - 1
        if new_data_idx in self._view_indices:
            self.row = self._view_indices.index(new_data_idx)
        else:
            self.row = len(self._view_indices) - 1
        visible_cols = [i for i, c in enumerate(self.columns) if c.visible]
        if visible_cols:
            self.col = visible_cols[0]
        self.ensure_visible()

    def delete_current_row(self):
        if not self._view_indices:
            return False
        data_idx = self._data_index(self.row)
        if data_idx < 0:
            return False
        if len(self.data) <= 1:
            self.set_status("Cannot delete the last row")
            return False

        deleted = self.data[data_idx].copy()
        self.deleted_rows_log.append({"index": data_idx, "data": deleted})

        # Remove from selections
        self.selected_rows.discard(data_idx)
        self.bookmarked_rows.discard(data_idx)

        if self.on_row_delete:
            self.on_row_delete(data_idx, deleted)

        del self.data[data_idx]

        # Shift selection/bookmark indices that were above the deleted row
        self.selected_rows = {i if i < data_idx else i - 1 for i in self.selected_rows}
        self.bookmarked_rows = {i if i < data_idx else i - 1 for i in self.bookmarked_rows}

        self._rebuild_view()
        self.set_status("Row deleted (F5 to undo)")
        return True

    def undo_last_deletion(self):
        if not self.deleted_rows_log:
            self.set_status("Nothing to undo")
            return False
        last = self.deleted_rows_log.pop()
        idx = min(last["index"], len(self.data))
        self.data.insert(idx, last["data"])

        # Shift selection/bookmark indices
        self.selected_rows = {i if i < idx else i + 1 for i in self.selected_rows}
        self.bookmarked_rows = {i if i < idx else i + 1 for i in self.bookmarked_rows}

        self._rebuild_view()
        if idx in self._view_indices:
            self.row = self._view_indices.index(idx)
        self.ensure_visible()
        self.set_status("Row restored")
        return True

    # ==================== SCROLLING ====================

    def ensure_visible(self):
        total = len(self._view_indices)
        if total == 0:
            self.scroll_offset = 0
            return
        if self.row < self.scroll_offset:
            self.scroll_offset = self.row
        elif self.row >= self.scroll_offset + self.visible_rows:
            self.scroll_offset = self.row - self.visible_rows + 1
        max_scroll = max(0, total - self.visible_rows)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

    def _get_visible_columns(self):
        """Return (frozen_cols, scrollable_cols) as lists of (col_index, col)."""
        frozen = []
        scrollable = []
        for i, col in enumerate(self.columns):
            if not col.visible:
                continue
            if col.frozen:
                frozen.append((i, col))
            else:
                scrollable.append((i, col))
        return frozen, scrollable

    def _get_rendered_columns(self):
        """Return the columns to render with their x positions, respecting h_scroll."""
        frozen, scrollable = self._get_visible_columns()

        rendered = []
        x = self.start_x + self.INDICATOR_W

        # Frozen columns first
        for ci, col in frozen:
            rendered.append((ci, col, x, False))
            x += col.width + 1

        # Separator position
        if frozen and scrollable:
            self._frozen_sep_x = x - 1
        else:
            self._frozen_sep_x = -1

        # Scrollable columns from h_scroll_offset
        visible_scrollable = scrollable[self.h_scroll_offset:]
        for ci, col in visible_scrollable:
            if x + col.width > self.max_x:
                break
            rendered.append((ci, col, x, True))
            x += col.width + 1

        return rendered

    # ==================== DRAWING ====================

    def draw(self):
        self._init_colors()
        self.update_dimensions()
        self._header_rects = []
        self._cell_rects = []
        self._indicator_rects = []

        rendered_cols = self._get_rendered_columns()

        self._draw_frame(rendered_cols)
        self._draw_header(rendered_cols)
        if self.show_filter_row:
            self._draw_filter_row(rendered_cols)
        self._draw_data(rendered_cols)
        if self.show_totals:
            self._draw_footer(rendered_cols)
        self._draw_scrollbar()
        self._draw_status_bar()

    def _draw_frame(self, rendered_cols):
        """Draw the Unicode border frame around the grid."""
        if self.start_y < 1:
            return

        # Calculate frame dimensions
        if not rendered_cols:
            return

        first_x = self.start_x + self.INDICATOR_W - 1
        last_col = rendered_cols[-1]
        last_x = last_col[2] + last_col[1].width

        data_rows = min(len(self._view_indices) - self.scroll_offset, self.visible_rows)
        data_rows = max(data_rows, 0)

        header_rows = 1
        filter_rows = 1 if self.show_filter_row else 0
        separator_row = 1  # line between header and data
        footer_rows = 2 if self.show_totals else 0  # separator line + footer data

        ty = self.start_y - 1
        by = self.start_y + header_rows + filter_rows + separator_row + data_rows + footer_rows

        border_attr = curses.color_pair(self.CLR_BORDER)

        # Top border
        self._safe_addstr(ty, first_x, self._tl, border_attr)
        for x in range(first_x + 1, min(last_x, self.max_x)):
            self._safe_addstr(ty, x, self._h, border_attr)
        self._safe_addstr(ty, last_x, self._tr, border_attr)

        # Bottom border
        self._safe_addstr(by, first_x, self._bl, border_attr)
        for x in range(first_x + 1, min(last_x, self.max_x)):
            self._safe_addstr(by, x, self._h, border_attr)
        self._safe_addstr(by, last_x, self._br, border_attr)

        # Side borders
        for y in range(ty + 1, by):
            self._safe_addstr(y, first_x, self._v, border_attr)
            self._safe_addstr(y, last_x, self._v, border_attr)

        # Header separator line
        sep_y = self.start_y + header_rows + filter_rows
        self._safe_addstr(sep_y, first_x, self._t_right, border_attr)
        for x in range(first_x + 1, min(last_x, self.max_x)):
            self._safe_addstr(sep_y, x, self._h, border_attr)
        self._safe_addstr(sep_y, last_x, self._t_left, border_attr)

        # Footer separator line (between last data row and footer row)
        footer_sep_y = -1
        if self.show_totals and data_rows > 0:
            footer_sep_y = by - 2
            self._safe_addstr(footer_sep_y, first_x, self._t_right, border_attr)
            for x in range(first_x + 1, min(last_x, self.max_x)):
                self._safe_addstr(footer_sep_y, x, self._h, border_attr)
            self._safe_addstr(footer_sep_y, last_x, self._t_left, border_attr)

        # Horizontal separator lines to skip (they get ┼ instead of │)
        h_lines = {sep_y}
        if footer_sep_y >= 0:
            h_lines.add(footer_sep_y)

        # Column separators — full height through all rows
        for ci, col, cx, is_scrollable in rendered_cols[:-1]:
            sep_x = cx + col.width
            if first_x < sep_x < last_x:
                self._safe_addstr(ty, sep_x, self._t_down, border_attr)
                self._safe_addstr(by, sep_x, self._t_up, border_attr)
                # Draw ┼ at horizontal separator intersections
                for hl in h_lines:
                    self._safe_addstr(hl, sep_x, self._cross, border_attr)
                # Draw │ through all other rows
                for y in range(ty + 1, by):
                    if y not in h_lines:
                        self._safe_addstr(y, sep_x, self._v, border_attr)

    def _draw_header(self, rendered_cols):
        """Draw column headers with sort indicators."""
        y = self.start_y
        header_attr = curses.color_pair(self.CLR_HEADER) | curses.A_BOLD

        # Indicator column header (2 chars, leaving room for frame border)
        self._safe_addstr(y, self.start_x, " " * (self.INDICATOR_W - 1), header_attr)

        for ci, col, x, is_scrollable in rendered_cols:
            # Build header text with sort indicator
            sort_ind = ""
            if self.sort_column == ci:
                sort_ind = " \u25b2" if self.sort_ascending else " \u25bc"

            label = col.label
            avail = col.width - len(sort_ind)
            if len(label) > avail:
                label = label[:avail]
            if col.numeric:
                txt = (label + sort_ind).rjust(col.width)
            else:
                txt = (label + sort_ind).ljust(col.width)

            attr = header_attr
            if self.coedit_mode and ci == self.coedit_col_idx:
                attr = curses.color_pair(self.CLR_COEDIT) | curses.A_BOLD
            elif self.inline_edit_mode and ci == self.editable_column_index:
                attr |= curses.A_UNDERLINE

            self._safe_addstr(y, x, txt, attr)
            self._header_rects.append((y, x, y, x + col.width - 1, ci))

    def _draw_filter_row(self, rendered_cols):
        """Draw the per-column filter input row."""
        y = self.start_y + 1
        filter_attr = curses.color_pair(self.CLR_FILTER)

        # Indicator area (2 chars, leaving room for frame border)
        self._safe_addstr(y, self.start_x, "F ", filter_attr | curses.A_BOLD)

        for ci, col, x, is_scrollable in rendered_cols:
            ft = col.filter_text
            if self.filter_mode and self.filter_col == ci:
                # Show cursor in active filter
                disp = self.filter_edit_buffer
                if len(disp) >= col.width:
                    disp = disp[-(col.width - 1):]
                disp = (disp + "_").ljust(col.width)
                attr = filter_attr | curses.A_BOLD
            else:
                disp = ft[:col.width].ljust(col.width) if ft else " ".ljust(col.width)
                attr = filter_attr
            self._safe_addstr(y, x, disp, attr)

    def _draw_data(self, rendered_cols):
        """Draw the data rows with indicators, zebra striping, and selection."""
        data_y_start = self.start_y + 1 + (1 if self.show_filter_row else 0) + 1
        total = len(self._view_indices)

        if total == 0:
            msg = "No matching rows"
            if any(c.filter_text for c in self.columns):
                msg += "  (Ctrl+L to clear filters)"
            self._safe_addstr(data_y_start, self.start_x + self.INDICATOR_W,
                              msg, curses.A_DIM)
            return

        end = min(self.scroll_offset + self.visible_rows, total)

        for vi in range(self.scroll_offset, end):
            y = data_y_start + (vi - self.scroll_offset)
            if y >= self.max_y - 1:
                break

            data_idx = self._view_indices[vi]
            row_data = self.data[data_idx]

            # Draw indicator
            self._draw_indicator(y, vi, data_idx)
            self._indicator_rects.append((y, self.start_x, y, self.start_x + self.INDICATOR_W - 1, vi))

            # Draw cells
            for ci, col, x, is_scrollable in rendered_cols:
                disp = self._cell_display(col, row_data)
                attr = self._get_attr(vi, ci, data_idx)
                w = min(col.width, self.max_x - x)
                if w <= 0:
                    continue

                # Align text
                if col.alignment == "right":
                    cell_text = disp[:w].rjust(w)
                elif col.alignment == "center":
                    cell_text = disp[:w].center(w)
                else:
                    cell_text = disp[:w].ljust(w)

                self._safe_addstr(y, x, cell_text, attr)
                self._cell_rects.append((y, x, y, x + w - 1, vi, ci))

    def _draw_indicator(self, y, view_idx, data_idx):
        """Draw the row indicator glyph (2 chars, leaving room for frame border)."""
        is_current = (view_idx == self.row)
        is_selected = data_idx in self.selected_rows
        is_bookmarked = data_idx in self.bookmarked_rows

        if is_current and self.edit_mode:
            glyph = "\u270e "  # ✎
            attr = curses.color_pair(self.CLR_EDIT) | curses.A_BOLD
        elif is_current:
            glyph = "\u25ba "  # ►
            attr = curses.color_pair(self.CLR_INDICATOR) | curses.A_BOLD
        elif is_bookmarked:
            glyph = "\u2691 "  # ⚑
            attr = curses.color_pair(self.CLR_BOOKMARK) | curses.A_BOLD
        elif is_selected:
            glyph = "\u2713 "  # ✓
            attr = curses.color_pair(self.CLR_SELECTED) | curses.A_BOLD
        else:
            glyph = "  "
            attr = curses.A_NORMAL

        self._safe_addstr(y, self.start_x, glyph, attr)
        # Re-draw the frame border that separates indicator from data
        border_x = self.start_x + self.INDICATOR_W - 1
        self._safe_addstr(y, border_x, self._v, curses.color_pair(self.CLR_BORDER))

    def _get_attr(self, view_idx, col_idx, data_idx):
        """Determine cell attribute based on state."""
        is_current_row = (view_idx == self.row)
        is_current_cell = is_current_row and (col_idx == self.col)
        is_selected = data_idx in self.selected_rows
        is_alt = (view_idx - self.scroll_offset) % 2 == 1

        # Column-edit mode — entire column yellow, current cell bold
        if self.coedit_mode and col_idx == self.coedit_col_idx:
            attr = curses.color_pair(self.CLR_COEDIT)
            if is_current_cell and not self.edit_mode:
                attr |= curses.A_BOLD
            return attr

        # Read-only appearance while coedit active
        if self.coedit_mode:
            if is_current_cell and not self.edit_mode:
                return curses.A_DIM
            if is_alt:
                return curses.color_pair(self.CLR_ALT_ROW) | curses.A_DIM
            return curses.A_DIM

        # Full-row highlight for read-only listing grids
        if self.row_select_mode and is_current_row:
            return curses.color_pair(self.CLR_SELECTED) | curses.A_BOLD

        # Current cell highlight
        if is_current_cell and not self.edit_mode:
            return curses.color_pair(self.CLR_SELECTED) | curses.A_BOLD

        # Inline edit mode column highlight
        if self.inline_edit_mode and is_current_row and col_idx == self.editable_column_index and not self.edit_mode:
            return curses.color_pair(self.CLR_SELECTED)

        # Selected row (multi-select via Space)
        if is_selected:
            return curses.color_pair(self.CLR_SELECTED)

        # Conditional row highlight via row_attr_fn
        if self.row_attr_fn:
            try:
                custom = self.row_attr_fn(self.data[data_idx])
            except Exception:
                custom = None
            if custom is not None:
                return custom

        # Alternating row
        if is_alt:
            attr = curses.color_pair(self.CLR_ALT_ROW) | curses.A_DIM
        else:
            attr = curses.A_NORMAL

        # Bold for editable columns
        if self.columns[col_idx].editable:
            attr |= curses.A_BOLD

        return attr

    def _draw_footer(self, rendered_cols):
        """Draw the footer with configurable aggregation per column."""
        if not self._view_indices or not rendered_cols:
            return

        data_y_start = self.start_y + 1 + (1 if self.show_filter_row else 0) + 1
        data_count = min(len(self._view_indices) - self.scroll_offset, self.visible_rows)
        y = data_y_start + data_count + 1  # +1 for separator line

        if y >= self.max_y - 1:
            return

        footer_attr = curses.color_pair(self.CLR_FOOTER) | curses.A_BOLD

        # Indicator area
        # Footer indicator (2 chars, leaving room for frame border)
        self._safe_addstr(y, self.start_x, "\u03a3 ", footer_attr)  # Σ

        # Get values from the view (filtered data)
        view_data = [self.data[i] for i in self._view_indices]

        for ci, col, x, is_scrollable in rendered_cols:
            ft = col.footer_type
            if ft == AGG_NONE:
                self._safe_addstr(y, x, " " * col.width, footer_attr)
                continue

            vals = []
            for rd in view_data:
                v = rd.get(col.name)
                if v is not None and v != "":
                    if col.numeric:
                        try:
                            vals.append(float(str(v).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    else:
                        vals.append(v)

            txt = ""
            if ft == AGG_SUM and col.numeric and vals:
                total = _numeric_round(sum(vals))
                txt = _numeric_format_number(total) if total != 0 else ""
            elif ft == AGG_COUNT:
                txt = f"#{len(vals)}"
            elif ft == AGG_AVG and col.numeric and vals:
                txt = f"x\u0304{_numeric_format_number(_numeric_round(sum(vals) / len(vals)))}"
            elif ft == AGG_MIN and vals:
                if col.numeric:
                    txt = _numeric_format_number(min(vals))
                else:
                    txt = str(min(vals, key=str))
            elif ft == AGG_MAX and vals:
                if col.numeric:
                    txt = _numeric_format_number(max(vals))
                else:
                    txt = str(max(vals, key=str))

            if col.alignment == "right" or col.numeric:
                txt = txt.rjust(col.width)
            else:
                txt = txt.ljust(col.width)

            self._safe_addstr(y, x, txt[:col.width], footer_attr)

    def _draw_scrollbar(self):
        """Draw vertical and horizontal scroll indicators."""
        total = len(self._view_indices)
        if total > self.visible_rows:
            max_scroll = total - self.visible_rows
            pct = int((self.scroll_offset / max_scroll) * 100) if max_scroll > 0 else 0
            txt = f" {self.scroll_offset + 1}-{min(self.scroll_offset + self.visible_rows, total)}/{total} ({pct}%) "
            self._safe_addstr(self.max_y - 2, self.max_x - len(txt) - 1, txt,
                              curses.color_pair(self.CLR_HEADER))

        # Horizontal scroll indicator
        _, scrollable = self._get_visible_columns()
        if self.h_scroll_offset > 0 or len(scrollable) > 0:
            visible_count = sum(1 for _, c, _, s in self._get_rendered_columns() if s)
            total_scrollable = len(scrollable)
            if total_scrollable > visible_count:
                h_txt = f" \u25c4 {self.h_scroll_offset + 1}/{total_scrollable} \u25ba "
                self._safe_addstr(self.max_y - 2, self.start_x, h_txt,
                                  curses.color_pair(self.CLR_HEADER))

    def _draw_status_bar(self):
        """Draw the status bar at the bottom of the screen."""
        if self.max_height > 0:
            return  # embedded grids skip status bar
        y = self.max_y - 1
        msg = self.get_status()

        # Build info sections
        total = len(self._view_indices)
        full_total = len(self.data)
        pos = f"Row {self.row + 1}/{total}" if total > 0 else "No data"
        if total < full_total:
            pos += f" (filtered from {full_total})"

        sel_count = len(self.selected_rows)
        sel_info = f"  Sel: {sel_count}" if sel_count > 0 else ""

        col_info = ""
        if 0 <= self.col < len(self.columns):
            col_info = f"  Col: {self.columns[self.col].label}"

        left = f" {pos}{sel_info}{col_info}"
        right = ""
        if msg:
            right = f" {msg} "

        # Sort indicator
        sort_info = ""
        if self.sort_column is not None:
            sc = self.columns[self.sort_column]
            arrow = "\u25b2" if self.sort_ascending else "\u25bc"
            sort_info = f"  Sort: {sc.label}{arrow}"

        bar = (left + sort_info).ljust(self.max_x - len(right) - 1) + right
        self._safe_addstr(y, 0, bar[:self.max_x], curses.color_pair(self.CLR_HEADER))

    # ==================== FORMATTING ====================

    def format_value(self, col, val):
        if val is None or val == "":
            return ""
        if col.numeric:
            formatted = _numeric_format_number(val)
            # Show blank for zero values in display (not during editing)
            if formatted == "0":
                return ""
            return formatted
        s = str(val)
        if len(s) > col.width:
            return s[:col.width - 1] + "\u2026"
        return s

    def decorate_lookup(self, col, val):
        """Resolve lookup values to display text via lookup_handler."""
        if col.lookup and self.lookup_handler:
            display = self.lookup_handler(col.lookup, val)
            if display is not None:
                s = str(display)
                if len(s) > col.width:
                    return s[:col.width - 1] + "\u2026"
                return s
        return str(val) if val is not None else ""

    def _cell_display(self, col, row_data):
        """Format a cell for drawing.

        Lookup columns with a lookupfill companion show the stored KEY (picker
        left column). The companion column shows the NAME (picker right column).
        Single lookup columns without lookupfill still resolve to display text.
        """
        val = self._field_value(col, row_data)
        if col.lookup and (col.lookup_fill or col.lookup_fill_map):
            return self.format_value(col, val)
        if col.lookup:
            return self.decorate_lookup(col, val)
        return self.format_value(col, val)

    def compute_row(self, row):
        for col in self.columns:
            if col.formula:
                try:
                    expr = col.formula
                    for k, v in row.items():
                        if k in expr:
                            val = float(v) if isinstance(v, (int, float)) or str(v).replace(".", "", 1).replace("-", "", 1).isdigit() else 0
                            expr = re.sub(rf"\b{k}\b", str(val), expr)
                    result = eval(expr, {"__builtins__": {}, "round": round}, {})
                    row[col.name] = _numeric_round(result)
                except (ValueError, TypeError, ZeroDivisionError, SyntaxError, NameError):
                    row[col.name] = 0

    # ==================== EDITING ====================

    def _open_lookup(self, initial_search=""):
        """Open lookup picker for the current column via F4 or typing.

        Uses on_lookup_pick callback if set, otherwise falls back to
        the built-in _show_lookup_picker dialog.

        on_lookup_pick signature:
            fn(col, lookup_name, items, current_value) -> picked_key or None

            col:            GridColumn object being edited
            lookup_name:    str — the lookup identifier (e.g. "product", "warehouse")
            items:          list of (key, display_text) tuples from lookup_handler
            current_value:  str — current cell value for pre-selection

        The callback should return the selected key (str) or None if cancelled.
        """
        if not self._view_indices:
            return
        col = self.columns[self.col]
        if not col.lookup:
            self.set_status("No lookup on this column")
            return
        if not self.lookup_handler:
            return
        # Use multi-column picker if LIST columns are defined
        if col.lookup_list_cols:
            items = self.lookup_handler(col.lookup, None, list_all=True, full_record=True)
        else:
            items = self.lookup_handler(col.lookup, None, list_all=True)
        if not items:
            self.set_status("Lookup returned no items")
            return

        row_data = self._view_row(self.row)
        if row_data is None:
            return
        current = str(self._field_value(col, row_data) or "")

        # Use custom pick dialog if provided, otherwise built-in
        if self.on_lookup_pick:
            picked = self.on_lookup_pick(col, col.lookup, items, current)
        else:
            picked = self._show_lookup_picker(col, items, current, initial_search=initial_search)

        if picked is not None:
            if isinstance(picked, tuple):
                picked_key, picked_display = picked
            else:
                picked_key, picked_display = picked, None

            old_value = self._field_value(col, row_data)
            self._set_field_value(col, row_data, picked_key)

            if col.lookup_fill_map and self.lookup_handler:
                # Multi-field fill: fetch full record and map fields
                record = self.lookup_handler(col.lookup, picked_key, full_record=True)
                if isinstance(record, dict):
                    for src, dest in col.lookup_fill_map.items():
                        if src in record:
                            row_data[dest] = record[src]
            elif col.lookup_fill:
                if col.lookup_fill_resolve and self.lookup_handler:
                    fill_val = self.lookup_handler(col.lookup, picked_key)
                else:
                    fill_val = picked_display
                if fill_val is not None:
                    row_data[col.lookup_fill] = fill_val

            self.compute_row(row_data)
            if self.on_cell_edit:
                self.on_cell_edit(self._data_index(self.row), col.store_field, old_value, picked_key)
            self.move_next_column()

    def start_edit(self, initial_key=None):
        if self.edit_mode:
            return
        if not self._view_indices:
            return
        col = self.columns[self.col]
        if self.coedit_mode and self.col != self.coedit_col_idx:
            self._ensure_coedit_column()
            col = self.columns[self.col]
        if not col.editable:
            self.set_status("Column is read-only")
            return

        # Lookup columns: open picker with typed char as initial search
        if col.lookup and self.lookup_handler:
            initial_search = chr(initial_key) if initial_key and 32 <= initial_key <= 126 else ""
            self._open_lookup(initial_search=initial_search)
            return

        # Dropdown columns: open combo picker
        if col.dropdown:
            initial_search = chr(initial_key) if initial_key and 32 <= initial_key <= 126 else ""
            self._open_dropdown(initial_search=initial_search)
            return

        # Validation: check column validator before entering edit
        self.edit_mode = True
        curses.curs_set(1)

        row_data = self._view_row(self.row)
        if row_data is None:
            self.edit_mode = False
            curses.curs_set(0)
            return

        current = str(self._field_value(col, row_data) or "")
        if col.numeric:
            if current not in ("", None):
                try:
                    num = float(str(current).replace(",", ""))
                    if abs(num - round(num)) < 1e-9:
                        current = str(int(round(num)))
                    else:
                        current = f"{num:.10f}".rstrip("0").rstrip(".")
                except (ValueError, TypeError):
                    current = ""
            else:
                current = ""
        self.edit_buffer = current
        self.edit_pos = len(self.edit_buffer)

        if initial_key and 32 <= initial_key <= 126:
            ch = chr(initial_key)
            if col.numeric:
                if _numeric_char_allowed(self.edit_buffer, self.edit_pos, ch):
                    self.edit_buffer = ch
                    self.edit_pos = 1
            else:
                self.edit_buffer = ch
                self.edit_pos = 1

        self.edit_loop(col)

    def edit_loop(self, col):
        while self.edit_mode:
            self.draw_edit_field()
            k = self.stdscr.getch()

            if k == 27:  # ESC — cancel
                self.edit_mode = False
                curses.curs_set(0)
                return

            elif k in (curses.KEY_ENTER, 10, 13, curses.PADENTER):  # Enter — save
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                if self.coedit_mode:
                    self._coedit_advance(start_edit_next=True)
                else:
                    self.move_next_column()
                return

            elif k == 9:  # Tab
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                if self.coedit_mode:
                    self._coedit_advance(start_edit_next=True)
                else:
                    self.move_next_column()
                return

            elif k == curses.KEY_UP:  # Up — save + move up
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                self.move_up()
                return

            elif k == curses.KEY_DOWN:  # Down — save + move down
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                if self.coedit_mode:
                    self._coedit_advance(start_edit_next=True)
                else:
                    self.move_down()
                return

            elif k == curses.KEY_F10 or k == 19:  # F10 or Ctrl+S — save + signal
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                self.save_requested = True
                return

            elif k == 21 and self.on_uom_toggle:  # Ctrl+U — save + toggle UOM
                self.save_edit(col)
                self.edit_mode = False
                curses.curs_set(0)
                self.on_uom_toggle()
                return

            elif k == curses.KEY_BACKSPACE or k in (127, 8):
                if self.edit_pos > 0:
                    self.edit_buffer = self.edit_buffer[:self.edit_pos - 1] + self.edit_buffer[self.edit_pos:]
                    self.edit_pos -= 1

            elif k == curses.KEY_DC:
                if self.edit_pos < len(self.edit_buffer):
                    self.edit_buffer = self.edit_buffer[:self.edit_pos] + self.edit_buffer[self.edit_pos + 1:]

            elif k == curses.KEY_LEFT:
                self.edit_pos = max(0, self.edit_pos - 1)
            elif k == curses.KEY_RIGHT:
                self.edit_pos = min(len(self.edit_buffer), self.edit_pos + 1)
            elif k == curses.KEY_HOME:
                self.edit_pos = 0
            elif k == curses.KEY_END:
                self.edit_pos = len(self.edit_buffer)

            elif 32 <= k <= 126:
                ch = chr(k)
                if col.numeric and not _numeric_char_allowed(self.edit_buffer, self.edit_pos, ch):
                    continue
                if len(self.edit_buffer) < col.width * 2:
                    self.edit_buffer = (self.edit_buffer[:self.edit_pos] + ch
                                        + self.edit_buffer[self.edit_pos:])
                    self.edit_pos += 1

    def draw_edit_field(self):
        data_y_start = self.start_y + 1 + (1 if self.show_filter_row else 0) + 1
        y = data_y_start + (self.row - self.scroll_offset)

        # Find x position for the current column
        rendered = self._get_rendered_columns()
        x = self.start_x
        col = self.columns[self.col]
        for ci, c, cx, _ in rendered:
            if ci == self.col:
                x = cx
                break

        if self.coedit_mode and self.col == self.coedit_col_idx:
            edit_attr = curses.color_pair(self.CLR_COEDIT) | curses.A_BOLD
        else:
            edit_attr = curses.color_pair(self.CLR_EDIT)
        w = col.width

        if col.numeric:
            disp_full = _numeric_format_raw(self.edit_buffer)
            visible_start = max(0, len(disp_full) - w)
            visible = disp_full[visible_start:visible_start + w]
            cell_text = visible.rjust(w)
            cursor_in_full = _numeric_raw_cursor_display(self.edit_buffer, self.edit_pos)
            cursor_in_visible = cursor_in_full - visible_start
            text_start = x + (w - len(visible))
            cursor_x = text_start + max(0, min(len(visible), cursor_in_visible))
            cursor_x = max(x, min(x + w - 1, cursor_x))
        else:
            disp = self.edit_buffer
            if len(disp) > w:
                disp = disp[-w:]
            cell_text = disp.ljust(w)
            cursor_x = x + min(self.edit_pos, w - 1)

        self._safe_addstr(y, x, cell_text, edit_attr)
        try:
            self.stdscr.move(y, cursor_x)
            self.stdscr.refresh()
        except curses.error:
            pass

    def _show_lookup_picker(self, col, items, current_value, initial_search=""):
        """Show a CxGrid-based popup picker for lookup columns.

        Supports both:
        - Legacy: items = [(key, display), ...] — shows 2 columns
        - Multi-column: items = [dict, ...] with col.lookup_list_cols

        Returns:
            The selected key (str or tuple), or None if cancelled.
        """
        curses.curs_set(1)

        # ── Determine mode and build uniform data ──
        multi_col = col.lookup_list_cols and items and isinstance(items[0], dict)

        if multi_col:
            list_cols = col.lookup_list_cols
            key_field = list_cols[0]["id"] if list_cols else "code"
            for lc in list_cols:
                if lc["id"].endswith(("_code", "_no", "_id")):
                    key_field = lc["id"]
                    break
            all_data = list(items)  # already list of dicts
            grid_cols = []
            for lc in list_cols:
                flags = ["ro"]
                if lc.get("numeric"):
                    flags.append("num")
                grid_cols.append(GridColumn(
                    name=lc["id"],
                    label=lc.get("label", lc["id"].replace("_", " ").title()),
                    width=lc.get("width", 12),
                    flags=flags
                ))
        else:
            key_field = "_key"
            all_data = [{"_key": str(k), "_desc": str(d)} for k, d in items]
            key_w = col.width if col.width >= 6 else max(
                6, max((len(str(k)) for k, _ in items), default=4))
            desc_w = 20
            if col.lookup_fill:
                for c in self.columns:
                    if c.name == col.lookup_fill:
                        desc_w = max(12, c.width)
                        break
            else:
                desc_w = max(12, max((len(str(d)) for _, d in items), default=8))
            grid_cols = [
                GridColumn(name="_key",
                           label=col.pick_key_label or "Code",
                           width=key_w, flags=["ro"]),
                GridColumn(name="_desc",
                           label=col.pick_desc_label or "Description",
                           width=desc_w, flags=["ro"]),
            ]

        # ── Popup dimensions ──
        # Search box takes 3 rows above the grid (border + search + separator)
        search_rows = 3
        max_data_rows = min(len(items), self.max_y - search_rows - 8)
        max_data_rows = max(max_data_rows, 3)
        # Grid height: header(1) + separator(1) + data rows + bottom border(1)
        grid_h = max_data_rows + 3

        # Width: use 80% of terminal, capped
        max_popup_w = min(self.max_x - 4, int(self.max_x * 0.85))
        # Grid internal layout: indicator(3) + sum(col widths) + separators + right border
        n_cols = len(grid_cols)
        total_col_w = sum(c.width for c in grid_cols)
        natural_w = 3 + total_col_w + n_cols + 1
        popup_w = min(natural_w, max_popup_w)
        # Shrink columns proportionally when popup is clamped
        if popup_w < natural_w and total_col_w > 0:
            avail_col = popup_w - 3 - n_cols - 1
            if avail_col > 0:
                ratio = avail_col / total_col_w
                for c in grid_cols:
                    c.width = max(4, int(c.width * ratio))
                used = sum(c.width for c in grid_cols)
                if used < avail_col:
                    grid_cols[-1].width += avail_col - used
        grid_w = popup_w

        popup_h = search_rows + grid_h

        # Center in terminal
        popup_y = max(1, (self.max_y - popup_h) // 2)
        popup_x = max(1, (self.max_x - popup_w) // 2)
        if popup_y + popup_h >= self.max_y:
            popup_h = self.max_y - popup_y - 1
            grid_h = popup_h - search_rows
            max_data_rows = grid_h - 3
        if popup_x + popup_w >= self.max_x:
            popup_w = self.max_x - popup_x - 1
            grid_w = popup_w

        # ── Search state ──
        search = initial_search

        # ── Styles ──
        border_attr = curses.color_pair(self.CLR_BORDER) | curses.A_BOLD
        search_attr = curses.color_pair(self.CLR_EDIT)

        # ── Helper: filter and build grid ──
        def _match(row, term):
            return any(term in str(v).lower()
                       for k, v in row.items()
                       if k not in ("rowid", "_parent_rowid"))

        def _make_grid(data):
            """Create a temporary CxGrid for the picker popup."""
            cols = [GridColumn(c.name, c.label, c.width,
                    c.flags[:]) for c in grid_cols]
            # Pre-set _skip_autofit so __init__'s autofit is skipped
            g = Grid.__new__(Grid)
            g._skip_autofit = True
            Grid.__init__(g, self.stdscr, cols, data,
                          start_y=popup_y + search_rows,
                          start_x=popup_x,
                          show_totals=False,
                          max_height=grid_h,
                          max_width=grid_w)
            return g

        prev_search = None
        filtered_data = all_data
        picker_grid = _make_grid(filtered_data)

        # Pre-select current value
        if not search and current_value:
            for i, row in enumerate(filtered_data):
                if str(row.get(key_field, "")) == str(current_value):
                    picker_grid.row = i
                    # Adjust scroll
                    if i >= max_data_rows:
                        picker_grid.scroll_offset = i - max_data_rows + 1
                    break

        while True:
            # Re-filter if search changed
            if search != prev_search:
                if search:
                    term = search.lower()
                    filtered_data = [r for r in all_data if _match(r, term)]
                else:
                    filtered_data = all_data
                picker_grid = _make_grid(filtered_data)
                prev_search = search

            # ── Draw grid first to get frame boundaries ──
            picker_grid.draw()

            # Get grid frame boundaries for search box alignment
            rendered = picker_grid._get_rendered_columns()
            if rendered:
                first_x = popup_x + picker_grid.INDICATOR_W - 1
                last_col = rendered[-1]
                last_x = last_col[2] + last_col[1].width
            else:
                first_x = popup_x + 2
                last_x = popup_x + popup_w - 1

            V = "\u2502"
            frame_w = last_x - first_x + 1

            # ── Draw search box aligned to grid frame ──
            # Top border
            top_line = "\u250c" + "\u2500" * (frame_w - 2) + "\u2510"
            self._safe_addstr(popup_y, first_x, top_line, border_attr)

            # Search field
            sy = popup_y + 1
            search_line = " Search: " + search
            inner_w = frame_w - 2
            search_line = search_line[:inner_w].ljust(inner_w)
            self._safe_addstr(sy, first_x, V, border_attr)
            self._safe_addstr(sy, first_x + 1, search_line, search_attr)
            self._safe_addstr(sy, last_x, V, border_attr)

            # Connect search box to grid top border — replace ┌/┐ with ├/┤
            self._safe_addstr(popup_y + 2, first_x, "\u251c", border_attr)
            self._safe_addstr(popup_y + 2, last_x, "\u2524", border_attr)

            # Count indicator on grid bottom border
            total = len(filtered_data)
            if total:
                info = f" {picker_grid.row + 1}/{total} "
            else:
                info = " 0/0 "
            # Find bottom border y from grid
            data_rows = min(len(picker_grid._view_indices) - picker_grid.scroll_offset,
                            picker_grid.visible_rows)
            data_rows = max(data_rows, 0)
            above = 2 + (1 if picker_grid.show_filter_row else 0)
            bot_y = picker_grid.start_y + above + data_rows
            info_x = last_x - len(info)
            if info_x > first_x + 2:
                self._safe_addstr(bot_y, info_x, info, border_attr)

            # No matches message
            if not filtered_data:
                msg = " No matches "
                msg_y = popup_y + search_rows + grid_h // 2
                msg_x = first_x + (frame_w - len(msg)) // 2
                self._safe_addstr(msg_y, msg_x, msg, curses.A_DIM)

            # Position cursor in search field
            cursor_x = min(first_x + 1 + len(" Search: ") + len(search),
                           last_x - 1)
            try:
                self.stdscr.move(sy, cursor_x)
            except curses.error:
                pass

            self.stdscr.refresh()

            # === Input ===
            k = self.stdscr.getch()

            if k == 27:  # ESC
                curses.curs_set(0)
                return None
            elif k in (curses.KEY_ENTER, 10, 13, curses.PADENTER):  # Enter
                curses.curs_set(0)
                if filtered_data and picker_grid._view_indices:
                    row = picker_grid._view_row(picker_grid.row)
                    if row:
                        picked_key = str(row.get(key_field, ""))
                        if multi_col:
                            return (picked_key, None)
                        else:
                            return (picked_key, row.get("_desc", ""))
                return None
            elif k in (curses.KEY_DOWN, curses.KEY_UP,
                       curses.KEY_NPAGE, curses.KEY_PPAGE,
                       curses.KEY_HOME, curses.KEY_END):
                # Delegate navigation to grid
                picker_grid.handle_input(k)
            elif k == curses.KEY_BACKSPACE or k in (127, 8):
                if search:
                    search = search[:-1]
            elif 32 <= k <= 126:
                search += chr(k)

    def _open_dropdown(self, initial_search=""):
        """Open dropdown combo picker for the current column."""
        if not self._view_indices:
            return
        col = self.columns[self.col]
        if not col.dropdown:
            return

        row_data = self._view_row(self.row)
        if row_data is None:
            return
        current = str(row_data.get(col.name, ""))
        picked = self._show_dropdown_picker(col, col.dropdown, current, initial_search=initial_search)
        if picked is not None:
            old_value = row_data.get(col.name)
            row_data[col.name] = picked
            self.compute_row(row_data)
            if self.on_cell_edit:
                self.on_cell_edit(self._data_index(self.row), col.name, old_value, picked)
            self.move_next_column()

    def _show_dropdown_picker(self, col, choices, current_value, initial_search=""):
        """Show a compact dropdown combo picker.

        Args:
            col: GridColumn being edited.
            choices: list of str — the dropdown options.
            current_value: str — current cell value for pre-selection.
            initial_search: str — pre-fill search filter.

        Returns:
            Selected value (str) or None if cancelled.
        """
        curses.curs_set(0)

        # Dimensions
        item_w = max(col.width, max((len(s) for s in choices), default=6))
        popup_w = item_w + 4  # border + space + text + space + border
        max_visible = min(len(choices), self.max_y - 6)
        max_visible = max(max_visible, 2)
        popup_h = max_visible + 2  # top + bottom border

        # Position below current cell
        data_y_start = self.start_y + 1 + (1 if self.show_filter_row else 0) + 1
        cell_y = data_y_start + (self.row - self.scroll_offset)
        rendered = self._get_rendered_columns()
        cell_x = self.start_x
        for ci, c, cx, _ in rendered:
            if ci == self.col:
                cell_x = cx
                break

        popup_y = cell_y + 1
        if popup_y + popup_h >= self.max_y:
            popup_y = max(1, cell_y - popup_h)
        popup_x = min(cell_x, self.max_x - popup_w - 1)
        popup_x = max(0, popup_x)

        # Pre-select current value
        selected = 0
        if not initial_search:
            for i, val in enumerate(choices):
                if val == current_value:
                    selected = i
                    break
        scroll = max(0, selected - max_visible + 1)

        search = initial_search

        border_attr = curses.color_pair(self.CLR_BORDER) | curses.A_BOLD
        header_attr = curses.color_pair(self.CLR_HEADER) | curses.A_BOLD
        sel_attr = curses.color_pair(self.CLR_SELECTED) | curses.A_BOLD
        normal_attr = curses.A_NORMAL

        V = "\u2502"
        inner_w = popup_w - 2

        while True:
            # Filter
            if search:
                filtered = [v for v in choices if search.lower() in v.lower()]
            else:
                filtered = list(choices)

            if not filtered:
                filtered_display = []
            else:
                selected = max(0, min(selected, len(filtered) - 1))
                if selected < scroll:
                    scroll = selected
                elif selected >= scroll + max_visible:
                    scroll = selected - max_visible + 1
                scroll = max(0, min(scroll, max(0, len(filtered) - max_visible)))
                filtered_display = filtered[scroll:scroll + max_visible]

            # Draw
            lx = popup_x
            rx = popup_x + popup_w - 1

            # Top border
            self._safe_addstr(popup_y, lx,
                              "\u250c" + "\u2500" * inner_w + "\u2510", border_attr)
            # Bottom border
            self._safe_addstr(popup_y + popup_h - 1, lx,
                              "\u2514" + "\u2500" * inner_w + "\u2518", border_attr)

            # Items
            for ri, val in enumerate(filtered_display):
                y = popup_y + 1 + ri
                is_sel = (scroll + ri == selected)
                attr = sel_attr if is_sel else normal_attr
                txt = (" " + val)[:inner_w].ljust(inner_w)
                self._safe_addstr(y, lx, V, border_attr)
                self._safe_addstr(y, lx + 1, txt, attr)
                self._safe_addstr(y, rx, V, border_attr)

            # Fill empty rows
            for ri in range(len(filtered_display), max_visible):
                y = popup_y + 1 + ri
                self._safe_addstr(y, lx, V, border_attr)
                self._safe_addstr(y, lx + 1, " " * inner_w, normal_attr)
                self._safe_addstr(y, rx, V, border_attr)

            # No matches — hint that Enter adds new value
            if not filtered_display and search:
                msg = " Enter=Add new "
                msg_y = popup_y + 1 + max_visible // 2
                msg_x = lx + max(1, (popup_w - len(msg)) // 2)
                self._safe_addstr(msg_y, msg_x, msg, curses.A_DIM)

            # Scroll indicator
            if filtered and len(filtered) > max_visible:
                info = f" {selected + 1}/{len(filtered)} "
                self._safe_addstr(popup_y + popup_h - 1, rx - len(info), info, border_attr)

            # Title with search text
            if search:
                title = f" {search}_ "
                self._safe_addstr(popup_y, lx + 2, title[:inner_w - 2], header_attr)

            self.stdscr.refresh()

            # Input
            k = self.stdscr.getch()

            if k == 27:
                return None
            elif k == 32:  # Space — confirm selection (not search)
                if filtered:
                    return filtered[selected]
                return None
            elif k in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                if filtered:
                    return filtered[selected]
                # No match but user typed something — accept as new custom value
                if search.strip():
                    new_val = search.strip()
                    col.dropdown.append(new_val)
                    _save_custom_options(col.name, col.dropdown)
                    return new_val
                return None
            elif k == curses.KEY_DOWN:
                if filtered:
                    selected = min(selected + 1, len(filtered) - 1)
            elif k == curses.KEY_UP:
                if filtered:
                    selected = max(0, selected - 1)
            elif k == curses.KEY_NPAGE:
                if filtered:
                    selected = min(selected + max_visible, len(filtered) - 1)
            elif k == curses.KEY_PPAGE:
                if filtered:
                    selected = max(0, selected - max_visible)
            elif k == curses.KEY_HOME:
                selected = 0
                scroll = 0
            elif k == curses.KEY_END:
                if filtered:
                    selected = len(filtered) - 1
            elif k == curses.KEY_BACKSPACE or k in (127, 8):
                if search:
                    search = search[:-1]
                    selected = 0
                    scroll = 0
            elif 33 <= k <= 126:  # printable except space (space confirms above)
                search += chr(k)
                selected = 0
                scroll = 0

    def save_edit(self, col):
        """Save the edit buffer. Returns True on success, False on validation failure."""
        row_data = self._view_row(self.row)
        if row_data is None:
            return True

        value = self.edit_buffer.strip()
        old_value = self._field_value(col, row_data)

        # Validate
        if col.validator:
            ok, msg = col.validator(value)
            if not ok:
                self.set_status(f"Invalid: {msg}")
                return False

        if col.numeric:
            self._set_field_value(col, row_data, _numeric_round(_numeric_parse(value)))
        else:
            self._set_field_value(col, row_data, value)

        self.compute_row(row_data)

        if self.on_cell_edit:
            field = col.store_field if col.lookup else col.name
            self.on_cell_edit(self._data_index(self.row), field,
                              old_value, self._field_value(col, row_data))

        return True

    # ==================== SORTING ====================

    def toggle_sort(self, col_idx=None):
        """Cycle sort on a column: none -> asc -> desc -> none."""
        if col_idx is None:
            col_idx = self.col

        if self.sort_column == col_idx:
            if self.sort_ascending:
                self.sort_ascending = False
            else:
                # Clear sort
                self.sort_column = None
                self.sort_ascending = True
                for c in self.columns:
                    c.sort_order = SORT_NONE
                self._rebuild_view()
                return
        else:
            # Clear previous
            for c in self.columns:
                c.sort_order = SORT_NONE
            self.sort_column = col_idx
            self.sort_ascending = True

        self.columns[col_idx].sort_order = SORT_ASC if self.sort_ascending else SORT_DESC
        self._rebuild_view()
        self.set_status(f"Sort: {self.columns[col_idx].label} {'Asc' if self.sort_ascending else 'Desc'}")

    def _sort_picker(self):
        """Show a popup to pick which column to sort (used in row_select_mode)."""
        # Build list of visible columns with their real indices
        vis = [(i, c) for i, c in enumerate(self.columns) if c.visible]
        if not vis:
            return

        max_y, max_x = self.stdscr.getmaxyx()
        label_w = max(len(c.label) for _, c in vis)
        w = min(max_x - 4, max(label_w + 8, 24))
        h = min(max_y - 4, len(vis) + 4)
        visible_rows = h - 4
        y = max(0, (max_y - h) // 2)
        x = max(0, (max_x - w) // 2)

        try:
            popup = curses.newwin(h, w, y, x)
        except curses.error:
            return
        popup.keypad(True)
        popup.bkgd(' ', curses.color_pair(self.CLR_HEADER))

        sel = 0
        scroll = 0
        # Pre-select the current sort column if any
        if self.sort_column is not None:
            for idx, (ci, _) in enumerate(vis):
                if ci == self.sort_column:
                    sel = idx
                    break

        while True:
            popup.erase()
            try:
                popup.attron(curses.color_pair(self.CLR_BORDER))
                popup.box()
                popup.attroff(curses.color_pair(self.CLR_BORDER))
            except curses.error:
                pass
            try:
                popup.addstr(0, 2, " Sort Column ",
                             curses.color_pair(self.CLR_BORDER) | curses.A_BOLD)
            except curses.error:
                pass
            try:
                popup.addstr(h - 1, 2, " Enter=Sort  Esc=Cancel ",
                             curses.color_pair(self.CLR_BORDER))
            except curses.error:
                pass

            if sel < scroll:
                scroll = sel
            if sel >= scroll + visible_rows:
                scroll = sel - visible_rows + 1

            for i in range(visible_rows):
                idx = scroll + i
                if idx >= len(vis):
                    break
                ci, col = vis[idx]
                ry = 2 + i
                is_sel = (idx == sel)
                # Show sort indicator
                sort_mark = ""
                if ci == self.sort_column:
                    sort_mark = " \u25b2" if self.sort_ascending else " \u25bc"
                label = f" {col.label}{sort_mark}"
                attr = curses.color_pair(self.CLR_SELECTED) if is_sel else curses.color_pair(self.CLR_HEADER)
                try:
                    popup.addstr(ry, 1, label.ljust(w - 2)[:w - 2], attr)
                except curses.error:
                    pass

            popup.refresh()
            key = popup.getch()

            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(vis) - 1:
                sel += 1
            elif key in (10, 13, curses.KEY_ENTER):
                ci, _ = vis[sel]
                self.stdscr.touchwin()
                self.stdscr.refresh()
                self.toggle_sort(ci)
                return
            elif key == 27:  # Escape
                self.stdscr.touchwin()
                self.stdscr.refresh()
                return

    def set_sort(self, col_name, ascending=True):
        """Programmatic sort by column name."""
        for i, c in enumerate(self.columns):
            if c.name == col_name:
                self.sort_column = i
                self.sort_ascending = ascending
                c.sort_order = SORT_ASC if ascending else SORT_DESC
                self._rebuild_view()
                return

    # ==================== FILTERING ====================

    def set_filter(self, col_name, filter_text):
        """Programmatic filter by column name."""
        for c in self.columns:
            if c.name == col_name:
                c.filter_text = filter_text
                self._rebuild_view()
                return

    def clear_all_filters(self):
        for c in self.columns:
            c.filter_text = ""
        self._rebuild_view()
        self.set_status("Filters cleared")

    def _enter_filter_mode(self, col_idx=None):
        """Enter filter input mode on a column."""
        if col_idx is not None:
            self.filter_col = col_idx
        else:
            self.filter_col = self.col
        # Ensure filter_col points to a visible column
        if not self.columns[self.filter_col].visible:
            self.filter_col = self._next_visible_col(self.filter_col)
            if self.filter_col is None:
                return
        self.filter_mode = True
        self.filter_edit_buffer = self.columns[self.filter_col].filter_text
        # Auto-scroll to make the filter column visible
        self._scroll_to_column(self.filter_col)

    def _next_visible_col(self, from_col, direction=1):
        """Find next visible column index in given direction, wrapping around."""
        n = len(self.columns)
        for _ in range(n):
            from_col = (from_col + direction) % n
            if self.columns[from_col].visible:
                return from_col
        return None

    def _scroll_to_column(self, col_idx):
        """Adjust h_scroll_offset so that col_idx is visible on screen."""
        col = self.columns[col_idx]
        if col.frozen:
            return  # frozen columns are always visible
        _, scrollable = self._get_visible_columns()
        for si, (ci, _) in enumerate(scrollable):
            if ci == col_idx:
                if si < self.h_scroll_offset:
                    self.h_scroll_offset = si
                else:
                    # Check if it's past the right edge — render to find out
                    rendered = self._get_rendered_columns()
                    rendered_cis = {r[0] for r in rendered}
                    if col_idx not in rendered_cis:
                        self.h_scroll_offset = si
                break

    def _exit_filter_mode(self, save=True):
        """Exit filter mode, optionally saving the filter text."""
        if save and 0 <= self.filter_col < len(self.columns):
            self.columns[self.filter_col].filter_text = self.filter_edit_buffer
            self._rebuild_view()
        self.filter_mode = False
        self.filter_edit_buffer = ""

    # ==================== SELECTION ====================

    def toggle_selection(self, view_idx=None):
        """Toggle selection on a row."""
        if view_idx is None:
            view_idx = self.row
        data_idx = self._data_index(view_idx)
        if data_idx < 0:
            return
        if data_idx in self.selected_rows:
            self.selected_rows.discard(data_idx)
        else:
            self.selected_rows.add(data_idx)
        if self.on_selection_change:
            self.on_selection_change(self.get_selected_rows())

    def select_all(self):
        """Select all currently visible (filtered) rows."""
        for vi in self._view_indices:
            self.selected_rows.add(vi)
        if self.on_selection_change:
            self.on_selection_change(self.get_selected_rows())
        self.set_status(f"Selected {len(self._view_indices)} rows")

    def clear_selection(self):
        self.selected_rows.clear()

    def get_selected_rows(self):
        """Return list of selected data rows."""
        return [self.data[i] for i in sorted(self.selected_rows) if i < len(self.data)]

    # ==================== BOOKMARKS ====================

    def toggle_bookmark(self, view_idx=None):
        if view_idx is None:
            view_idx = self.row
        data_idx = self._data_index(view_idx)
        if data_idx < 0:
            return
        if data_idx in self.bookmarked_rows:
            self.bookmarked_rows.discard(data_idx)
            self.set_status("Bookmark removed")
        else:
            self.bookmarked_rows.add(data_idx)
            self.set_status("Bookmark added")

    def goto_next_bookmark(self):
        if not self.bookmarked_rows:
            self.set_status("No bookmarks")
            return
        # Find next bookmarked row in view after current position
        for vi in range(self.row + 1, len(self._view_indices)):
            if self._view_indices[vi] in self.bookmarked_rows:
                self.row = vi
                self.ensure_visible()
                return
        # Wrap around
        for vi in range(0, self.row):
            if self._view_indices[vi] in self.bookmarked_rows:
                self.row = vi
                self.ensure_visible()
                return

    def goto_prev_bookmark(self):
        if not self.bookmarked_rows:
            self.set_status("No bookmarks")
            return
        for vi in range(self.row - 1, -1, -1):
            if self._view_indices[vi] in self.bookmarked_rows:
                self.row = vi
                self.ensure_visible()
                return
        for vi in range(len(self._view_indices) - 1, self.row, -1):
            if self._view_indices[vi] in self.bookmarked_rows:
                self.row = vi
                self.ensure_visible()
                return

    # ==================== COLUMN AUTO-FIT ====================

    def auto_fit_column(self, col_idx=None):
        """Auto-fit a column width to its content."""
        if col_idx is None:
            col_idx = self.col
        if col_idx < 0 or col_idx >= len(self.columns):
            return
        col = self.columns[col_idx]
        max_w = len(col.label) + 2  # label + sort indicator space

        for row_data in self.data:
            val = self.format_value(col, row_data.get(col.name))
            max_w = max(max_w, len(val))

        max_w = max(col.min_width, min(max_w, self.max_x // 3))
        col.width = max_w
        self.set_status(f"Column '{col.label}' width: {max_w}")

    def auto_fit_all_columns(self):
        for i in range(len(self.columns)):
            self.auto_fit_column(i)
        self.set_status("All columns auto-fitted")

    def _open_column_config(self):
        """Open the column config dialog for visibility and reorder."""
        dlg = ColumnConfigDialog(self.stdscr, self.columns)
        if dlg.run():
            # Apply changes: update column list with new order
            self.columns = dlg.cols
            # Ensure cursor is on a visible column
            visible_cols = [i for i, c in enumerate(self.columns) if c.visible]
            if visible_cols and self.col not in visible_cols:
                self.col = visible_cols[0]
            self.h_scroll_offset = 0
            # Re-stretch columns to fill terminal with new visible set
            self._columns_stretched = False
            self._stretch_columns_to_fit()
            self._rebuild_view()
            self.set_status("Column config applied")

    # ==================== NAVIGATION ====================

    def move_down(self):
        """Move cursor down one row."""
        if not self._view_indices:
            return
        if self.row < len(self._view_indices) - 1:
            self.row += 1
            self.ensure_visible()
        else:
            self.set_status("End of data")
        self._ensure_coedit_column()

    def move_up(self):
        if self.coedit_mode:
            if not self._view_indices:
                return
            self.row = max(0, self.row - 1)
            self.ensure_visible()
            self._ensure_coedit_column()
            return
        if self.show_filter_row and not self.filter_mode:
            if not self._view_indices or self.row == 0:
                self._enter_filter_mode()
                return
        if not self._view_indices:
            return
        self.row = max(0, self.row - 1)
        self.ensure_visible()

    def move_next_column(self):
        """Tab: next column, wrap to next row."""
        if self.coedit_mode:
            self._coedit_advance(start_edit_next=False)
            return
        visible_cols = [i for i, c in enumerate(self.columns) if c.visible]
        if not visible_cols:
            return
        try:
            cur_pos = visible_cols.index(self.col)
        except ValueError:
            self.col = visible_cols[0]
            return
        if cur_pos < len(visible_cols) - 1:
            self.col = visible_cols[cur_pos + 1]
        else:
            if self.row < len(self._view_indices) - 1:
                self.row += 1
                self.col = visible_cols[0]
                self.ensure_visible()
            elif self.on_need_new_row:
                # Last column of last row — request a new row
                self.on_need_new_row()
        self._ensure_col_visible()

    def move_prev_column(self):
        """Shift+Tab: previous column, wrap to previous row."""
        if self.coedit_mode:
            self._ensure_coedit_column()
            if self.row > 0:
                self.row -= 1
                self.ensure_visible()
            return
        visible_cols = [i for i, c in enumerate(self.columns) if c.visible]
        if not visible_cols:
            return
        try:
            cur_pos = visible_cols.index(self.col)
        except ValueError:
            self.col = visible_cols[-1]
            return
        if cur_pos > 0:
            self.col = visible_cols[cur_pos - 1]
        else:
            if self.row > 0:
                self.row -= 1
                self.col = visible_cols[-1]
                self.ensure_visible()
        self._ensure_col_visible()

    def page_down(self):
        total = len(self._view_indices)
        self.row = min(self.row + self.visible_rows, total - 1) if total > 0 else 0
        self.ensure_visible()

    def page_up(self):
        self.row = max(0, self.row - self.visible_rows)
        self.ensure_visible()

    def go_home(self):
        self.row = 0
        self.ensure_visible()

    def go_end(self):
        total = len(self._view_indices)
        self.row = total - 1 if total > 0 else 0
        self.ensure_visible()

    def _ensure_col_visible(self):
        """Adjust horizontal scroll so the current column is visible."""
        _, scrollable = self._get_visible_columns()
        # Find position in scrollable list
        for idx, (ci, col) in enumerate(scrollable):
            if ci == self.col:
                if idx < self.h_scroll_offset:
                    self.h_scroll_offset = idx
                # Check if it's past the visible area
                rendered = self._get_rendered_columns()
                visible_ci = {rci for rci, _, _, _ in rendered}
                if self.col not in visible_ci:
                    self.h_scroll_offset = max(0, idx)
                return

    def scroll_left(self):
        if self.h_scroll_offset > 0:
            self.h_scroll_offset -= 1

    def scroll_right(self):
        _, scrollable = self._get_visible_columns()
        if self.h_scroll_offset < len(scrollable) - 1:
            self.h_scroll_offset += 1

    # ==================== MOUSE ====================

    def handle_mouse(self, bstate, mx, my):
        """Handle mouse events."""
        # Scroll wheel
        if bstate & curses.BUTTON4_PRESSED:
            if self.row > 0:
                self.row = max(0, self.row - 3)
                self.ensure_visible()
            return

        if bstate & curses.BUTTON5_PRESSED:
            total = len(self._view_indices)
            if self.row < total - 1:
                self.row = min(total - 1, self.row + 3)
                self.ensure_visible()
            return

        button1 = (getattr(curses, "BUTTON1_CLICKED", 0) |
                   getattr(curses, "BUTTON1_PRESSED", 0))
        if not (bstate & button1):
            return

        # Check header clicks — sort
        for (hy, hx1, _, hx2, ci) in self._header_rects:
            if my == hy and hx1 <= mx <= hx2:
                self.toggle_sort(ci)
                return

        # Check indicator clicks — toggle selection
        for (iy, ix1, _, ix2, vi) in self._indicator_rects:
            if my == iy and ix1 <= mx <= ix2:
                self.toggle_selection(vi)
                return

        # Check cell clicks — navigate
        for (cy, cx1, _, cx2, vi, ci) in self._cell_rects:
            if my == cy and cx1 <= mx <= cx2:
                self.row = vi
                if self.coedit_mode:
                    self.col = self.coedit_col_idx
                else:
                    self.col = ci
                self.ensure_visible()
                return

    # ==================== INPUT DISPATCH ====================

    def handle_input(self, key):
        """Main input handler — dispatches keys to actions."""
        if self.edit_mode:
            return

        # Filter mode input
        if self.filter_mode:
            self._handle_filter_input(key)
            return

        # Column-edit mode — vertical entry column
        if self.coedit_mode and not self.edit_mode:
            if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                self._ensure_coedit_column()
                self.start_edit()
                return
            if key == 9:
                self._coedit_advance(start_edit_next=True)
                return
            if key in (curses.KEY_RIGHT, curses.KEY_LEFT):
                return
            if key == 353:
                self.move_prev_column()
                return

        # Key dispatch table
        # In row_select_mode, Left/Right scroll horizontally instead of column-hop
        if self.row_select_mode:
            lr_right = self.scroll_right
            lr_left = self.scroll_left
        else:
            lr_right = self.move_next_column
            lr_left = self.move_prev_column
        dispatch = {
            curses.KEY_DOWN: self.move_down,
            curses.KEY_UP: self.move_up,
            curses.KEY_RIGHT: lr_right,
            curses.KEY_LEFT: lr_left,
            curses.KEY_HOME: self.go_home,
            curses.KEY_END: self.go_end,
            curses.KEY_NPAGE: self.page_down,
            curses.KEY_PPAGE: self.page_up,
            curses.KEY_F5: self.undo_last_deletion,
            curses.KEY_F6: lambda: self._sort_picker() if self.row_select_mode else self.toggle_sort(),
            curses.KEY_F7: self._on_toggle_filter,
            curses.KEY_F2: self.goto_next_bookmark,
            4: self.delete_current_row,       # Ctrl+D
            2: lambda: self.toggle_bookmark(),  # Ctrl+B
            1: self.select_all,               # Ctrl+A
            6: self._on_cycle_footer,         # Ctrl+F
            12: self.clear_all_filters,       # Ctrl+L
            23: lambda: self.auto_fit_column(),  # Ctrl+W
        }

        handler = dispatch.get(key)
        if handler:
            handler()
            return

        # Enter — next column (or coedit handled above)
        if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
            if self.coedit_mode:
                self.start_edit()
            else:
                self.move_next_column()
            return

        # Tab / Shift+Tab
        if key == 9:
            if self.coedit_mode:
                self._coedit_advance(start_edit_next=True)
            else:
                self.move_next_column()
            return
        if key == 353:
            self.move_prev_column()
            return

        # F4 — open lookup picker on current column
        if key == curses.KEY_F4:
            col = self.columns[self.col] if 0 <= self.col < len(self.columns) else None
            if col and col.lookup and self.lookup_handler:
                self._open_lookup()
            elif col and col.dropdown:
                self._open_dropdown()
            return

        # Space — dropdown columns open combo, others toggle row selection
        if key == 32:
            col = self.columns[self.col] if 0 <= self.col < len(self.columns) else None
            if col and col.dropdown:
                self._open_dropdown()
            else:
                self.toggle_selection()
            return

        # Mouse
        if key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                self.handle_mouse(bstate, mx, my)
            except curses.error:
                pass
            return

        # Alt+Left / Alt+Right — horizontal scroll (ESC + arrow)
        if key == 27:
            self.stdscr.nodelay(True)
            next_key = self.stdscr.getch()
            self.stdscr.nodelay(False)
            if next_key == -1:
                if self.coedit_mode:
                    self.exit_coedit()
                    return
                return  # Plain ESC — caller handles exit
            if next_key == curses.KEY_LEFT:
                self.scroll_left()
                return
            if next_key == curses.KEY_RIGHT:
                self.scroll_right()
                return
            if next_key == ord('c') or next_key == ord('C'):
                self._open_column_config()
                return
            return

        # Direct typing starts editing (only when rows exist)
        if 32 <= key <= 126 and self._view_indices:
            if self.coedit_mode:
                self._ensure_coedit_column()
            if 0 <= self.col < len(self.columns) and self.columns[self.col].editable:
                self.start_edit(key)

    def _handle_filter_input(self, key):
        """Handle input while in filter editing mode."""
        if key == 27:  # ESC — hide filter row and clear filters
            self._exit_filter_mode(save=False)
            self.show_filter_row = False
            for c in self.columns:
                c.filter_text = ""
            self._rebuild_view()
            self.set_status("Filter OFF — filters cleared")
        elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):  # Enter — apply
            self._exit_filter_mode(save=True)
        elif key == 9:  # Tab — apply and move to next column filter
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            nxt = self._next_visible_col(cur, 1)
            if nxt is not None:
                self._enter_filter_mode(nxt)
        elif key == 353:  # Shift+Tab — apply and move to previous column filter
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            prev = self._next_visible_col(cur, -1)
            if prev is not None:
                self._enter_filter_mode(prev)
        elif key == curses.KEY_DC:  # Delete — clear current column filter
            cur = self.filter_col
            self.filter_edit_buffer = ""
            self._exit_filter_mode(save=True)
            self._enter_filter_mode(cur)
            self.set_status("Filter cleared")
        elif key == 12:  # Ctrl+L — clear ALL filters
            cur = self.filter_col
            self.filter_edit_buffer = ""
            self.filter_mode = False
            self.clear_all_filters()
            self._enter_filter_mode(cur)
        elif key == curses.KEY_BACKSPACE or key in (127, 8):
            if self.filter_edit_buffer:
                self.filter_edit_buffer = self.filter_edit_buffer[:-1]
                # Live-apply filter as user types/deletes
                self.columns[self.filter_col].filter_text = self.filter_edit_buffer
                self._rebuild_view()
        elif 32 <= key <= 126:
            self.filter_edit_buffer += chr(key)
            # Live-apply filter as user types
            self.columns[self.filter_col].filter_text = self.filter_edit_buffer
            self._rebuild_view()
        elif key == curses.KEY_LEFT:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            prev = self._next_visible_col(cur, -1)
            if prev is not None:
                self._enter_filter_mode(prev)
        elif key == curses.KEY_RIGHT:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            nxt = self._next_visible_col(cur, 1)
            if nxt is not None:
                self._enter_filter_mode(nxt)
        elif key in (curses.KEY_DOWN, curses.KEY_UP):
            # Leave filter row — save and return focus to data grid
            self._exit_filter_mode(save=True)
            if key == curses.KEY_DOWN and self._view_indices:
                self.ensure_visible()
            elif key == curses.KEY_UP and self._view_indices and self.row > 0:
                self.move_up()

    def _on_toggle_filter(self):
        """Toggle filter row visibility. Turning OFF clears all filters."""
        if self.filter_mode:
            self._exit_filter_mode(save=True)
        self.show_filter_row = not self.show_filter_row
        if self.show_filter_row:
            self._enter_filter_mode()
            self.set_status("Filter ON — type, Tab/Shift+Tab: next/prev column, Esc: cancel")
        else:
            self.filter_mode = False
            # Clear all filters when hiding the filter row
            for c in self.columns:
                c.filter_text = ""
            self._rebuild_view()
            self.set_status("Filter OFF — filters cleared")

    def _on_cycle_footer(self):
        """Cycle footer aggregation type for the current column."""
        if not self.show_totals:
            self.show_totals = True
        col = self.columns[self.col]
        try:
            idx = AGG_CYCLE.index(col.footer_type)
        except ValueError:
            idx = 0
        col.footer_type = AGG_CYCLE[(idx + 1) % len(AGG_CYCLE)]
        self.set_status(f"Footer '{col.label}': {col.footer_type.upper()}")

    # ==================== PUBLIC API ====================

    def get_current_row(self):
        """Get the current row data dict."""
        return self._view_row(self.row)

    def get_cell_value(self, view_idx, col_name):
        row_data = self._view_row(view_idx)
        if row_data:
            return row_data.get(col_name)
        return None

    def get_filtered_data(self):
        """Return all rows in current view (filtered + sorted)."""
        return [self.data[i] for i in self._view_indices]

    def set_data(self, data):
        """Replace all data and rebuild view."""
        self.data = data
        self.row = 0
        self.scroll_offset = 0
        self.selected_rows.clear()
        self.bookmarked_rows.clear()
        self.deleted_rows_log.clear()
        for row in self.data:
            self.compute_row(row)
        self._rebuild_view()

    def export_json(self, col_name=None):
        """Export filtered data as JSON. If col_name given, only rows where that column > 0."""
        if col_name:
            result = []
            for r in self.get_filtered_data():
                try:
                    if float(str(r.get(col_name, 0)).replace(",", "")) > 0:
                        result.append(r)
                except (ValueError, TypeError):
                    pass
            return json.dumps(result, default=str, indent=2)
        return json.dumps(self.get_filtered_data(), default=str, indent=2)
