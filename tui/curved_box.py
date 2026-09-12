"""
CurvedBoxUI -- Reusable curses widget for drawing rounded boxes, tabs,
grids with scroll/selection, click zones, and badges.

Ported from pystock/ui/CurvedBoxUI.py for dbnocode.
"""
import curses
import time

COLS = 3  # Grid column count — shared between draw_grid and input handling

# Event types
EVT_CLICK = "click"
EVT_DBLCLICK = "dblclick"

# Box UI color pairs live in a dedicated band (70+) so they never collide
# with the grid/report/dialog/search theme pairs (20-65) defined in themes.py.
_CP_OFFSET = 70


class CurvedBoxUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        self.stdscr.keypad(True)
        self.stdscr.clear()

        # Enable mouse support
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

        self._init_colors()

        # Rounded characters
        self.tl = "\u256d"
        self.tr = "\u256e"
        self.bl = "\u2570"
        self.br = "\u256f"
        self.h_line = "\u2500"
        self.v_line = "\u2502"

        # Store positions
        self.tab_rects = []
        self.item_rects = []

        # Scroll & selection
        self.scroll_offset = 0
        self.max_visible_rows = 0
        self.selected_index = -1

        # --- Click event system ---
        self._click_zones = []       # [(y1, x1, y2, x2, zone_id, data)]
        self._callbacks = {}         # {(zone_id, event_type): callback_fn}
        self._last_click_time = 0.0
        self._last_click_zone = None
        self._dblclick_threshold = 0.4  # seconds

    def _init_colors(self):
        """Set CurvedBoxUI color pairs. Safe to re-call after theme resets.

        init_pair can raise curses.error on terminals with a limited color-pair
        table; ignore those so the box still draws (just without color).
        """
        try:
            curses.init_pair(_CP_OFFSET + 1, curses.COLOR_BLUE, curses.COLOR_BLACK)     # Normal border
            curses.init_pair(_CP_OFFSET + 2, curses.COLOR_WHITE, curses.COLOR_BLUE)     # Selected box/tab
            curses.init_pair(_CP_OFFSET + 3, curses.COLOR_CYAN, curses.COLOR_BLACK)     # Badge
            curses.init_pair(_CP_OFFSET + 4, curses.COLOR_BLACK, curses.COLOR_WHITE)    # Search box
            curses.init_pair(_CP_OFFSET + 5, curses.COLOR_BLACK, curses.COLOR_WHITE)    # Normal tab
            curses.init_pair(_CP_OFFSET + 6, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Highlight border
            curses.init_pair(_CP_OFFSET + 7, curses.COLOR_BLUE, curses.COLOR_BLACK)     # Fill bar
        except curses.error:
            pass

    # --- Click zone registration ---

    def clear_zones(self):
        """Clear all registered click zones. Call at the start of each draw cycle."""
        self._click_zones = []

    def add_zone(self, y, x, h, w, zone_id, data=None):
        """Register a rectangular click zone with an ID and optional data payload."""
        self._click_zones.append((y, x, y + h - 1, x + w - 1, zone_id, data))

    def on(self, zone_id, event_type, callback):
        """Register a callback for a zone + event type."""
        self._callbacks[(zone_id, event_type)] = callback

    def off(self, zone_id, event_type):
        """Remove a callback."""
        self._callbacks.pop((zone_id, event_type), None)

    def clear_callbacks(self):
        """Remove all callbacks."""
        self._callbacks.clear()

    def _find_zone(self, my, mx):
        """Find the topmost zone at (my, mx). Returns (zone_id, data) or (None, None)."""
        for (y1, x1, y2, x2, zone_id, data) in reversed(self._click_zones):
            if y1 <= my <= y2 and x1 <= mx <= x2:
                return zone_id, data
        return None, None

    def _fire(self, zone_id, data, event_type):
        """Fire callbacks for a zone event."""
        cb = self._callbacks.get((zone_id, event_type))
        if cb:
            cb(zone_id, data, event_type)
            return True
        cb = self._callbacks.get(("*", event_type))
        if cb:
            cb(zone_id, data, event_type)
            return True
        return False

    def handle_mouse(self, bstate, mx, my):
        """Process a mouse event and fire registered callbacks."""
        # Accept both CLICKED and PRESSED — Windows curses often only sends PRESSED
        is_click = (bstate & curses.BUTTON1_CLICKED) or (bstate & curses.BUTTON1_PRESSED)
        if not is_click:
            if bstate & curses.BUTTON4_PRESSED:
                return {"zone_id": None, "data": None, "event_type": "scroll_up", "handled": False}
            if bstate & curses.BUTTON5_PRESSED:
                return {"zone_id": None, "data": None, "event_type": "scroll_down", "handled": False}
            return None

        zone_id, data = self._find_zone(my, mx)
        if zone_id is None:
            self._last_click_zone = None
            return None

        now = time.monotonic()
        elapsed = now - self._last_click_time

        if (self._last_click_zone == zone_id and elapsed <= self._dblclick_threshold):
            event_type = EVT_DBLCLICK
            self._last_click_zone = None
            self._last_click_time = 0.0
        else:
            event_type = EVT_CLICK
            self._last_click_zone = zone_id
            self._last_click_time = now

        handled = self._fire(zone_id, data, event_type)
        return {"zone_id": zone_id, "data": data, "event_type": event_type, "handled": handled}

    # --- Safe drawing ---

    def _safe_addstr(self, y, x, text, attr=curses.A_NORMAL):
        max_y, max_x = self.stdscr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        available = max_x - x
        if available <= 0:
            return
        text = text[:available]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    # --- Shared drawing ---
    def draw_box(self, y, x, height, width, title="", color_pair=1, attr=curses.A_NORMAL,
                 fill_pct=None, fill_color=7, zone_id=None, zone_data=None):
        """Draw a rounded box. color_pair values are auto-offset."""
        style = curses.color_pair(_CP_OFFSET + color_pair) | attr

        self._safe_addstr(y, x, self.tl, style)
        self._safe_addstr(y, x + width - 1, self.tr, style)
        self._safe_addstr(y + height - 1, x, self.bl, style)
        self._safe_addstr(y + height - 1, x + width - 1, self.br, style)

        for i in range(1, width - 1):
            self._safe_addstr(y, x + i, self.h_line, style)
            self._safe_addstr(y + height - 1, x + i, self.h_line, style)

        for i in range(1, height - 1):
            self._safe_addstr(y + i, x, self.v_line, style)
            self._safe_addstr(y + i, x + width - 1, self.v_line, style)

        if title:
            self._safe_addstr(y, x + 2, f" {title} ", style)

        if fill_pct is not None and fill_pct > 0:
            fill_pct = max(0, min(100, fill_pct))
            inner_w = width - 2
            inner_h = height - 2
            if inner_w > 0 and inner_h > 0:
                total_cells = inner_w * inner_h
                filled_cells = int(total_cells * fill_pct / 100)
                fill_style = curses.color_pair(_CP_OFFSET + fill_color) | curses.A_REVERSE
                cells_drawn = 0
                for row_off in range(inner_h - 1, -1, -1):
                    if cells_drawn >= filled_cells:
                        break
                    row_y = y + 1 + row_off
                    cols_this_row = min(inner_w, filled_cells - cells_drawn)
                    self._safe_addstr(row_y, x + 1, " " * cols_this_row, fill_style)
                    cells_drawn += cols_this_row

        if zone_id is not None:
            self.add_zone(y, x, height, width, zone_id, zone_data)

    def write_text(self, y, x, text, bold=False, color_pair=None, reverse=False, surface=None):
        """Write text at any absolute position with optional styling."""
        if surface is not None:
            attr = curses.color_pair(_CP_OFFSET + surface) | curses.A_REVERSE
            if bold:
                attr |= curses.A_BOLD
            self._safe_addstr(y, x, text, attr)
            return

        attr = curses.A_NORMAL
        if bold:
            attr |= curses.A_BOLD
        if reverse:
            attr |= curses.A_REVERSE
        if color_pair is not None:
            attr |= curses.color_pair(_CP_OFFSET + color_pair)
        self._safe_addstr(y, x, text, attr)

    def write_inside(self, box_y, box_x, box_h, box_w, row, col, text,
                     bold=False, color_pair=None, reverse=False, surface=None):
        """Write text inside a box at a relative (row, col) position."""
        inner_w = box_w - 2
        inner_h = box_h - 2
        if row < 0 or row >= inner_h or col < 0 or col >= inner_w:
            return
        max_len = inner_w - col
        if max_len <= 0:
            return
        text = text[:max_len]
        if surface is not None:
            text = text.ljust(max_len)
        abs_y = box_y + 1 + row
        abs_x = box_x + 1 + col
        self.write_text(abs_y, abs_x, text, bold=bold, color_pair=color_pair,
                        reverse=reverse, surface=surface)

    def write_on_border(self, box_y, box_x, box_h, box_w, side, offset, text,
                        bold=False, color_pair=None, reverse=False):
        """Write text on a box border."""
        if side == "top":
            abs_y = box_y
            abs_x = box_x + 1 + offset
            max_len = box_w - 2 - offset
        elif side == "bottom":
            abs_y = box_y + box_h - 1
            abs_x = box_x + 1 + offset
            max_len = box_w - 2 - offset
        elif side == "left":
            abs_y = box_y + 1 + offset
            abs_x = box_x
            max_len = 1
        elif side == "right":
            abs_y = box_y + 1 + offset
            abs_x = box_x + box_w - 1
            max_len = 1
        else:
            return

        if max_len <= 0:
            return
        text = text[:max_len]
        self.write_text(abs_y, abs_x, text, bold=bold, color_pair=color_pair, reverse=reverse)

    def draw_badge(self, y, x, text, color_pair=3):
        """Draw a badge (reversed text block) at absolute position."""
        self._safe_addstr(y, x, f" {text} ",
                          curses.color_pair(_CP_OFFSET + color_pair) | curses.A_REVERSE)

    # --- Curved Tabs ---
    def draw_tabs(self, y, x, tabs, active_idx=0):
        self.tab_rects = []
        current_x = x
        tab_height = 3
        gap = 0

        for i, name in enumerate(tabs):
            tab_width = len(name) + 2
            selected = (i == active_idx)
            color = 2 if selected else 5

            self.tab_rects.append((y, current_x, y + tab_height - 1, current_x + tab_width - 1, i))

            self.draw_box(y, current_x, tab_height, tab_width, color_pair=color,
                          zone_id=f"tab:{i}", zone_data=i)
            self.write_inside(y, current_x, tab_height, tab_width, row=0, col=0,
                              text=name, color_pair=color)
            current_x += tab_width + gap

    def get_clicked_tab(self, my, mx):
        for (y1, x1, y2, x2, idx) in self.tab_rects:
            if y1 <= my <= y2 and x1 <= mx <= x2:
                return idx
        return None

    # --- Search box ---
    def draw_search_box(self, y, x, width, text=""):
        self.draw_box(y, x, 3, width)
        self.write_inside(y, x, 3, width, row=0, col=1, text="Search: ")
        max_text_len = width - 12
        displayed = text if len(text) <= max_text_len else text[-max_text_len:]
        self.write_inside(y, x, 3, width, row=0, col=9, text=displayed, color_pair=4)

    # --- Grid with Scroll & Selection ---
    def draw_grid(self, items, start_y=8, start_x=2, box_h=5, box_w=22, gap_x=1, gap_y=1):
        self.item_rects = []
        if not items:
            self.write_text(start_y, start_x, "No items found")
            return

        available_height = self.stdscr.getmaxyx()[0] - start_y - 1
        self.max_visible_rows = max(1, available_height // (box_h + gap_y))
        total_rows = (len(items) + COLS - 1) // COLS

        max_scroll = max(0, total_rows - self.max_visible_rows)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        start_row = self.scroll_offset
        end_row = start_row + self.max_visible_rows

        for idx, item in enumerate(items):
            row = idx // COLS
            if not (start_row <= row < end_row):
                continue
            col = idx % COLS
            y = start_y + (row - start_row) * (box_h + gap_y)
            x = start_x + col * (box_w + gap_x)

            self.item_rects.append((y, x, y + box_h - 1, x + box_w - 1, idx))

            is_selected_item = (idx == self.selected_index)
            if is_selected_item:
                color_pair = 6
                box_attr = curses.A_BOLD
            elif item.get("selected", False):
                color_pair = 2
                box_attr = curses.A_REVERSE
            else:
                color_pair = 1
                box_attr = curses.A_NORMAL

            fill_pct = item.get("fill")
            fill_color = item.get("fill_color", 7)
            self.draw_box(y, x, box_h, box_w, item["title"],
                          color_pair=color_pair, attr=box_attr,
                          fill_pct=fill_pct, fill_color=fill_color,
                          zone_id=f"item:{idx}", zone_data={"index": idx, "item": item})

            sfc = fill_color if fill_pct else None

            if sfc is not None:
                for row_i in range(box_h - 2):
                    self.write_inside(y, x, box_h, box_w, row=row_i, col=0,
                                      text="", surface=sfc)

            self.write_inside(y, x, box_h, box_w, row=0, col=2,
                              text=item["text"],
                              bold=item.get("selected", False) or is_selected_item,
                              surface=sfc)

            if "badge" in item:
                self.write_on_border(y, x, box_h, box_w, "top", box_w - 7,
                                     f" {item['badge']} ", color_pair=3, reverse=True)

        if total_rows > self.max_visible_rows:
            max_scroll = total_rows - self.max_visible_rows
            percent = int((self.scroll_offset / max_scroll) * 100)
            indicator_y = start_y + self.max_visible_rows * (box_h + gap_y)
            self.write_text(indicator_y, start_x, f"\u25bc Scroll {percent}% \u25b2",
                            color_pair=4, reverse=True)

    def get_clicked_item(self, my, mx):
        for (y1, x1, y2, x2, idx) in self.item_rects:
            if y1 <= my <= y2 and x1 <= mx <= x2:
                return idx
        return None

    def refresh(self):
        self.stdscr.refresh()
