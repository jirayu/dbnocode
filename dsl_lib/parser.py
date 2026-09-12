import re
from typing import Dict, List, Any
from collections import defaultdict

QUOTED = r'"([^"]+)"'
INT = r'(\d+)'

class DSLParser:
    def __init__(self, text: str):
        self.raw = text
        self.lines = [re.sub(r'//.*', '', l).strip() for l in text.splitlines() if l.strip()]
        self.pos = 0
        self.app = {}

    def _next(self):
        if self.pos < len(self.lines):
            l = self.lines[self.pos]
            self.pos += 1
            return l
        return None

    def _peek(self):
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def parse(self) -> Dict[str, Any]:
        self.app = {
            "meta": {}, "datasource": None,
            "forms": {}, "grids": {}, "layouts": {}, "actions": {},
            "top_menu": None, "center_menu": None
        }
        while self.pos < len(self.lines):
            line = self._next()
            if line.upper().startswith("APP"):
                m = re.match(rf'^APP\s+{QUOTED}\s+VERSION\s+{QUOTED}', line, re.I)
                if m:
                    self.app["meta"]["name"] = m.group(1)
                    self.app["meta"]["version"] = m.group(2)
            elif line.upper().startswith("DATASOURCE"):
                m = re.match(rf'^DATASOURCE\s+{QUOTED}\s+ADAPTER\s+{QUOTED}', line, re.I)
                if m:
                    self.app["datasource"] = {"name": m.group(1), "adapter": m.group(2).lower()}
            elif line.upper().startswith("FORM"):
                m = re.match(rf'^FORM\s+{QUOTED}', line, re.I)
                if m:
                    fid = m.group(1)
                    self.app["forms"][fid] = self._parse_block("END FORM", self._parse_form_fields)
            elif line.upper().startswith("GRID"):
                m = re.match(rf'^GRID\s+{QUOTED}\s+BELONGS_TO\s+{QUOTED}', line, re.I)
                if m:
                    gid, parent = m.group(1), m.group(2)
                    grid_def = {
                        "parent": parent,
                        "columns": self._parse_block("END GRID", self._parse_grid_cols)
                    }
                    # Optional TOTALS flag
                    tm = re.search(r'TOTALS\s+(\d)', line, re.I)
                    if tm:
                        grid_def["show_totals"] = int(tm.group(1)) == 1
                    self.app["grids"][gid] = grid_def
            elif line.upper().startswith("LAYOUT"):
                m = re.match(rf'^LAYOUT\s+{QUOTED}\s+TITLE\s+{QUOTED}\s+POSITION\s+{INT}\s+{INT}\s+WIDTH\s+{INT}\s+HEIGHT\s+{INT}\s+BORDER\s+(\d)', line, re.I)
                if not m:
                    m = re.match(rf'^LAYOUT\s+{QUOTED}\s+POSITION\s+{INT}\s+{INT}\s+WIDTH\s+{INT}\s+HEIGHT\s+{INT}\s+BORDER\s+(\d)', line, re.I)
                if m:
                    lid = m.group(1)
                    title = m.group(2) if len(m.groups()) == 7 else lid
                    y, x, w, h, border = m.groups()[-5:]
                    self.app["layouts"][lid] = {
                        "title": title, "position": {"y": int(y), "x": int(x), "width": int(w), "height": int(h)},
                        "border": int(border), "fields": {}, "buttons": {}
                    }
                    self._parse_layout_content(lid)
            elif line.upper().startswith("ACTION"):
                m = re.match(rf'^ACTION\s+{QUOTED}\s+GOTO\s+{QUOTED}', line, re.I)
                if m:
                    self.app["actions"][m.group(1)] = {"type": "goto", "target": m.group(2)}
                m = re.match(rf'^ACTION\s+{QUOTED}\s+EXIT', line, re.I)
                if m:
                    self.app["actions"][m.group(1)] = {"type": "exit"}
        return self.app

    def _parse_block(self, end_marker: str, parser_fn):
        items = []
        while self._peek() and not self._peek().upper() == end_marker:
            line = self._next()
            parsed = parser_fn(line)
            if parsed: items.append(parsed)
        self._next()
        return items

    def _parse_form_fields(self, line: str):
        # SECTION "label" — visual separator, not a field
        sm = re.match(rf'^SECTION\s+{QUOTED}', line, re.I)
        if sm:
            return {"type": "SECTION", "label": sm.group(1)}
        m = re.match(rf'^FIELD\s+{QUOTED}\s+TYPE\s+(\w+)', line, re.I)
        if not m: return None
        field = {"id": m.group(1), "type": m.group(2).upper()}
        if "REQUIRED" in line.upper(): field["required"] = True
        if "LEN" in line:
            lm = re.search(r'LEN\s+(\d+)', line, re.I)
            if lm: field["length"] = int(lm.group(1))
        if "DEFAULT" in line:
            dm = re.search(r'DEFAULT\s+(.+?)\s*$', line, re.I)
            if dm: field["default"] = dm.group(1).strip()
        if "LOOKUP" in line:
            lm = re.search(rf'LOOKUP\s+{QUOTED}', line, re.I)
            if lm: field["lookup"] = lm.group(1)
        if "ENUM" in line:
            em = re.search(r'ENUM\s*\[(.*?)\]', line, re.I)
            if em: field["enum_list"] = [v.strip().strip('"') for v in em.group(1).split(',')]
        if "PREFIX" in line.upper():
            pm = re.search(rf'PREFIX\s+{QUOTED}', line, re.I)
            if pm: field["prefix"] = pm.group(1)
        if "SPAN" in line.upper():
            sm = re.search(r'SPAN\s+(\d+)', line, re.I)
            if sm: field["span"] = int(sm.group(1))
        return field

    def _parse_grid_cols(self, line: str):
        m = re.match(rf'^COLUMN\s+{QUOTED}\s+TYPE\s+(\w+)', line, re.I)
        if not m: return None
        col = {"id": m.group(1), "type": m.group(2).upper()}
        if "WIDTH" in line:
            wm = re.search(r'WIDTH\s+(\d+)', line, re.I)
            if wm: col["width"] = int(wm.group(1))
        if "LOOKUP" in line:
            lm = re.search(rf'LOOKUP\s+{QUOTED}', line, re.I)
            if lm: col["lookup"] = lm.group(1)
        if "LOOKUPFILL" in line.upper():
            fm = re.search(rf'LOOKUPFILL\s+{QUOTED}', line, re.I)
            if fm: col["lookupfill"] = fm.group(1)
        if "FORMULA" in line.upper():
            fm = re.search(rf'FORMULA\s+{QUOTED}', line, re.I)
            if fm: col["formula"] = fm.group(1)
        if "ENUM" in line:
            em = re.search(r'ENUM\s*\[(.*?)\]', line, re.I)
            if em: col["enum_list"] = [v.strip().strip('"') for v in em.group(1).split(',')]
        if "READONLY" in line.upper():
            col["readonly"] = True
        return col

    def _parse_layout_content(self, layout_id: str):
        while self._peek() and not self._peek().upper() == "END LAYOUT":
            line = self._next()
            if line.upper() == "TOP_MENU":
                self.app["layouts"][layout_id]["top_menu"] = self._parse_top_menu()
            elif line.upper() == "CENTER_MENU":
                self.app["layouts"][layout_id]["center_menu"] = self._parse_center_menu()
            elif line.upper().startswith("FORM"):
                m = re.match(rf'^FORM\s+{QUOTED}\s+Y=(\d+)\s+X=(\d+)', line, re.I)
                if m:
                    placement = {"y": int(m.group(2)), "x": int(m.group(3))}
                    cm = re.search(r'COLS=(\d+)', line, re.I)
                    if cm:
                        placement["cols"] = int(cm.group(1))
                    self.app["layouts"][layout_id]["fields"][m.group(1)] = placement
            elif line.upper().startswith("GRID"):
                m = re.match(rf'^GRID\s+{QUOTED}\s+Y=(\d+)\s+X=(\d+)\s+HEIGHT=(\d+)', line, re.I)
                if m:
                    self.app["layouts"][layout_id]["fields"][m.group(1)] = {"y": int(m.group(2)), "x": int(m.group(3)), "height": int(m.group(4))}
            elif line.upper().startswith("TABS"):
                m = re.match(r'^TABS\s+Y=(\d+)\s+X=(\d+)\s+HEIGHT=(\d+)', line, re.I)
                if m:
                    tabs = {"y": int(m.group(1)), "x": int(m.group(2)),
                            "height": int(m.group(3)), "items": []}
                    while self._peek() and not self._peek().upper() == "END TABS":
                        tl = self._next()
                        tm = re.match(rf'^TAB\s+{QUOTED}\s+GRID\s+{QUOTED}', tl, re.I)
                        if tm:
                            tabs["items"].append({"label": tm.group(1), "grid": tm.group(2)})
                    self._next()  # consume END TABS
                    self.app["layouts"][layout_id]["tabs"] = tabs
            elif line.upper().startswith("BUTTON"):
                m = re.match(rf'^BUTTON\s+{QUOTED}\s+RUN\s+{QUOTED}\s+Y=(\d+)\s+X=(\d+)', line, re.I)
                if m:
                    self.app["layouts"][layout_id]["buttons"][m.group(1)] = {"action": m.group(2), "y": int(m.group(3)), "x": int(m.group(4))}

    def _parse_top_menu(self):
        menus = []
        while self._peek() and not self._peek().upper() == "END TOP_MENU":
            line = self._next()
            if line.upper().startswith("PULLDOWN") or line.upper().startswith("GROUP"):
                m = re.match(rf'^(?:PULLDOWN|GROUP)\s+{QUOTED}', line, re.I)
                if m:
                    pd = {"label": m.group(1), "items": []}
                    while self._peek() and self._peek().upper() not in ("END PULLDOWN", "END GROUP"):
                        pl = self._next()
                        if pl.upper() == "SEPARATOR":
                            pd["items"].append({"separator": True})
                        else:
                            im = re.match(rf'^ITEM\s+{QUOTED}\s+RUN\s+{QUOTED}', pl, re.I)
                            if im:
                                it = {"label": im.group(1), "action": im.group(2)}
                                hm = re.search(rf'HOTKEY\s+{QUOTED}', pl, re.I)
                                if hm: it["hotkey"] = hm.group(1)
                                pd["items"].append(it)
                    self._next()
                    menus.append(pd)
        self._next()
        return menus

    def _parse_center_menu(self):
        items = []
        while self._peek() and not self._peek().upper() == "END CENTER_MENU":
            line = self._next()
            m = re.match(rf'^ITEM\s+{QUOTED}\s+RUN\s+{QUOTED}', line, re.I)
            if m:
                    it = {"label": m.group(1), "action": m.group(2)}
                    hm = re.search(r'HOTKEY\s+{QUOTED}', line, re.I)
                    if hm: it["hotkey"] = hm.group(1)
                    items.append(it)
        self._next()
        return items