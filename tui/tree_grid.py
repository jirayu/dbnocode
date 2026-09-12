"""
TreeGrid — hierarchical inline-editable tree grid for nested document structures.

Data stored as recursive JSON tree: each node dict has a `children` key
containing sub-nodes. Flattened to display slots with tree art (├─ └─ │ ▼ ▶).

Adapted from pystock's TreeGrid for dbnocode's curses-based TUI.
"""

import curses
import uuid
from typing import List, Optional, Callable, Any


# ---------------------------------------------------------------------------
# TreeColDef
# ---------------------------------------------------------------------------

class TreeColDef:
    """Column definition for TreeGrid — extends basic col with tree-specific flags."""
    def __init__(self, name, label, width=10,
                 field_type='text', readonly=False, align='left',
                 lookup=None, formula=None, formula_dec=2,
                 is_tree_col=False, subtotal=False,
                 noshow_on_parent=False, enum_list=None,
                 lookup_fill=None):
        self.name = name
        self.label = label
        self.width = width
        self.field_type = field_type
        self.formula = formula
        self.formula_dec = formula_dec
        self.readonly = readonly or bool(formula)
        self.align = align
        self.lookup = lookup
        self.lookup_fill = lookup_fill
        self.is_tree_col = is_tree_col
        self.subtotal = subtotal
        self.noshow_on_parent = noshow_on_parent
        self.enum_list = enum_list or []
        self.numeric = field_type in ('INT', 'FLOAT', 'num') or align == 'right'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tree_can_unicode() -> bool:
    try:
        import sys
        enc = getattr(sys.stdout, 'encoding', '') or 'utf-8'
        '│'.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _tree_art_prefix(ancestor_has_next: list, is_last: bool, depth: int, unicode_ok: bool) -> str:
    if depth <= 0:
        return ''
    if unicode_ok:
        v, blank, mid, end = '│ ', '  ', '├─', '└─'
    else:
        v, blank, mid, end = '| ', '  ', '+-', '\\-'
    parts = [v if has_next else blank for has_next in ancestor_has_next]
    parts.append(end if is_last else mid)
    return ''.join(parts)


def _safe_addstr(win, y, x, text, attr=0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        avail = w - x
        if y >= h - 1:
            avail -= 1
        if avail <= 0:
            return
        win.addstr(y, x, str(text)[:avail], attr)
    except curses.error:
        pass


def _fit(s, width, align='left'):
    if width <= 0:
        return ''
    text = str(s or '')[:width]
    if len(text) >= width:
        return text
    pad = ' ' * (width - len(text))
    if align == 'right':
        return pad + text
    return text + pad


def _fmt_num(val, decimals=2):
    if val is None or val == '':
        return ''
    try:
        num = float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return str(val)
    if num == 0:
        return ''
    if num == int(num):
        return f'{int(num):,}'
    s = f'{abs(num):.{decimals}f}'.rstrip('0').rstrip('.')
    sign = '-' if num < 0 else ''
    if '.' in s:
        int_part, dec_part = s.split('.', 1)
        return f'{sign}{int(int_part):,}.{dec_part}'
    return f'{sign}{int(s):,}'


# ---------------------------------------------------------------------------
# TreeGrid
# ---------------------------------------------------------------------------

class TreeGrid:
    """
    Scrollable inline-editable tree grid for nested document structures.

    Data is stored as recursive JSON tree: each node dict has a `children`
    key (or configured children_key) containing sub-nodes.

    Public interface:
      load(nested_data)  - accepts raw nested list (list of root nodes)
      get_data() -> list - returns nested list with edits applied
      run() -> (data, exit_key) - event loop
    """

    def __init__(self, stdscr, cols: List[TreeColDef],
                 children_key: str = 'children',
                 tree_label_field: str = '',
                 y0: int = 0, x0: int = 0,
                 visible_rows: int = 8,
                 on_change=None,
                 name: str = '',
                 lookup_handler=None,
                 on_row_updated=None):
        self.stdscr = stdscr
        self.cols = cols
        self.children_key = children_key
        self.tree_label_field = tree_label_field or (cols[0].name if cols else '')
        self.y0 = y0
        self.x0 = x0
        self.visible = visible_rows
        self.on_change = on_change
        self.on_row_updated = on_row_updated
        self.name = name
        self.lookup_handler = lookup_handler

        self._unicode_ok = _tree_can_unicode()

        # Collapsed node IDs
        self._collapsed: set = set()

        # Flat slot list - rebuilt by _reflatten()
        # Each slot: {node, parent_list, index, depth, has_children, is_last,
        #             is_expanded, ancestor_has_next}
        self._slots: list = []
        self._root: list = []

        # Cursor + scroll
        self.cur_row: int = 0
        self.scroll: int = 0

        # For layout.py compatibility
        self.save_requested = False
        self._grid_id = name

    # -- Tree-column helpers -----------------------------------------------

    @property
    def _tree_col(self) -> Optional[TreeColDef]:
        for c in self.cols:
            if c.is_tree_col:
                return c
        return self.cols[0] if self.cols else None

    def _new_node(self) -> dict:
        node = {c.name: '' for c in self.cols if not c.formula}
        node['id'] = uuid.uuid4().hex
        node[self.children_key] = []
        return node

    # -- Node ID management ------------------------------------------------

    def _ensure_ids(self, nodes: list):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            if not n.get('id'):
                n['id'] = uuid.uuid4().hex
            self._ensure_ids(n.get(self.children_key) or [])

    # -- Flatten / reflatten -----------------------------------------------

    def _reflatten(self):
        self._slots = []
        self._walk(self._root, self._root, 0, [])
        if self._slots:
            self.cur_row = max(0, min(self.cur_row, len(self._slots) - 1))
        else:
            self.cur_row = 0

    def _walk(self, nodes: list, parent_list: list, depth: int, ancestor_has_next: list):
        if not isinstance(nodes, list):
            return
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            is_last = (i == len(nodes) - 1)
            children = node.get(self.children_key) or []
            has_children = bool(children)
            node_id = node.get('id', id(node))
            is_expanded = node_id not in self._collapsed

            slot = {
                'node': node,
                'parent_list': parent_list,
                'index': i,
                'depth': depth,
                'has_children': has_children,
                'is_last': is_last,
                'is_expanded': is_expanded,
                'ancestor_has_next': ancestor_has_next,
            }
            self._slots.append(slot)

            if has_children and is_expanded:
                self._walk(children, children, depth + 1,
                           ancestor_has_next + [not is_last])

    def _build_display_row(self, slot: dict) -> dict:
        node = slot['node']
        depth = slot['depth']
        is_last = slot['is_last']
        anc = slot['ancestor_has_next']
        has_children = slot['has_children']
        is_expanded = slot['is_expanded']

        prefix = _tree_art_prefix(anc, is_last, depth, self._unicode_ok)

        if has_children:
            indicator = ('\u25bc ' if self._unicode_ok else 'v ') if is_expanded else ('\u25b6 ' if self._unicode_ok else '> ')
        else:
            indicator = '  '

        tree_col = self._tree_col
        if tree_col:
            label_val = str(node.get(self.tree_label_field) or node.get(tree_col.name) or '').strip()
            tree_label = f'{prefix}{indicator}{label_val}'
        else:
            tree_label = ''

        row = {}
        for c in self.cols:
            if c.is_tree_col:
                row[c.name] = tree_label
            elif c.formula:
                row[c.name] = ''
            elif c.subtotal and has_children:
                total = 0.0
                for ch in (node.get(self.children_key) or []):
                    if isinstance(ch, dict):
                        try:
                            total += float(ch.get(c.name) or 0)
                        except (TypeError, ValueError):
                            pass
                row[c.name] = _fmt_num(total, c.formula_dec)
            elif c.noshow_on_parent and has_children:
                row[c.name] = ''
            else:
                val = node.get(c.name, '')
                if c.numeric and val != '':
                    row[c.name] = _fmt_num(val, c.formula_dec)
                else:
                    row[c.name] = val
        return row

    # -- Load / get_data ---------------------------------------------------

    def load(self, data: list):
        self._root = data if isinstance(data, list) else []
        self._ensure_ids(self._root)
        if not self._root:
            self._root.append(self._new_node())
        self._reflatten()
        self.cur_row = 0
        self.scroll = 0

    def get_data(self) -> list:
        return self._clean(self._root)

    def _clean(self, nodes: list) -> list:
        result = []
        formula_names = {c.name for c in self.cols if c.formula}
        for n in (nodes or []):
            if not isinstance(n, dict):
                continue
            kids = self._clean(n.get(self.children_key) or [])
            label_val = str(n.get(self.tree_label_field, '') or '').strip()
            if not label_val and not kids:
                has_content = any(
                    str(n.get(c.name, '') or '').strip()
                    for c in self.cols
                    if not c.is_tree_col and not c.formula
                )
                if not has_content:
                    continue
            out = {k: v for k, v in n.items() if k not in formula_names}
            out[self.children_key] = kids
            result.append(out)
        return result

    # -- Drawing -----------------------------------------------------------

    def _draw_header(self):
        try:
            h, w = self.stdscr.getmaxyx()
            y = self.y0
            if y >= h:
                return
            x = self.x0 + 1
            self.stdscr.move(y, self.x0)
            self.stdscr.clrtoeol()
            for c in self.cols:
                if x >= w - 1:
                    break
                available = min(c.width, w - x - 1)
                if available <= 0:
                    break
                lbl = _fit(c.label, available)
                _safe_addstr(self.stdscr, y, x, lbl, curses.A_BOLD)
                x += c.width + 1
        except curses.error:
            pass

    def _draw_row(self, slot_idx: int, screen_row: int, active: bool):
        try:
            h, w = self.stdscr.getmaxyx()
            y = self.y0 + 2 + screen_row  # +1 header, +1 separator
            if y >= h - 1:
                return
            slot = self._slots[slot_idx]
            row = self._build_display_row(slot)

            attr = curses.A_REVERSE if active else curses.A_NORMAL
            self.stdscr.move(y, self.x0)
            self.stdscr.clrtoeol()
            x = self.x0 + 1
            for c in self.cols:
                if x >= w - 1:
                    break
                available = min(c.width, w - x - 1)
                if available <= 0:
                    break
                val = str(row.get(c.name) or '')
                align = 'right' if c.numeric and not c.is_tree_col else 'left'
                cell = _fit(val, available, align)
                _safe_addstr(self.stdscr, y, x, cell, attr)
                x += c.width + 1
        except curses.error:
            pass

    def draw(self):
        try:
            h, w = self.stdscr.getmaxyx()
            # Clear grid area
            for y in range(self.y0, min(self.y0 + self.visible + 3, h)):
                try:
                    self.stdscr.move(y, self.x0)
                    self.stdscr.clrtoeol()
                except curses.error:
                    pass
            self._draw_header()
            # Separator under header
            sep_y = self.y0 + 1
            if sep_y < h:
                sep_char = '\u2500' if self._unicode_ok else '-'
                sep_len = w - self.x0 - 1
                _safe_addstr(self.stdscr, sep_y, self.x0, sep_char * sep_len)
            # Data rows
            for si in range(self.visible):
                slot_idx = self.scroll + si
                if slot_idx < len(self._slots):
                    self._draw_row(slot_idx, si, slot_idx == self.cur_row)
        except curses.error:
            pass

    # -- Tree mutations ----------------------------------------------------

    def _toggle_expand(self):
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        if not slot['has_children']:
            return
        node_id = slot['node'].get('id', id(slot['node']))
        if node_id in self._collapsed:
            self._collapsed.discard(node_id)
        else:
            self._collapsed.add(node_id)
        self._reflatten()

    def _add_root(self):
        node = self._new_node()
        self._root.append(node)
        self._reflatten()
        self.cur_row = len(self._slots) - 1
        self._scroll_to_cursor()

    def _add_child(self):
        if not self._slots:
            self._add_root()
            return
        slot = self._slots[self.cur_row]
        parent = slot['node']
        if self.children_key not in parent or not isinstance(parent[self.children_key], list):
            parent[self.children_key] = []
        node = self._new_node()
        parent[self.children_key].append(node)
        parent_id = parent.get('id', id(parent))
        self._collapsed.discard(parent_id)
        self._reflatten()
        for i, s in enumerate(self._slots):
            if s['node'].get('id') == node.get('id'):
                self.cur_row = i
                break
        self._scroll_to_cursor()

    def _add_sibling(self):
        if not self._slots:
            self._add_root()
            return
        slot = self._slots[self.cur_row]
        parent_list = slot['parent_list']
        idx = slot['index']
        node = self._new_node()
        parent_list.insert(idx + 1, node)
        self._reflatten()
        for i, s in enumerate(self._slots):
            if s['node'].get('id') == node.get('id'):
                self.cur_row = i
                break
        self._scroll_to_cursor()

    def _delete_current(self):
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        node = slot['node']

        def _count(n):
            kids = n.get(self.children_key) or []
            return sum(1 + _count(k) for k in kids if isinstance(k, dict))

        child_count = _count(node)
        if child_count > 0:
            if not self._confirm(f'Delete node and {child_count} child(ren)?'):
                return
        parent_list = slot['parent_list']
        idx = slot['index']
        parent_list.pop(idx)
        self._reflatten()
        if self._slots:
            self.cur_row = max(0, min(self.cur_row, len(self._slots) - 1))
        self._notify_change()

    def _indent(self):
        """Make current node a child of previous sibling (Tab)."""
        if not self._slots or self.cur_row == 0:
            return
        slot = self._slots[self.cur_row]
        parent_list = slot['parent_list']
        idx = slot['index']
        if idx == 0:
            return
        node = parent_list.pop(idx)
        prev_sib = parent_list[idx - 1]
        if self.children_key not in prev_sib or not isinstance(prev_sib[self.children_key], list):
            prev_sib[self.children_key] = []
        prev_sib[self.children_key].append(node)
        sib_id = prev_sib.get('id', id(prev_sib))
        self._collapsed.discard(sib_id)
        self._reflatten()
        self._find_and_set_cursor(node)
        self._scroll_to_cursor()

    def _outdent(self):
        """Promote current node to sibling of its parent (Shift+Tab)."""
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        if slot['depth'] == 0:
            return
        node = slot['node']
        parent_list = slot['parent_list']
        parent_node = None
        parent_slot = None
        for s in self._slots:
            kids = s['node'].get(self.children_key) or []
            if kids is parent_list:
                parent_node = s['node']
                parent_slot = s
                break
        if parent_node is None:
            return
        idx = slot['index']
        parent_list.pop(idx)
        grandparent_list = parent_slot['parent_list']
        gp_idx = parent_slot['index']
        grandparent_list.insert(gp_idx + 1, node)
        self._reflatten()
        self._find_and_set_cursor(node)
        self._scroll_to_cursor()

    def _move_up(self):
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        idx = slot['index']
        if idx == 0:
            return
        parent_list = slot['parent_list']
        parent_list[idx], parent_list[idx - 1] = parent_list[idx - 1], parent_list[idx]
        self._reflatten()
        self._find_and_set_cursor(slot['node'])
        self._scroll_to_cursor()

    def _move_down(self):
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        parent_list = slot['parent_list']
        idx = slot['index']
        if idx >= len(parent_list) - 1:
            return
        parent_list[idx], parent_list[idx + 1] = parent_list[idx + 1], parent_list[idx]
        self._reflatten()
        self._find_and_set_cursor(slot['node'])
        self._scroll_to_cursor()

    # -- Cell editing (popup dialog) ---------------------------------------

    def _edit_current_node(self):
        if not self._slots:
            return
        slot = self._slots[self.cur_row]
        node = slot['node']
        self._edit_node_dialog(node)
        self._reflatten()
        self._notify_change()

    def _edit_node_dialog(self, node: dict):
        """Popup dialog to edit tree node fields using curses."""
        # Build editable field list (skip tree-art col and formula cols)
        fields = []
        for c in self.cols:
            if c.is_tree_col and c.name != self.tree_label_field:
                continue
            if c.formula:
                continue
            if c.readonly:
                continue
            fields.append(c)

        if not fields:
            return

        term_h, term_w = self.stdscr.getmaxyx()
        n_cols = 2 if len(fields) > 4 else 1
        rows_needed = ((len(fields) + n_cols - 1) // n_cols) * 2 + 6
        dlg_w = min(max(60, term_w - 10), term_w - 2)
        dlg_h = min(rows_needed, term_h - 2)
        dy = max(0, (term_h - dlg_h) // 2)
        dx = max(0, (term_w - dlg_w) // 2)

        try:
            dlg = curses.newwin(dlg_h, dlg_w, dy, dx)
        except curses.error:
            return
        dlg.keypad(True)

        col_w = (dlg_w - 4) // n_cols

        # Build input buffers from node data
        buffers = []
        for f in fields:
            val = str(node.get(f.name, '') or '')
            buffers.append(val)

        cur_field = 0

        while True:
            # Draw dialog
            dlg.erase()
            try:
                dlg.box()
                dlg.addstr(0, 2, ' Edit Node ', curses.A_BOLD)
                hint = ' F10:Save  ESC:Cancel '
                dlg.addstr(dlg_h - 1, max(2, dlg_w - len(hint) - 2), hint)
            except curses.error:
                pass

            for fi, f in enumerate(fields):
                col_idx = fi % n_cols
                row_idx = fi // n_cols
                fy = 2 + row_idx * 2
                fx = 2 + col_idx * col_w

                label = f.label[:12].ljust(12)
                attr = curses.A_BOLD if fi == cur_field else curses.A_NORMAL
                _safe_addstr(dlg, fy, fx, label, attr)

                has_lookup = bool(f.lookup and self.lookup_handler)
                has_opts = bool(f.enum_list)
                suffix_w = 5 if has_lookup else (3 if has_opts else 0)
                input_w = min(f.width, col_w - 14 - suffix_w)
                if input_w < 4:
                    input_w = 4
                val_display = buffers[fi][:input_w].ljust(input_w)
                input_attr = curses.A_REVERSE if fi == cur_field else curses.A_UNDERLINE
                _safe_addstr(dlg, fy, fx + 13, val_display, input_attr)
                if has_lookup:
                    _safe_addstr(dlg, fy, fx + 13 + input_w, ' [F4]', curses.A_DIM)
                elif has_opts:
                    _safe_addstr(dlg, fy, fx + 13 + input_w, ' [/]', curses.A_DIM)

            dlg.refresh()
            key = dlg.getch()

            if key == 27:  # ESC - cancel
                break
            elif key == curses.KEY_F10:  # Save
                for fi, f in enumerate(fields):
                    node[f.name] = buffers[fi]
                break
            elif key in (curses.KEY_DOWN, 10, 13, curses.KEY_ENTER):
                cur_field = (cur_field + 1) % len(fields)
            elif key == curses.KEY_UP:
                cur_field = (cur_field - 1) % len(fields)
            elif key == 9:  # Tab
                cur_field = (cur_field + 1) % len(fields)
            elif key == curses.KEY_BTAB or key == 353:  # Shift-Tab
                cur_field = (cur_field - 1) % len(fields)
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
                if buffers[cur_field]:
                    buffers[cur_field] = buffers[cur_field][:-1]
            elif key == curses.KEY_DC:
                buffers[cur_field] = ''
            elif key == curses.KEY_F4 and fields[cur_field].lookup and self.lookup_handler:
                # F4 lookup — open SearchDialog picker
                lookup_name = fields[cur_field].lookup
                all_items = self.lookup_handler(lookup_name, None, list_all=True, full_record=True)
                if all_items:
                    from .widgets import SearchDialog
                    # Find key/display fields from first record
                    skip = {"rowid", "_parent_rowid"}
                    flds = [k for k in all_items[0] if k not in skip]
                    # Guess value_field and display_field
                    value_field = flds[0] if flds else "code"
                    display_field = flds[1] if len(flds) > 1 else value_field
                    for f2 in flds:
                        if f2.endswith(("_no", "_code", "_id")):
                            value_field = f2
                            break
                    for f2 in flds:
                        if f2.endswith("_name") or f2 == "name":
                            display_field = f2
                            break
                    # Close edit dialog temporarily, show picker on main screen
                    try:
                        dlg.erase()
                        dlg.refresh()
                        self.stdscr.touchwin()
                        self.stdscr.refresh()
                    except curses.error:
                        pass
                    result = SearchDialog.show(
                        self.stdscr, 2, 2, 40,
                        all_items, display_field, value_field,
                        title=f"Pick {lookup_name}"
                    )
                    if result:
                        buffers[cur_field] = str(result.get(value_field, '') or '')
                        # Auto-fill related fields
                        fill = fields[cur_field].lookup_fill
                        if fill:
                            if isinstance(fill, dict):
                                for src, dest in fill.items():
                                    val = str(result.get(src, '') or '')
                                    # Update buffer if dest is editable field
                                    filled = False
                                    for fi2, f2 in enumerate(fields):
                                        if f2.name == dest:
                                            buffers[fi2] = val
                                            filled = True
                                            break
                                    # Also set node directly (for readonly fields not in fields list)
                                    node[dest] = val
                            elif isinstance(fill, str):
                                val = str(result.get(fill, '') or '')
                                for fi2, f2 in enumerate(fields):
                                    if f2.name == fill:
                                        buffers[fi2] = val
                                node[fill] = val
            elif key == ord('/') and fields[cur_field].enum_list:
                # Cycle through enum options
                opts = fields[cur_field].enum_list
                cur_val = buffers[cur_field]
                try:
                    idx = opts.index(cur_val)
                    buffers[cur_field] = opts[(idx + 1) % len(opts)]
                except ValueError:
                    buffers[cur_field] = opts[0]
            elif 32 <= key <= 126:
                buffers[cur_field] += chr(key)

        # Cleanup
        try:
            dlg.erase()
            dlg.refresh()
            self.stdscr.touchwin()
            self.stdscr.refresh()
        except curses.error:
            pass

    # -- Scroll helpers ----------------------------------------------------

    def _scroll_to_cursor(self):
        if self.cur_row < self.scroll:
            self.scroll = self.cur_row
        elif self.cur_row >= self.scroll + self.visible:
            self.scroll = self.cur_row - self.visible + 1
        self.scroll = max(0, self.scroll)

    def _find_and_set_cursor(self, node):
        node_id = node.get('id', id(node))
        for i, s in enumerate(self._slots):
            if s['node'].get('id') == node_id:
                self.cur_row = i
                return

    def _notify_change(self):
        if self.on_row_updated:
            try:
                self.on_row_updated(self.get_data())
            except Exception:
                pass

    def _confirm(self, msg: str) -> bool:
        """Simple Y/N confirmation popup."""
        term_h, term_w = self.stdscr.getmaxyx()
        w = min(len(msg) + 10, term_w - 4)
        h = 5
        y = max(0, (term_h - h) // 2)
        x = max(0, (term_w - w) // 2)
        try:
            win = curses.newwin(h, w, y, x)
        except curses.error:
            return False
        win.keypad(True)
        try:
            win.box()
            _safe_addstr(win, 1, 2, msg[:w - 4])
            _safe_addstr(win, 3, 2, 'Y=Yes  N/ESC=No', curses.A_BOLD)
            win.refresh()
            key = win.getch()
            return key in (ord('y'), ord('Y'))
        finally:
            try:
                win.erase()
                win.refresh()
                self.stdscr.touchwin()
                self.stdscr.refresh()
            except curses.error:
                pass

    # -- Main event loop ---------------------------------------------------

    def run(self, data: list = None) -> tuple:
        """
        Run tree grid event loop.
        Returns (tree_data, exit_key).

        Tree operations: F3 (add sibling), F4 (add child), Enter (edit),
        Del (delete), Space/Right (expand/collapse), Left (collapse),
        F7 (move up), + (move down), Tab (indent).
        """
        if data is not None:
            self.load(data)

        _PASS_KEYS = {
            curses.KEY_F10,   # save
            27,               # ESC
            curses.KEY_F6,    # focus switch
        }

        while True:
            self.draw()
            try:
                self.stdscr.refresh()
            except curses.error:
                pass
            key = self.stdscr.getch()

            if key in _PASS_KEYS:
                return self.get_data(), key

            # Navigation
            if key == curses.KEY_UP:
                if self.cur_row > 0:
                    self.cur_row -= 1
                    self._scroll_to_cursor()
            elif key == curses.KEY_DOWN:
                if self.cur_row < len(self._slots) - 1:
                    self.cur_row += 1
                    self._scroll_to_cursor()
            elif key in (curses.KEY_PPAGE, curses.KEY_HOME):
                self.cur_row = 0
                self.scroll = 0
            elif key in (curses.KEY_NPAGE, curses.KEY_END):
                self.cur_row = max(0, len(self._slots) - 1)
                self._scroll_to_cursor()

            # Expand/collapse
            elif key == curses.KEY_RIGHT:
                if self._slots and self._slots[self.cur_row]['has_children']:
                    self._toggle_expand()
            elif key == ord(' '):
                if self._slots and self._slots[self.cur_row]['has_children']:
                    self._toggle_expand()
            elif key == curses.KEY_LEFT:
                if self._slots:
                    slot = self._slots[self.cur_row]
                    node_id = slot['node'].get('id', id(slot['node']))
                    if slot['has_children'] and node_id not in self._collapsed:
                        self._collapsed.add(node_id)
                        self._reflatten()
                    elif slot['depth'] > 0:
                        for i, s in enumerate(self._slots):
                            if (s['depth'] == slot['depth'] - 1
                                    and s['node'].get(self.children_key) is slot['parent_list']):
                                self.cur_row = i
                                break

            # Edit
            elif key in (10, 13, curses.KEY_ENTER):
                self._edit_current_node()

            # Tree structure
            elif key == curses.KEY_F3:
                if self._slots:
                    self._add_sibling()
                else:
                    self._add_root()
                self._edit_current_node()
            elif key == curses.KEY_F4:
                self._add_child()
                self._edit_current_node()
            elif key == curses.KEY_F7:
                self._move_up()
            elif key == ord('+'):
                self._move_down()
            elif key == curses.KEY_DC:
                self._delete_current()

            elif key == curses.KEY_IC:
                pass  # Ins ignored

            # Tab = indent (make child of previous sibling)
            elif key == ord('\t'):
                slot = self._slots[self.cur_row] if self._slots else None
                if slot and slot['index'] > 0:
                    self._indent()
                else:
                    return self.get_data(), key  # pass through

            else:
                return self.get_data(), key

    def invalidate(self):
        pass
