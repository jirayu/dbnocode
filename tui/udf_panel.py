"""User-Defined Fields (UDF) panel.

Renders a boxed panel on the right side of a form, showing user-configurable
fields. Schema stored in udf_schemas.json, values stored as nested 'udf' dict
inside each record.

F6 toggles focus between main form and UDF panel.
F7 opens schema editor to add/edit/remove UDF fields.
"""
import curses
import json
import os
from typing import List, Dict, Optional, Any

from .themes import CLR_FORM_BORDER, CLR_FORM_LABEL, CLR_FORM_INPUT
from .widgets import TextInput, NumericInput, DateInput, Combobox, Checkbox

UDF_SCHEMAS_FILE = "udf_schemas.json"

# Supported UDF field types
UDF_TYPES = ["TEXT", "NUMBER", "DATE", "CHECKBOX", "SELECT"]


def _load_schemas() -> dict:
    try:
        with open(UDF_SCHEMAS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_schemas(data: dict):
    try:
        with open(UDF_SCHEMAS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def get_schema(schema_name: str) -> List[dict]:
    """Get UDF schema fields. Each: {name, label, type, options?}."""
    schemas = _load_schemas()
    return schemas.get(schema_name, [])


def save_schema(schema_name: str, fields: List[dict]):
    """Save UDF schema fields."""
    schemas = _load_schemas()
    schemas[schema_name] = fields
    _save_schemas(schemas)


class UDFPanel:
    """Right-side UDF panel with widgets derived from schema."""

    def __init__(self, stdscr, schema_name: str, panel_x: int, panel_y: int,
                 panel_w: int, panel_h: int):
        self.stdscr = stdscr
        self.schema_name = schema_name
        self.panel_x = panel_x
        self.panel_y = panel_y
        self.panel_w = panel_w
        self.panel_h = panel_h
        self.focused = False
        self.focus_idx = 0
        self.widgets = []
        self._field_ids = []  # parallel to widgets
        self._rebuild_widgets()

    def _rebuild_widgets(self):
        """Create widgets from current schema."""
        schema = get_schema(self.schema_name)
        self.widgets = []
        self._field_ids = []
        label_w = 0
        for sf in schema:
            label_w = max(label_w, len(sf.get("label", sf["name"])))
        label_w = min(label_w + 2, self.panel_w // 2)
        field_w = self.panel_w - label_w - 4

        for i, sf in enumerate(schema):
            y = self.panel_y + 2 + i
            if y >= self.panel_y + self.panel_h - 1:
                break
            wx = self.panel_x + label_w + 2
            ftype = sf.get("type", "TEXT").upper()

            if ftype == "NUMBER":
                w = NumericInput(y, wx, min(field_w, 15))
            elif ftype == "DATE":
                w = DateInput(y, wx, 12)
            elif ftype == "CHECKBOX":
                w = Checkbox(y, wx, sf.get("label", sf["name"]))
            elif ftype == "SELECT":
                opts = sf.get("options", [])
                if isinstance(opts, str):
                    opts = [o.strip() for o in opts.split(",") if o.strip()]
                w = Combobox(y, wx, min(field_w, 25), opts or [""],
                             field_name=f"udf_{sf['name']}")
            else:
                w = TextInput(y, wx, min(field_w, 30))

            self.widgets.append(w)
            self._field_ids.append(sf["name"])
        if self.widgets:
            self.focus_idx = min(self.focus_idx, len(self.widgets) - 1)

    def draw(self):
        """Draw the UDF panel box and widgets."""
        border_attr = curses.color_pair(CLR_FORM_BORDER)
        label_attr = curses.color_pair(CLR_FORM_LABEL)
        x = self.panel_x
        y = self.panel_y
        w = self.panel_w
        h = self.panel_h

        # Draw box
        try:
            for row in range(y, min(y + h, curses.LINES)):
                self.stdscr.addstr(row, x, " " * min(w, curses.COLS - x - 1))
            self.stdscr.attron(border_attr)
            # Top
            self.stdscr.addstr(y, x, "┌" + "─" * (w - 2) + "┐")
            # Bottom
            if y + h - 1 < curses.LINES:
                self.stdscr.addstr(y + h - 1, x, "└" + "─" * (w - 2) + "┘")
            # Sides
            for row in range(y + 1, min(y + h - 1, curses.LINES)):
                self.stdscr.addstr(row, x, "│")
                self.stdscr.addstr(row, x + w - 1, "│")
            self.stdscr.attroff(border_attr)
            # Title
            title = " User Define "
            if self.focused:
                title_attr = border_attr | curses.A_BOLD
            else:
                title_attr = border_attr
            self.stdscr.addstr(y, x + 2, title, title_attr)
            # F7 hint
            hint = " F7=Edit "
            self.stdscr.addstr(y + h - 1, x + w - len(hint) - 1, hint,
                               curses.A_DIM)
        except curses.error:
            pass

        # Draw fields
        schema = get_schema(self.schema_name)
        label_w = 0
        for sf in schema:
            label_w = max(label_w, len(sf.get("label", sf["name"])))
        label_w = min(label_w + 2, self.panel_w // 2)

        for i, (sf, widget) in enumerate(zip(schema, self.widgets)):
            wy = self.panel_y + 2 + i
            if wy >= self.panel_y + self.panel_h - 1:
                break
            label = sf.get("label", sf["name"])
            try:
                self.stdscr.addstr(wy, x + 2, f"{label}:", label_attr)
            except curses.error:
                pass
            widget.focused = self.focused and (i == self.focus_idx)
            widget.draw(self.stdscr)

    def handle_key(self, key) -> bool:
        """Handle key when UDF panel is focused. Returns True if consumed."""
        if not self.widgets:
            return False

        # Navigate between fields
        if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER, curses.KEY_DOWN, 9):
            self.focus_idx = (self.focus_idx + 1) % len(self.widgets)
            return True
        if key in (curses.KEY_UP, curses.KEY_BTAB, 353):
            self.focus_idx = (self.focus_idx - 1) % len(self.widgets)
            return True

        # Pass to focused widget
        w = self.widgets[self.focus_idx]
        return w.handle_key(key)

    def get_values(self) -> dict:
        """Collect UDF values as {field_name: value}."""
        result = {}
        for fid, w in zip(self._field_ids, self.widgets):
            result[fid] = w.value
        return result

    def load_values(self, udf_data: dict):
        """Load UDF values from record's 'udf' dict."""
        if not udf_data:
            return
        for fid, w in zip(self._field_ids, self.widgets):
            if fid in udf_data:
                w.load(udf_data[fid])

    def cursor_pos(self):
        """Return (y, x) for terminal cursor when UDF panel has focus."""
        if not self.widgets:
            return (self.panel_y + 2, self.panel_x + 3)
        w = self.widgets[self.focus_idx]
        cx = w.cursor_x()
        cy = w.cursor_y() if hasattr(w, 'cursor_y') else w.y
        return (cy, cx)

    def schema_editor(self):
        """Open F7 schema editor popup."""
        schema = list(get_schema(self.schema_name))
        max_y, max_x = self.stdscr.getmaxyx()
        box_h = min(max(len(schema) + 6, 10), max_y - 4)
        box_w = min(50, max_x - 4)
        by = max(0, (max_y - box_h) // 2)
        bx = max(0, (max_x - box_w) // 2)

        sel = 0
        while True:
            # Draw editor
            try:
                win = curses.newwin(box_h, box_w, by, bx)
            except curses.error:
                return
            win.keypad(True)
            win.erase()
            win.border()
            win.addstr(0, 2, " UDF Schema Editor ", curses.A_BOLD)
            win.addstr(box_h - 1, 2, " F3=Add Enter=Edit Del=Remove ESC=Done ",
                       curses.A_DIM)

            for i, sf in enumerate(schema):
                if i + 1 >= box_h - 2:
                    break
                label = sf.get("label", sf["name"])
                ftype = sf.get("type", "TEXT")
                line = f"  {label:<20s} {ftype}"
                attr = curses.A_REVERSE | curses.A_BOLD if i == sel else curses.A_NORMAL
                try:
                    win.addstr(i + 2, 1, line[:box_w - 3].ljust(box_w - 3), attr)
                except curses.error:
                    pass

            if not schema:
                try:
                    win.addstr(3, 3, "No fields defined. Press F3 to add.",
                               curses.A_DIM)
                except curses.error:
                    pass

            win.refresh()
            key = win.getch()

            if key == 27:
                break
            elif key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(schema) - 1:
                sel += 1
            elif key == curses.KEY_F3:
                new_field = self._field_dialog(None)
                if new_field:
                    schema.append(new_field)
                    save_schema(self.schema_name, schema)
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                if schema:
                    edited = self._field_dialog(schema[sel])
                    if edited:
                        schema[sel] = edited
                        save_schema(self.schema_name, schema)
            elif key in (curses.KEY_DC, 4):  # Del or Ctrl+D
                if schema:
                    del schema[sel]
                    sel = min(sel, max(0, len(schema) - 1))
                    save_schema(self.schema_name, schema)

        # Cleanup
        try:
            self.stdscr.touchwin()
            self.stdscr.refresh()
        except curses.error:
            pass
        # Rebuild widgets with updated schema
        self._rebuild_widgets()

    def _field_dialog(self, existing: Optional[dict]) -> Optional[dict]:
        """Add/edit a single UDF field. Returns field dict or None."""
        max_y, max_x = self.stdscr.getmaxyx()
        dh, dw = 12, min(46, max_x - 6)
        dy = max(0, (max_y - dh) // 2)
        dx = max(0, (max_x - dw) // 2)

        try:
            win = curses.newwin(dh, dw, dy, dx)
        except curses.error:
            return None
        win.keypad(True)

        name = existing["name"] if existing else ""
        label = existing.get("label", "") if existing else ""
        ftype_idx = 0
        options = ""
        if existing:
            et = existing.get("type", "TEXT").upper()
            if et in UDF_TYPES:
                ftype_idx = UDF_TYPES.index(et)
            options = ",".join(existing.get("options", []))

        fields = [name, label, str(ftype_idx), options]
        field_labels = ["Name:", "Label:", "Type:", "Options:"]
        focus = 0

        while True:
            win.erase()
            win.border()
            title = " Edit Field " if existing else " Add Field "
            win.addstr(0, 2, title, curses.A_BOLD)
            win.addstr(dh - 1, 2, " F10=Save  ESC=Cancel ", curses.A_DIM)

            for i, (fl, fv) in enumerate(zip(field_labels, fields)):
                fy = 2 + i * 2
                attr_l = curses.A_BOLD if i == focus else curses.A_NORMAL
                try:
                    win.addstr(fy, 3, fl, attr_l)
                except curses.error:
                    pass
                if i == 2:  # Type selector
                    disp = UDF_TYPES[ftype_idx]
                    attr_v = curses.A_REVERSE if i == focus else curses.A_NORMAL
                    try:
                        win.addstr(fy, 14, f" {disp} ", attr_v)
                    except curses.error:
                        pass
                else:
                    val = fv[:dw - 18]
                    attr_v = curses.A_REVERSE if i == focus else curses.A_UNDERLINE
                    try:
                        win.addstr(fy, 14, val.ljust(dw - 18), attr_v)
                    except curses.error:
                        pass

            win.refresh()
            key = win.getch()

            if key == 27:
                return None
            elif key in (curses.KEY_F10, 19):
                if not fields[0].strip():
                    continue  # name required
                result = {
                    "name": fields[0].strip(),
                    "label": fields[1].strip() or fields[0].strip(),
                    "type": UDF_TYPES[ftype_idx],
                }
                if UDF_TYPES[ftype_idx] == "SELECT" and fields[3].strip():
                    result["options"] = [o.strip() for o in fields[3].split(",")
                                         if o.strip()]
                return result
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER,
                         curses.KEY_DOWN, 9):
                focus = (focus + 1) % 4
            elif key in (curses.KEY_UP, curses.KEY_BTAB, 353):
                focus = (focus - 1) % 4
            elif focus == 2:  # Type field — arrow keys cycle
                if key in (curses.KEY_RIGHT, ord(' ')):
                    ftype_idx = (ftype_idx + 1) % len(UDF_TYPES)
                elif key == curses.KEY_LEFT:
                    ftype_idx = (ftype_idx - 1) % len(UDF_TYPES)
            elif key == curses.KEY_BACKSPACE or key in (127, 8):
                if fields[focus]:
                    fields[focus] = fields[focus][:-1]
            elif 32 <= key <= 126:
                fields[focus] += chr(key)
