import curses
from curses.textpad import rectangle
from typing import Dict, List, Any, Optional, Callable
from datetime import date
from .themes import CLR_FORM_INPUT


def _fmt_num(val):
    """Format a numeric value cleanly: no trailing .0, blank for 0, commas."""
    if val is None or val == "":
        return ""
    try:
        num = float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return str(val)
    if num == 0:
        return ""
    if num == int(num):
        return f"{int(num):,}"
    # Keep meaningful decimals, strip trailing zeros
    s = f"{num:,.10f}".rstrip("0").rstrip(".")
    return s


class BaseWidget:
    def __init__(self, y: int, x: int, width: int, value: Any = None):
        self.y = y
        self.x = x
        self.width = width
        self.value = value
        self.focused = False
        self._readonly = False
        self.on_change: Optional[Callable] = None

    def set_value(self, val):
        self.value = val
        if self.on_change:
            self.on_change(val)

    def draw(self, stdscr):
        display = str(self.value or "")[:self.width].ljust(self.width)
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, display, attr)

    def handle_key(self, key) -> bool:
        return False

    def cursor_x(self) -> int:
        """Return the X position where the terminal cursor should be placed."""
        return self.x

    def load(self, val):
        """Load a value from data (for editing existing records)."""
        self.set_value(val)


class TextInput(BaseWidget):
    """Editable single-line text field for STRING types."""
    def __init__(self, y: int, x: int, width: int, value: str = ""):
        super().__init__(y, x, width, value)
        self._default = str(value or "")
        self.buffer = self._default
        self.cursor = len(self.buffer)

    def load(self, val):
        """Load value; if empty and a default was set, use the default."""
        if not val and self._default:
            val = self._default
        self.buffer = str(val or "")
        self.cursor = len(self.buffer)
        self.set_value(self.buffer)

    def draw(self, stdscr):
        display = self.buffer[:self.width].ljust(self.width)
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, display, attr)

    def cursor_x(self) -> int:
        return self.x + min(self.cursor, self.width - 1)

    def handle_key(self, key) -> bool:
        if self._readonly:
            return False
        if key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if self.cursor > 0:
                self.buffer = self.buffer[:self.cursor-1] + self.buffer[self.cursor:]
                self.cursor -= 1
                self.set_value(self.buffer)
            return True
        elif key == curses.KEY_DC:
            if self.cursor < len(self.buffer):
                self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor+1:]
                self.set_value(self.buffer)
            return True
        elif key == curses.KEY_LEFT:
            if self.cursor > 0:
                self.cursor -= 1
            return True
        elif key == curses.KEY_RIGHT:
            if self.cursor < len(self.buffer):
                self.cursor += 1
            return True
        elif key == curses.KEY_HOME:
            self.cursor = 0
            return True
        elif key == curses.KEY_END:
            self.cursor = len(self.buffer)
            return True
        elif 32 <= key <= 126:
            if len(self.buffer) < self.width:
                self.buffer = self.buffer[:self.cursor] + chr(key) + self.buffer[self.cursor:]
                self.cursor += 1
                self.set_value(self.buffer)
            return True
        return False

    def load(self, val):
        self.buffer = str(val or "")
        self.cursor = len(self.buffer)
        self.set_value(self.buffer)


class MultiLineInput(BaseWidget):
    """Fixed-row multi-line text input.

    Enter moves between lines within the group (not to next widget).
    Tab/Shift-Tab exits the group. Value is stored as newline-joined string.
    """
    def __init__(self, y: int, x: int, width: int, rows: int = 3,
                 value: str = ""):
        super().__init__(y, x, width, value)
        self.rows = rows
        self._lines = [""] * rows
        self._line_idx = 0
        self._cursors = [0] * rows
        if value:
            self._load_text(str(value))

    def _load_text(self, text: str):
        parts = text.split("\n")
        for i in range(self.rows):
            self._lines[i] = parts[i] if i < len(parts) else ""
            self._cursors[i] = len(self._lines[i])
        self._sync_value()

    def _sync_value(self):
        joined = "\n".join(self._lines).rstrip("\n")
        self.set_value(joined)

    @property
    def height(self):
        return self.rows

    def load(self, val):
        self._load_text(str(val or ""))
        self._line_idx = 0

    def draw(self, stdscr):
        for i in range(self.rows):
            line = self._lines[i][:self.width].ljust(self.width)
            if self.focused and i == self._line_idx:
                attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE
            elif self.focused:
                attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_DIM
            else:
                attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
            try:
                stdscr.addstr(self.y + i, self.x, line, attr)
            except curses.error:
                pass

    def cursor_x(self) -> int:
        return self.x + min(self._cursors[self._line_idx], self.width - 1)

    def cursor_y(self) -> int:
        return self.y + self._line_idx

    def handle_key(self, key) -> bool:
        if self._readonly:
            return False
        li = self._line_idx
        buf = self._lines[li]
        cur = self._cursors[li]

        # Enter moves to next line within group
        if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
            if li < self.rows - 1:
                self._line_idx = li + 1
                return True
            return False  # at last line — let form move to next widget

        # Up/Down within group
        if key == curses.KEY_UP:
            if li > 0:
                self._line_idx = li - 1
                self._cursors[li - 1] = min(self._cursors[li - 1],
                                             len(self._lines[li - 1]))
                return True
            return False
        if key == curses.KEY_DOWN:
            if li < self.rows - 1:
                self._line_idx = li + 1
                self._cursors[li + 1] = min(self._cursors[li + 1],
                                             len(self._lines[li + 1]))
                return True
            return False

        # Standard text editing on current line
        if key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if cur > 0:
                self._lines[li] = buf[:cur-1] + buf[cur:]
                self._cursors[li] = cur - 1
                self._sync_value()
            return True
        elif key == curses.KEY_DC:
            if cur < len(buf):
                self._lines[li] = buf[:cur] + buf[cur+1:]
                self._sync_value()
            return True
        elif key == curses.KEY_LEFT:
            if cur > 0:
                self._cursors[li] = cur - 1
            return True
        elif key == curses.KEY_RIGHT:
            if cur < len(buf):
                self._cursors[li] = cur + 1
            return True
        elif key == curses.KEY_HOME:
            self._cursors[li] = 0
            return True
        elif key == curses.KEY_END:
            self._cursors[li] = len(buf)
            return True
        elif 32 <= key <= 126:
            if len(buf) < self.width:
                self._lines[li] = buf[:cur] + chr(key) + buf[cur:]
                self._cursors[li] = cur + 1
                self._sync_value()
            return True
        return False


class Checkbox(BaseWidget):
    def __init__(self, y: int, x: int, label: str, value: bool = False):
        super().__init__(y, x, len(label) + 4, value)
        self.label = label

    def draw(self, stdscr):
        mark = "X" if self.value else " "
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT)
        stdscr.addstr(self.y, self.x, f"[{mark}] {self.label}", attr)

    def cursor_x(self) -> int:
        return self.x + 1  # on the checkbox character

    def handle_key(self, key) -> bool:
        if key in (ord(' '), curses.KEY_ENTER, 10, 13, curses.PADENTER):
            self.set_value(not bool(self.value))
            return True
        return False

    def load(self, val):
        self.set_value(bool(val))


# ── Numeric formatting helpers (ported from pystock) ─────────────────────────

def _num_clean(s: str) -> str:
    """Strip commas and non-numeric chars, keep digits, dot, leading minus."""
    s = str(s or '').replace(',', '').strip()
    if not s:
        return ''
    if s == '-':
        return '-'
    out = []
    seen_dot = False
    for i, ch in enumerate(s):
        if ch.isdigit():
            out.append(ch)
        elif ch == '-' and i == 0:
            out.append('-')
        elif ch == '.' and not seen_dot:
            out.append('.')
            seen_dot = True
    return ''.join(out)


def _num_format(raw: str) -> str:
    """Format a raw numeric string with commas: 1234.5 -> 1,234.5"""
    raw = str(raw or '')
    if raw in ('', '-', '.', '-.'):
        return raw
    sign = ''
    rest = raw
    if rest.startswith('-'):
        sign = '-'
        rest = rest[1:]
    had_dot = '.' in rest
    if had_dot:
        int_part, dec_part = rest.split('.', 1)
    else:
        int_part, dec_part = rest, ''
    if int_part == '':
        fmt_int = '0' if had_dot else ''
    else:
        digits = ''.join(ch for ch in int_part if ch.isdigit())
        if not digits:
            fmt_int = '0' if had_dot else ''
        else:
            parts = []
            while digits:
                parts.append(digits[-3:])
                digits = digits[:-3]
            fmt_int = ','.join(reversed(parts))
    if had_dot:
        return f'{sign}{fmt_int}.{dec_part}'
    return f'{sign}{fmt_int}'


def _num_commas_before(digit_pos: int, int_len: int) -> int:
    """How many commas appear before digit_pos in a comma-formatted integer of int_len digits."""
    if int_len <= 3 or digit_pos <= 0:
        return 0
    first = int_len % 3
    if first == 0:
        first = 3
    if digit_pos <= first:
        return 0
    return 1 + (digit_pos - first - 1) // 3


class NumericInput(BaseWidget):
    def __init__(self, y: int, x: int, width: int, value: float = 0,
                 decimals: int = 2, min_val: float = None, max_val: float = None):
        super().__init__(y, x, width, value)
        self.decimals = decimals
        self.min_val = min_val
        self.max_val = max_val
        # Store raw unformatted string (no commas)
        if value is not None and value != "" and value != 0:
            if isinstance(value, float) and value == int(value):
                self.raw = _num_clean(str(int(value)))
            else:
                self.raw = _num_clean(str(value))
        else:
            self.raw = ""
        self.raw_cursor = len(self.raw)

    def _formatted_and_cursor(self):
        """Return (formatted_display, display_cursor_pos) from raw buffer."""
        raw = _num_clean(self.raw)
        if raw != self.raw:
            self.raw = raw
        cur = max(0, min(self.raw_cursor, len(raw)))

        sign_len = 1 if raw.startswith('-') else 0
        rest = raw[sign_len:]
        dot_at = rest.find('.')
        int_len = dot_at if dot_at >= 0 else len(rest)

        fmt = _num_format(raw)

        p = cur - sign_len
        if p <= int_len:
            disp_cursor = sign_len + p + _num_commas_before(p, int_len)
        else:
            commas_total = (max(0, int_len - 1) // 3) if int_len > 0 else 0
            fmt_int_len = int_len + commas_total
            disp_cursor = sign_len + fmt_int_len + (p - int_len)
        disp_cursor = max(0, min(disp_cursor, len(fmt)))
        return fmt, disp_cursor

    def draw(self, stdscr):
        fmt, _ = self._formatted_and_cursor()
        if not fmt:
            display = ""
        else:
            display = fmt
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, display.rjust(self.width)[:self.width], attr)

    def cursor_x(self) -> int:
        fmt, disp_cur = self._formatted_and_cursor()
        # Right-justified: offset = padding
        padding = max(0, self.width - len(fmt))
        pos = self.x + padding + disp_cur
        # Keep cursor within the field bounds
        return min(pos, self.x + self.width - 1)

    def handle_key(self, key) -> bool:
        if key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if self.raw_cursor > 0:
                self.raw = self.raw[:self.raw_cursor-1] + self.raw[self.raw_cursor:]
                self.raw_cursor -= 1
                self._commit()
            return True
        elif key == curses.KEY_LEFT:
            if self.raw_cursor > 0:
                self.raw_cursor -= 1
            return True
        elif key == curses.KEY_RIGHT:
            if self.raw_cursor < len(self.raw):
                self.raw_cursor += 1
            return True
        elif key == curses.KEY_HOME:
            self.raw_cursor = 0
            return True
        elif key == curses.KEY_END:
            self.raw_cursor = len(self.raw)
            return True
        elif 32 <= key <= 126:
            ch = chr(key)
            if ch.isdigit():
                # Clear leading zero when typing a new digit
                if self.raw == "0" and ch != "0":
                    self.raw = ""
                    self.raw_cursor = 0
                self.raw = self.raw[:self.raw_cursor] + ch + self.raw[self.raw_cursor:]
                self.raw_cursor += 1
                self._commit()
                return True
            elif ch == '.' and '.' not in self.raw:
                self.raw = self.raw[:self.raw_cursor] + ch + self.raw[self.raw_cursor:]
                self.raw_cursor += 1
                self._commit()
                return True
            elif ch == '-' and self.raw_cursor == 0 and not self.raw.startswith('-'):
                self.raw = '-' + self.raw
                self.raw_cursor += 1
                self._commit()
                return True
        return False

    def load(self, val):
        """Load a numeric value — syncs both self.value and self.raw for display."""
        if val is None or val == "":
            self.raw = ""
            self.raw_cursor = 0
            self.set_value(None)
            return
        try:
            num = float(val)
        except (ValueError, TypeError):
            self.raw = ""
            self.raw_cursor = 0
            self.set_value(None)
            return
        if num == int(num):
            self.raw = _num_clean(str(int(num)))
        else:
            self.raw = _num_clean(str(num))
        self.raw_cursor = len(self.raw)
        self.set_value(num)

    def _commit(self):
        try:
            val = float(self.raw) if self.raw and self.raw not in ('-', '.', '-.') else None
            if val is not None:
                if self.min_val is not None and val < self.min_val:
                    val = self.min_val
                if self.max_val is not None and val > self.max_val:
                    val = self.max_val
            self.set_value(val)
        except ValueError:
            pass

    def load(self, val):
        if val is not None and val != "":
            # Parse to number, blank for zero
            try:
                num = float(str(val).replace(",", ""))
            except (ValueError, TypeError):
                num = 0
            if num == 0:
                self.raw = ""
            elif num == int(num):
                self.raw = _num_clean(str(int(num)))
            else:
                self.raw = _num_clean(str(val))
        else:
            self.raw = ""
        self.raw_cursor = len(self.raw)
        self._commit()


# ── Date Input with Mask (ported from pystock) ───────────────────────────────

class DateInput(BaseWidget):
    """Date field with ____-__-__ mask. Cursor skips over dashes."""
    # Display positions that accept digit input (0-based)
    SLOT_POS = [0, 1, 2, 3, 5, 6, 8, 9]  # skip 4(-) and 7(-)

    def __init__(self, y: int, x: int, width: int, value=None):
        super().__init__(y, x, max(width, 10), value)
        self.slots = ['_'] * 8  # 8 digit slots: YYYYMMDD
        self.slot_cursor = 0
        if value:
            self._load_value(value)

    def _load_value(self, v):
        s = str(v or '').strip()
        digits = ''.join(c for c in s if c.isdigit())[:8]
        self.slots = ['_'] * 8
        for i, d in enumerate(digits):
            self.slots[i] = d
        # Advance cursor to first empty slot
        self.slot_cursor = self._first_empty()

    def _first_empty(self) -> int:
        for i, c in enumerate(self.slots):
            if c == '_':
                return i
        return 7  # all filled, cursor on last

    def _display(self) -> str:
        s = self.slots
        return f"{s[0]}{s[1]}{s[2]}{s[3]}-{s[4]}{s[5]}-{s[6]}{s[7]}"

    def _is_complete(self) -> bool:
        return all(c != '_' for c in self.slots)

    def _to_date(self):
        if not self._is_complete():
            return None
        try:
            y = int(''.join(self.slots[0:4]))
            m = int(''.join(self.slots[4:6]))
            d = int(''.join(self.slots[6:8]))
            return date(y, m, d)
        except (ValueError, IndexError):
            return None

    def draw(self, stdscr):
        display = self._display()
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, display.ljust(self.width)[:self.width], attr)
        # When focused, highlight the current slot position
        if self.focused and 0 <= self.slot_cursor < 8:
            pos = self.SLOT_POS[self.slot_cursor]
            ch = self.slots[self.slot_cursor]
            stdscr.addstr(self.y, self.x + pos, ch,
                          curses.A_BOLD | curses.A_UNDERLINE)

    def cursor_x(self) -> int:
        if 0 <= self.slot_cursor < 8:
            return self.x + self.SLOT_POS[self.slot_cursor]
        return self.x + 9

    def handle_key(self, key) -> bool:
        if key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if self.slot_cursor > 0:
                self.slot_cursor -= 1
                self.slots[self.slot_cursor] = '_'
                self.set_value(self._to_date())
            return True
        elif key == curses.KEY_LEFT:
            if self.slot_cursor > 0:
                self.slot_cursor -= 1
            return True
        elif key == curses.KEY_RIGHT:
            if self.slot_cursor < 7:
                self.slot_cursor += 1
            return True
        elif key == curses.KEY_HOME:
            self.slot_cursor = 0
            return True
        elif key == curses.KEY_END:
            self.slot_cursor = 7
            return True
        elif 32 <= key <= 126:
            ch = chr(key)
            if ch in ('t', 'T'):
                # Fill with today's date
                today = date.today()
                digits = today.strftime('%Y%m%d')
                for i, d in enumerate(digits):
                    self.slots[i] = d
                self.slot_cursor = 7
                self.set_value(today)
                return True
            if ch.isdigit() and self.slot_cursor < 8:
                self.slots[self.slot_cursor] = ch
                if self.slot_cursor < 7:
                    self.slot_cursor += 1
                self.set_value(self._to_date())
                return True
        return False

    def load(self, val):
        self._load_value(val)
        self.set_value(self._to_date())


# ── Option Dropdown Popup (ported from pystock) ──────────────────────────────

class OptionDropdown:
    """Popup dropdown list positioned below a form field.
    Supports type-ahead filtering and adding new values."""
    @staticmethod
    def show(stdscr, field_y: int, field_x: int, field_w: int,
             current: str, options: list, field_name: str = "") -> Optional[str]:
        from .cxgrid import _save_custom_options
        opts = [o for o in (options or []) if o is not None]
        if not opts:
            return None

        max_y, max_x = stdscr.getmaxyx()
        inner_w = max(len(str(o)) for o in opts) + 4
        w = min(max_x - field_x - 1, max(inner_w, field_w, 10))
        visible = min(len(opts), max(4, max_y - field_y - 4))
        h = visible + 2  # top/bottom border

        # Position below field; fall back to above if near bottom
        y = field_y + 1
        if y + h >= max_y:
            y = max(0, field_y - h)
        x = min(field_x, max(0, max_x - w - 1))

        # Pre-select current value
        cur = 0
        cur_upper = (current or '').strip().upper()
        for i, o in enumerate(opts):
            if str(o).strip().upper() == cur_upper:
                cur = i
                break

        scroll = max(0, cur - visible + 1)
        search = ""

        # Create popup window
        try:
            popup = curses.newwin(h, w, y, x)
        except curses.error:
            return None
        popup.keypad(True)

        result = None
        while True:
            # Filter by search text
            if search:
                filtered = [o for o in opts if search.lower() in str(o).lower()]
            else:
                filtered = list(opts)

            cur = max(0, min(cur, len(filtered) - 1)) if filtered else 0
            if filtered:
                if cur < scroll:
                    scroll = cur
                elif cur >= scroll + visible:
                    scroll = cur - visible + 1
                scroll = max(0, min(scroll, max(0, len(filtered) - visible)))

            popup.erase()
            try:
                popup.box()
            except curses.error:
                pass

            # Title with search text
            if search:
                title = f" {search}_ "
                try:
                    popup.addstr(0, 2, title[:w - 4], curses.A_BOLD)
                except curses.error:
                    pass

            if filtered:
                for i in range(visible):
                    idx = scroll + i
                    if idx >= len(filtered):
                        break
                    ry = 1 + i
                    active_row = (idx == cur)
                    if active_row:
                        attr = curses.A_REVERSE | curses.A_BOLD
                        marker = "> "
                    else:
                        attr = 0
                        marker = "  "
                    label = str(filtered[idx])[:w - 5]
                    try:
                        popup.addstr(ry, 1, f"{marker}{label.ljust(w - 5)}", attr)
                    except curses.error:
                        pass
            elif search:
                # No matches — hint that Enter adds new value
                msg = "Enter=Add new"
                try:
                    popup.addstr(1 + visible // 2, max(1, (w - len(msg)) // 2),
                                 msg, curses.A_DIM)
                except curses.error:
                    pass

            popup.refresh()
            key = popup.getch()

            if key == 27:  # ESC
                break
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER, ord(' ')):
                if filtered:
                    result = filtered[cur]
                elif search.strip():
                    # Add new custom value
                    new_val = search.strip()
                    opts.append(new_val)
                    options.append(new_val)  # mutate caller's list
                    if field_name:
                        _save_custom_options(field_name, options)
                    result = new_val
                break
            elif key == curses.KEY_UP:
                if filtered:
                    cur = (cur - 1) % len(filtered)
                    if cur == len(filtered) - 1:
                        scroll = max(0, len(filtered) - visible)
            elif key == curses.KEY_DOWN:
                if filtered:
                    cur = (cur + 1) % len(filtered)
                    if cur == 0:
                        scroll = 0
            elif key == curses.KEY_BACKSPACE or key in (127, 8):
                if search:
                    search = search[:-1]
                    cur = 0
                    scroll = 0
            elif 33 <= key <= 126:
                search += chr(key)
                cur = 0
                scroll = 0

        # Clean up popup
        try:
            popup.erase()
            popup.refresh()
            stdscr.touchwin()
            stdscr.refresh()
        except curses.error:
            pass

        return result


# ── Search Dialog Popup (ported from pystock SearchDialog) ─────────────────────

class SearchDialog:
    """Professional popup search window with colored UI, column headers,
    scrollable results, and type-to-filter."""

    # Color pair IDs (initialized once).
    # NOTE: keep these OUT of the 20-54 (cxgrid) and 40-49 (cxreport) ranges
    # to avoid init_pair collisions — whichever module init_pair's an ID last
    # wins, corrupting the other's colors. Use 60-65 for SearchDialog.
    _colors_ready = False
    CLR_BORDER = 60
    CLR_SEARCH = 61
    CLR_HEADER = 62
    CLR_NORMAL = 63
    CLR_SELECTED = 64
    CLR_DIM = 65

    @classmethod
    def _init_colors(cls):
        # Colors initialized centrally by tui.themes.init_colors()
        cls._colors_ready = True

    @staticmethod
    def show(stdscr, field_y: int, field_x: int, field_w: int,
             data: List[Dict], display_field: str = "name",
             value_field: str = "code", title: str = "Search",
             list_columns: list = None,
             current_value: str = None) -> Optional[Dict]:
        if not data:
            # Show "No records" message popup
            SearchDialog._init_colors()
            max_y, max_x = stdscr.getmaxyx()
            msg = "No records found. Add data first."
            w = len(msg) + 4
            h = 5
            py = max(0, (max_y - h) // 2)
            px = max(0, (max_x - w) // 2)
            popup = curses.newwin(h, w, py, px)
            popup.attrset(curses.color_pair(SearchDialog.CLR_BORDER))
            popup.border()
            popup.addstr(0, 2, f" {title} ", curses.color_pair(SearchDialog.CLR_BORDER) | curses.A_BOLD)
            popup.addstr(2, 1, msg.center(w - 2), curses.color_pair(SearchDialog.CLR_NORMAL))
            popup.addstr(h - 1, max(0, w - 18), " Press any key ", curses.color_pair(SearchDialog.CLR_DIM))
            popup.refresh()
            stdscr.getch()
            return None

        SearchDialog._init_colors()
        CLR_BORDER = SearchDialog.CLR_BORDER
        CLR_SEARCH = SearchDialog.CLR_SEARCH
        CLR_HEADER = SearchDialog.CLR_HEADER
        CLR_NORMAL = SearchDialog.CLR_NORMAL
        CLR_SELECTED = SearchDialog.CLR_SELECTED
        CLR_DIM = SearchDialog.CLR_DIM

        max_y, max_x = stdscr.getmaxyx()

        # ── Multi-column mode: show all LIST columns ──
        if list_columns and len(list_columns) > 1:
            # Use all LIST column definitions for display
            col_defs = [{"id": c["id"],
                         "label": c["id"].replace("_", " ").title(),
                         "width": c.get("width", 12),
                         "numeric": c.get("numeric", False)} for c in list_columns]
        else:
            # Legacy 2-column mode
            col_defs = None

        if col_defs:
            # Multi-column sizing
            col_widths = [c["width"] for c in col_defs]
            col_labels = [c["label"] for c in col_defs]
            col_ids = [c["id"] for c in col_defs]
            n = len(col_widths)
            # Interior: each col uses (cw + 2 padding), plus 2 side margins
            inner_w = sum(col_widths) + n * 2
            w = min(max_x - 4, max(inner_w + 4, 50))
            # Available width for all columns: w - 4 (borders+margins) - (n-1)*2 (gaps)
            avail_total = w - 4 - (n - 1) * 2
            if sum(col_widths) > avail_total:
                # Shrink from last column backward
                excess = sum(col_widths) - avail_total
                for ci in range(n - 1, -1, -1):
                    shrink = min(excess, col_widths[ci] - 4)
                    if shrink > 0:
                        col_widths[ci] -= shrink
                        excess -= shrink
                    if excess <= 0:
                        break
            elif avail_total > sum(col_widths):
                # Distribute excess to last column
                col_widths[-1] += avail_total - sum(col_widths)
        else:
            # Legacy 2-column sizing
            vf_label = value_field.replace("_", " ").title()
            df_label = display_field.replace("_", " ").title()
            if list_columns:
                col_map = {c["id"]: c.get("width", 12) for c in list_columns}
                col_val_w = col_map.get(value_field, max(len(vf_label), 10)) + 1
                col_disp_w = col_map.get(display_field, max(len(df_label), 12)) + 1
            else:
                col_val_w = max(len(vf_label), max(len(str(r.get(value_field, ""))) for r in data)) + 1
                col_disp_w = max(len(df_label), max(len(str(r.get(display_field, ""))) for r in data)) + 1
            inner_w = col_val_w + col_disp_w + 6
            w = min(max_x - 4, max(inner_w, 50))
            col_disp_w = max(col_disp_w, w - col_val_w - 6)

        h = min(max_y - 4, 22)
        visible = h - 6  # border(1) + search(1) + header(1) + separator(1) + status(1) + border(1)

        # Center on terminal
        y = max(0, (max_y - h) // 2)
        x = max(0, (max_x - w) // 2)

        try:
            popup = curses.newwin(h, w, y, x)
        except curses.error:
            return None
        popup.keypad(True)

        search = ""
        cur = 0
        scroll = 0
        filtered = list(data)
        result = None

        # Fields that are identity/system keys — excluded from auto-confirm check
        _ID_SUFFIXES = ("_id", "_uuid", "_unid", "_uid", "_rowid")
        _ID_NAMES    = {"id", "uuid", "unid", "uid", "rowid", "_parent_rowid"}

        def _is_identity_field(k):
            if k in _ID_NAMES:
                return True
            kl = k.lower()
            if any(kl.endswith(s) for s in _ID_SUFFIXES):
                return True
            if "uuid" in kl or "unid" in kl:
                return True
            return False

        # Pre-select current value
        if current_value is not None:
            for i, row in enumerate(filtered):
                if str(row.get(value_field, "")) == str(current_value):
                    cur = i
                    if cur >= visible:
                        scroll = cur - visible + 1
                    break

        def _filter():
            nonlocal filtered, cur, scroll
            if not search:
                filtered = list(data)
            else:
                term = search.lower()
                filtered = [r for r in data
                            if any(term in str(v).lower()
                                   for k, v in r.items()
                                   if k not in ("rowid", "_parent_rowid"))]
            cur = 0
            scroll = 0

        def _auto_confirm_single():
            """Return True and set result if exactly one match on non-identity fields."""
            nonlocal result
            if len(filtered) != 1 or not search:
                return False
            row = filtered[0]
            term = search.lower()
            # Check if match is on at least one meaningful (non-identity) field
            meaningful_match = any(
                term in str(v).lower()
                for k, v in row.items()
                if not _is_identity_field(k)
            )
            if meaningful_match:
                result = row
                return True
            return False

        while True:
            popup.erase()

            # ── Border ──
            try:
                popup.attron(curses.color_pair(CLR_BORDER))
                popup.box()
                popup.attroff(curses.color_pair(CLR_BORDER))
            except curses.error:
                pass

            # ── Title ──
            try:
                popup.addstr(0, 2, f" {title} ",
                             curses.color_pair(CLR_BORDER) | curses.A_BOLD)
            except curses.error:
                pass

            # ── Search bar (row 1) ──
            try:
                popup.addstr(1, 2, "Search: ",
                             curses.color_pair(CLR_HEADER) | curses.A_BOLD)
                search_w = w - 12
                search_display = search[-search_w:] if len(search) > search_w else search
                popup.addstr(1, 10, search_display.ljust(search_w)[:search_w],
                             curses.color_pair(CLR_SEARCH))
            except curses.error:
                pass

            # ── Column headers (row 2) ──
            try:
                hx = 2
                if col_defs:
                    for ci, label in enumerate(col_labels):
                        cw = col_widths[ci]
                        max_cw = w - 2 - hx
                        if max_cw <= 0:
                            break
                        cw_draw = min(cw, max_cw)
                        popup.addstr(2, hx, label[:cw_draw].ljust(cw_draw),
                                     curses.color_pair(CLR_HEADER) | curses.A_BOLD)
                        hx += cw + 2
                else:
                    popup.addstr(2, hx, vf_label[:col_val_w].ljust(col_val_w),
                                 curses.color_pair(CLR_HEADER) | curses.A_BOLD)
                    hx += col_val_w + 1
                    popup.addstr(2, hx, df_label[:col_disp_w].ljust(col_disp_w),
                                 curses.color_pair(CLR_HEADER) | curses.A_BOLD)
            except curses.error:
                pass

            # ── Separator line (row 3) ──
            try:
                sep = "\u2500" * (w - 2)
                popup.addstr(3, 1, sep, curses.color_pair(CLR_DIM))
            except curses.error:
                pass

            # ── Data rows (row 4+) ──
            for i in range(visible):
                idx = scroll + i
                ry = 4 + i
                if ry >= h - 1:
                    break
                if idx >= len(filtered):
                    break
                row = filtered[idx]
                is_sel = (idx == cur)
                # The selected color pair already defines foreground/background.
                # Adding A_REVERSE here would swap it again in themes such as YELLOW.
                attr = curses.color_pair(CLR_SELECTED) | curses.A_BOLD if is_sel \
                    else curses.color_pair(CLR_NORMAL)

                # Clear row background
                try:
                    popup.addstr(ry, 1, " " * (w - 2), attr)
                except curses.error:
                    pass

                if col_defs:
                    # Multi-column rendering
                    rx = 2
                    for ci, cid in enumerate(col_ids):
                        cw = col_widths[ci]
                        # Clip to popup interior
                        max_cw = w - 2 - rx
                        if max_cw <= 0:
                            break
                        cw_draw = min(cw, max_cw)
                        raw = row.get(cid, "")
                        if col_defs[ci].get("numeric"):
                            val_str = _fmt_num(raw)
                            val_str = val_str[:cw_draw].rjust(cw_draw)
                        else:
                            val_str = str(raw)[:cw_draw].ljust(cw_draw)
                        try:
                            popup.addstr(ry, rx, val_str, attr)
                        except curses.error:
                            pass
                        rx += cw + 2
                else:
                    # Legacy 2-column
                    val_str = str(row.get(value_field, ""))[:col_val_w]
                    try:
                        popup.addstr(ry, 2, val_str.ljust(col_val_w), attr)
                    except curses.error:
                        pass
                    disp_str = str(row.get(display_field, ""))[:col_disp_w]
                    try:
                        popup.addstr(ry, 2 + col_val_w + 1, disp_str.ljust(col_disp_w), attr)
                    except curses.error:
                        pass

            # ── Scrollbar ──
            if len(filtered) > visible and visible > 0:
                sb_height = max(1, visible * visible // len(filtered))
                sb_pos = scroll * visible // len(filtered) if len(filtered) > 0 else 0
                for si in range(visible):
                    ry = 4 + si
                    if ry >= h - 1:
                        break
                    ch = "\u2588" if sb_pos <= si < sb_pos + sb_height else "\u2502"
                    try:
                        popup.addstr(ry, w - 1, ch, curses.color_pair(CLR_DIM))
                    except curses.error:
                        pass

            # ── Status bar ──
            try:
                count_str = f" {len(filtered)} found "
                popup.addstr(h - 1, w - len(count_str) - 1, count_str,
                             curses.color_pair(CLR_DIM))
            except curses.error:
                pass

            # Position cursor in search field
            try:
                cursor_pos = min(len(search), w - 13)
                popup.move(1, 10 + cursor_pos)
                curses.curs_set(1)
            except curses.error:
                pass

            popup.refresh()
            key = popup.getch()

            if key == 27:  # ESC
                break
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                if filtered:
                    result = filtered[cur]
                break
            elif key == curses.KEY_UP:
                if cur > 0:
                    cur -= 1
                    if cur < scroll:
                        scroll = cur
            elif key == curses.KEY_DOWN:
                if cur < len(filtered) - 1:
                    cur += 1
                    if cur >= scroll + visible:
                        scroll = cur - visible + 1
            elif key == curses.KEY_PPAGE:
                cur = max(0, cur - visible)
                scroll = max(0, scroll - visible)
            elif key == curses.KEY_NPAGE:
                cur = min(len(filtered) - 1, cur + visible)
                scroll = min(max(0, len(filtered) - visible), scroll + visible)
            elif key == curses.KEY_HOME:
                cur = 0
                scroll = 0
            elif key == curses.KEY_END:
                cur = max(0, len(filtered) - 1)
                scroll = max(0, len(filtered) - visible)
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
                if search:
                    search = search[:-1]
                    _filter()
            elif 32 <= key <= 126:
                search += chr(key)
                _filter()
                if _auto_confirm_single():
                    break

        # Clean up
        try:
            curses.curs_set(0)
            popup.erase()
            popup.refresh()
            stdscr.touchwin()
            stdscr.refresh()
        except curses.error:
            pass

        return result


class Combobox(BaseWidget):
    def __init__(self, y: int, x: int, width: int, options: List[str],
                 value: Any = None, field_name: str = ""):
        super().__init__(y, x, width, value)
        from .cxgrid import merge_custom_options
        self.field_name = field_name
        self.options = merge_custom_options(field_name, options) if field_name else list(options)
        self.sel_idx = self.options.index(value) if value in self.options else 0
        self.set_value(self.options[self.sel_idx] if self.options else None)
        self._stdscr = None  # set by layout renderer for dropdown popup

    def draw(self, stdscr):
        self._stdscr = stdscr
        display = str(self.options[self.sel_idx]) if self.options else ""
        indicator = " [v]" if self.focused else "    "
        text = display + indicator
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, text[:self.width].ljust(self.width), attr)

    def cursor_x(self) -> int:
        display = str(self.options[self.sel_idx]) if self.options else ""
        return self.x + min(len(display), self.width - 1)

    def handle_key(self, key) -> bool:
        if not self.options:
            return False
        if key == ord(' '):
            # Open dropdown popup (Space only — Enter navigates to next field)
            if self._stdscr:
                current = str(self.options[self.sel_idx])
                choice = OptionDropdown.show(
                    self._stdscr, self.y, self.x, self.width,
                    current, self.options, field_name=self.field_name
                )
                if choice is not None:
                    if choice in self.options:
                        self.sel_idx = self.options.index(choice)
                    else:
                        self.options.append(choice)
                        self.sel_idx = len(self.options) - 1
                    self.set_value(choice)
            return True
        if key == curses.KEY_RIGHT or key == curses.KEY_DOWN:
            self.sel_idx = (self.sel_idx + 1) % len(self.options)
            self.set_value(self.options[self.sel_idx])
            return True
        if key == curses.KEY_LEFT or key == curses.KEY_UP:
            self.sel_idx = (self.sel_idx - 1) % len(self.options)
            self.set_value(self.options[self.sel_idx])
            return True
        return False

    def load(self, val):
        val_str = str(val) if val is not None else ""
        for i, o in enumerate(self.options):
            if str(o) == val_str:
                self.sel_idx = i
                self.set_value(o)
                return
        # Value not in options — add it (custom/legacy data)
        if val_str:
            self.options.append(val_str)
            self.sel_idx = len(self.options) - 1
        self.set_value(val)


class LookupField(BaseWidget):
    def __init__(self, y: int, x: int, width: int, lookup_data: List[Dict],
                 display_field: str = "name", value_field: str = "code",
                 value: Any = None, list_columns: list = None):
        super().__init__(y, x, width, value)
        self.lookup_data = lookup_data
        self.display_field = display_field
        self.value_field = value_field
        self.list_columns = list_columns  # LIST column defs for popup sizing
        self.sel_idx = 0
        self._stdscr = None

    def draw(self, stdscr):
        self._stdscr = stdscr
        # Show the actual value (e.g. "PO-0001"), not the display_field
        display = str(self.value or "")
        attr = curses.color_pair(CLR_FORM_INPUT) | curses.A_REVERSE if self.focused else curses.color_pair(CLR_FORM_INPUT) | curses.A_UNDERLINE
        stdscr.addstr(self.y, self.x, display.ljust(self.width)[:self.width], attr)
        btn_attr = curses.A_BOLD if self.focused else 0
        try:
            stdscr.addstr(self.y, self.x + self.width + 1, "[F4]", btn_attr)
        except curses.error:
            pass

    def cursor_x(self) -> int:
        return self.x

    def handle_key(self, key) -> bool:
        # Space or F4 opens search dialog (even when data is empty)
        if key in (ord(' '), curses.KEY_F4):
            if self._stdscr:
                result = SearchDialog.show(
                    self._stdscr, self.y, self.x, self.width,
                    self.lookup_data, self.display_field, self.value_field,
                    list_columns=self.list_columns,
                    current_value=self.value
                )
                if result is not None:
                    self.set_value(result.get(self.value_field, ""))
                    # sync sel_idx
                    for i, row in enumerate(self.lookup_data):
                        if row.get(self.value_field) == self.value:
                            self.sel_idx = i
                            break
            return True
        # Arrow keys cycle inline (only when data exists)
        if not self.lookup_data:
            return False
        if key in (curses.KEY_RIGHT, curses.KEY_DOWN):
            self.sel_idx = (self.sel_idx + 1) % len(self.lookup_data)
            self.set_value(self.lookup_data[self.sel_idx][self.value_field])
            return True
        if key in (curses.KEY_LEFT, curses.KEY_UP):
            self.sel_idx = (self.sel_idx - 1) % len(self.lookup_data)
            self.set_value(self.lookup_data[self.sel_idx][self.value_field])
            return True
        return False

    def load(self, val):
        self.set_value(val)
        for i, row in enumerate(self.lookup_data):
            if row.get(self.value_field) == val:
                self.sel_idx = i
                break


# ── Section Separator (non-interactive, for form grouping) ───────────────────

class SectionSeparator:
    """Non-interactive section header drawn as ── Label ──────"""
    def __init__(self, y: int, x: int, width: int, label: str):
        self.y = y
        self.x = x
        self.width = width
        self.label = label
        self.focused = False  # never focused
        self.is_section = True

    def draw(self, stdscr):
        from .themes import CLR_FORM_SECTION
        if self.label:
            tag = f" {self.label} "
            lead = "\u2500\u2500"  # ──
            trail_len = max(0, self.width - len(tag) - len(lead))
            trail = "\u2500" * trail_len
            line = lead + tag + trail
        else:
            line = "\u2500" * self.width
        try:
            stdscr.addstr(self.y, self.x, line[:self.width],
                          curses.A_BOLD | curses.color_pair(CLR_FORM_SECTION))
        except curses.error:
            pass
