import re
from typing import List, Dict, Any, Set
from dataclasses import dataclass

@dataclass
class ValidationError:
    code: str; message: str; line: int=0; fix: str=""
@dataclass
class ValidationResult:
    valid: bool=True; errors: List[ValidationError]=None; warnings: List[ValidationError]=None; summary: Dict=None

ALLOWED_TYPES = {"STRING","INT","FLOAT","DATE","DATETIME","BOOL","ENUM"}
ALLOWED_COMMANDS = {"SET","CALC","LOAD","SAVE","DELETE","LOOKUP","SQL","IF","ELSE","ENDIF","FOR","NEXT","EXIT","RETURN","RAISE","SHOW","WARN","FOCUS","REFRESH","GOTO","RUN"}

ALLOWED_ADAPTERS = {"sqlite", "postgres", "firebird"}
ALLOWED_HOTKEYS = {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "ESC", "ENTER"}
QUOTED_ID = r'"[a-zA-Z0-9_]+"'

class DSLValidator:
    def __init__(self):
        self.result = ValidationResult(errors=[], warnings=[], summary={})
        self.lines = []
        self.line_num = 0
        self.declared = {"forms": set(), "grids": set(), "actions": set(), "screens": set()}

    def _error(self, code: str, message: str, fix: str = ""):
        self.result.valid = False
        self.result.errors.append(ValidationError(code, message, self.line_num, fix))

    def validate(self, text: str, compact: bool = False) -> ValidationResult:
        self.result = ValidationResult(errors=[], warnings=[], summary={})
        self.line_num = 0
        self.declared = {"forms": set(), "grids": set(), "actions": set(), "screens": set()}
        # Preserve original line numbers by keeping (line_num, stripped_content) pairs
        self.lines = []
        for i, raw_line in enumerate(text.splitlines(), 1):
            stripped = re.sub(r'//.*', '', raw_line).strip()
            if stripped:
                self.lines.append((i, stripped))

        if compact:
            # Compact DSL is structurally permissive about closers: the
            # parser treats a bare `END` as a universal block terminator
            # (with rare exceptions like ENDSCRIPT/END RESET). Attempting to
            # enforce specific closers produces false positives on working
            # scripts, so we do NOT block-balance compact here — the parser
            # is the authority on structure and will raise on real errors.
            #
            # What we DO collect: declared forms/grids/actions from compact's
            # unquoted syntax, so the dialect-neutral HOTKEY and undeclared-
            # ACTION checks below actually fire for compact scripts (the
            # verbose QUOTED_ID regexes would otherwise match nothing).
            for _, l in self.lines:
                m = re.match(r'^FORM\s+(\w+)', l, re.I)
                if m:
                    self.declared["forms"].add(m.group(1))
                m = re.match(r'^GRID\s+(\w+)', l, re.I)
                if m:
                    self.declared["grids"].add(m.group(1))
                m = re.match(r'^ACTION\s+(\w+)', l, re.I)
                if m:
                    self.declared["actions"].add(m.group(1))
            self._validate_compact_structure()
            for _, l in self.lines:
                if not l.startswith('"'):
                    continue
                m = re.search(r'=>\s*([\w.:-]+)', l)
                if m:
                    target = m.group(1)
                    base = target.split('.', 1)[0]
                    builtins = {"EXIT", "REPORTS", "SETTINGS", "SWITCH", "RESET",
                                "POS", "POS.SCREEN", "WHMAP", "B2B"}
                    if (not target.lower().startswith("report:")
                            and base.upper() not in builtins and base not in self.declared["forms"]
                            and base not in self.declared["actions"]):
                        self.line_num = next(n for n, line in self.lines if line == l)
                        self._error("UNDECLARED_TARGET", f"Menu target '{target}' not defined")
        else:
            block_pairs = {
                "FORM": "END FORM", "GRID": "END GRID", "LAYOUT": "END LAYOUT",
                "TOP_MENU": "END TOP_MENU",
                "PULLDOWN": "END PULLDOWN", "CENTER_MENU": "END CENTER_MENU",
                "TABS": "END TABS"
            }
            for start, end in block_pairs.items():
                no_y = r'(?!.*\bY=)' if start in ("FORM", "GRID") else r''
                s = sum(1 for _, l in self.lines if re.match(rf'^{start}\b{no_y}', l, re.I))
                e = sum(1 for _, l in self.lines if re.match(rf'^{end}$', l, re.I))
                if s != e:
                    self._error("BLOCK_MISMATCH", f"{start} / {end} count mismatch")

            for _, l in self.lines:
                if re.match(rf'^FORM\s+{QUOTED_ID}', l, re.I):
                    self.declared["forms"].add(re.search(QUOTED_ID, l).group().strip('"'))
                if re.match(rf'^GRID\s+{QUOTED_ID}', l, re.I):
                    self.declared["grids"].add(re.search(QUOTED_ID, l).group().strip('"'))
                if re.match(rf'^ACTION\s+{QUOTED_ID}', l, re.I):
                    self.declared["actions"].add(re.search(QUOTED_ID, l).group().strip('"'))
                if re.match(rf'^LAYOUT\s+{QUOTED_ID}', l, re.I):
                    self.declared["screens"].add(re.search(QUOTED_ID, l).group().strip('"'))

        for orig_line_num, l in self.lines:
            self.line_num = orig_line_num
            if "HOTKEY" in l:
                m = re.search(r'HOTKEY\s+"([^"]+)"', l, re.I)
                if m and m.group(1) not in ALLOWED_HOTKEYS:
                    self._error("INVALID_HOTKEY", f"Bad hotkey: {m.group(1)}", f"Use one of: {ALLOWED_HOTKEYS}")
            if "RUN" in l:
                m = re.search(r'RUN\s+"([^"]+)"', l, re.I)
                if m and m.group(1) not in self.declared["actions"]:
                    self._error("UNDECLARED_ACTION", f"Action '{m.group(1)}' not defined")

        self.result.summary = {
            "forms_declared": len(self.declared["forms"]),
            "grids_declared": len(self.declared["grids"])
        }
        return self.result

    def _validate_compact_structure(self):
        stack = []
        starts = {"FORM": "FORM", "MENU": "MENU", "REPORT": "REPORT",
                  "FIELDSET": "FIELDSET",
                  "SUBROUTINE": "SUBROUTINE", "RESET": "RESET",
                  "DETAIL": "DETAIL", "POSTING": "POSTING", "WORKFLOW": "WORKFLOW",
                  "COMPUTED": "COMPUTED", "HIGHLIGHT": "HIGHLIGHT", "LISTVIEW": "LISTVIEW",
                  "SCRIPT": "SCRIPT", "STOCK": "STOCK", "TREE_DETAIL": "TREE_DETAIL",
                  "COLUMNS": "COLUMNS", "CROSSTAB": "CROSSTAB", "GROUP": "GROUP",
                  "CENTER": "CENTER", "SAVE": "SAVE", "FOR": "FOR",
                  "FOREACH": "FOREACH", "IF": "IF", "WRITE": "WRITE"}
        for number, line in self.lines:
            up = line.upper()
            if up.startswith("END ") or up in ("END", "ENDSCRIPT", "ENDSUBROUTINE"):
                token = up.replace("ENDSCRIPT", "SCRIPT").replace("ENDSUBROUTINE", "SUBROUTINE")
                name = token[4:].strip() if token.startswith("END ") else ("" if token == "END" else token)
                if not stack or (name and name != stack[-1]):
                    self.line_num = number
                    self._error("MALFORMED_TERMINATOR", f"Unexpected compact block terminator '{line}'")
                else:
                    stack.pop()
                continue
            keyword = up.split()[0] if up else ""
            if up.startswith("ON SAVE"):
                stack.append("SAVE")
                continue
            if keyword == "GROUP" and not re.search(r'GROUP\s+"', line, re.I):
                continue
            if keyword in starts:
                stack.append(starts[keyword])
        if stack:
            self.line_num = self.lines[-1][0]
            self._error("BLOCK_MISMATCH", f"Unclosed compact block: {stack[-1]}")


def validate_app_definition(app: Dict[str, Any]) -> ValidationResult:
    """Validate the parser's dialect-neutral application definition.

    Parser-generated definitions do not retain reliable source locations, so
    semantic diagnostics intentionally use line 0.
    """
    result = ValidationResult(errors=[], warnings=[], summary={})

    def error(code: str, message: str, fix: str = ""):
        result.valid = False
        result.errors.append(ValidationError(code, message, 0, fix))

    meta = app.get("meta") or {}
    if not meta.get("name"):
        error("MISSING_APP_NAME", "APP metadata is missing a name")
    if not meta.get("version"):
        error("MISSING_APP_VERSION", "APP metadata is missing a version")
    datasource = app.get("datasource") or {}
    if not datasource.get("name"):
        error("MISSING_DATASOURCE_NAME", "DATASOURCE metadata is missing a name")
    adapter = str(datasource.get("adapter", "")).lower()
    if not adapter:
        error("MISSING_ADAPTER", "DATASOURCE metadata is missing an adapter")
    elif adapter not in ALLOWED_ADAPTERS:
        error("UNSUPPORTED_ADAPTER", f"Unsupported adapter '{adapter}'",
              "Use sqlite, postgres, or firebird")

    forms = app.get("forms") or {}
    grids = app.get("grids") or {}
    layouts = app.get("layouts") or {}
    actions = app.get("actions") or {}
    reports = app.get("reports") or {}
    all_ids = {}
    for kind, values in (("form", forms), ("grid", grids),
                         ("layout", layouts), ("action", actions),
                         ("report", reports)):
        for ident in values:
            if ident in all_ids:
                error("DUPLICATE_ID", f"ID '{ident}' is used by both {all_ids[ident]} and {kind}")
            else:
                all_ids[ident] = kind
    for ident in app.get("_duplicate_ids", []):
        error("DUPLICATE_ID", f"Duplicate generated or declared ID '{ident}'")

    field_sets = {}
    for form_id, fields in forms.items():
        ids = set()
        for field in fields or []:
            if field.get("type") in {"SECTION", "SPACER"}:
                continue
            for option in field.get("_invalid_options", []):
                error("INVALID_NUMBER_OPTION", f"Field '{form_id}.{field.get('id', '?')}' has invalid {option}")
            for unknown in field.get("_unknown_flags", []):
                error("UNKNOWN_FIELD_FLAG", f"Field '{form_id}.{field.get('id', '?')}' has unknown compact flag '{unknown}'")
            fid = field.get("id")
            if not fid:
                continue
            if fid in ids:
                error("DUPLICATE_FIELD_ID", f"Form '{form_id}' contains duplicate field '{fid}'")
            ids.add(fid)
        field_sets[form_id] = ids
        for field in fields or []:
            if field.get("type") in {"SECTION", "SPACER"}:
                continue
            for option in ("span", "rows", "length"):
                if option in field and (not isinstance(field[option], int) or field[option] <= 0):
                    error("INVALID_NUMBER_OPTION", f"Field '{form_id}.{field.get('id', '?')}' has invalid {option}")
            if "readonly_below" in field and (
                    not isinstance(field["readonly_below"], int)
                    or field["readonly_below"] < 0):
                error("INVALID_PERMISSION_LEVEL", f"Field '{form_id}.{field.get('id', '?')}' has invalid readonly_below level")
            if field.get("type") not in ALLOWED_TYPES:
                error("INVALID_FIELD_TYPE", f"Field '{form_id}.{field.get('id', '?')}' has invalid type '{field.get('type')}'")
            if field.get("type") == "ENUM" and not field.get("enum_list"):
                error("INVALID_ENUM", f"Field '{form_id}.{field.get('id', '?')}' has no enum values")

    for grid_id, grid in grids.items():
        parent = grid.get("parent")
        if parent not in forms:
            error("INVALID_GRID_PARENT", f"Grid '{grid_id}' references missing form '{parent}'")
        columns = grid.get("columns") or []
        seen = set()
        for col in columns:
            cid = col.get("id")
            if cid in seen:
                error("DUPLICATE_COLUMN_ID", f"Grid '{grid_id}' contains duplicate column '{cid}'")
            seen.add(cid)
            if col.get("type") not in ALLOWED_TYPES:
                error("INVALID_COLUMN_TYPE", f"Grid '{grid_id}' column '{cid}' has invalid type '{col.get('type')}'")
            if "width" in col and (not isinstance(col["width"], int) or col["width"] <= 0):
                error("INVALID_NUMBER_OPTION", f"Grid '{grid_id}' column '{cid}' has invalid width")
            for unknown in col.get("_unknown_flags", []):
                error("UNKNOWN_FIELD_FLAG", f"Column '{grid_id}.{cid}' has unknown compact flag '{unknown}'")
            for option in col.get("_invalid_options", []):
                error("INVALID_NUMBER_OPTION", f"Grid '{grid_id}' column '{cid}' has invalid {option}")
            if "readonly_below" in col and (
                    not isinstance(col["readonly_below"], int)
                    or col["readonly_below"] < 0):
                error("INVALID_PERMISSION_LEVEL", f"Grid '{grid_id}' column '{cid}' has invalid readonly_below level")

    for layout_id, layout in layouts.items():
        for ref in (layout.get("fields") or {}):
            if ref not in forms and ref not in grids:
                error("INVALID_LAYOUT_REFERENCE", f"Layout '{layout_id}' references missing form/grid '{ref}'")
        tabs = layout.get("tabs") or {}
        for item in tabs.get("items", []):
            if item.get("grid") not in grids:
                error("INVALID_LAYOUT_REFERENCE", f"Layout '{layout_id}' tab references missing grid '{item.get('grid')}'")
        for button in (layout.get("buttons") or {}).values():
            if button.get("action") not in actions:
                error("INVALID_ACTION_REFERENCE", f"Layout '{layout_id}' references missing action '{button.get('action')}'")

    for action_id, action in actions.items():
        target = action.get("target")
        if action.get("type") == "goto" and target not in layouts:
            error("INVALID_ACTION_TARGET", f"Action '{action_id}' targets missing layout '{target}'")
        if action.get("type") == "report" and target not in reports:
            error("INVALID_ACTION_TARGET", f"Action '{action_id}' targets missing report '{target}'")

    def menu_items(items):
        for item in items or []:
            if item.get("section") or item.get("separator"):
                continue
            action = item.get("action")
            if action not in actions:
                error("INVALID_MENU_TARGET", f"Menu item '{item.get('label', '')}' references missing action '{action}'")
    for item_group in (app.get("top_menus") or []):
        menu_items(item_group.get("items"))
    for layout in layouts.values():
        menu_items(layout.get("center_menu"))
        for group in layout.get("top_menus") or []:
            menu_items(group.get("items"))

    for layout_id, layout in layouts.items():
        workflow = layout.get("workflow") or {}
        scripts = layout.get("scripts") or {}
        for transition in workflow.get("transitions", []):
            script = transition.get("script") or transition.get("action")
            if script and script not in scripts and script not in (app.get("subroutines") or {}):
                error("INVALID_WORKFLOW_SCRIPT", f"Workflow in '{layout_id}' references missing script '{script}'")
        for item in workflow.get("actions", []):
            if item.get("script") and item["script"] not in scripts and item["script"] not in (app.get("subroutines") or {}):
                error("INVALID_WORKFLOW_SCRIPT", f"Workflow in '{layout_id}' references missing script '{item['script']}'")

    for report_id, report in reports.items():
        source = report.get("source")
        if not source:
            error("MISSING_REPORT_SOURCE", f"Report '{report_id}' has no source")
        source_fields = set()
        for form_id, fields in forms.items():
            if form_id == source or form_id.removesuffix("_form") == source:
                source_fields = field_sets.get(form_id, set())
                break
        if source_fields:
            for col in report.get("columns") or []:
                cid = col.get("id")
                if cid not in source_fields and not col.get("formula"):
                    error("INVALID_REPORT_COLUMN", f"Report '{report_id}' column '{cid}' is not in source '{source}'")
    result.summary = {"forms": len(forms), "grids": len(grids), "layouts": len(layouts), "actions": len(actions), "reports": len(reports)}
    return result


class SemanticValidator:
    """Small reusable facade for validating a parsed app definition."""

    def validate(self, app: Dict[str, Any]) -> ValidationResult:
        return validate_app_definition(app)


