import curses
from curses.textpad import rectangle
from typing import List, Dict, Optional
from tui.themes import CLR_HEADER, CLR_SELECTED, CLR_STATUS_BAR, CLR_FORM_BORDER, CLR_FORM_SECTION
from tui.statusline import StatusLine

# ── Layout constants ──────────────────────────────────────────────────
_COL_W = 34        # content width per column
_2COL_MIN_W = 84   # minimum terminal width for 2-column mode
# Box chars
_HL = "─"
_VL = "│"
_TT = "┬"
_BT = "┴"

# Unicode arrow for selected item
_ARROW = "▸"


class MenuManager:
    """Handles center menu with optional 2-column layout (pystock-style).

    Items are dicts with keys: label, action, hotkey, separator, section.
    """
    def __init__(self, stdscr, layout_width: int, layout_height: int):
        self.stdscr = stdscr
        self.w = layout_width
        self.h = layout_height
        self.active_pulldown: Optional[str] = None
        self.top_menus: List[Dict] = []
        self.center_menu: Optional[Dict] = None
        self.title: str = ""
        # 2-column state
        self._active_col = 0
        self._col_mem = [0, 0]
        self._col_scroll = [0, 0]
        self._split = None
        self.status_line = StatusLine(stdscr)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS |
                             getattr(curses, "REPORT_MOUSE_POSITION", 0))
        except curses.error:
            pass

    def define_top_pulldown(self, menu_items: List[Dict]):
        """Define top bar + pull-down menus (kept for non-main-menu layouts)."""
        self.top_menus = menu_items

    def define_center_menu(self, menu_items: List[Dict]):
        """Define center main menu."""
        self.center_menu = menu_items

    # ── Item type helpers ─────────────────────────────────────────────

    def _items(self):
        if not self.center_menu:
            return []
        return self.center_menu.get("items", [])

    def _is_sep(self, item):
        return item.get("separator", False)

    def _is_section(self, item):
        return item.get("section", False)

    def _is_nonsel(self, item):
        return self._is_sep(item) or self._is_section(item)

    def _is_nonsel_idx(self, idx):
        items = self._items()
        if 0 <= idx < len(items):
            return self._is_nonsel(items[idx])
        return True

    # ── 2-column helpers ──────────────────────────────────────────────

    def _use_2col(self):
        return self.stdscr.getmaxyx()[1] >= _2COL_MIN_W

    def _get_split(self):
        """Find split point at section header nearest midpoint."""
        if self._split is not None:
            return self._split
        items = self._items()
        n = len(items)
        mid = n // 2
        best, best_d = mid, n
        for i in range(n):
            if self._is_section(items[i]):
                d = abs(i - mid)
                if d < best_d:
                    best_d, best = d, i
        self._split = best
        return best

    def _col_range(self, col):
        sp = self._get_split()
        if col == 0:
            return (0, sp)
        return (sp, len(self._items()))

    def _first_sel_in_col(self, col, direction=1):
        items = self._items()
        s, e = self._col_range(col)
        indices = range(s, e) if direction >= 0 else range(e - 1, s - 1, -1)
        for i in indices:
            if not self._is_nonsel(items[i]):
                return i
        return s

    def _skip_nonsel(self, idx, direction=1):
        """Skip non-selectable items. Returns valid index."""
        items = self._items()
        n = len(items)
        for _ in range(n):
            if 0 <= idx < n and not self._is_nonsel(items[idx]):
                return idx
            idx = (idx + direction) % n
        return 0

    def _skip_nonsel_in_col(self, idx, col, direction=1):
        """Skip non-selectable items within a column."""
        items = self._items()
        s, e = self._col_range(col)
        for _ in range(e - s + 1):
            if s <= idx < e and not self._is_nonsel(items[idx]):
                return idx
            if direction > 0:
                idx += 1
                if idx >= e:
                    idx = e - 1
                    break
            else:
                idx -= 1
                if idx < s:
                    idx = s
                    break
        # Fallback: find any selectable
        for i in range(s, e):
            if not self._is_nonsel(items[i]):
                return i
        return s

    def _col_of(self, idx):
        return 0 if idx < self._get_split() else 1

    # ── Scroll helpers ────────────────────────────────────────────────

    def _avail_rows(self):
        return max(4, self.stdscr.getmaxyx()[0] - 4)

    def _mouse_menu_index(self, mx, my):
        """Return the center-menu item under a mouse click, if any."""
        items = self._items()
        if not items or my < 2:
            return None
        avail = self._avail_rows()
        h, w = self.stdscr.getmaxyx()
        cw = _COL_W
        if self._use_2col():
            box_w = cw * 2 + 6
            box_x = max(1, (w - box_w) // 2)
            if box_x + 2 <= mx < box_x + 2 + cw:
                col = 0
                x_start = box_x + 2
            elif box_x + cw + 5 <= mx < box_x + cw + 5 + cw:
                col = 1
                x_start = box_x + cw + 5
            else:
                return None
            start, end = self._col_range(col)
            idx = start + self._col_scroll[col] + (my - 2)
        else:
            box_w = cw + 4
            box_x = max(1, (w - box_w) // 2)
            if not (box_x + 1 <= mx < box_x + box_w - 1):
                return None
            start = self._col_scroll[0]
            end = len(items)
            idx = start + (my - 2)
        if 0 <= idx < min(end, start + avail) and not self._is_nonsel(items[idx]):
            return idx
        return None

    def _ensure_visible(self, sel):
        avail = self._avail_rows()
        if self._use_2col():
            col = self._col_of(sel)
            self._active_col = col
            s, _ = self._col_range(col)
            local = sel - s
            sc = self._col_scroll[col]
            if local < sc:
                sc = local
            if local >= sc + avail:
                sc = local - avail + 1
            self._col_scroll[col] = max(0, sc)
        else:
            sc = self._col_scroll[0]
            if sel < sc:
                sc = sel
            if sel >= sc + avail:
                sc = sel - avail + 1
            self._col_scroll[0] = max(0, sc)

    # ── Hotkey check ──────────────────────────────────────────────────

    def _check_hotkey(self, key: int) -> str:
        """Check if a pressed key matches any hotkey across all menus."""
        hotkey_map = {
            curses.KEY_F1: "F1", curses.KEY_F2: "F2", curses.KEY_F3: "F3",
            curses.KEY_F4: "F4", curses.KEY_F5: "F5", curses.KEY_F6: "F6",
            curses.KEY_F7: "F7", curses.KEY_F8: "F8", curses.KEY_F9: "F9",
            curses.KEY_F10: "F10", 27: "ESC", 10: "ENTER",
        }
        key_name = hotkey_map.get(key)
        if not key_name:
            return ""
        # Search center menu
        if self.center_menu:
            for item in self.center_menu.get("items", []):
                if item.get("hotkey") == key_name:
                    return item["action"]
        # Search top menus (kept for layouts that use them)
        for menu in self.top_menus:
            for item in menu.get("items", []):
                if item.get("hotkey") == key_name:
                    return item["action"]
        return ""

    # ── Drawing ───────────────────────────────────────────────────────

    def _safe_addstr(self, y, x, text, attr=curses.A_NORMAL):
        max_y, max_x = self.stdscr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        text = text[:max_x - x]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _draw_box(self, y, x, h, w, title=""):
        """Draw a box with optional title in top border."""
        border_attr = curses.color_pair(CLR_FORM_BORDER)
        try:
            self.stdscr.attron(border_attr)
            rectangle(self.stdscr, y, x, y + h - 1, x + w - 1)
            self.stdscr.attroff(border_attr)
        except curses.error:
            pass
        if title:
            tstr = f" {title} "
            tx = x + max(1, (w - len(tstr)) // 2)
            self._safe_addstr(y, tx, tstr, curses.A_BOLD | border_attr)

    def _draw_col_items(self, col, cx, avail, menu_y, sel):
        """Draw items for one column."""
        items = self._items()
        h = self.stdscr.getmaxyx()[0]
        s, e = self._col_range(col)
        sc = self._col_scroll[col]
        cw = _COL_W
        n_col = e - s
        vis = min(n_col - sc, avail)

        # Scroll indicators
        if sc > 0:
            self._safe_addstr(menu_y - 1, cx + cw - 4, " \u2191 ",
                              curses.A_BOLD)
        if sc + vis < n_col:
            self._safe_addstr(menu_y + vis, cx + cw - 4, " \u2193 ",
                              curses.A_BOLD)

        for row in range(vis):
            i = s + sc + row
            y = menu_y + row
            if y >= h - 1:
                break
            item = items[i]

            if self._is_sep(item):
                self._safe_addstr(y, cx, _HL * cw, curses.A_DIM)
                continue

            if self._is_section(item):
                label = item["label"].upper()
                self._safe_addstr(y, cx,
                                  f" {label:<{cw - 2}} ",
                                  curses.A_BOLD | curses.color_pair(CLR_FORM_SECTION))
                continue

            lbl = item.get("label", "")
            if i == sel:
                attr = curses.color_pair(CLR_SELECTED) | curses.A_BOLD
                self._safe_addstr(y, cx,
                                  f" {_ARROW} {lbl:<{cw - 4}} ", attr)
            else:
                self._safe_addstr(y, cx,
                                  f"   {lbl:<{cw - 4}} ", curses.A_BOLD)

    def _draw_center_menu(self, sel):
        """Draw center menu — single or 2-column based on terminal width."""
        items = self._items()
        if not items:
            return

        h, w = self.stdscr.getmaxyx()
        avail = self._avail_rows()
        menu_y = 2
        cw = _COL_W

        if self._use_2col():
            # ── 2-column layout ───────────────────────────────────────
            box_w = cw * 2 + 6
            box_x = max(1, (w - box_w) // 2)
            lc_x = box_x + 2
            rc_x = box_x + cw + 5
            div_x = box_x + cw + 3

            s0, e0 = self._col_range(0)
            s1, e1 = self._col_range(1)
            vis0 = min(e0 - s0 - self._col_scroll[0], avail)
            vis1 = min(e1 - s1 - self._col_scroll[1], avail)
            vis = max(vis0, vis1, 1)

            self._draw_box(menu_y - 1, box_x, vis + 2, box_w, self.title)

            # Vertical divider
            for row in range(vis):
                self._safe_addstr(menu_y + row, div_x, _VL, curses.A_BOLD)
            # Connect to borders
            self._safe_addstr(menu_y - 1, div_x, _TT, curses.A_BOLD)
            self._safe_addstr(menu_y + vis, div_x, _BT, curses.A_BOLD)

            self._draw_col_items(0, lc_x, avail, menu_y, sel)
            self._draw_col_items(1, rc_x, avail, menu_y, sel)

            self.status_line.set_page("menu_2col")
        else:
            # ── Single-column layout ──────────────────────────────────
            n = len(items)
            sc = self._col_scroll[0]
            vis = min(n - sc, avail)

            box_w = cw + 4
            box_x = max(1, (w - box_w) // 2)

            self._draw_box(menu_y - 1, box_x, vis + 2, box_w, self.title)

            # Scroll indicators
            if sc > 0:
                self._safe_addstr(menu_y - 1, box_x + cw - 2, " \u2191 ",
                                  curses.A_BOLD)
            if sc + vis < n:
                self._safe_addstr(menu_y + vis, box_x + cw - 2, " \u2193 ",
                                  curses.A_BOLD)

            for row in range(vis):
                i = sc + row
                y = menu_y + row
                if y >= h - 1:
                    break
                item = items[i]

                if self._is_sep(item):
                    self._safe_addstr(y, box_x + 1, _HL * (cw + 2),
                                      curses.A_DIM)
                    continue

                if self._is_section(item):
                    label = item["label"].upper()
                    self._safe_addstr(y, box_x + 1,
                                      f" {label:<{cw}} ",
                                      curses.A_BOLD | curses.color_pair(CLR_FORM_SECTION))
                    continue

                lbl = item.get("label", "")
                if i == sel:
                    attr = curses.color_pair(CLR_SELECTED) | curses.A_BOLD
                    self._safe_addstr(y, box_x + 1,
                                      f" {_ARROW} {lbl:<{cw - 3}} ", attr)
                else:
                    self._safe_addstr(y, box_x + 1,
                                      f"   {lbl:<{cw - 3}} ", curses.A_BOLD)

            self.status_line.set_page("menu")

        self.status_line.draw(y=h - 1)

    # ── Top bar + pulldown (kept for non-main-menu layouts) ───────────

    def _draw_top_bar(self):
        """Draw permanent top menu bar — fixed at Y=0"""
        max_x = self.stdscr.getmaxyx()[1]
        bar_w = max(self.w, max_x)
        hdr_attr = curses.color_pair(CLR_HEADER)
        self.stdscr.addstr(0, 0, " " * min(bar_w, max_x - 1), hdr_attr)
        x = 1
        # Draw title on the left of the bar
        if self.title:
            title_str = f" {self.title} "
            try:
                self.stdscr.addstr(0, x, title_str, hdr_attr | curses.A_BOLD)
            except curses.error:
                pass
            x += len(title_str) + 1
        for idx, menu in enumerate(self.top_menus):
            label = f" {menu['label']} "
            attr = curses.A_BOLD | hdr_attr if self.active_pulldown == menu["label"] else hdr_attr
            self.stdscr.addstr(0, x, label, attr)
            menu["x_start"] = x
            x += len(label)

    def _draw_pulldown(self, menu_label: str):
        """Draw dropdown *over* content — clears fully on close"""
        menu = next(m for m in self.top_menus if m["label"] == menu_label)
        items = menu["items"]
        # Calculate width including hotkey labels
        label_w = max((len(i.get("label", "")) for i in items), default=10)
        hotkey_w = max((len(i.get("hotkey", "")) for i in items), default=0)
        max_w = label_w + (hotkey_w + 3 if hotkey_w else 0) + 4
        start_x = menu.get("x_start", 1)
        start_y = 2  # Items start at y=2, frame at y=1 (below top bar)

        # Clamp to screen bounds
        max_y, max_x = self.stdscr.getmaxyx()
        if start_x + max_w + 1 >= max_x:
            start_x = max(1, max_x - max_w - 2)

        # Draw dropdown frame
        rectangle(self.stdscr, start_y-1, start_x-1, start_y + len(items), start_x + max_w)
        for i, item in enumerate(items):
            if item.get("separator"):
                self.stdscr.addstr(start_y + i, start_x, "─" * max_w)
            else:
                attr = curses.color_pair(CLR_SELECTED) | curses.A_BOLD if i == menu.get("sel_idx", 0) else 0
                lbl = item["label"]
                hk = item.get("hotkey", "")
                if hk:
                    text = f" {lbl.ljust(label_w)}  {hk.rjust(hotkey_w)} "
                else:
                    text = f" {lbl.ljust(max_w - 2)} "
                try:
                    self.stdscr.addstr(start_y + i, start_x, text[:max_w], attr)
                except curses.error:
                    pass

    def clear_menu_layer(self):
        self.active_pulldown = None
        self.stdscr.touchwin()
        self.stdscr.refresh()

    # ── Run methods ───────────────────────────────────────────────────

    def run(self, default_mode: str = "hybrid") -> str:
        """Run menu loop — returns selected action ID."""
        if self.center_menu:
            return self._run_center_only()
        if self.top_menus:
            return self._run_hybrid()
        return ""

    def _run_center_only(self) -> str:
        """PyStock-style center menu with optional 2-column layout."""
        items = self._items()
        if not items:
            return ""

        n = len(items)
        sel = 0
        # Skip to first selectable
        sel = self._skip_nonsel(sel, 1)
        # Reset 2-col state
        self._split = None
        self._col_scroll = [0, 0]
        self._active_col = 0
        self._col_mem = [0, 0]

        while True:
            self._ensure_visible(sel)
            self.stdscr.erase()
            self._draw_center_menu(sel)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            two = self._use_2col()
            avail = self._avail_rows()

            if key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    click = (getattr(curses, "BUTTON1_CLICKED", 0) |
                             getattr(curses, "BUTTON1_PRESSED", 0))
                    if bstate & click:
                        clicked = self._mouse_menu_index(mx, my)
                        if clicked is not None:
                            return items[clicked].get("action", "")
                except curses.error:
                    pass
                continue

            # Check hotkeys first
            hotkey_action = self._check_hotkey(key)
            if hotkey_action:
                return hotkey_action

            if two:
                col = self._active_col
                s, e = self._col_range(col)

                if key in (curses.KEY_LEFT, curses.KEY_RIGHT, 9,
                           curses.KEY_BTAB):  # Tab = 9
                    # Switch column
                    new_col = 1 - col
                    self._col_mem[col] = sel
                    prev = self._col_mem[new_col]
                    ns, ne = self._col_range(new_col)
                    if ns <= prev < ne and not self._is_nonsel_idx(prev):
                        sel = prev
                    else:
                        sel = self._first_sel_in_col(new_col)
                    self._active_col = new_col
                elif key == curses.KEY_UP:
                    if sel > s:
                        sel -= 1
                        sel = self._skip_nonsel_in_col(sel, col, -1)
                elif key == curses.KEY_DOWN:
                    if sel < e - 1:
                        sel += 1
                        sel = self._skip_nonsel_in_col(sel, col, 1)
                elif key == curses.KEY_PPAGE:
                    sel = max(s, sel - avail)
                    sel = self._skip_nonsel_in_col(sel, col, -1)
                elif key == curses.KEY_NPAGE:
                    sel = min(e - 1, sel + avail)
                    sel = self._skip_nonsel_in_col(sel, col, 1)
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    if 0 <= sel < n and not self._is_nonsel_idx(sel):
                        return items[sel].get("action", "")
                elif key == 27:  # ESC
                    return ""
                elif 32 <= key <= 126:
                    # First-character match across all items
                    ch = chr(key).upper()
                    for i, item in enumerate(items):
                        if not self._is_nonsel(item):
                            lbl = item.get("label", "").lstrip()
                            if lbl and lbl[0].upper() == ch:
                                sel = i
                                self._active_col = self._col_of(i)
                                return items[i].get("action", "")
                elif key == curses.KEY_RESIZE:
                    self._split = None  # recalculate on resize
                    continue
            else:
                # Single-column mode
                if key == curses.KEY_UP:
                    sel = (sel - 1) % n
                    sel = self._skip_nonsel(sel, -1)
                elif key == curses.KEY_DOWN:
                    sel = (sel + 1) % n
                    sel = self._skip_nonsel(sel, 1)
                elif key == curses.KEY_PPAGE:
                    sel = max(0, sel - avail)
                    sel = self._skip_nonsel(sel, -1)
                elif key == curses.KEY_NPAGE:
                    sel = min(n - 1, sel + avail)
                    sel = self._skip_nonsel(sel, 1)
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    if 0 <= sel < n and not self._is_nonsel_idx(sel):
                        return items[sel].get("action", "")
                elif key == 27:  # ESC
                    return ""
                elif 32 <= key <= 126:
                    ch = chr(key).upper()
                    for i, item in enumerate(items):
                        if not self._is_nonsel(item):
                            lbl = item.get("label", "").lstrip()
                            if lbl and lbl[0].upper() == ch:
                                return items[i].get("action", "")
                elif key == curses.KEY_RESIZE:
                    self._split = None
                    continue

    def _run_hybrid(self) -> str:
        """Hybrid mode: top bar with pulldown menus."""
        if self.center_menu:
            self.center_menu["sel_idx"] = 0
        # Auto-open first pulldown when there's no center menu
        if not self.center_menu and self.top_menus and not self.active_pulldown:
            self.active_pulldown = self.top_menus[0]["label"]
            self.top_menus[0]["sel_idx"] = 0
        while True:
            self.stdscr.clear()
            if self.top_menus:
                self._draw_top_bar()
            if self.active_pulldown:
                self._draw_pulldown(self.active_pulldown)
            elif self.center_menu:
                sel = self.center_menu.get("sel_idx", 0)
                self._draw_center_menu(sel)

            self.stdscr.refresh()
            key = self.stdscr.getch()

            # Tab toggles pulldown menu bar
            if key == 9 and self.top_menus:
                if self.active_pulldown:
                    self.active_pulldown = None
                else:
                    self.active_pulldown = self.top_menus[0]["label"]
                continue

            # Check hotkeys (F1-F10) across all menus
            hotkey_action = self._check_hotkey(key)
            if hotkey_action:
                return hotkey_action

            if self.active_pulldown:
                # Navigate pulldown menu
                if key == curses.KEY_LEFT:
                    idx = next(i for i,m in enumerate(self.top_menus) if m["label"] == self.active_pulldown)
                    new_menu = self.top_menus[(idx-1)%len(self.top_menus)]
                    new_menu["sel_idx"] = 0
                    self.active_pulldown = new_menu["label"]
                elif key == curses.KEY_RIGHT:
                    idx = next(i for i,m in enumerate(self.top_menus) if m["label"] == self.active_pulldown)
                    new_menu = self.top_menus[(idx+1)%len(self.top_menus)]
                    new_menu["sel_idx"] = 0
                    self.active_pulldown = new_menu["label"]
                elif key == curses.KEY_UP:
                    m = next(m for m in self.top_menus if m["label"] == self.active_pulldown)
                    idx = m.get("sel_idx", 0) - 1
                    while idx >= 0 and m["items"][idx].get("separator"):
                        idx -= 1
                    if idx >= 0:
                        m["sel_idx"] = idx
                    else:
                        for j in range(len(m["items"])-1, -1, -1):
                            if not m["items"][j].get("separator"):
                                m["sel_idx"] = j; break
                elif key == curses.KEY_DOWN:
                    m = next(m for m in self.top_menus if m["label"] == self.active_pulldown)
                    idx = m.get("sel_idx", 0) + 1
                    while idx < len(m["items"]) and m["items"][idx].get("separator"):
                        idx += 1
                    if idx < len(m["items"]):
                        m["sel_idx"] = idx
                    else:
                        for j in range(len(m["items"])):
                            if not m["items"][j].get("separator"):
                                m["sel_idx"] = j; break
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    m = next(m for m in self.top_menus if m["label"] == self.active_pulldown)
                    return m["items"][m.get("sel_idx", 0)]["action"]
                elif key == 27:
                    if self.center_menu:
                        self.active_pulldown = None
                    else:
                        # Pulldown-only mode — keep first pulldown open
                        self.active_pulldown = self.top_menus[0]["label"]
                        self.top_menus[0]["sel_idx"] = 0
            else:
                # Navigate center menu
                if self.center_menu:
                    items = self._items()
                    sel_idx = self.center_menu.get("sel_idx", 0)
                    if key == curses.KEY_UP and sel_idx > 0:
                        sel_idx -= 1
                        sel_idx = self._skip_nonsel(sel_idx, -1)
                        self.center_menu["sel_idx"] = sel_idx
                    elif key == curses.KEY_DOWN and sel_idx < len(items)-1:
                        sel_idx += 1
                        sel_idx = self._skip_nonsel(sel_idx, 1)
                        self.center_menu["sel_idx"] = sel_idx
                    elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                        if not self._is_nonsel_idx(sel_idx):
                            return items[sel_idx].get("action", "")
                    elif 32 <= key <= 126:
                        ch = chr(key).upper()
                        for i, item in enumerate(items):
                            if not self._is_nonsel(item):
                                lbl = item.get("label", "").lstrip()
                                if lbl and lbl[0].upper() == ch:
                                    return items[i].get("action", "")
                    elif key == 27:
                        return ""
                elif key == 27:
                    return ""
