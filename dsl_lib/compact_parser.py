"""Compact DSL Parser — produces the same app dict as DSLParser but from shorter syntax.

Compact syntax:
  APP "Name" VERSION "1.0"
  DATASOURCE "db" ADAPTER "sqlite"

  FORM name TITLE "Title" COLS 2
    SECTION "Label"
    field_id | width | flags...
    DETAIL "Tab Label" AS detail_name TOTALS
      col_id | width | flags...
    END DETAIL
    LIST col:width col:width ...
  END

  MENU "App Title"
    PULLDOWN "Label"
      "Item" => form_name
      "List" => form_name.list HOTKEY F2
      ---
      "Exit" => EXIT HOTKEY ESC
    END
    CENTER
      "1. Items" => item
      "2. Exit" => EXIT
    END
  END

Field flags:
  req, num, date, ro, hidden,
  prefix:XXX, span:N, default:X,
  opts:A,B,C  (enum),
  lookup:name, fill:field, formula:expr
"""

import copy
import re
from typing import Dict, List, Any

QUOTED = r'"([^"]+)"'


class DSLParseError(SyntaxError):
    """Raised when a DSL construct cannot be parsed unambiguously."""


class CompactDSLParser:
    def __init__(self, text: str):
        self.raw = text
        self.lines = [re.sub(r'//.*', '', l).rstrip() for l in text.splitlines()]
        self.pos = 0
        self.app = {}

    def _remember_id(self, ident: str):
        if ident in self.app.get("_all_ids", set()):
            self.app.setdefault("_duplicate_ids", []).append(ident)
        self.app.setdefault("_all_ids", set()).add(ident)

    def _next(self, skip_blank=True):
        while self.pos < len(self.lines):
            l = self.lines[self.pos].strip()
            self.pos += 1
            if skip_blank and l == '':
                continue
            return l
        return None

    def _peek(self, skip_blank=True):
        saved = self.pos
        while saved < len(self.lines):
            l = self.lines[saved].strip()
            if skip_blank and l == '':
                saved += 1
                continue
            return l
        return None

    def parse(self) -> Dict[str, Any]:
        self.app = {
            "meta": {}, "datasource": None,
            "forms": {}, "grids": {}, "layouts": {}, "actions": {},
            "reports": {}, "subroutines": {}, "fieldsets": {},
            "top_menu": None, "center_menu": None
        }
        while self.pos < len(self.lines):
            line = self._next()
            if line is None:
                break
            up = line.upper()

            if re.match(r'^APP\b', up):
                m = re.fullmatch(rf'APP\s+{QUOTED}\s+VERSION\s+{QUOTED}', line, re.I)
                if not m:
                    raise DSLParseError(f"Invalid APP declaration: {line}")
                self.app["meta"]["name"] = m.group(1)
                self.app["meta"]["version"] = m.group(2)

            elif re.match(r'^DATASOURCE\b', up):
                m = re.fullmatch(rf'DATASOURCE\s+{QUOTED}\s+ADAPTER\s+{QUOTED}', line, re.I)
                if not m:
                    raise DSLParseError(f"Invalid DATASOURCE declaration: {line}")
                self.app["datasource"] = {"name": m.group(1), "adapter": m.group(2).lower()}

            elif re.match(r'^FIELDSET\b', up):
                self._parse_fieldset(line)

            elif re.match(r'^FORM\b', up):
                self._parse_form(line)

            elif re.match(r'^MENU\b', up):
                self._parse_menu(line)

            elif re.match(r'^REPORT\b', up):
                self._parse_report(line)

            elif re.match(r'^SUBROUTINE\b', up):
                self._parse_subroutine(line)

            elif re.match(r'^RESET\b', up):
                if up != "RESET":
                    raise DSLParseError(f"Invalid RESET declaration: {line}")
                self._parse_reset()

            elif re.match(r'^(FORM|MENU|REPORT|SUBROUTINE)\b', up):
                raise DSLParseError(f"Invalid top-level declaration: {line}")

            elif up not in ("---",):
                raise DSLParseError(f"Unknown top-level declaration: {line}")

        self._inject_system_menu()
        self.app.pop("fieldsets", None)
        self.app.pop("_all_ids", None)
        return self.app

    # ── Default system menu items ──────────────────────────────

    _SYSTEM_ITEMS = [
        {"label": "Business Settings", "action": "business_settings"},
        {"label": "B2B InBox",         "action": "b2b_inbox"},
        {"label": "B2B OutBox",        "action": "b2b_outbox"},
        {"label": "Reset All Data",    "action": "reset_data"},
    ]

    _SYSTEM_ACTIONS = {
        "business_settings": {"type": "settings"},
        "b2b_inbox":         {"type": "b2b_inbox"},
        "b2b_outbox":        {"type": "b2b_outbox"},
        "reset_data":        {"type": "reset"},
    }

    def _inject_system_menu(self):
        """Ensure system menu items exist in every project."""
        # Register actions
        for name, act_def in self._SYSTEM_ACTIONS.items():
            if name not in self.app["actions"]:
                self.app["actions"][name] = act_def

        # Collect system items not already in any menu
        existing_actions = set()
        main_layout = self.app["layouts"].get("main_menu")
        if main_layout:
            for item in (main_layout.get("center_menu") or []):
                existing_actions.add(item.get("action", ""))
            for pd in (main_layout.get("top_menus") or []):
                for item in pd.get("items", []):
                    existing_actions.add(item.get("action", ""))

        missing = [item for item in self._SYSTEM_ITEMS
                   if item["action"] not in existing_actions]

        if not missing:
            return

        if main_layout:
            top_menus = main_layout.get("top_menus") or self.app.get("top_menus")
            if top_menus:
                # Pulldown style — append System group to top bar only
                sys_pd = {"label": "System", "items": [dict(i) for i in missing]}
                top_menus.append(sys_pd)
                self.app["top_menus"] = top_menus
            else:
                # Center/mainmenu style — append System section to center_menu
                center = main_layout.get("center_menu")
                if center is None:
                    center = []
                    main_layout["center_menu"] = center
                center.append({"label": "System", "section": True})
                for item in missing:
                    center.append(dict(item))
        else:
            # No menu at all — create default system menu
            self.app["actions"]["exit_app"] = {"type": "exit"}
            self.app["actions"]["goto_main"] = {"type": "goto", "target": "main_menu"}
            items = [dict(i) for i in self._SYSTEM_ITEMS]
            items.append({"label": "Exit", "action": "exit_app"})
            layout = {
                "title": "System Menu",
                "position": {"y": 0, "x": 0, "width": 80, "height": 24},
                "border": 1, "fields": {}, "buttons": {},
                "center_menu": items,
            }
            self.app["layouts"]["main_menu"] = layout

    # ── FORM block ──────────────────────────────────────────────

    def _parse_fieldset(self, header: str):
        """Parse a reusable FIELDSET block for compact forms."""
        m = re.match(r'^FIELDSET\s+(\w+)', header, re.I)
        if not m:
            raise DSLParseError(f"Invalid FIELDSET declaration: {header}")
        name = m.group(1)
        items = []

        while self._peek(skip_blank=False) is not None:
            peeked = self._peek()
            if peeked and peeked.upper() in ("END", "END FIELDSET"):
                break
            line = self._next(skip_blank=False)
            items.extend(self._parse_fieldset_items(line, name))
        self._next()  # consume END / END FIELDSET
        self.app["fieldsets"][name] = items

    def _parse_fieldset_items(self, line: str, fieldset_name: str) -> List[dict]:
        """Parse one fieldset line into zero or more reusable form items."""
        if line == "":
            return [{"type": "SPACER"}]

        up = line.upper().strip()
        if up.startswith("USE "):
            return self._expand_fieldset_references(line[4:].strip(), fieldset_name)
        if up.startswith("SECTION "):
            sm = re.match(rf'^SECTION\s+{QUOTED}', line, re.I)
            if not sm:
                raise DSLParseError(f"Invalid SECTION in FIELDSET '{fieldset_name}': {line}")
            return [{"type": "SECTION", "label": sm.group(1)}]
        if "|" in line:
            field = self._parse_field_line(line)
            return [field] if field else []
        raise DSLParseError(f"Invalid FIELDSET line in '{fieldset_name}': {line}")

    def _expand_fieldset_references(self, refs: str, owner: str) -> List[dict]:
        """Expand one or more comma-separated fieldset references."""
        items = []
        for raw_name in refs.split(","):
            name = raw_name.strip()
            if not name:
                continue
            source = self.app["fieldsets"].get(name)
            if source is None:
                raise DSLParseError(
                    f"FIELDSET '{owner}' references undefined fieldset '{name}'")
            items.extend(copy.deepcopy(source))
        return items

    def _parse_form(self, header: str):
        """Parse a compact FORM block and generate forms, grids, layouts, actions."""
        # FORM name TITLE "Title" COLS 2
        m = re.match(r'^FORM\s+(\w+)', header, re.I)
        if not m:
            raise DSLParseError(f"Invalid FORM declaration: {header}")
        name = m.group(1)
        form_id = f"{name}_form"
        grid_id = f"{name}_grid"
        self._remember_id(form_id)
        self._remember_id(grid_id)

        # Extract optional TITLE
        tm = re.search(rf'TITLE\s+{QUOTED}', header, re.I)
        title = tm.group(1) if tm else name.replace("_", " ").title()

        # Extract optional COLS
        cm = re.search(r'COLS\s+(\d+)', header, re.I)
        cols = int(cm.group(1)) if cm else 1

        # Detect ENTRY mode (full-screen grid entry)
        is_entry = bool(re.search(r'\bENTRY\b', header, re.I))

        fields = []       # form fields
        list_cols = []     # LIST column specs
        details = []       # DETAIL blocks: (label, detail_name, columns, totals)
        workflow = None    # POSTING or WORKFLOW block
        on_save = None     # ON SAVE rules
        scripts = {}       # SCRIPT blocks (name -> parsed AST)
        computed = []      # COMPUTED section: [{name, expr}]
        highlights = []    # HIGHLIGHT section: [{expr, style}]
        list_views = []    # LISTVIEW section: [{label, filter}]
        udf_schema = None  # UDF panel schema name

        # Parse body until END — blank lines become spacers
        while self._peek(skip_blank=False) is not None:
            peeked = self._peek()  # skip-blank peek for END check
            if peeked and peeked.upper() == "END":
                break
            line = self._next(skip_blank=False)
            if line == '':
                fields.append({"type": "SPACER"})
                continue
            up = line.upper().strip()

            if up.startswith("USE "):
                fields.extend(self._expand_fieldset_references(line[4:].strip(), name))

            elif up.startswith("SECTION "):
                sm = re.match(rf'^SECTION\s+{QUOTED}', line, re.I)
                if sm:
                    fields.append({"type": "SECTION", "label": sm.group(1)})

            elif up.startswith("TREE_DETAIL "):
                detail = self._parse_tree_detail(line)
                if detail:
                    details.append(detail)

            elif up.startswith("DETAIL "):
                detail = self._parse_detail(line)
                if detail:
                    details.append(detail)

            elif up.startswith("UDF "):
                # UDF "schema_name" — user-defined fields panel
                um = re.match(rf'^UDF\s+{QUOTED}', line, re.I)
                if um:
                    udf_schema = um.group(1)

            elif up.startswith("POSTING "):
                workflow = self._parse_posting(line)

            elif up.startswith("ON SAVE"):
                on_save = self._parse_posting_steps_until("END SAVE")

            elif up.startswith("SCRIPT "):
                sname, sinstructions = self._parse_script_block(line)
                if sname:
                    scripts[sname] = sinstructions

            elif up.startswith("WORKFLOW "):
                workflow = self._parse_workflow_block(line)

            elif up == "COMPUTED":
                # Parse COMPUTED section: field_name = expression
                while self._peek() and self._peek().upper().strip() != "END COMPUTED":
                    cline = self._next().strip()
                    if "=" in cline:
                        cname, cexpr = cline.split("=", 1)
                        computed.append({"name": cname.strip(), "expr": cexpr.strip()})
                self._next()  # consume END COMPUTED

            elif up == "HIGHLIGHT":
                # Parse HIGHLIGHT section: condition | style
                while self._peek() and self._peek().upper().strip() != "END HIGHLIGHT":
                    hline = self._next().strip()
                    if "|" in hline:
                        parts = [p.strip() for p in hline.split("|")]
                        if len(parts) >= 2:
                            highlights.append({"expr": parts[0], "style": parts[1].lower()})
                self._next()  # consume END HIGHLIGHT

            elif up == "LISTVIEW":
                # LISTVIEW block: label | filter_expr  (* = no filter)
                while self._peek() and self._peek().upper().strip() != "END LISTVIEW":
                    vline = self._next().strip()
                    if "|" in vline:
                        parts = [p.strip() for p in vline.split("|", 1)]
                        if len(parts) == 2:
                            lbl = parts[0].strip('"').strip("'")
                            flt = parts[1].strip()
                            list_views.append({"label": lbl, "filter": flt})
                self._next()  # consume END LISTVIEW

            elif up.startswith("LIST "):
                # LIST part_no:12 part_name:30 ...
                list_part = line[4:].strip()
                for chunk in list_part.split():
                    if ":" in chunk:
                        cname, cwidth = chunk.rsplit(":", 1)
                        try:
                            list_cols.append((cname.strip(), int(cwidth)))
                        except ValueError:
                            list_cols.append((cname.strip(), 12))

            elif "|" in line:
                field = self._parse_field_line(line)
                if field:
                    fields.append(field)
        self._next()  # consume END

        # ── Generate form ──
        self.app["forms"][form_id] = fields

        # ── Generate listing grid ──
        if list_cols:
            grid_columns = []
            # Build a type map from form fields
            type_map = {f["id"]: f for f in fields if f.get("id")}
            listed_ids = {cname for cname, _ in list_cols}
            for cname, cwidth in list_cols:
                fdef = type_map.get(cname, {})
                col = {"id": cname, "type": fdef.get("type", "STRING"), "width": cwidth}
                grid_columns.append(col)
            # Append remaining form fields as hidden so F8 can reveal them
            for f in fields:
                fid = f.get("id")
                if not fid or fid in listed_ids or f.get("type") == "SECTION":
                    continue
                col = {"id": fid, "type": f.get("type", "STRING"),
                       "width": min(f.get("length", 12), 20), "hidden": True}
                grid_columns.append(col)
            gdef = {"parent": form_id, "columns": grid_columns}
            if list_views:
                gdef["list_views"] = list_views
            self.app["grids"][grid_id] = gdef
        else:
            # Auto-generate grid from all non-section fields
            grid_columns = []
            for f in fields:
                if f.get("type") == "SECTION":
                    continue
                col = {"id": f["id"], "type": f.get("type", "STRING"),
                       "width": min(f.get("length", 12), 20)}
                grid_columns.append(col)
            gdef = {"parent": form_id, "columns": grid_columns}
            if list_views:
                gdef["list_views"] = list_views
            self.app["grids"][grid_id] = gdef

        # ── Generate detail grids ──
        tab_items = []
        for detail_tuple in details:
            # Flat DETAIL: 6 elements; TREE_DETAIL: 7 elements (extra tree_meta dict)
            if len(detail_tuple) == 7:
                detail_label, detail_name, detail_cols, has_totals, coedit_col, prefill, tree_meta = detail_tuple
            else:
                detail_label, detail_name, detail_cols, has_totals, coedit_col, prefill = detail_tuple
                tree_meta = None
            d_form_id = f"{detail_name}_form"
            d_grid_id = f"{detail_name}_grid"
            self._remember_id(d_form_id)
            self._remember_id(d_grid_id)

            # Detail form fields
            d_fields = []
            for dc in detail_cols:
                d_fields.append({
                    "id": dc["id"],
                    "type": dc.get("type", "STRING"),
                    **({"length": dc["width"]} if "width" in dc else {})
                })
            self.app["forms"][d_form_id] = d_fields

            # Detail grid
            d_grid = {"parent": d_form_id, "columns": detail_cols}
            if has_totals:
                d_grid["show_totals"] = True
            if tree_meta:
                d_grid["tree_detail"] = True
                d_grid["children_key"] = tree_meta.get("children_key", "children")
                d_grid["tree_label_field"] = tree_meta.get("tree_label_field", "")
            self.app["grids"][d_grid_id] = d_grid

            tab_item = {"label": detail_label, "grid": d_grid_id}
            if coedit_col:
                tab_item["coedit"] = coedit_col
            if prefill:
                tab_item["prefill"] = prefill
            if tree_meta:
                tab_item["tree_detail"] = True
            tab_items.append(tab_item)

        # ── Generate layouts ──
        # Form layout (add_name)
        add_layout_id = f"add_{name}"
        self._remember_id(add_layout_id)
        if is_entry and tab_items:
            # ENTRY mode: full-screen grid entry layout
            entry_tab = tab_items[0]  # primary detail grid
            entry_grid_id = entry_tab["grid"]
            form_layout = {
                "title": title,
                "entry": True,
                "position": {"y": 0, "x": 0, "width": 80, "height": 24},
                "border": 1,
                "fields": {entry_grid_id: {"y": 3, "x": 1, "height": 20}},
                "header_form": form_id,
                "header_fields": [f for f in fields if f.get("type") != "SECTION"],
                "entry_grid": entry_grid_id,
                "entry_tab": entry_tab,
                "buttons": {}
            }
        else:
            form_layout = {
                "title": title,
                "position": {"y": 0, "x": 0, "width": 80, "height": 24},
                "border": 1,
                "fields": {form_id: {"y": 3, "x": 2}},
                "buttons": {}
            }
            if cols > 1:
                form_layout["fields"][form_id]["cols"] = cols
            if tab_items:
                # Simulate physical layout to compute actual row count
                # (mirrors tui/layout.py field placement logic)
                row_offset = 0
                cur_col = 0
                for f in fields:
                    if f.get("type") == "SECTION":
                        if cur_col > 0:
                            row_offset += 1
                            cur_col = 0
                        row_offset += 1
                        continue
                    span = f.get("span", 1)
                    if cols > 1 and cur_col + span > cols and cur_col > 0:
                        row_offset += 1
                        cur_col = 0
                    cur_col += span
                    if cur_col >= cols:
                        cur_col = 0
                        row_offset += 1
                    # Multi-line fields (rows:N) consume extra rows
                    extra = f.get("rows", 1) - 1
                    if extra > 0:
                        row_offset += extra
                if cur_col > 0:
                    row_offset += 1
                rows = row_offset
                tab_y = 3 + rows + 1  # +1 for spacing
                form_layout["tabs"] = {
                    "y": tab_y, "x": 2, "height": 14,
                    "items": tab_items
                }
        if workflow:
            form_layout["workflow"] = workflow
        if on_save:
            form_layout["on_save"] = on_save
        if scripts:
            form_layout["scripts"] = scripts
        if computed:
            form_layout["computed"] = computed
        if udf_schema:
            form_layout["udf_schema"] = udf_schema
        self.app["layouts"][add_layout_id] = form_layout

        # List layout (list_name)
        list_layout_id = f"list_{name}"
        self._remember_id(list_layout_id)
        list_layout = {
            "title": f"{title} List" if "List" not in title else title,
            "position": {"y": 0, "x": 0, "width": 80, "height": 24},
            "border": 1,
            "fields": {grid_id: {"y": 3, "x": 2, "height": 18}},
            "buttons": {}
        }
        if highlights:
            list_layout["highlights"] = highlights
        self.app["layouts"][list_layout_id] = list_layout

        # ── Generate actions ──
        self.app["actions"][f"goto_{name}_add"] = {"type": "goto", "target": add_layout_id}
        self.app["actions"][f"goto_{name}_list"] = {"type": "goto", "target": list_layout_id}
        # Short form: bare name opens list (list-first pattern)
        self.app["actions"][f"goto_{name}"] = {"type": "goto", "target": list_layout_id}
        for action_id in (f"goto_{name}_add", f"goto_{name}_list", f"goto_{name}"):
            self._remember_id(action_id)

    def _parse_field_line(self, line: str) -> dict:
        """Parse: field_id | width | flags..."""
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            return None

        fid = parts[0]
        field = {"id": fid, "type": "STRING"}

        # Width / length
        if len(parts) > 1 and parts[1]:
            try:
                field["length"] = int(parts[1])
            except ValueError:
                field["_invalid_options"] = ["length"]

        # Flags
        if len(parts) > 2:
            flags_str = parts[2]
            self._apply_field_flags(field, flags_str)

        return field

    def _apply_field_flags(self, field: dict, flags_str: str):
        """Parse flag tokens into field dict properties."""
        unknown = []
        # Handle opts: specially since values contain spaces (comma-separated)
        opts_m = re.search(r'opts:(.+?)(?:\s+(?:req|num|date|ro|hidden|upper|prefix|span|rows|default|lookup|fill|formula|key|copydetail|filter|readonly_below)\b|$)', flags_str)
        if opts_m:
            vals = [v.strip() for v in opts_m.group(1).split(",")]
            field["type"] = "ENUM"
            field["enum_list"] = vals
            # Remove opts from flags_str for remaining parsing
            flags_str = flags_str[:opts_m.start()] + flags_str[opts_m.end():]

        # Handle formula: specially (may contain spaces) — must be last flag
        formula_m = re.search(r'\bformula:(.+)', flags_str, re.I)
        if formula_m:
            field["formula"] = formula_m.group(1).strip()
            flags_str = flags_str[:formula_m.start()]

        # Extract lookup with optional INCLUDE/EXCLUDE/CASCADE modifiers
        lookup_full_m = re.search(
            r'\blookup:(\w+)'
            r'(?:\s+INCLUDE\s+([\w,]+))?'
            r'(?:\s+EXCLUDE\s+([\w,]+))?'
            r'(?:\s+CASCADE\s+([\w\->]+))?',
            flags_str, re.I
        )
        if lookup_full_m:
            field["lookup"] = lookup_full_m.group(1)
            if lookup_full_m.group(2):
                field["lookup_include"] = [f.strip() for f in lookup_full_m.group(2).split(",") if f.strip()]
            if lookup_full_m.group(3):
                field["lookup_exclude"] = [f.strip() for f in lookup_full_m.group(3).split(",") if f.strip()]
            if lookup_full_m.group(4):
                field["lookup_cascade"] = lookup_full_m.group(4)
            flags_str = flags_str[:lookup_full_m.start()] + flags_str[lookup_full_m.end():]

        tokens = flags_str.split()
        for tok in tokens:
            tok_lower = tok.lower()
            if tok_lower.startswith(":") and tok[1:] in field.get("enum_list", []):
                continue
            if tok_lower == "req":
                field["required"] = True
            elif tok_lower == "num":
                field["type"] = "FLOAT"
            elif tok_lower == "date":
                field["type"] = "DATE"
            elif tok_lower == "ro":
                field["readonly"] = True
            elif tok_lower.startswith("readonly_below:"):
                try:
                    field["readonly_below"] = int(tok.split(":", 1)[1])
                except (ValueError, IndexError):
                    field.setdefault("_invalid_options", []).append("readonly_below")
            elif tok_lower == "hidden":
                field["hidden"] = True
            elif tok_lower == "upper":
                field["upper"] = True
            elif tok_lower == "key":
                field["_key"] = True
            elif tok_lower.startswith("prefix:"):
                prefix_spec = tok[7:]
                if ":" in prefix_spec:
                    parts = prefix_spec.split(":", 1)
                    field["prefix"] = parts[0]
                    field["prefix_format"] = parts[1].upper()
                else:
                    field["prefix"] = prefix_spec
            elif tok_lower.startswith("span:"):
                try:
                    field["span"] = int(tok[5:])
                except ValueError:
                    field.setdefault("_invalid_options", []).append("span")
            elif tok_lower.startswith("rows:"):
                try:
                    field["rows"] = int(tok[5:])
                except ValueError:
                    field.setdefault("_invalid_options", []).append("rows")
            elif tok_lower.startswith("default:"):
                field["default"] = tok[8:]
            elif tok_lower.startswith("lookup:"):
                pass  # handled by lookup_full_m above
            elif tok_lower in ("email", "phone", "uuid", "json", "color", "time", "timestamp"):
                field["subtype"] = tok_lower
            elif tok_lower in ("currency", "percentage"):
                field["subtype"] = tok_lower
                field["type"] = "FLOAT"
            elif tok_lower.startswith("fill:"):
                fill_val = tok[5:]
                if "=>" in fill_val:
                    # Mapped pairs: fill:src=>dest,src2=>dest2
                    fill_map = {}
                    for pair in fill_val.split(","):
                        if "=>" in pair:
                            src, dest = pair.split("=>", 1)
                            fill_map[src.strip()] = dest.strip()
                        else:
                            fill_map[pair.strip()] = pair.strip()
                    field["lookupfill"] = fill_map
                elif "," in fill_val:
                    # Multiple simple fields: fill:field1,field2
                    fill_map = {}
                    for f in fill_val.split(","):
                        f = f.strip()
                        if f:
                            fill_map[f] = f
                    field["lookupfill"] = fill_map
                else:
                    field["lookupfill"] = fill_val
            elif tok_lower.startswith("copydetail:"):
                field["copydetail"] = tok[11:]
            elif tok_lower.startswith("filter:"):
                filters = {}
                for pair in tok[7:].split(","):
                    if "=" in pair:
                        fk, fv = pair.split("=", 1)
                        filters[fk.strip()] = fv.strip()
                field["lookup_filter"] = filters
            else:
                unknown.append(tok)
        if unknown:
            field["_unknown_flags"] = unknown

    # ── DETAIL block ────────────────────────────────────────────

    def _parse_detail(self, header: str):
        """Parse DETAIL "Label" AS name [TOTALS]"""
        m = re.match(rf'^DETAIL\s+{QUOTED}\s+AS\s+(\w+)', header, re.I)
        if not m:
            return None
        label = m.group(1)
        detail_name = m.group(2)
        has_totals = "TOTALS" in header.upper()

        columns = []
        coedit_col = None
        prefill = None  # PREFILL source_table [field_map]
        while self._peek() and self._peek().upper() != "END DETAIL":
            line = self._next()
            up_line = line.strip().upper()
            if up_line.startswith("COEDIT "):
                # Support single or comma-separated: COEDIT order_qty  or  COEDIT order_qty,discount
                raw = line.strip().split(None, 1)[1].strip()
                if "," in raw:
                    coedit_col = [c.strip() for c in raw.split(",") if c.strip()]
                else:
                    coedit_col = raw
            elif up_line.startswith("PREFILL "):
                # PREFILL product   or   PREFILL product filter:on_hand>0
                pf_parts = line.strip().split(None, 1)[1].strip()
                pf_tokens = pf_parts.split()
                pf_source = pf_tokens[0]
                pf_filter = None
                for pt in pf_tokens[1:]:
                    if pt.lower().startswith("filter:"):
                        pf_filter = pt[7:]
                prefill = {"source": pf_source, "filter": pf_filter}
            elif "|" in line:
                col = self._parse_col_line(line)
                if col:
                    columns.append(col)
        self._next()  # consume END DETAIL

        return (label, detail_name, columns, has_totals, coedit_col, prefill)

    def _parse_tree_detail(self, header: str):
        """Parse TREE_DETAIL "Label" AS name [children_key:xxx] [tree_label:xxx]"""
        m = re.match(rf'^TREE_DETAIL\s+{QUOTED}\s+AS\s+(\w+)', header, re.I)
        if not m:
            return None
        label = m.group(1)
        detail_name = m.group(2)

        # Optional config: children_key:xxx tree_label:xxx
        children_key = 'children'
        tree_label_field = ''
        ck_m = re.search(r'children_key:\s*(\w+)', header, re.I)
        if ck_m:
            children_key = ck_m.group(1)
        tl_m = re.search(r'tree_label:\s*(\w+)', header, re.I)
        if tl_m:
            tree_label_field = tl_m.group(1)

        columns = []
        while self._peek() and self._peek().upper().strip() != "END TREE_DETAIL":
            line = self._next()
            if "|" in line:
                col = self._parse_col_line(line)
                if col:
                    columns.append(col)
        self._next()  # consume END TREE_DETAIL

        # Mark first column as tree column if tree_label not set
        if columns and not tree_label_field:
            tree_label_field = columns[0]['id']

        # Return tuple with tree-specific metadata (7 elements vs 6 for flat DETAIL)
        return (label, detail_name, columns, False, None, None,
                {'tree_detail': True, 'children_key': children_key,
                 'tree_label_field': tree_label_field})

    def _parse_col_line(self, line: str) -> dict:
        """Parse a detail column: col_id | width | flags..."""
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            return None

        cid = parts[0]
        col = {"id": cid, "type": "STRING"}

        if len(parts) > 1 and parts[1]:
            try:
                col["width"] = int(parts[1])
            except ValueError:
                col["_invalid_options"] = ["width"]

        if len(parts) > 2:
            flags_str = parts[2]
            self._apply_col_flags(col, flags_str)

        return col

    def _apply_col_flags(self, col: dict, flags_str: str):
        """Parse column flag tokens."""
        unknown = []
        # Handle formula: — must be last flag (captures everything after)
        formula_m = re.search(r'\bformula:(.+)', flags_str, re.I)
        if formula_m:
            col["formula"] = formula_m.group(1).strip()
            flags_str = flags_str[:formula_m.start()]

        # Handle opts:
        opts_m = re.search(r'opts:(.+?)(?:\s+(?:req|num|date|ro|hidden|upper|prefix|span|rows|default|lookup|fill|formula|key|copydetail|filter|readonly_below)\b|$)', flags_str)
        if opts_m:
            col["enum_list"] = [v.strip() for v in opts_m.group(1).split(",")]
            flags_str = flags_str[:opts_m.start()] + flags_str[opts_m.end():]

        # Extract lookup with optional INCLUDE/EXCLUDE/CASCADE modifiers
        lookup_full_m = re.search(
            r'\blookup:(\w+)'
            r'(?:\s+INCLUDE\s+([\w,]+))?'
            r'(?:\s+EXCLUDE\s+([\w,]+))?'
            r'(?:\s+CASCADE\s+([\w\->]+))?',
            flags_str, re.I
        )
        if lookup_full_m:
            col["lookup"] = lookup_full_m.group(1)
            if lookup_full_m.group(2):
                col["lookup_include"] = [f.strip() for f in lookup_full_m.group(2).split(",") if f.strip()]
            if lookup_full_m.group(3):
                col["lookup_exclude"] = [f.strip() for f in lookup_full_m.group(3).split(",") if f.strip()]
            if lookup_full_m.group(4):
                col["lookup_cascade"] = lookup_full_m.group(4)
            flags_str = flags_str[:lookup_full_m.start()] + flags_str[lookup_full_m.end():]

        tokens = flags_str.split()
        for tok in tokens:
            tok_lower = tok.lower()
            if tok_lower == "req":
                col["required"] = True
            elif tok_lower == "num":
                col["type"] = "FLOAT"
            elif tok_lower == "date":
                col["type"] = "DATE"
            elif tok_lower == "ro":
                col["readonly"] = True
            elif tok_lower.startswith("readonly_below:"):
                try:
                    col["readonly_below"] = int(tok.split(":", 1)[1])
                except (ValueError, IndexError):
                    col.setdefault("_invalid_options", []).append("readonly_below")
            elif tok_lower == "hidden":
                col["hidden"] = True
            elif tok_lower == "upper":
                col["upper"] = True
            elif tok_lower.startswith("lookup:"):
                pass  # handled by lookup_full_m above
            elif tok_lower in ("email", "phone", "uuid", "json", "color", "time", "timestamp"):
                col["subtype"] = tok_lower
            elif tok_lower in ("currency", "percentage"):
                col["subtype"] = tok_lower
                col["type"] = "FLOAT"
            elif tok_lower.startswith("fill:"):
                fill_val = tok[5:]
                if "=>" in fill_val:
                    fill_map = {}
                    for pair in fill_val.split(","):
                        if "=>" in pair:
                            src, dest = pair.split("=>", 1)
                            fill_map[src.strip()] = dest.strip()
                        else:
                            fill_map[pair.strip()] = pair.strip()
                    col["lookupfill"] = fill_map
                elif "," in fill_val:
                    fill_map = {}
                    for f in fill_val.split(","):
                        f = f.strip()
                        if f:
                            fill_map[f] = f
                    col["lookupfill"] = fill_map
                else:
                    col["lookupfill"] = fill_val
            else:
                unknown.append(tok)
        if unknown:
            col["_unknown_flags"] = unknown

    # ── POSTING block ──────────────────────────────────────────

    def _parse_posting(self, header: str) -> dict:
        """Parse POSTING ON status_field with transitions and optional rules.

        Syntax:
          POSTING ON status
            Draft > Posted : post
              FOR EACH po_line
                FIND item BY part_no
                UPDATE item.on_order += line.order_qty
              END FOR
            Posted > Void : void
              FOR EACH po_line
                FIND item BY part_no
                UPDATE item.on_order -= line.order_qty
              END FOR
          END POSTING
        """
        m = re.match(r'^POSTING\s+ON\s+(\w+)', header, re.I)
        if not m:
            return None
        status_field = m.group(1)
        transitions = []
        TRANS_RE = re.compile(r'^(\w+)\s*>\s*(\w+)\s*:\s*(\w+)')
        RULE_STARTS = {"FOR", "FIND", "UPDATE", "IF", "WRITE"}

        while self._peek() and self._peek().upper() != "END POSTING":
            line = self._next().strip()
            tm = TRANS_RE.match(line)
            if tm:
                trans = {
                    "from": tm.group(1),
                    "to": tm.group(2),
                    "action": tm.group(3),
                    "rules": []
                }
                # Peek for rule lines under this transition
                while self._peek():
                    p = self._peek().strip()
                    up = p.split()[0].upper() if p else ""
                    if up in RULE_STARTS:
                        step = self._parse_posting_step()
                        if step:
                            trans["rules"].append(step)
                    else:
                        break  # next transition or END POSTING
                transitions.append(trans)
        self._next()  # consume END POSTING
        return {"status_field": status_field, "transitions": transitions}

    def _parse_posting_step(self) -> dict:
        """Parse a single posting rule step from the current position."""
        line = self._next().strip()
        up = line.upper()

        # FOR EACH <detail>
        fm = re.match(r'^FOR\s+EACH\s+(\w+)', line, re.I)
        if fm:
            detail = fm.group(1)
            steps = self._parse_posting_steps_until("END FOR")
            return {"type": "for_each", "detail": detail, "steps": steps}

        # FIND <table> BY <field> [OF <parent_table> USING <link_field>]
        fm = re.match(r'^FIND\s+(\w+)\s+BY\s+(\w+)\s+OF\s+(\w+)\s+USING\s+(\w+)', line, re.I)
        if fm:
            return {"type": "find", "table": fm.group(1), "by": fm.group(2),
                    "parent_table": fm.group(3), "link_field": fm.group(4)}
        fm = re.match(r'^FIND\s+(\w+)\s+BY\s+(\w+)', line, re.I)
        if fm:
            return {"type": "find", "table": fm.group(1), "by": fm.group(2)}

        # UPDATE <table>.<field> <op> <expr>
        fm = re.match(r'^UPDATE\s+(\w+\.\w+)\s*(\+\=|\-\=|\=)\s*(.+)', line, re.I)
        if fm:
            return {
                "type": "update",
                "target": fm.group(1),
                "op": fm.group(2),
                "expr": fm.group(3).strip()
            }

        # IF <condition>
        fm = re.match(r'^IF\s+(.+)', line, re.I)
        if fm and not up.startswith("IF ") == False:
            cond = fm.group(1).strip()
            steps = self._parse_posting_steps_until("END IF")
            return {"type": "if", "cond": cond, "steps": steps}

        # WRITE <table>
        fm = re.match(r'^WRITE\s+(\w+)', line, re.I)
        if fm:
            table = fm.group(1)
            fields = {}
            last_find = None
            while self._peek() and self._peek().strip().upper() != "END WRITE":
                fline = self._next().strip()
                feq = re.match(r'^(\w+)\s*\=\s*(.+)', fline)
                if feq:
                    fields[feq.group(1)] = feq.group(2).strip()
            self._next()  # consume END WRITE
            return {"type": "write", "table": table, "fields": fields}

        return None

    def _parse_posting_steps_until(self, end_token: str) -> list:
        """Parse posting steps until encountering end_token (e.g. END FOR, END IF)."""
        steps = []
        RULE_STARTS = {"FOR", "FIND", "UPDATE", "IF", "WRITE"}
        while self._peek():
            p = self._peek().strip().upper()
            if p == end_token:
                self._next()  # consume end token
                return steps
            first = p.split()[0] if p else ""
            if first in RULE_STARTS:
                step = self._parse_posting_step()
                if step:
                    steps.append(step)
            else:
                self._next()  # skip unrecognized line
        return steps

    # ── SCRIPT / WORKFLOW blocks ─────────────────────────────────

    def _parse_script_block(self, header: str):
        """Parse SCRIPT name ... ENDSCRIPT block.

        Collects body lines and parses them via the script engine parser.
        Returns (name, parsed_instructions) or (None, None).
        """
        m = re.match(r'^SCRIPT\s+(\w+)', header, re.I)
        if not m:
            return None, None
        name = m.group(1)
        body_lines = []
        while self._peek() and self._peek().upper() not in ("ENDSCRIPT", "END SCRIPT"):
            body_lines.append(self._next())
        self._next()  # consume ENDSCRIPT
        from dsl_lib.script_engine import parse_script
        instructions = parse_script(body_lines)
        return name, instructions

    def _parse_subroutine(self, header: str):
        """Parse top-level SUBROUTINE name(param1, param2) ... ENDSUBROUTINE.

        Subroutines are global reusable scripts callable from any form via
        CALL name(arg1, arg2). Parameters are bound to ctx vars at call time.
        """
        m = re.match(r'^SUBROUTINE\s+(\w+)\s*\(([^)]*)\)', header, re.I)
        if not m:
            # No-param form: SUBROUTINE name
            m2 = re.match(r'^SUBROUTINE\s+(\w+)', header, re.I)
            if not m2:
                return
            name = m2.group(1)
            params = []
        else:
            name = m.group(1)
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]

        body_lines = []
        while self._peek() and self._peek().upper() not in ("ENDSUBROUTINE", "END SUBROUTINE"):
            body_lines.append(self._next())
        self._next()  # consume ENDSUBROUTINE

        from dsl_lib.script_engine import parse_script
        instructions = parse_script(body_lines)
        self.app["subroutines"][name] = {
            "params": params,
            "body": instructions,
        }
        self._remember_id(name)

    def _parse_workflow_block(self, header: str):
        """Parse WORKFLOW ON field ... END WORKFLOW block.

        Returns workflow dict with transitions and optional actions.
        Supports:
          FromStatus > ToStatus : script_name     (status transition)
          ---                                      (separator in F5 menu)
          ACTION "Label" : script_name             (action, no status change)
        """
        m = re.match(r'^WORKFLOW\s+ON\s+(\w+)', header, re.I)
        if not m:
            return None
        status_field = m.group(1)
        transitions = []
        actions = []
        TRANS_RE = re.compile(r'^(\w+)\s*>\s*(\w+)\s*:\s*(\w+)')
        ACTION_RE = re.compile(rf'^ACTION\s+{QUOTED}\s*:\s*(\w+)', re.I)
        while self._peek() and self._peek().upper() != "END WORKFLOW":
            line = self._next().strip()
            if line == "---" or line.upper() == "SEPARATOR":
                actions.append({"separator": True})
                continue
            am = ACTION_RE.match(line)
            if am:
                actions.append({
                    "label": am.group(1),
                    "script": am.group(2),
                })
                continue
            tm = TRANS_RE.match(line)
            if tm:
                transitions.append({
                    "from": tm.group(1),
                    "to": tm.group(2),
                    "action": tm.group(3),
                    "script": tm.group(3)
                })
        self._next()  # consume END WORKFLOW
        result = {"status_field": status_field, "transitions": transitions}
        if actions:
            result["actions"] = actions
        return result

    # ── MENU block ──────────────────────────────────────────────

    def _parse_menu(self, header: str):
        """Parse MENU block, generate main_menu layout with top_menu, center_menu, and actions.

        Syntax:
          MENU "Title"            — default main-menu (center) style
          MENU "Title" PULLDOWN   — top-bar pulldown style
          MENU "Title" MAINMENU   — explicit center style

        Groups inside the MENU block can use either GROUP or PULLDOWN keyword.
        """
        tm = re.match(rf'^MENU\s+{QUOTED}\s*(PULLDOWN|MAINMENU)?', header, re.I)
        if not tm:
            raise DSLParseError(f"Invalid MENU declaration: {header}")
        menu_title = tm.group(1)
        menu_type = (tm.group(2) or "MAINMENU").upper() if tm else "MAINMENU"

        top_menus = []
        center_items = []

        while self._peek() and self._peek().upper() != "END":
            line = self._next()
            up = line.upper().strip()

            if up.startswith("PULLDOWN ") or up.startswith("GROUP "):
                pm = re.match(rf'^(?:PULLDOWN|GROUP)\s+{QUOTED}', line, re.I)
                if pm:
                    pd = {"label": pm.group(1), "items": []}
                    while self._peek() and not re.match(r'^END(\s+GROUP)?$', self._peek().strip(), re.I):
                        pl = self._next().strip()
                        if pl == "---" or pl.upper() == "SEPARATOR":
                            pd["items"].append({"separator": True})
                        else:
                            item = self._parse_menu_item(pl)
                            if item:
                                pd["items"].append(item)
                    self._next()  # consume END / END GROUP
                    top_menus.append(pd)

            elif up.startswith("CENTER"):
                while self._peek() and self._peek().upper().strip() != "END":
                    cl = self._next().strip()
                    item = self._parse_menu_item(cl)
                    if item:
                        center_items.append(item)
                self._next()  # consume END

        self._next()  # consume final END

        # Generate main_menu layout
        layout = {
            "title": menu_title,
            "position": {"y": 0, "x": 0, "width": 80, "height": 24},
            "border": 1, "fields": {}, "buttons": {}
        }
        if center_items:
            # Explicit CENTER block — use as-is
            layout["center_menu"] = center_items
        if top_menus:
            if menu_type == "PULLDOWN":
                # Pulldown mode: store as top_menus for top-bar rendering
                layout["top_menus"] = top_menus
            elif not center_items:
                # Main-menu mode: flatten groups into center menu with section headers
                flat = []
                for pd in top_menus:
                    flat.append({"label": pd["label"], "section": True})
                    for item in pd["items"]:
                        flat.append(dict(item))
                layout["center_menu"] = flat
        # Always store top_menus for hotkey scanning
        if top_menus:
            self.app["top_menus"] = top_menus

        self.app["layouts"]["main_menu"] = layout

        # Generate exit action
        self.app["actions"]["exit_app"] = {"type": "exit"}
        self.app["actions"]["goto_main"] = {"type": "goto", "target": "main_menu"}

    def _parse_menu_item(self, line: str) -> dict:
        """Parse: "Label" => target [HOTKEY key]"""
        m = re.match(rf'^{QUOTED}\s*=>\s*(\S+)', line)
        if not m:
            return None
        label = m.group(1)
        target = m.group(2)

        # Resolve target to action name
        if target.upper() == "EXIT":
            action_name = "exit_app"
        elif target.upper() == "RESET":
            action_name = "reset_data"
            self.app["actions"]["reset_data"] = {"type": "reset"}
        elif target.upper() == "SWITCH":
            action_name = "switch_company"
            self.app["actions"]["switch_company"] = {"type": "switch"}
        elif target.upper() == "REPORTS":
            action_name = "report_browser"
            self.app["actions"]["report_browser"] = {"type": "report_browser"}
        elif target.upper() == "WHMAP":
            action_name = "warehouse_map"
            self.app["actions"]["warehouse_map"] = {"type": "whmap"}
        elif target.upper() == "POS.SCREEN":
            action_name = "pos_screen"
            self.app["actions"]["pos_screen"] = {"type": "pos_screen"}
        elif target.upper() == "SETTINGS":
            action_name = "business_settings"
            self.app["actions"]["business_settings"] = {"type": "settings"}
        elif target.upper() == "B2B.INBOX":
            action_name = "b2b_inbox"
            self.app["actions"]["b2b_inbox"] = {"type": "b2b_inbox"}
        elif target.upper() == "B2B.OUTBOX":
            action_name = "b2b_outbox"
            self.app["actions"]["b2b_outbox"] = {"type": "b2b_outbox"}
        elif target.startswith("report:"):
            report_name = target[7:]
            action_name = f"goto_report_{report_name}"
            self.app["actions"][action_name] = {"type": "report", "target": report_name}
        elif target.endswith(".list"):
            form_name = target[:-5]
            action_name = f"goto_{form_name}_list"
        elif target.endswith(".add"):
            form_name = target[:-4]
            action_name = f"goto_{form_name}_add"
        else:
            # Bare name opens list (list-first pattern)
            action_name = f"goto_{target}_list"

        item = {"label": label, "action": action_name}

        # Optional hotkey
        hm = re.search(r'HOTKEY\s+(\S+)', line, re.I)
        if hm:
            item["hotkey"] = hm.group(1)

        return item

    # ── REPORT block ───────────────────────────────────────────

    def _parse_report(self, header: str):
        """Parse REPORT name TITLE "Title" ... END REPORT block."""
        m = re.match(r'^REPORT\s+(\w+)', header, re.I)
        if not m:
            return
        name = m.group(1)
        tm = re.search(rf'TITLE\s+{QUOTED}', header, re.I)
        title = tm.group(1) if tm else name.replace("_", " ").title()

        report = {
            "name": name,
            "title": title,
            "source": "",
            "params": [],
            "where": "",
            "order": "",
            "columns": [],
            "groups": [],
            "totals": None,
            "crosstab": None,
        }

        while self._peek() and self._peek().upper() != "END REPORT":
            line = self._next()
            up = line.upper().strip()

            if up.startswith("SOURCE "):
                report["source"] = line.strip().split(None, 1)[1].strip()

            elif up.startswith("ORDER "):
                report["order"] = line.strip().split(None, 1)[1].strip()

            elif up.startswith("WHERE "):
                report["where"] = line.strip()[6:].strip()

            elif up.startswith("PARAMS"):
                params = []
                while self._peek() and self._peek().upper().strip() != "END PARAMS":
                    pline = self._next()
                    if "|" in pline:
                        pf = self._parse_report_param(pline)
                        if pf:
                            params.append(pf)
                self._next()  # consume END PARAMS
                report["params"] = params

            elif up.startswith("COLUMNS"):
                cols = []
                while self._peek() and self._peek().upper().strip() != "END COLUMNS":
                    cline = self._next()
                    if "|" in cline:
                        col = self._parse_report_col(cline)
                        if col:
                            cols.append(col)
                self._next()  # consume END COLUMNS
                report["columns"] = cols

            elif up.startswith("GROUP "):
                grp = self._parse_group_line(line)
                if grp:
                    report["groups"].append(grp)

            elif up.startswith("TOTALS "):
                tot = self._parse_totals_line(line)
                if tot:
                    report["totals"] = tot

            elif up.startswith("CROSSTAB"):
                ct = self._parse_crosstab_block()
                if ct:
                    report["crosstab"] = ct

        self._next()  # consume END REPORT
        self._remember_id(name)
        self.app["reports"][name] = report

    def _parse_report_param(self, line: str) -> dict:
        """Parse param field: id | Label | width | flags."""
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            return None
        fid = parts[0]
        field = {"id": fid, "type": "STRING", "label": fid}
        if len(parts) > 1 and parts[1]:
            field["label"] = parts[1]
        if len(parts) > 2 and parts[2]:
            try:
                field["length"] = int(parts[2])
            except ValueError:
                pass
        if len(parts) > 3:
            self._apply_field_flags(field, parts[3])
        return field

    def _parse_report_col(self, line: str) -> dict:
        """Parse report column: id | Label | width | flags."""
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            return None
        cid = parts[0]
        col = {"id": cid, "type": "STRING", "label": cid,
               "width": 12, "sum": False, "formula": None}
        if len(parts) > 1 and parts[1]:
            col["label"] = parts[1]
        if len(parts) > 2 and parts[2]:
            try:
                col["width"] = int(parts[2])
            except ValueError:
                pass
        if len(parts) > 3:
            flags_str = parts[3]
            # Handle formula: specially — captures everything after formula:
            formula_m = re.search(r'\bformula:(.+)', flags_str, re.I)
            if formula_m:
                col["formula"] = formula_m.group(1).strip()
                flags_str = flags_str[:formula_m.start()]
            tokens = flags_str.split()
            for tok in tokens:
                tl = tok.lower()
                if tl == "num":
                    col["type"] = "FLOAT"
                elif tl == "sum":
                    col["sum"] = True
                elif tl == "grpkey":
                    col["grpkey"] = True
        return col

    def _parse_group_line(self, line: str) -> dict:
        """Parse: GROUP field1 [field2] | Label | col:agg col:agg"""
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            return None
        # Part 0: "GROUP field1 field2..."
        fields_part = parts[0].strip()
        # Remove "GROUP" prefix
        fields_str = re.sub(r'^GROUP\s+', '', fields_part, flags=re.I).strip()
        fields = fields_str.split()
        label = parts[1].strip() if len(parts) > 1 else "Subtotal"
        aggregations = {}
        if len(parts) > 2:
            for chunk in parts[2].strip().split():
                if ":" in chunk:
                    col, agg = chunk.split(":", 1)
                    aggregations[col.strip()] = agg.strip()
        return {"fields": fields, "label": label, "aggregations": aggregations}

    def _parse_totals_line(self, line: str) -> dict:
        """Parse: TOTALS | Label | col:agg col:agg"""
        parts = [p.strip() for p in line.split("|")]
        label = parts[1].strip() if len(parts) > 1 else "Grand Total"
        aggregations = {}
        if len(parts) > 2:
            for chunk in parts[2].strip().split():
                if ":" in chunk:
                    col, agg = chunk.split(":", 1)
                    aggregations[col.strip()] = agg.strip()
        return {"label": label, "aggregations": aggregations}

    def _parse_crosstab_block(self) -> dict:
        """Parse CROSSTAB key:value block until END CROSSTAB."""
        ct = {"row": "", "col": "", "value": "", "agg": "sum",
              "row_total": True, "col_total": True}
        while self._peek() and self._peek().upper().strip() != "END CROSSTAB":
            line = self._next().strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key == "row":
                    ct["row"] = val
                elif key == "col":
                    ct["col"] = val
                elif key == "value":
                    # value can be field:agg like "on_hand:sum"
                    if ":" in val:
                        vf, va = val.split(":", 1)
                        ct["value"] = vf.strip()
                        ct["agg"] = va.strip()
                    else:
                        ct["value"] = val
                elif key == "row_total":
                    ct["row_total"] = val.lower() in ("yes", "true", "1")
                elif key == "col_total":
                    ct["col_total"] = val.lower() in ("yes", "true", "1")
                elif key == "fill":
                    ct["fill"] = val
        self._next()  # consume END CROSSTAB
        return ct

    # ── RESET block ────────────────────────────────────────────
    def _parse_reset(self):
        """Parse RESET block with CLEAR and UPDATE directives."""
        reset_def = {"clears": [], "updates": []}
        while self._peek():
            up = self._peek().strip().upper()
            if up == "END RESET":
                self._next()
                break
            line = self._next().strip()
            up = line.upper()
            if up.startswith("CLEAR "):
                tables = [t.strip() for t in line[6:].split(",")]
                reset_def["clears"].extend(tables)
            elif up.startswith("UPDATE "):
                m = re.match(r'^UPDATE\s+(\w+)\s+SET\s+(.+)', line, re.I)
                if m:
                    table = m.group(1)
                    sets = {}
                    for pair in m.group(2).split(","):
                        k, _, v = pair.partition("=")
                        sets[k.strip()] = v.strip()
                    reset_def["updates"].append({"table": table, "sets": sets})
        self.app["reset"] = reset_def
