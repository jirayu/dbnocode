"""Interactive Report Designer — create/edit/save reports at runtime.

Provides:
- Persistence layer (save/load report defs in _reports table)
- Column editor (dual-panel field selector with N/S/G flags)
- Report editor form (full-screen, field-by-field navigation)
"""
import curses
import json
from typing import List, Dict, Optional


# ── Persistence ─────────────────────────────────────────────────────────────

def ensure_reports_table(db):
    """Create _reports table if it doesn't exist."""
    db.execute('CREATE TABLE IF NOT EXISTS "_reports" (data TEXT)')


def load_saved_reports(db) -> Dict[str, dict]:
    """Load all saved reports from _reports table. Returns {name: report_def}."""
    ensure_reports_table(db)
    rows = db.query('SELECT rowid, data FROM "_reports"')
    result = {}
    for r in rows:
        try:
            obj = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            if isinstance(obj, dict) and obj.get("name"):
                obj["_rowid"] = r["rowid"]
                obj["_source"] = "designer"
                result[obj["name"]] = obj
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def save_report(db, report_def: dict):
    """Save a report definition. INSERT or UPDATE by name."""
    ensure_reports_table(db)
    clean = {k: v for k, v in report_def.items() if not k.startswith("_")}
    data_json = json.dumps(clean, ensure_ascii=False)

    existing = db.query(
        'SELECT rowid FROM "_reports" WHERE json_extract(data, \'$.name\') = ?',
        (report_def["name"],))
    if existing:
        db.execute('UPDATE "_reports" SET data = ? WHERE rowid = ?',
                   (data_json, existing[0]["rowid"]))
    else:
        db.execute('INSERT INTO "_reports" (data) VALUES (?)', (data_json,))


def delete_report(db, report_name: str):
    """Delete a saved report by name."""
    ensure_reports_table(db)
    db.execute(
        'DELETE FROM "_reports" WHERE json_extract(data, \'$.name\') = ?',
        (report_name,))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe(win, y, x, text, attr=curses.A_NORMAL):
    """Safe addstr that clips to window bounds."""
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x < 0 or x >= max_x:
        return
    text = text[:max_x - x]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _field_label(field_id: str) -> str:
    """Convert field_id to display label: 'unit_price' → 'Unit Price'."""
    return field_id.replace("_", " ").title()


def _auto_detect_flags(name: str):
    """Auto-detect numeric/sum/group flags from field name."""
    n = name.lower()
    is_num = any(k in n for k in
                 ('qty', 'price', 'cost', 'total', 'amount', 'discount',
                  'balance', 'on_hand', 'on_order', 'allocated', 'available'))
    is_sum = any(k in n for k in ('total', 'price', 'cost', 'amount', 'qty'))
    return is_num, is_sum


def _infer_width(name: str, label: str, sample_rows: list) -> int:
    """Infer column width from field name and sample data."""
    lengths = [len(str(label))]
    for r in sample_rows[:200]:
        if isinstance(r, dict):
            lengths.append(len(str(r.get(name) or '')))
    mx = max(lengths) if lengths else len(label)
    if name.endswith('_name') or name in {'part_name', 'customer_name', 'vendor_name'}:
        return min(40, max(16, mx))
    if name.endswith('_no') or name in {'id', 'doc_id'}:
        return min(22, max(12, mx))
    if name.endswith('_date') or name == 'month':
        return 12
    if any(k in name.lower() for k in ('qty', 'price', 'cost', 'total', 'amount', 'discount')):
        return min(16, max(10, mx))
    return min(28, max(10, mx))


def _discover_sample(db, source_table: str, limit: int = 50) -> list:
    """Load sample rows from a table for column inference."""
    try:
        rows = db.query(f'SELECT data FROM "{source_table}" LIMIT ?', (limit,))
    except Exception:
        try:
            rows = db.query(f'SELECT data FROM "{source_table}"')
            rows = rows[:limit]
        except Exception:
            return []
    result = []
    for r in rows:
        try:
            obj = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            if isinstance(obj, dict):
                # Skip detail rows and empty records
                if obj.get("_parent_rowid") is not None:
                    continue
                real_keys = [k for k in obj
                             if k != "rowid" and not k.startswith("_")]
                if not real_keys:
                    continue
                result.append(obj)
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def _draw_field_row(win, y, label, value, is_cur, label_w, val_x, vw,
                    hint="", cursor_pos=None):
    """Draw a single form field row with consistent styling."""
    max_y, max_x = win.getmaxyx()
    if y >= max_y - 1:
        return

    # Clear line
    try:
        win.addstr(y, 1, " " * (max_x - 2))
    except curses.error:
        pass

    # Row indicator
    if is_cur:
        _safe(win, y, 1, "\u25ba", curses.A_BOLD)

    # Label
    lattr = curses.A_BOLD if is_cur else curses.A_DIM
    _safe(win, y, 3, f"{label:>{label_w}s}", lattr)
    _safe(win, y, 3 + label_w, " ", curses.A_NORMAL)

    # Value area
    if cursor_pos is not None and is_cur:
        # Editable text with cursor
        display = value[:vw]
        cx = min(cursor_pos, len(display))
        before = display[:cx]
        after = display[cx:]
        if before:
            _safe(win, y, val_x, before, curses.A_NORMAL)
        ch = after[0] if after else ' '
        _safe(win, y, val_x + cx, ch, curses.A_REVERSE)
        if len(after) > 1:
            _safe(win, y, val_x + cx + 1, after[1:], curses.A_NORMAL)
        used = len(display) + (1 if not after else 0)
        pad = vw - used
        if pad > 0:
            _safe(win, y, val_x + used, "\u2500" * pad, curses.A_DIM)
    elif is_cur:
        # Current but non-editable (dropdown/action/toggle)
        disp = value or "(none)"
        _safe(win, y, val_x, disp[:vw], curses.A_REVERSE)
        if hint:
            hx = val_x + len(disp) + 1
            _safe(win, y, hx, hint[:max_x - hx - 1], curses.A_DIM)
    else:
        # Not current
        disp = value or "(empty)"
        attr = curses.A_NORMAL if value else curses.A_DIM
        _safe(win, y, val_x, disp[:vw], attr)


# ── Formula / Calculated Column Dialogs ─────────────────────────────────────

def _edit_formula(stdscr, field_id: str, current: str = "") -> Optional[str]:
    """Popup to edit a formula expression. Returns formula or None if cancelled."""
    max_y, max_x = stdscr.getmaxyx()
    pw = min(70, max_x - 6)
    ph = 8
    px = (max_x - pw) // 2
    py = (max_y - ph) // 2

    win = curses.newwin(ph, pw, py, px)
    value = current or ""
    cursor = len(value)
    curses.curs_set(1)

    while True:
        win.erase()
        win.border()
        _safe(win, 0, 2, f" Formula: {field_id} ", curses.A_BOLD)
        _safe(win, 2, 2, "Expression:", curses.A_DIM)
        # Show value with cursor
        vw = pw - 4
        display = value[:vw]
        _safe(win, 3, 2, display, curses.A_NORMAL)
        # Pad rest
        pad = vw - len(display)
        if pad > 0:
            _safe(win, 3, 2 + len(display), "\u2500" * pad, curses.A_DIM)
        # Help text
        _safe(win, 5, 2, "IF() ROUND() MAX() MIN() ABS() ISBLANK()",
              curses.A_DIM)
        _safe(win, 6, 2, "F10:Save  Del:Clear  ESC:Cancel", curses.A_DIM)
        # Position cursor
        cx = min(cursor, vw - 1)
        try:
            win.move(3, 2 + cx)
        except curses.error:
            pass
        win.refresh()
        key = win.getch()

        if key == curses.KEY_F10:
            curses.curs_set(0)
            del win
            return value.strip()
        elif key == 27:
            curses.curs_set(0)
            del win
            return None
        elif key in (curses.KEY_DC,) and not value:
            curses.curs_set(0)
            del win
            return ""
        elif key in (curses.KEY_BACKSPACE, 8, 127):
            if cursor > 0:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif key == curses.KEY_DC:
            if cursor < len(value):
                value = value[:cursor] + value[cursor + 1:]
        elif key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif key == curses.KEY_HOME:
            cursor = 0
        elif key == curses.KEY_END:
            cursor = len(value)
        elif 32 <= key <= 126:
            ch = chr(key)
            value = value[:cursor] + ch + value[cursor:]
            cursor += 1


def _add_calc_column(stdscr) -> Optional[dict]:
    """Popup to add a new calculated column (name + formula)."""
    max_y, max_x = stdscr.getmaxyx()
    pw = min(70, max_x - 6)
    ph = 10
    px = (max_x - pw) // 2
    py = (max_y - ph) // 2

    win = curses.newwin(ph, pw, py, px)
    fields = ["", ""]  # [name, formula]
    labels = ["Name:", "Formula:"]
    cursors = [0, 0]
    field = 0
    curses.curs_set(1)

    while True:
        win.erase()
        win.border()
        _safe(win, 0, 2, " New Calculated Column ", curses.A_BOLD)
        vw = pw - 14
        for i in range(2):
            y = 2 + i * 2
            is_cur = (i == field)
            indicator = "\u25ba " if is_cur else "  "
            _safe(win, y, 2, f"{indicator}{labels[i]:>8s}", curses.A_BOLD if is_cur else curses.A_DIM)
            display = fields[i][:vw]
            _safe(win, y, 13, display, curses.A_NORMAL)
            pad = vw - len(display)
            if pad > 0:
                _safe(win, y, 13 + len(display), "\u2500" * pad, curses.A_DIM)
        _safe(win, 7, 2, "IF() ROUND() MAX() MIN() ABS()", curses.A_DIM)
        _safe(win, 8, 2, "Enter:Next  F10:Save  ESC:Cancel", curses.A_DIM)
        # Cursor position
        cx = min(cursors[field], vw - 1)
        try:
            win.move(2 + field * 2, 13 + cx)
        except curses.error:
            pass
        win.refresh()
        key = win.getch()

        if key == curses.KEY_F10:
            name = fields[0].strip()
            formula = fields[1].strip()
            curses.curs_set(0)
            del win
            if not name or not formula:
                return None
            # Sanitize name to valid identifier
            name = name.replace(" ", "_").lower()
            return {
                "id": name,
                "label": _field_label(name),
                "width": 14,
                "type": "FLOAT",
                "sum": False,
                "group_by": False,
                "formula": formula,
            }
        elif key == 27:
            curses.curs_set(0)
            del win
            return None
        elif key in (10, 13, curses.KEY_ENTER, curses.KEY_DOWN):
            field = (field + 1) % 2
        elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
            field = (field - 1) % 2
        elif key in (curses.KEY_BACKSPACE, 8, 127):
            ci = cursors[field]
            if ci > 0:
                fields[field] = fields[field][:ci - 1] + fields[field][ci:]
                cursors[field] -= 1
        elif key == curses.KEY_DC:
            ci = cursors[field]
            if ci < len(fields[field]):
                fields[field] = fields[field][:ci] + fields[field][ci + 1:]
        elif key == curses.KEY_LEFT:
            cursors[field] = max(0, cursors[field] - 1)
        elif key == curses.KEY_RIGHT:
            cursors[field] = min(len(fields[field]), cursors[field] + 1)
        elif key == curses.KEY_HOME:
            cursors[field] = 0
        elif key == curses.KEY_END:
            cursors[field] = len(fields[field])
        elif 32 <= key <= 126:
            ci = cursors[field]
            ch = chr(key)
            fields[field] = fields[field][:ci] + ch + fields[field][ci:]
            cursors[field] += 1


# ── Crosstab Config Dialog ──────────────────────────────────────────────────

def _crosstab_config_dialog(stdscr, all_fields: list, numeric_fields: list,
                            current: dict = None) -> Optional[dict]:
    """Popup to configure crosstab: row, column, value fields.
    Returns dict with row/col/value or None if cancelled."""
    from tui.widgets import OptionDropdown

    current = current or {}
    max_y, max_x = stdscr.getmaxyx()
    pw = min(60, max_x - 6)
    ph = 12
    px = (max_x - pw) // 2
    py = (max_y - ph) // 2

    values = [
        current.get("row", ""),
        current.get("col", ""),
        current.get("value", ""),
    ]
    labels = ["Row Field", "Col Field", "Value"]
    field = 0

    curses.curs_set(0)

    while True:
        win = curses.newwin(ph, pw, py, px)
        win.erase()
        win.border()
        _safe(win, 0, 2, " Crosstab Configuration ", curses.A_BOLD)

        vw = pw - 18
        for i in range(3):
            y = 2 + i * 2
            is_cur = (i == field)
            indicator = "\u25ba " if is_cur else "  "
            _safe(win, y, 2, f"{indicator}{labels[i]:>10s}:",
                  curses.A_BOLD if is_cur else curses.A_DIM)
            disp = values[i] or "(none)"
            attr = curses.A_REVERSE if is_cur else curses.A_NORMAL
            _safe(win, y, 16, disp[:vw], attr)

        _safe(win, 9, 2, "Space:Pick  Enter:Next  Del:Clear",
              curses.A_DIM)
        _safe(win, 10, 2, "F10:Save  ESC:Cancel", curses.A_DIM)
        win.refresh()
        key = stdscr.getch()

        if key in (10, 13, curses.KEY_ENTER, curses.KEY_DOWN):
            field = (field + 1) % 3
        elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
            field = (field - 1) % 3
        elif key == ord(' '):
            # Pick from fields — value field from numeric, others from all
            options = numeric_fields if field == 2 else all_fields
            if options:
                abs_y = py + 2 + field * 2
                abs_x = px + 16
                picked = OptionDropdown.show(
                    stdscr, abs_y, abs_x, vw, values[field], options)
                if picked is not None:
                    values[field] = picked
        elif key in (curses.KEY_DC, curses.KEY_BACKSPACE, 8, 127):
            values[field] = ""
        elif key == curses.KEY_F10:
            del win
            if not values[0] or not values[1] or not values[2]:
                return None
            return {"row": values[0], "col": values[1], "value": values[2]}
        elif key == 27:
            del win
            return None
        elif key == curses.KEY_RESIZE:
            del win
            max_y, max_x = stdscr.getmaxyx()
            pw = min(60, max_x - 6)
            px = (max_x - pw) // 2
            py = (max_y - ph) // 2
            continue
        del win


def _page_elements_dialog(stdscr, current: dict = None) -> Optional[dict]:
    """Popup to configure page header/footer elements for PDF export."""
    current = current or {}
    options = [
        ("title", "Title"),
        ("subtitle", "Subtitle"),
        ("page_no", "Page No"),
        ("printed_at", "Printed At"),
        ("row_count", "Row Count"),
    ]
    state = {
        "title": True,
        "subtitle": True,
        "page_no": True,
        "printed_at": True,
        "row_count": True,
    }
    state.update({k: bool(v) for k, v in current.items()})

    max_y, max_x = stdscr.getmaxyx()
    pw = min(52, max_x - 6)
    ph = 11
    px = (max_x - pw) // 2
    py = (max_y - ph) // 2
    row = 0

    curses.curs_set(0)
    while True:
        win = curses.newwin(ph, pw, py, px)
        win.erase()
        win.border()
        _safe(win, 0, 2, " Page Elements ", curses.A_BOLD)
        for i, (key, label) in enumerate(options):
            y = 2 + i
            marker = "[x]" if state.get(key) else "[ ]"
            attr = curses.A_REVERSE if i == row else curses.A_NORMAL
            _safe(win, y, 3, f"{marker} {label}", attr)
        _safe(win, ph - 2, 2, "Space:Toggle  F10:Save  ESC:Cancel",
              curses.A_DIM)
        win.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, curses.KEY_BTAB, 353):
            row = (row - 1) % len(options)
        elif key in (curses.KEY_DOWN, 9, 10, 13, curses.KEY_ENTER):
            row = (row + 1) % len(options)
        elif key == ord(' '):
            opt_key = options[row][0]
            state[opt_key] = not state.get(opt_key, False)
        elif key == curses.KEY_F10:
            del win
            return state
        elif key == 27:
            del win
            return None
        elif key == curses.KEY_RESIZE:
            del win
            max_y, max_x = stdscr.getmaxyx()
            pw = min(52, max_x - 6)
            px = (max_x - pw) // 2
            py = (max_y - ph) // 2
            continue
        del win


def _page_elements_display(page_elements: dict) -> str:
    labels = {
        "title": "Title",
        "subtitle": "Subtitle",
        "page_no": "Page No",
        "printed_at": "Printed At",
        "row_count": "Row Count",
    }
    enabled = [label for key, label in labels.items()
               if page_elements.get(key, False)]
    return ", ".join(enabled) if enabled else "(none)"


# ── Column Editor (dual-panel) ──────────────────────────────────────────────

def column_editor(stdscr, db, source_table: str,
                  current_columns: List[dict],
                  mode: str = "docs") -> Optional[List[dict]]:
    """Dual-panel column selector with N/S/G flags. Returns column list or None."""
    # Load sample data for field discovery and width inference
    sample = _discover_sample(db, source_table)

    if mode == "lines":
        # Expand lines to discover line-level fields
        expanded = []
        for doc in sample:
            header = {k: v for k, v in doc.items() if k != "lines"}
            lines = doc.get("lines")
            if isinstance(lines, list):
                for ln in lines:
                    if isinstance(ln, dict):
                        merged = dict(header)
                        merged.update(ln)
                        expanded.append(merged)
            else:
                expanded.append(header)
        sample = expanded

    # Discover fields from sample
    fields_set = set()
    for doc in sample:
        fields_set.update(k for k in doc.keys() if not k.startswith("_"))
    all_fields = sorted(fields_set)

    if not all_fields:
        # Fallback to DB discover
        all_fields = db.discover_fields(source_table)
    if not all_fields:
        return None

    # Build selected list from current_columns
    selected = []
    for c in (current_columns or []):
        col = {
            "id": c["id"],
            "label": c.get("label", _field_label(c["id"])),
            "width": c.get("width", 12),
            "type": c.get("type", "STRING"),
            "sum": c.get("sum", False),
            "group_by": c.get("group_by", False),
        }
        if c.get("formula"):
            col["formula"] = c["formula"]
        selected.append(col)

    # Available = all_fields minus selected
    selected_ids = {c["id"] for c in selected}
    available = [f for f in all_fields if f not in selected_ids]

    panel = 0  # 0=left (available), 1=right (selected)
    left_sel = 0
    right_sel = 0
    left_scroll = 0
    right_scroll = 0

    curses.curs_set(0)

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        div_x = max_x // 3  # left panel takes 1/3
        vis_rows = max_y - 5  # title(1) + header(1) + sep(1) + status(2)

        # ── Title bar ──
        title = f" Column Editor \u2500 {source_table} "
        _safe(stdscr, 0, 0, " " * max_x, curses.A_REVERSE | curses.A_BOLD)
        _safe(stdscr, 0, 1, title, curses.A_REVERSE | curses.A_BOLD)

        # ── Left panel header ──
        lhdr = f" Available ({len(available)})"
        lattr = curses.A_BOLD | curses.A_REVERSE if panel == 0 else curses.A_BOLD
        _safe(stdscr, 1, 0, lhdr.ljust(div_x), lattr)

        # ── Right panel header ──
        rhdr = f" Selected ({len(selected)})"
        rattr = curses.A_BOLD | curses.A_REVERSE if panel == 1 else curses.A_BOLD
        _safe(stdscr, 1, div_x + 1, rhdr.ljust(max_x - div_x - 1), rattr)

        # ── Separator line ──
        _safe(stdscr, 2, 0, "\u2500" * max_x, curses.A_DIM)

        # ── Draw vertical divider ──
        for row in range(1, max_y - 2):
            _safe(stdscr, row, div_x, "\u2502", curses.A_DIM)

        # ── Left panel: available fields ──
        if left_sel >= len(available):
            left_sel = max(0, len(available) - 1)
        if left_sel < left_scroll:
            left_scroll = left_sel
        if left_sel >= left_scroll + vis_rows:
            left_scroll = left_sel - vis_rows + 1

        for i in range(left_scroll, min(left_scroll + vis_rows, len(available))):
            y = 3 + (i - left_scroll)
            if y >= max_y - 2:
                break
            name = available[i]
            is_cur = (panel == 0 and i == left_sel)
            indicator = "\u25ba " if is_cur else "  "
            line = f"{indicator}{name}"
            attr = curses.A_REVERSE if is_cur else curses.A_NORMAL
            _safe(stdscr, y, 1, line[:div_x - 2], attr)

        if not available:
            _safe(stdscr, 3, 2, "(all fields selected)", curses.A_DIM)

        # ── Right panel: selected columns ──
        if right_sel >= len(selected):
            right_sel = max(0, len(selected) - 1)
        if right_sel < right_scroll:
            right_scroll = right_sel
        if right_sel >= right_scroll + vis_rows:
            right_scroll = right_sel - vis_rows + 1

        rw = max_x - div_x - 2  # right panel usable width
        # Column header for right panel
        col_hdr = f" {'#':>2s} {'Field':<16s} {'Label':<14s} {'W':>3s}  N  S  G  F"
        _safe(stdscr, 2, div_x + 2, col_hdr[:rw], curses.A_BOLD | curses.A_DIM)

        for i in range(right_scroll, min(right_scroll + vis_rows, len(selected))):
            y = 3 + (i - right_scroll)
            if y >= max_y - 2:
                break
            col = selected[i]
            is_cur = (panel == 1 and i == right_sel)
            n_flag = "N" if col["type"] == "FLOAT" else "\u00b7"
            s_flag = "S" if col.get("sum") else "\u00b7"
            g_flag = "G" if col.get("group_by") else "\u00b7"
            f_flag = "F" if col.get("formula") else "\u00b7"
            line = f" {i+1:2d} {col['id']:<16s} {col['label']:<14s} {col['width']:>3d}  {n_flag}  {s_flag}  {g_flag}  {f_flag}"
            attr = curses.A_REVERSE if is_cur else curses.A_NORMAL
            _safe(stdscr, y, div_x + 2, line[:rw], attr)
            # Show formula below if current
            if is_cur and col.get("formula"):
                fy = y + 1
                if fy < max_y - 2:
                    _safe(stdscr, fy, div_x + 5,
                          f"= {col['formula']}"[:rw - 4], curses.A_DIM)

        if not selected:
            _safe(stdscr, 3, div_x + 3, "(no columns selected)", curses.A_DIM)

        # ── Status bar ──
        sy = max_y - 2
        _safe(stdscr, sy, 0, "\u2500" * max_x, curses.A_DIM)
        hints1 = " Tab:Switch  Enter:Add/Remove  N:Num  S:Sum  G:Group  F:Formula  +/-:Reorder"
        hints2 = " A:Add All  C:Calc Column   F10:Save   ESC:Cancel"
        _safe(stdscr, sy, 0, hints1[:max_x], curses.A_DIM)
        _safe(stdscr, max_y - 1, 0, hints2[:max_x], curses.A_DIM)

        stdscr.refresh()
        key = stdscr.getch()

        # ── Navigation ──
        if key == 9:  # Tab
            panel = 1 - panel
        elif key == curses.KEY_UP:
            if panel == 0 and left_sel > 0:
                left_sel -= 1
            elif panel == 1 and right_sel > 0:
                right_sel -= 1
        elif key == curses.KEY_DOWN:
            if panel == 0 and left_sel < len(available) - 1:
                left_sel += 1
            elif panel == 1 and right_sel < len(selected) - 1:
                right_sel += 1
        elif key == curses.KEY_PPAGE:
            if panel == 0:
                left_sel = max(0, left_sel - vis_rows)
            else:
                right_sel = max(0, right_sel - vis_rows)
        elif key == curses.KEY_NPAGE:
            if panel == 0:
                left_sel = min(len(available) - 1, left_sel + vis_rows)
            else:
                right_sel = min(len(selected) - 1, right_sel + vis_rows)

        # ── Add field (Enter/Right on left panel) ──
        elif key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT) and panel == 0:
            if available:
                fid = available.pop(left_sel)
                is_num, is_sum = _auto_detect_flags(fid)
                w = _infer_width(fid, _field_label(fid), sample)
                selected.append({
                    "id": fid,
                    "label": _field_label(fid),
                    "width": w,
                    "type": "FLOAT" if is_num else "STRING",
                    "sum": is_sum,
                    "group_by": False,
                })
                right_sel = len(selected) - 1
                if left_sel >= len(available):
                    left_sel = max(0, len(available) - 1)
                # Auto-switch when left exhausted
                if not available:
                    panel = 1

        # ── Remove field (Del/Backspace/Left on right panel) ──
        elif key in (curses.KEY_DC, curses.KEY_BACKSPACE, 8, 127,
                     curses.KEY_LEFT) and panel == 1:
            if selected:
                removed = selected.pop(right_sel)
                available.append(removed["id"])
                available.sort()
                if right_sel >= len(selected):
                    right_sel = max(0, len(selected) - 1)

        # ── Remove on Enter in right panel ──
        elif key in (10, 13, curses.KEY_ENTER) and panel == 1:
            if selected:
                removed = selected.pop(right_sel)
                available.append(removed["id"])
                available.sort()
                if right_sel >= len(selected):
                    right_sel = max(0, len(selected) - 1)

        # ── Add all (A on left panel) ──
        elif key in (ord('a'), ord('A')) and panel == 0:
            for fid in available:
                is_num, is_sum = _auto_detect_flags(fid)
                w = _infer_width(fid, _field_label(fid), sample)
                selected.append({
                    "id": fid,
                    "label": _field_label(fid),
                    "width": w,
                    "type": "FLOAT" if is_num else "STRING",
                    "sum": is_sum,
                    "group_by": False,
                })
            available.clear()
            left_sel = 0
            # Auto-switch to right panel
            panel = 1
            right_sel = 0

        # ── Toggle numeric (N on right panel) ──
        elif key in (ord('n'), ord('N')) and panel == 1:
            if selected:
                col = selected[right_sel]
                col["type"] = "STRING" if col["type"] == "FLOAT" else "FLOAT"

        # ── Toggle sum (S on right panel) ──
        elif key in (ord('s'), ord('S')) and panel == 1:
            if selected:
                col = selected[right_sel]
                col["sum"] = not col.get("sum", False)
                if col["sum"]:
                    col["type"] = "FLOAT"

        # ── Toggle group_by (G on right panel) ──
        elif key in (ord('g'), ord('G')) and panel == 1:
            if selected:
                col = selected[right_sel]
                col["group_by"] = not col.get("group_by", False)

        # ── Edit formula (F on right panel) ──
        elif key in (ord('f'), ord('F')) and panel == 1:
            if selected:
                col = selected[right_sel]
                formula = _edit_formula(stdscr, col["id"],
                                        col.get("formula", ""))
                if formula is not None:
                    col["formula"] = formula
                    if formula:
                        col["type"] = "FLOAT"

        # ── Add calculated column (C on right panel or left panel) ──
        elif key in (ord('c'), ord('C')):
            new_col = _add_calc_column(stdscr)
            if new_col:
                selected.append(new_col)
                right_sel = len(selected) - 1
                panel = 1

        # ── Reorder (+/- on right panel) ──
        elif key == ord('+') and panel == 1:
            if selected and right_sel < len(selected) - 1:
                selected[right_sel], selected[right_sel + 1] = \
                    selected[right_sel + 1], selected[right_sel]
                right_sel += 1
        elif key == ord('-') and panel == 1:
            if selected and right_sel > 0:
                selected[right_sel], selected[right_sel - 1] = \
                    selected[right_sel - 1], selected[right_sel]
                right_sel -= 1

        # ── Save (F10) ──
        elif key == curses.KEY_F10:
            if not selected:
                continue
            return selected

        # ── Cancel (ESC) ──
        elif key == 27:
            return None

        elif key == curses.KEY_RESIZE:
            continue


# ── Report Editor Form (full-screen) ────────────────────────────────────────

def report_editor(stdscr, db, report_def: dict = None) -> Optional[dict]:
    """Edit or create a report definition. Returns report_def or None."""
    from tui.widgets import OptionDropdown

    is_new = report_def is None
    if is_new:
        report_def = {
            "name": "", "title": "", "source": "",
            "category": "", "where": "", "order": "",
            "params": [], "columns": [], "groups": [],
            "totals": None, "crosstab": None, "limit": 1000,
            "page_elements": {
                "title": True, "subtitle": True, "page_no": True,
                "printed_at": True, "row_count": True,
            },
        }

    # Extract editable state
    source = report_def.get("source", "")
    columns = list(report_def.get("columns", []))
    group_by = ""
    if report_def.get("groups"):
        group_by = ", ".join(report_def["groups"][0].get("fields", []))
    # Auto-populate from G-flagged columns if no groups defined
    if not group_by:
        g_cols = [c["id"] for c in columns if c.get("group_by")]
        if g_cols:
            group_by = ", ".join(g_cols)
    has_totals = report_def.get("totals") is not None

    categories = ["", "Sales", "Purchase", "Inventory",
                   "Finance", "Production", "Other"]

    modes = ["Documents", "Lines"]

    # Form fields
    FIELD_NAME = 0
    FIELD_TITLE = 1
    FIELD_CATEGORY = 2
    FIELD_SOURCE = 3
    FIELD_MODE = 4
    FIELD_COLUMNS = 5
    FIELD_ORDER = 6
    FIELD_GROUP = 7
    FIELD_FILTER = 8
    FIELD_LIMIT = 9
    FIELD_TOTALS = 10
    FIELD_CROSSTAB = 11
    FIELD_PAGE = 12

    # Map stored mode to display
    stored_mode = report_def.get("dataset_mode", "docs")
    mode_display = "Lines" if stored_mode == "lines" else "Documents"

    # Crosstab config
    ct_config = dict(report_def.get("crosstab") or {})
    page_elements = {
        "title": True,
        "subtitle": True,
        "page_no": True,
        "printed_at": True,
        "row_count": True,
    }
    page_elements.update({
        k: bool(v) for k, v in (report_def.get("page_elements") or {}).items()
    })

    def _ct_display():
        if ct_config.get("row") and ct_config.get("col") and ct_config.get("value"):
            return f"{ct_config['row']} x {ct_config['col']} = {ct_config['value']}"
        return "(none)"

    values = [
        report_def.get("name", ""),                            # 0 Name
        report_def.get("title", ""),                            # 1 Title
        report_def.get("category", ""),                         # 2 Category
        source,                                                 # 3 Source
        mode_display,                                           # 4 Mode
        "",                                                     # 5 Columns
        report_def.get("order", ""),                            # 6 Sort By
        group_by,                                               # 7 Group By
        report_def.get("where", ""),                            # 8 Filter
        str(report_def.get("limit", 1000)),                     # 9 Limit
        "Yes" if has_totals else "No",                          # 10 Totals
        _ct_display(),                                          # 11 Crosstab
        _page_elements_display(page_elements),                  # 12 Page Elements
    ]

    labels = ["Name", "Title", "Category", "Source", "Mode",
              "Columns", "Sort By", "Group By", "Filter",
              "Limit", "Totals", "Crosstab", "Page Elem"]
    types = ["text", "text", "pick", "pick", "pick",
              "action", "pick", "pick", "text",
              "text", "toggle", "action", "action"]
    hints = ["", "", "Space: pick", "Space: pick table",
              "Space: Documents/Lines",
              "Space/F9: edit columns", "Space: pick field",
              "Space: pick field", "", "", "Space: toggle",
              "Space: configure", "Space: configure"]

    field_idx = 0 if is_new else FIELD_COLUMNS
    cursors = [len(v) for v in values]
    status = ""

    tables = db.get_table_names()
    curses.curs_set(0)

    def _col_ids():
        return [c["id"] for c in columns]

    def _update_columns_display():
        if columns:
            names = ", ".join(c["id"] for c in columns[:5])
            if len(columns) > 5:
                names += f"... ({len(columns)} total)"
            flags = []
            g_cols = [c["id"] for c in columns if c.get("group_by")]
            s_cols = [c["id"] for c in columns if c.get("sum")]
            if g_cols:
                flags.append(f"G:{len(g_cols)}")
            if s_cols:
                flags.append(f"S:{len(s_cols)}")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            values[FIELD_COLUMNS] = f"{len(columns)} cols: {names}{flag_str}"
        else:
            values[FIELD_COLUMNS] = "0 columns"

    def _sync_flags_to_fields():
        """Auto-populate Group By and Totals from column G/S flags."""
        g_cols = [c["id"] for c in columns if c.get("group_by")]
        s_cols = [c["id"] for c in columns if c.get("sum")]
        if g_cols and not values[FIELD_GROUP].strip():
            values[FIELD_GROUP] = ", ".join(g_cols)
            cursors[FIELD_GROUP] = len(values[FIELD_GROUP])
        if s_cols and values[FIELD_TOTALS] == "No":
            values[FIELD_TOTALS] = "Yes"

    _update_columns_display()

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        label_w = 10
        val_x = label_w + 5

        # ── Title bar ──
        bar = " New Report " if is_new else " Edit Report "
        _safe(stdscr, 0, 0, " " * max_x, curses.A_REVERSE | curses.A_BOLD)
        _safe(stdscr, 0, 1, bar, curses.A_REVERSE | curses.A_BOLD)

        # ── Fields ──
        vw = max_x - val_x - 4
        for i in range(len(labels)):
            y = 2 + i * 2
            is_cur = (i == field_idx)

            if types[i] == "text":
                _draw_field_row(stdscr, y, labels[i], values[i], is_cur,
                                label_w, val_x, vw,
                                cursor_pos=cursors[i] if is_cur else None)
            elif types[i] in ("pick", "action"):
                _draw_field_row(stdscr, y, labels[i], values[i], is_cur,
                                label_w, val_x, vw,
                                hint=hints[i] if is_cur else "")
            elif types[i] == "toggle":
                _draw_field_row(stdscr, y, labels[i], values[i], is_cur,
                                label_w, val_x, vw,
                                hint=hints[i] if is_cur else "")

        # ── Column preview (if columns selected) ──
        preview_y = 2 + len(labels) * 2 + 1
        if columns and preview_y < max_y - 3:
            _safe(stdscr, preview_y, 3,
                  "\u2500 Column Preview \u2500", curses.A_DIM)
            hdr = f"  {'Field':<18s} {'Label':<16s} {'W':>3s}  N  S  G"
            _safe(stdscr, preview_y + 1, 3, hdr[:max_x - 6],
                  curses.A_BOLD | curses.A_DIM)
            for ci, col in enumerate(columns):
                py = preview_y + 2 + ci
                if py >= max_y - 3:
                    remaining = len(columns) - ci
                    _safe(stdscr, py, 5, f"... {remaining} more",
                          curses.A_DIM)
                    break
                n = "N" if col["type"] == "FLOAT" else " "
                s = "S" if col.get("sum") else " "
                g = "G" if col.get("group_by") else " "
                line = f"  {col['id']:<18s} {col['label']:<16s} {col['width']:>3d}  {n}  {s}  {g}"
                _safe(stdscr, py, 3, line[:max_x - 6], curses.A_NORMAL)

        # ── Status ──
        if status:
            _safe(stdscr, max_y - 3, 3, status[:max_x - 6], curses.A_BOLD)

        # ── Hotkey bar ──
        _safe(stdscr, max_y - 2, 0, "\u2500" * max_x, curses.A_DIM)
        bar_text = " F10:Save   F9:Columns   Del:Clear field   ESC:Cancel"
        _safe(stdscr, max_y - 1, 1, bar_text, curses.A_DIM)

        stdscr.refresh()
        key = stdscr.getch()

        # ── Navigation: Enter/Down/Tab = next, Up/Shift-Tab = prev ──
        if key in (curses.KEY_DOWN, 9, 10, 13, curses.KEY_ENTER):
            field_idx = (field_idx + 1) % len(labels)
            status = ""
        elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
            field_idx = (field_idx - 1) % len(labels)
            status = ""

        # ── Space: context-sensitive action (dropdown/toggle/columns) ──
        # Only intercept for non-text fields; text fields handle space as input
        elif key == ord(' ') and types[field_idx] != "text":
            if field_idx == FIELD_CATEGORY:
                picked = OptionDropdown.show(
                    stdscr, 2 + FIELD_CATEGORY * 2, val_x, vw,
                    values[FIELD_CATEGORY], categories)
                if picked is not None:
                    values[FIELD_CATEGORY] = picked
                    cursors[FIELD_CATEGORY] = len(picked)

            elif field_idx == FIELD_SOURCE:
                if tables:
                    picked = OptionDropdown.show(
                        stdscr, 2 + FIELD_SOURCE * 2, val_x, vw,
                        values[FIELD_SOURCE], tables)
                    if picked is not None and picked != values[FIELD_SOURCE]:
                        values[FIELD_SOURCE] = picked
                        source = picked
                        columns = []
                        _update_columns_display()
                        values[FIELD_ORDER] = ""
                        values[FIELD_GROUP] = ""

            elif field_idx == FIELD_MODE:
                picked = OptionDropdown.show(
                    stdscr, 2 + FIELD_MODE * 2, val_x, vw,
                    values[FIELD_MODE], modes)
                if picked is not None:
                    values[FIELD_MODE] = picked
                    if source and columns:
                        columns = []
                        _update_columns_display()

            elif field_idx == FIELD_COLUMNS:
                if not source:
                    status = "Pick a Source table first"
                else:
                    cur_mode = "lines" if values[FIELD_MODE] == "Lines" else "docs"
                    result = column_editor(
                        stdscr, db, source, columns, mode=cur_mode)
                    if result is not None:
                        columns = result
                        _sync_flags_to_fields()
                    _update_columns_display()

            elif field_idx == FIELD_ORDER:
                col_ids = _col_ids()
                if col_ids:
                    picked = OptionDropdown.show(
                        stdscr, 2 + FIELD_ORDER * 2, val_x, vw,
                        values[FIELD_ORDER], col_ids)
                    if picked is not None:
                        cur = values[FIELD_ORDER].strip()
                        if cur:
                            values[FIELD_ORDER] = f"{cur}, {picked}"
                        else:
                            values[FIELD_ORDER] = picked
                        cursors[FIELD_ORDER] = len(values[FIELD_ORDER])
                else:
                    status = "Select columns first (F9)"

            elif field_idx == FIELD_GROUP:
                col_ids = _col_ids()
                if col_ids:
                    picked = OptionDropdown.show(
                        stdscr, 2 + FIELD_GROUP * 2, val_x, vw,
                        values[FIELD_GROUP], col_ids)
                    if picked is not None:
                        cur = values[FIELD_GROUP].strip()
                        if cur:
                            values[FIELD_GROUP] = f"{cur}, {picked}"
                        else:
                            values[FIELD_GROUP] = picked
                        cursors[FIELD_GROUP] = len(values[FIELD_GROUP])
                else:
                    status = "Select columns first (F9)"

            elif field_idx == FIELD_TOTALS:
                values[FIELD_TOTALS] = (
                    "No" if values[FIELD_TOTALS] == "Yes" else "Yes")

            elif field_idx == FIELD_CROSSTAB:
                col_ids = _col_ids()
                if not col_ids:
                    status = "Select columns first (F9)"
                else:
                    num_ids = [c["id"] for c in columns
                               if c.get("type") == "FLOAT" or c.get("sum")]
                    result = _crosstab_config_dialog(
                        stdscr, col_ids, num_ids, ct_config)
                    if result is not None:
                        ct_config.update(result)
                        values[FIELD_CROSSTAB] = _ct_display()

            elif field_idx == FIELD_PAGE:
                result = _page_elements_dialog(stdscr, page_elements)
                if result is not None:
                    page_elements = result
                    values[FIELD_PAGE] = _page_elements_display(page_elements)

        # ── F9: column editor shortcut from any field ──
        elif key == curses.KEY_F9:
            if not source:
                status = "Pick a Source table first"
            else:
                cur_mode = "lines" if values[FIELD_MODE] == "Lines" else "docs"
                result = column_editor(
                    stdscr, db, source, columns, mode=cur_mode)
                if result is not None:
                    columns = result
                    _sync_flags_to_fields()
                _update_columns_display()

        # ── F10: save ──
        elif key == curses.KEY_F10:
            name = values[FIELD_NAME].strip()
            title = values[FIELD_TITLE].strip()
            category = values[FIELD_CATEGORY].strip()
            source = values[FIELD_SOURCE].strip()
            dataset_mode = "lines" if values[FIELD_MODE] == "Lines" else "docs"
            order = values[FIELD_ORDER].strip()
            group_by = values[FIELD_GROUP].strip()
            where = values[FIELD_FILTER].strip()
            has_totals = (values[FIELD_TOTALS] == "Yes")
            try:
                limit = max(50, min(5000,
                            int(values[FIELD_LIMIT].strip() or "1000")))
            except ValueError:
                limit = 1000

            if not name:
                status = "Name is required"
                field_idx = FIELD_NAME
                continue
            if not source:
                status = "Source table is required"
                field_idx = FIELD_SOURCE
                continue
            if not columns:
                status = "Select columns first (F9)"
                field_idx = FIELD_COLUMNS
                continue

            # Build report_def
            rd = {
                "name": name,
                "title": title or _field_label(name),
                "category": category,
                "source": source,
                "dataset_mode": dataset_mode,
                "params": [],
                "where": where,
                "order": order,
                "columns": columns,
                "groups": [],
                "totals": None,
                "crosstab": ct_config if ct_config.get("row") else None,
                "limit": limit,
                "page_elements": page_elements,
            }

            # Build groups from group_by + sum columns
            sum_set = {c["id"] for c in columns if c.get("sum")}
            if group_by:
                gfields = [f.strip() for f in group_by.split(",")
                           if f.strip()]
                aggs = {f: "sum" for f in sum_set}
                rd["groups"] = [{
                    "fields": gfields,
                    "label": f"{_field_label(gfields[0])} Total",
                    "aggregations": aggs,
                }]

            # Build totals
            if has_totals and sum_set:
                rd["totals"] = {
                    "label": "Grand Total",
                    "aggregations": {f: "sum" for f in sum_set},
                }

            save_report(db, rd)
            return rd

        # ── ESC: cancel ──
        elif key == 27:
            return None

        # ── Delete/Backspace on pick fields: clear value ──
        elif (key in (curses.KEY_DC, curses.KEY_BACKSPACE, 8, 127)
              and types[field_idx] in ("pick", "action")):
            if field_idx in (FIELD_ORDER, FIELD_GROUP):
                values[field_idx] = ""
                cursors[field_idx] = 0
                status = f"{labels[field_idx]} cleared"
            elif field_idx == FIELD_CROSSTAB:
                ct_config.clear()
                values[FIELD_CROSSTAB] = _ct_display()
                status = "Crosstab cleared"
            elif field_idx == FIELD_PAGE:
                page_elements = {
                    "title": True,
                    "subtitle": True,
                    "page_no": True,
                    "printed_at": True,
                    "row_count": True,
                }
                values[FIELD_PAGE] = _page_elements_display(page_elements)
                status = "Page elements reset"

        # ── Text editing (only for text fields) ──
        elif types[field_idx] == "text":
            ci = cursors[field_idx]
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if ci > 0:
                    values[field_idx] = (values[field_idx][:ci - 1] +
                                         values[field_idx][ci:])
                    cursors[field_idx] -= 1
            elif key == curses.KEY_DC:
                if ci < len(values[field_idx]):
                    values[field_idx] = (values[field_idx][:ci] +
                                         values[field_idx][ci + 1:])
            elif key == curses.KEY_LEFT:
                cursors[field_idx] = max(0, ci - 1)
            elif key == curses.KEY_RIGHT:
                cursors[field_idx] = min(len(values[field_idx]), ci + 1)
            elif key == curses.KEY_HOME:
                cursors[field_idx] = 0
            elif key == curses.KEY_END:
                cursors[field_idx] = len(values[field_idx])
            elif 32 <= key <= 126:
                ch = chr(key)
                values[field_idx] = (values[field_idx][:ci] + ch +
                                      values[field_idx][ci:])
                cursors[field_idx] += 1

        elif key == curses.KEY_RESIZE:
            continue
