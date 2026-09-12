"""Point of Sale screen — curses-based.

Core scan-and-sell workflow: barcode scan, cart management, totals with
big-digit display, save draft, post (FIFO stock deduction).

Usage from runner:
    from tui.pos_screen import PosScreen
    screen = PosScreen(stdscr, db, app, lookup_data)
    screen.run()
"""
import curses
import json
import re
from datetime import date

from .statusline import StatusLine
from .themes import (CLR_FORM_BORDER, CLR_FORM_LABEL, CLR_FORM_SECTION,
                     CLR_STATUS_BAR, CLR_HEADER, CLR_SELECTED, CLR_FORM_INPUT,
                     CLR_FORM_INPUT_FOCUS, theme_picker)

# ── Scan text parser ────────────────────────────────────────────────

def _parse_scan_text(raw):
    """Parse barcode input into (qty, code, force_new_line).

    Formats:  "ABC123"   -> (1, "ABC123", False)
              "2xABC123" -> (2, "ABC123", False)
              "*ABC123"  -> (1, "ABC123", True)   # always new line
    """
    raw = (raw or "").strip()
    if not raw:
        return 0.0, "", False
    if raw.startswith("*"):
        return 1.0, raw[1:].strip(), True
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[xX\*]\s*(.+)$", raw)
    if m:
        qty = max(0.0, float(m.group(1) or 1))
        return qty, m.group(2).strip(), False
    return 1.0, raw, False


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Big digit display ───────────────────────────────────────────────

_DIGIT_GLYPHS = {
    "0": ["┌───┐", "│   │", "│   │", "│   │", "└───┘"],
    "1": ["  │  ", "  │  ", "  │  ", "  │  ", "  │  "],
    "2": ["┌───┐", "    │", "┌───┘", "│    ", "└───┘"],
    "3": ["┌───┐", "    │", " ───┤", "    │", "└───┘"],
    "4": ["│   │", "│   │", "└───┤", "    │", "    │"],
    "5": ["┌───┐", "│    ", "└───┐", "    │", "└───┘"],
    "6": ["┌───┐", "│    ", "├───┐", "│   │", "└───┘"],
    "7": ["┌───┐", "    │", "   │ ", "  │  ", "  │  "],
    "8": ["┌───┐", "│   │", "├───┤", "│   │", "└───┘"],
    "9": ["┌───┐", "│   │", "└───┤", "    │", "└───┘"],
    ".": ["     ", "     ", "     ", "  ·  ", "  ·  "],
    ",": ["     ", "     ", "     ", "  ·  ", " ·   "],
    "-": ["     ", "     ", "─────", "     ", "     "],
    " ": ["     ", "     ", "     ", "     ", "     "],
}


def _big_digits(text):
    """Render number as 5-line ASCII art using box-drawing chars."""
    rows = [""] * 5
    for ch in str(text or ""):
        g = _DIGIT_GLYPHS.get(ch, _DIGIT_GLYPHS[" "])
        for i in range(5):
            rows[i] += g[i] + " "
    return rows


# ── Default cart columns (fallback when no DSL definition) ─────────

_DEFAULT_CART_COLS = [
    ("part_no",    "Part No",   14),
    ("part_name",  "Name",      24),
    ("quantity",   "Qty",        8),
    ("uom",        "UOM",        5),
    ("unit_price", "Price",     10),
    ("line_total", "Total",     12),
]

VAT_RATE_DEFAULT = 7.0  # fallback when no settings


def _label_from_id(field_id):
    """Convert field_id like 'part_name' to 'Part Name'."""
    return field_id.replace("_", " ").title()


class PosScreen:
    """Curses-based Point of Sale screen."""

    def __init__(self, stdscr, db, app, lookup_data, runner=None):
        self.stdscr = stdscr
        self.db = db
        self.app = app
        self.lookup_data = lookup_data
        self.runner = runner
        self.status_line = StatusLine(stdscr)
        self.status_line.set_page("pos")

        # ── Read config from DSL form definitions ──────────────────
        self.cart_cols = list(_DEFAULT_CART_COLS)
        self._payment_opts = ["CASH", "TRANSFER", "CREDIT-CARD", "SCANNED"]
        self._slip_prefix = "POS"

        # Cart columns from detail grid
        pos_grid = app.get("grids", {}).get("pos_slip_line_grid", {})
        grid_cols = pos_grid.get("columns", [])
        if grid_cols:
            self.cart_cols = [
                (c["id"], _label_from_id(c["id"]), c.get("width", 10))
                for c in grid_cols
            ]

        # Payment options + slip prefix from form fields
        pos_form = app.get("forms", {}).get("pos_slip_form", [])
        for f in pos_form:
            fid = f.get("id", "")
            if fid == "payment" and f.get("enum_list"):
                self._payment_opts = list(f["enum_list"])
            if fid == "slip_no" and f.get("prefix"):
                self._slip_prefix = f["prefix"]

        # Load tax rate from business settings
        self.vat_rate = VAT_RATE_DEFAULT
        if runner:
            try:
                rate = runner.get_setting("tax_rate_pct", "")
                if rate:
                    self.vat_rate = float(rate)
            except (ValueError, TypeError):
                pass

        # State
        self.hdr = {
            "slip_no": "",
            "issue_date": date.today().isoformat(),
            "payment": self._payment_opts[0] if self._payment_opts else "CASH",
            "customer_id": "",
            "customer_name": "",
            "status": "Draft",
        }
        self.lines = []
        self.edit_rowid = None
        self.cart_sel = 0  # selected cart row

        # Focus zones: 0=scan input, 1=cart
        self._focus = 0
        self._scan_buf = ""
        self._scan_cursor = 0
        self._payment_idx = 0
        self._message = ""
        self._message_attr = curses.A_NORMAL

    # ── Item lookup ─────────────────────────────────────────────────

    def _find_item(self, code):
        """Find item by barcode_tag or part_no from pre-loaded lookup data."""
        code_upper = code.strip().upper()
        for row in self.lookup_data.get("item", []):
            if (row.get("barcode_tag") or "").strip().upper() == code_upper:
                return row
            if (row.get("part_no") or "").strip().upper() == code_upper:
                return row
        return None

    # ── Scan / cart logic ───────────────────────────────────────────

    def _apply_scan(self, raw):
        """Apply barcode scan to cart. Returns True if item found."""
        qty, code, force_new = _parse_scan_text(raw)
        if not code or qty <= 0:
            return False

        item = self._find_item(code)
        if not item:
            self._message = f"Not found: {code}"
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False

        part_no = (item.get("part_no") or "").strip()
        uom = (item.get("uom") or "").strip()
        price = _safe_float(item.get("unit_price"))
        name = item.get("part_name") or item.get("code_name") or ""

        # Try merge with existing line
        if not force_new:
            for ln in reversed(self.lines):
                if (ln.get("part_no") or "").strip().upper() == part_no.upper():
                    if (ln.get("uom") or "").strip().upper() == uom.upper():
                        ln["quantity"] = _safe_float(ln["quantity"]) + qty
                        self._message = f"+{qty} {part_no}"
                        self._message_attr = curses.A_DIM
                        return True

        # New line
        self.lines.append({
            "part_no": part_no,
            "part_name": name,
            "quantity": qty,
            "uom": uom,
            "unit_price": price,
        })
        self.cart_sel = len(self.lines) - 1
        self._message = f"Added {part_no}"
        self._message_attr = curses.A_DIM
        return True

    def _totals(self):
        """Return (total, vat, net)."""
        total = 0.0
        for ln in self.lines:
            q = _safe_float(ln.get("quantity"))
            p = _safe_float(ln.get("unit_price"))
            total += q * p
        vat = round(total * (self.vat_rate / 100.0), 2)
        net = round(total + vat, 2)
        return round(total, 2), vat, net

    def _line_total(self, ln):
        return round(_safe_float(ln.get("quantity")) *
                     _safe_float(ln.get("unit_price")), 2)

    # ── Auto-number ─────────────────────────────────────────────────

    def _next_slip_no(self):
        """Generate next POS slip number."""
        prefix = self._slip_prefix
        max_seq = 0
        try:
            rows = self.db.query('SELECT rowid, data FROM "pos_slip"')
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    sn = doc.get("slip_no", "")
                    if sn.startswith(prefix):
                        seq = int(sn[len(prefix):].lstrip("-").lstrip("0") or "0")
                        max_seq = max(max_seq, seq)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
        except Exception:
            pass
        return f"{prefix}-{max_seq + 1:05d}"

    # ── Drawing ─────────────────────────────────────────────────────

    def _draw(self):
        max_y, max_x = self.stdscr.getmaxyx()
        self.stdscr.erase()

        border_attr = curses.color_pair(CLR_FORM_BORDER)
        label_attr = curses.color_pair(CLR_FORM_LABEL)
        section_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
        header_attr = curses.color_pair(CLR_HEADER)

        # Layout dimensions
        right_w = min(40, max(28, max_x // 3))
        left_w = max_x - right_w - 1
        cart_y = 3
        cart_h = max_y - 5  # leave room for status bar

        # ── Title bar ───────────────────────────────────────────────
        title = " Point of Sale "
        try:
            self.stdscr.addstr(0, 0, "─" * max_x, border_attr)
            self.stdscr.addstr(0, 2, title, section_attr)
        except curses.error:
            pass

        # ── Header info ─────────────────────────────────────────────
        slip = self.hdr.get("slip_no") or "(new)"
        dt = self.hdr.get("issue_date", "")
        pay = self.hdr.get("payment", "CASH")
        status = self.hdr.get("status", "Draft")
        info = f" Slip: {slip}    Date: {dt}    Payment: {pay}    Status: {status}"
        try:
            self.stdscr.addstr(1, 0, info[:max_x - 1], label_attr)
            self.stdscr.addstr(2, 0, "─" * max_x, border_attr)
        except curses.error:
            pass

        # ── Cart grid (left panel) ──────────────────────────────────
        self._draw_cart(cart_y, 0, left_w, cart_h)

        # ── Vertical divider ────────────────────────────────────────
        for row in range(cart_y, min(cart_y + cart_h, max_y - 1)):
            try:
                self.stdscr.addstr(row, left_w, "│", border_attr)
            except curses.error:
                pass

        # ── Right panel ─────────────────────────────────────────────
        rx = left_w + 2
        rw = right_w - 3

        # Big digit totals
        total, vat, net = self._totals()
        net_str = f"{net:,.2f}"
        digits = _big_digits(net_str)
        dy = cart_y + 1
        for i, row_str in enumerate(digits):
            try:
                self.stdscr.addstr(dy + i, rx, row_str[:rw],
                                   section_attr)
            except curses.error:
                pass

        # Summary text
        sy = dy + 6
        try:
            self.stdscr.addstr(sy, rx, f"Total:  {total:>12,.2f}", label_attr)
            self.stdscr.addstr(sy + 1, rx,
                               f"VAT {self.vat_rate:.0f}%: {vat:>12,.2f}",
                               label_attr)
            self.stdscr.addstr(sy + 2, rx, f"Net:    {net:>12,.2f}",
                               section_attr)
        except curses.error:
            pass

        # Scan input
        scan_y = sy + 5
        try:
            self.stdscr.addstr(scan_y, rx, "Scan:", label_attr)
        except curses.error:
            pass
        input_x = rx + 6
        input_w = min(rw - 6, 24)
        if input_w > 0:
            # Draw input field
            if self._focus == 0:
                input_attr = curses.color_pair(CLR_FORM_INPUT_FOCUS)
            else:
                input_attr = curses.color_pair(CLR_FORM_INPUT)
            disp = self._scan_buf[-input_w:]
            try:
                self.stdscr.addstr(scan_y, input_x,
                                   disp.ljust(input_w), input_attr)
            except curses.error:
                pass

        # Payment display
        try:
            self.stdscr.addstr(scan_y + 2, rx,
                               f"Pay:  {self.hdr['payment']}", label_attr)
            self.stdscr.addstr(scan_y + 3, rx,
                               "  (Tab to change)", curses.A_DIM)
        except curses.error:
            pass

        # Message line
        if self._message:
            try:
                self.stdscr.addstr(scan_y + 5, rx,
                                   self._message[:rw], self._message_attr)
            except curses.error:
                pass

        # ── Status bar ──────────────────────────────────────────────
        self.status_line.set_visible("F8", self.hdr.get("status") == "Posted")
        self.status_line.draw()

        # ── Cursor ──────────────────────────────────────────────────
        if self._focus == 0 and input_w > 0:
            cx = input_x + min(len(self._scan_buf), input_w - 1)
            try:
                curses.curs_set(1)
                self.stdscr.move(scan_y, cx)
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        self.stdscr.refresh()

    def _draw_cart(self, y, x, w, h):
        """Draw the cart grid with line items."""
        header_attr = curses.color_pair(CLR_HEADER)
        sel_attr = curses.color_pair(CLR_SELECTED)
        right_align = {"quantity", "unit_price", "line_total"}
        gap = 2  # spacing between columns

        # Calculate column widths — give Name leftover space
        fixed_total = sum(cw for fid, _, cw in self.cart_cols if fid != "part_name")
        fixed_gaps = gap * len(self.cart_cols)
        name_w = max(10, w - fixed_total - fixed_gaps - 1)
        col_widths = []
        for fid, _, cw in self.cart_cols:
            col_widths.append(name_w if fid == "part_name" else cw)

        # Build absolute x offsets for each column
        col_x = []
        cx = x + 1
        for cw in col_widths:
            col_x.append(cx)
            cx += cw + gap

        # Header row
        try:
            self.stdscr.addstr(y, x, " " * w, header_attr)
            for i, (fid, label, _) in enumerate(self.cart_cols):
                text = label[:col_widths[i]]
                if fid in right_align:
                    text = text.rjust(col_widths[i])
                self.stdscr.addstr(y, col_x[i], text, header_attr)
        except curses.error:
            pass

        # Data rows
        max_rows = h - 2
        for ri in range(max_rows):
            ry = y + 1 + ri
            is_sel = (self._focus == 1 and ri == self.cart_sel)
            attr = sel_attr if is_sel else curses.A_NORMAL
            try:
                self.stdscr.addstr(ry, x, " " * w, attr)
            except curses.error:
                pass
            if ri < len(self.lines):
                ln = self.lines[ri]
                lt = self._line_total(ln)
                vals = {
                    "part_no": ln.get("part_no", ""),
                    "part_name": ln.get("part_name", ""),
                    "quantity": f"{_safe_float(ln.get('quantity')):.0f}",
                    "uom": ln.get("uom", ""),
                    "unit_price": f"{_safe_float(ln.get('unit_price')):,.2f}",
                    "line_total": f"{lt:,.2f}",
                }
                for i, (fid, _, _) in enumerate(self.cart_cols):
                    v = str(vals.get(fid, ""))[:col_widths[i]]
                    if fid in right_align:
                        v = v.rjust(col_widths[i])
                    try:
                        self.stdscr.addstr(ry, col_x[i], v, attr)
                    except curses.error:
                        pass

        # Cart item count
        count_text = f" {len(self.lines)} item(s)"
        try:
            self.stdscr.addstr(y + max_rows + 1, x, count_text[:w],
                               curses.A_DIM)
        except curses.error:
            pass

    # ── Save / Post / Void ──────────────────────────────────────────

    def _save_draft(self):
        """Save POS slip as draft."""
        if not self.db:
            return False
        if not self.hdr.get("slip_no"):
            self.hdr["slip_no"] = self._next_slip_no()
        total, vat, net = self._totals()
        doc = dict(self.hdr)
        doc["subtotal"] = total
        doc["tax_rate"] = self.vat_rate
        doc["tax_amt"] = vat
        doc["grand_total"] = net
        doc["lines"] = self.lines
        try:
            if self.edit_rowid:
                self.db.execute(
                    'UPDATE "pos_slip" SET data = ? WHERE rowid = ?',
                    (json.dumps(doc), self.edit_rowid))
            else:
                _, rowid = self.db.execute(
                    'INSERT INTO "pos_slip" (data) VALUES (?)',
                    (json.dumps(doc),))
                self.edit_rowid = rowid
            self._message = f"Saved: {self.hdr['slip_no']}"
            self._message_attr = curses.A_DIM
            return True
        except Exception as e:
            self._message = f"Save error: {e}"
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False

    def _post_slip(self):
        """Post POS slip — delegate to DSL script engine if available."""
        if not self.lines:
            self._message = "No lines to post"
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False
        if self.hdr.get("status") == "Posted":
            self._message = "Already posted"
            return False

        # Save first
        self.hdr["status"] = "Draft"
        if not self._save_draft():
            return False

        # Check for DSL script engine path
        layout = self.app.get("layouts", {}).get("pos_slip_add", {})
        scripts = dict(layout.get("scripts", {}))
        # Merge global subroutines
        for sname, sdef in self.app.get("subroutines", {}).items():
            if sname not in scripts:
                scripts[sname] = sdef

        if "post" in scripts and self.runner:
            return self._post_via_script(scripts)
        return self._post_hardcoded()

    def _post_via_script(self, scripts):
        """Post using DSL script engine."""
        from dsl_lib.script_engine import ScriptExecutor, ScriptError

        # Build context matching _handle_workflow format
        detail_rows = []
        for ln in self.lines:
            has_data = any(
                v for k, v in ln.items()
                if k not in ("rowid", "_parent_rowid")
                and v not in ("", 0, 0.0, None))
            if has_data:
                detail_rows.append(ln)

        ctx = {
            "id": self.edit_rowid,
            "table": "pos_slip",
            "fields": dict(self.hdr),
            "lines": detail_rows,
            "vars": {},
            "scripts": scripts,
        }

        has_txn = hasattr(self.db, "begin")
        try:
            if has_txn:
                self.db.begin()
            executor = ScriptExecutor(self.db, scripts,
                                      settings_fn=self.runner.get_setting)
            executor.run(scripts["post"], ctx)

            # Mark as posted
            self.hdr["status"] = "Posted"
            self._save_draft()
            if has_txn:
                self.db.commit()
            # Refresh lookup data so on_hand reflects deductions
            self.lookup_data = self.runner._load_lookups()
            self._message = f"Posted: {self.hdr['slip_no']}"
            self._message_attr = curses.A_DIM
            return True
        except ScriptError as e:
            if has_txn:
                try:
                    self.db.rollback()
                except Exception:
                    pass
            self._message = str(e)
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False
        except Exception as e:
            if has_txn:
                try:
                    self.db.rollback()
                except Exception:
                    pass
            self._message = f"Post error: {e}"
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False

    def _post_hardcoded(self):
        """Fallback: hardcoded stock deduction when no DSL script."""
        # Validate stock availability
        need_by_part = {}
        for ln in self.lines:
            pn = (ln.get("part_no") or "").strip()
            qty = _safe_float(ln.get("quantity"))
            if pn and qty > 0:
                need_by_part[pn] = need_by_part.get(pn, 0.0) + qty

        for pn, need in need_by_part.items():
            item = self._find_item(pn)
            if not item:
                self._message = f"Item not found: {pn}"
                self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
                return False
            on_hand = _safe_float(item.get("on_hand"))
            if on_hand + 0.001 < need:
                self._message = f"Insufficient stock: {pn} (need {need}, have {on_hand})"
                self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
                return False

        # Deduct stock
        has_txn = hasattr(self.db, "begin")
        try:
            if has_txn:
                self.db.begin()
            for ln in self.lines:
                pn = (ln.get("part_no") or "").strip()
                qty = _safe_float(ln.get("quantity"))
                if not pn or qty <= 0:
                    continue
                item = self._find_item(pn)
                if not item:
                    continue
                item_rowid = item.get("rowid")
                # Update item on_hand
                new_oh = max(0, _safe_float(item.get("on_hand")) - qty)
                item_doc = dict(item)
                item_doc.pop("rowid", None)
                item_doc["on_hand"] = new_oh
                self.db.execute(
                    'UPDATE "item" SET data = ? WHERE rowid = ?',
                    (json.dumps(item_doc), item_rowid))
                # Write item_card entry
                card_doc = {
                    "trans_date": self.hdr.get("issue_date", ""),
                    "trans_type": "POS",
                    "doc_no": self.hdr.get("slip_no", ""),
                    "receive_qty": 0,
                    "issue_qty": qty,
                    "balance": new_oh,
                    "unit_cost": _safe_float(ln.get("unit_price")),
                    "_parent_rowid": item_rowid,
                }
                self.db.execute(
                    'INSERT INTO "item_card" (data) VALUES (?)',
                    (json.dumps(card_doc),))

            # Mark as posted
            self.hdr["status"] = "Posted"
            self._save_draft()
            if has_txn:
                self.db.commit()
            self._message = f"Posted: {self.hdr['slip_no']}"
            self._message_attr = curses.A_DIM
            return True
        except Exception as e:
            if has_txn:
                try:
                    self.db.rollback()
                except Exception:
                    pass
            self._message = f"Post error: {e}"
            self._message_attr = curses.color_pair(CLR_FORM_SECTION) | curses.A_BOLD
            return False

    # ── Main loop ───────────────────────────────────────────────────

    def run(self):
        """Run POS screen. Returns when user presses ESC."""
        self.stdscr.keypad(True)
        self.stdscr.timeout(-1)

        while True:
            self._draw()
            key = self.stdscr.getch()

            # Tab — cycle focus
            if key == 9:
                if self._focus == 0:
                    self._focus = 1
                else:
                    # Cycle payment on second tab, then back to scan
                    self._payment_idx = (self._payment_idx + 1) % len(self._payment_opts)
                    self.hdr["payment"] = self._payment_opts[self._payment_idx]
                    self._focus = 0

            # Shift-Tab
            elif key in (curses.KEY_BTAB, 353):
                self._focus = 0

            # ESC
            elif key == 27:
                # Check for escape sequence
                self.stdscr.nodelay(True)
                k2 = self.stdscr.getch()
                self.stdscr.nodelay(False)
                if k2 == -1:
                    return  # pure ESC — exit

            # F5 — Post
            elif key == curses.KEY_F5:
                self._post_slip()

            # F8 — Void (placeholder)
            elif key == curses.KEY_F8:
                if self.hdr.get("status") == "Posted":
                    self._message = "Void not yet implemented"

            # F9 — Theme
            elif key == curses.KEY_F9:
                theme_picker(self.stdscr)

            # F10 / Ctrl+S — Save
            elif key in (curses.KEY_F10, 19):
                self._save_draft()

            # F3 — New slip
            elif key == curses.KEY_F3:
                self.hdr = {
                    "slip_no": "",
                    "issue_date": date.today().isoformat(),
                    "payment": self._payment_opts[0] if self._payment_opts else "CASH",
                    "customer_id": "",
                    "customer_name": "",
                    "status": "Draft",
                }
                self.lines = []
                self.edit_rowid = None
                self.cart_sel = 0
                self._scan_buf = ""
                self._message = ""

            # ── Scan input focused ──────────────────────────────────
            elif self._focus == 0:
                if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    if self._scan_buf.strip():
                        self._apply_scan(self._scan_buf.strip())
                        self._scan_buf = ""
                        self._scan_cursor = 0
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if self._scan_buf:
                        self._scan_buf = self._scan_buf[:-1]
                elif key == curses.KEY_DC:
                    self._scan_buf = ""
                elif key == curses.KEY_DOWN:
                    self._focus = 1
                elif 32 <= key <= 126:
                    self._scan_buf += chr(key)

            # ── Cart focused ────────────────────────────────────────
            elif self._focus == 1:
                if key == curses.KEY_UP:
                    if self.cart_sel > 0:
                        self.cart_sel -= 1
                    else:
                        self._focus = 0
                elif key == curses.KEY_DOWN:
                    if self.cart_sel < len(self.lines) - 1:
                        self.cart_sel += 1
                elif key in (curses.KEY_DC, 4):  # Del or Ctrl+D
                    if self.lines and 0 <= self.cart_sel < len(self.lines):
                        removed = self.lines.pop(self.cart_sel)
                        self._message = f"Removed {removed.get('part_no', '')}"
                        self._message_attr = curses.A_DIM
                        if self.cart_sel >= len(self.lines) and self.lines:
                            self.cart_sel = len(self.lines) - 1
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    # Edit qty inline
                    if self.lines and 0 <= self.cart_sel < len(self.lines):
                        self._edit_qty(self.cart_sel)

    def _edit_qty(self, idx):
        """Inline edit quantity for cart line."""
        ln = self.lines[idx]
        current = str(_safe_float(ln.get("quantity")))
        buf = current

        max_y, max_x = self.stdscr.getmaxyx()
        # Find qty column position using same layout math as _draw_cart
        gap = 2
        left_w = max_x - min(40, max(28, max_x // 3)) - 1
        fixed_total = sum(cw for fid, _, cw in self.cart_cols if fid != "part_name")
        fixed_gaps = gap * len(self.cart_cols)
        name_w = max(10, left_w - fixed_total - fixed_gaps - 1)
        qx = 1
        for fid, _, cw in self.cart_cols:
            if fid == "quantity":
                break
            qx += (name_w if fid == "part_name" else cw) + gap
        qy = 4 + idx  # cart starts at y=3, header=1 row, data starts at y=4
        qw = 8

        while True:
            try:
                self.stdscr.addstr(qy, qx,
                                   buf.rjust(qw)[:qw],
                                   curses.color_pair(CLR_FORM_INPUT_FOCUS))
                curses.curs_set(1)
                self.stdscr.move(qy, qx + qw - 1)
                self.stdscr.refresh()
            except curses.error:
                pass

            k = self.stdscr.getch()
            if k in (curses.KEY_ENTER, 10, 13, curses.PADENTER, 9):
                val = _safe_float(buf, _safe_float(ln.get("quantity")))
                if val > 0:
                    ln["quantity"] = val
                break
            elif k == 27:
                break
            elif k in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf = buf[:-1]
            elif 48 <= k <= 57 or k == 46:  # digits and dot
                buf += chr(k)
