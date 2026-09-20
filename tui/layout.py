import curses
from curses.textpad import rectangle
from typing import Dict, List, Any, Optional
from .widgets import (BaseWidget, TextInput, MultiLineInput, Checkbox,
                      NumericInput, DateInput, Combobox, LookupField,
                      SectionSeparator, OptionDropdown, SearchDialog)
from .menu import MenuManager
from .themes import (CLR_FORM_BORDER, CLR_FORM_LABEL, CLR_FORM_SECTION,
                     CLR_STATUS_BAR, CLR_HEADER)
from .statusline import StatusLine
from .udf_panel import UDFPanel

# Try to import the advanced cxgrid; fall back to basic EditableGrid
try:
    from .cxgrid import Grid as CxGrid, GridColumn
    HAS_CXGRID = True
except ImportError:
    HAS_CXGRID = False

from .full_grid import EditableGrid

CTRL_U_KEY = 21

try:
    from .tree_grid import TreeGrid, TreeColDef
    HAS_TREEGRID = True
except ImportError:
    HAS_TREEGRID = False


def _safe_float(v):
    """Convert a value to float, returning 0.0 on failure."""
    if v is None or v == '':
        return 0.0
    try:
        return float(str(v).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0


def _SUM(lst, field):
    """SUM(lines, field_name) — sum a field across a list of row dicts."""
    fname = str(field)
    if not isinstance(lst, list):
        return 0.0
    return sum(_safe_float(r.get(fname, 0)) for r in lst if isinstance(r, dict))


def _COUNT(lst):
    """COUNT(lines) — number of non-empty rows."""
    if not isinstance(lst, list):
        return 0
    return sum(1 for r in lst if isinstance(r, dict)
               and any(str(v).strip() for v in r.values()))


def _IF(cond, true_val, false_val=0):
    return true_val if cond else false_val


def eval_computed_formula(expr, header, lines, detail_col_names=None):
    """Evaluate a COMPUTED formula that may reference lines (detail rows)."""
    if not expr:
        return ''
    expr = expr.lstrip('=').strip()
    ctx = {'__builtins__': {}, 'abs': abs, 'min': min, 'max': max, 'round': round,
           'SUM': _SUM, 'COUNT': _COUNT, 'IF': _IF,
           'lines': lines if lines is not None else []}
    # Expose header values
    for k, v in header.items():
        ctx[k] = _safe_float(v) if isinstance(v, (int, float, str)) and k != '__builtins__' else v
    # Expose detail column names as strings for SUM(lines, col_name) syntax
    for name in (detail_col_names or []):
        if name in ctx and f'{name}_hdr' not in ctx:
            ctx[f'{name}_hdr'] = ctx.get(name)
        ctx[name] = name
    try:
        result = eval(expr, ctx)  # noqa: S307
    except Exception:
        return ''
    if isinstance(result, (int, float)):
        if result == 0:
            return ""
        if isinstance(result, float) and result == int(result):
            return f"{int(result):,}"
        if isinstance(result, float):
            # Keep meaningful decimals, strip trailing zeros
            s = f"{result:,.2f}".rstrip("0").rstrip(".")
            return s
        return f"{result:,}"
    return str(result)


def _eval_highlight_condition(expr, row):
    """Evaluate a condition expression against a row dict. Returns bool."""
    if not expr:
        return False
    expr = str(expr).lstrip('=').strip()
    ctx = {
        '__builtins__': {},
        'abs': abs, 'min': min, 'max': max, 'round': round,
        'str': str, 'len': len,
        'ISBLANK': lambda value: value is None or str(value).strip() == '',
        'ISNULL': lambda value: value is None or value == '',
    }
    for k, v in row.items():
        if k.startswith('_'):
            continue
        sv = str(v) if v is not None else ''
        fv = _safe_float(v)
        # If it looks numeric, expose as float; otherwise keep as string
        if fv != 0.0 or sv.strip() in ('0', '0.0', '0.00'):
            ctx[k] = fv
        else:
            ctx[k] = sv
        ctx[f'{k}_str'] = sv
    try:
        return bool(eval(expr, ctx))  # noqa: S307
    except Exception:
        return False


def _uom_needs_conversion(uom, pack_uom, pack_qty) -> bool:
    pack_num = _safe_float(pack_qty)
    if pack_num <= 1:
        return False
    u = str(uom or '').strip().upper()
    p = str(pack_uom or '').strip().upper()
    return bool(u and p and u == p)


def _base_qty(qty, uom, pack_uom, pack_qty) -> float:
    qty_num = _safe_float(qty)
    pack_num = _safe_float(pack_qty)
    if _uom_needs_conversion(uom, pack_uom, pack_num):
        return qty_num * pack_num
    return qty_num


def _display_qty(base_qty, uom, pack_uom, pack_qty) -> float:
    base_num = _safe_float(base_qty)
    pack_num = _safe_float(pack_qty)
    if _uom_needs_conversion(uom, pack_uom, pack_num):
        return base_num / pack_num if pack_num else 0.0
    return base_num


class LayoutRenderer:
    def __init__(self, stdscr, form_defs: Dict[str, Any], grid_defs: Dict[str, Any],
                 layout_def: Dict, lookup_data: Dict[str, List],
                 grid_data: Dict[str, List] = None, edit_context: Dict = None,
                 column_configs: Dict = None, sort_configs: Dict = None,
                 db=None, app_grids: Dict = None, default_resolver=None,
                 user_context: Dict = None):
        self.stdscr = stdscr
        self.form_defs = form_defs
        self.grid_defs = grid_defs
        self.layout_def = layout_def
        self.lookup_data = lookup_data
        self.grid_data = grid_data or {}
        self.edit_context = edit_context
        self.column_configs = column_configs or {}
        self.sort_configs = sort_configs or {}
        self.db = db
        self.app_grids = app_grids or {}
        self._default_resolver = default_resolver
        self.user_context = dict(user_context or {})
        try:
            self.user_level = max(0, int(self.user_context.get("level", 0)))
        except (TypeError, ValueError):
            self.user_level = 0
        self.widgets: List[BaseWidget] = []
        self.cxgrids: List[CxGrid] = [] if HAS_CXGRID else []
        self.basic_grids: List[EditableGrid] = []
        self.buttons: List[Dict] = []
        self.sections: List[SectionSeparator] = []
        self.tabs_def = None
        self.active_tab = 0
        self.tab_grids: List = []  # grid per tab
        self.values: Dict[str, Any] = {}
        self.menu: Optional[MenuManager] = None
        self.status_line = StatusLine(stdscr)
        self._figlet_h = self._calc_figlet_height()
        self._item_master_syncing = False
        self._init_menus()
        self._init_widgets()
        # UDF panel — init before grids so grids know available width
        self.udf_panel = None
        self._udf_focused = False
        self._init_udf()
        self._init_grids()
        self._init_tabs()
        self._init_buttons()
        # COMPUTED field definitions
        self._computed_defs = self.layout_def.get("computed", [])
        # Load edit data into widgets if editing an existing record
        if self.edit_context and self.edit_context.get("data"):
            self.load_values(self.edit_context["data"])
            self._recompute_header()

    def _confirm_action(self, message: str, title: str = "Confirm") -> bool:
        """Show a centered Y/N confirmation dialog.

        Returns True only for an explicit Y keypress. Any rendering problem or
        other key is treated as cancel to keep destructive actions safe.
        """
        try:
            my, mx = self.stdscr.getmaxyx()
            bw = min(max(len(message) + 6, len(title) + 6), max(20, mx - 4))
            bh = 5
            py = max(0, (my - bh) // 2)
            px = max(0, (mx - bw) // 2)
            cwin = curses.newwin(bh, bw, py, px)
            cwin.keypad(True)
            cwin.border()
            cwin.addstr(0, 2, f" {title} ", curses.A_BOLD)
            cwin.addstr(2, 2, message[:bw - 4], curses.A_BOLD | curses.A_REVERSE)
            cwin.refresh()
            return cwin.getch() in (ord('y'), ord('Y'))
        except curses.error:
            return False

    def _init_menus(self):
        rect = self.layout_def["position"]
        self.menu = MenuManager(self.stdscr, rect["width"], rect["height"])

        self.menu.title = self.layout_def.get("title", "")
        if "top_menus" in self.layout_def:
            self.menu.define_top_pulldown(self.layout_def["top_menus"])
        if "center_menu" in self.layout_def:
            self.menu.define_center_menu({"items": self.layout_def.get("center_menu", [])})

    def _init_widgets(self):
        """Create widget instances from form field definitions placed in this layout.

        Supports multi-column layout via COLS=N on FORM placement and
        SPAN N on individual fields.
        """
        for ref_id, placement in self.layout_def.get("fields", {}).items():
            form_def = self.form_defs.get(ref_id)
            if not form_def or not isinstance(form_def, list):
                continue
            base_y = placement.get("y", 0)
            base_x = placement.get("x", 0)
            num_cols = placement.get("cols", 1)
            # Use terminal width so fields scale to actual screen
            if self.stdscr:
                _, layout_w = self.stdscr.getmaxyx()
            else:
                rect = self.layout_def.get("position", {})
                layout_w = rect.get("width", 80)

            # Column width calculation
            total_w = layout_w - base_x * 2 - 2
            col_w = total_w // num_cols if num_cols > 1 else total_w

            # Calculate max label width per column (use all non-section/spacer fields)
            real_fields = [f for f in form_def if f.get("type") not in ("SECTION", "SPACER")]
            max_label = max((len(f.get("id", "")) for f in real_fields), default=0)
            label_w = max_label + 2  # label + ": "

            row_offset = 0
            cur_col = 0  # current column position (0-based)

            for i, field in enumerate(form_def):
                ftype = field.get("type", "STRING")
                span = field.get("span", 1)

                # SPACER — blank line in DSL becomes empty row
                if ftype == "SPACER":
                    if cur_col > 0:
                        row_offset += 1
                        cur_col = 0
                    row_offset += 1
                    continue

                # SECTION separator — always full width, starts new row
                if ftype == "SECTION":
                    if cur_col > 0:
                        row_offset += 1
                        cur_col = 0
                    y = base_y + row_offset
                    sep_width = total_w
                    sep = SectionSeparator(y, base_x, sep_width, field.get("label", ""))
                    self.sections.append(sep)
                    row_offset += 1
                    continue

                # If span > remaining columns, wrap to next row
                if cur_col + span > num_cols and cur_col > 0:
                    row_offset += 1
                    cur_col = 0

                fid = field.get("id", "")
                field_len = field.get("length", 20)
                y = base_y + row_offset
                lx = base_x + cur_col * col_w
                wx = lx + label_w

                # Constrain field width to available space in spanned columns
                avail_w = col_w * span - label_w - 1
                width = min(field_len, max(avail_w, 5))

                if ftype == "BOOL":
                    widget = Checkbox(y, wx, fid, value=False)
                elif ftype in ("INT", "FLOAT"):
                    decimals = 0 if ftype == "INT" else 2
                    subtype = field.get("subtype", "")
                    if subtype == "percentage":
                        decimals = 4  # store as decimal (0.08 = 8%)
                    default = field.get("default", 0)
                    if self._default_resolver:
                        default = self._default_resolver(default)
                    try:
                        default = float(default)
                    except (ValueError, TypeError):
                        default = 0
                    widget = NumericInput(y, wx, width, value=default, decimals=decimals)
                    if subtype:
                        widget._subtype = subtype
                elif ftype == "DATE" or ftype == "DATETIME":
                    widget = DateInput(y, wx, 12)
                elif ftype == "ENUM" and "enum_list" in field:
                    widget = Combobox(y, wx, width, field["enum_list"],
                                     field_name=field.get("id", ""))
                elif "lookup" in field:
                    ldata = self.lookup_data.get(field["lookup"], [])
                    # Apply lookup filters (e.g. filter:status=Posted)
                    if "lookup_filter" in field:
                        flt = field["lookup_filter"]
                        ldata = [r for r in ldata
                                 if all(str(r.get(fk, "")) == fv
                                        for fk, fv in flt.items())]
                    # Filter for pending delivery when copydetail is present
                    if "copydetail" in field and self.db:
                        import json as _json
                        src_detail = field["copydetail"]  # e.g. "po_line"
                        link_field = field.get("id", "")  # e.g. "po_number"
                        try:
                            ldata = self._filter_pending_delivery(
                                ldata, src_detail, link_field)
                        except Exception:
                            pass
                    vf, df = "code", "name"
                    if ldata:
                        sample = ldata[0]
                        skip = {"rowid", "_parent_rowid"}
                        fields = [k for k in sample if k not in skip
                                  and not k.startswith("_")]
                        # Get visible LIST columns for this lookup table
                        lookup_grid_id = field["lookup"] + "_grid"
                        list_col_ids = []
                        _lgrid = self.app_grids.get(lookup_grid_id)
                        if _lgrid:
                            list_col_ids = [c["id"] for c in _lgrid.get("columns", [])
                                            if not c.get("hidden")]
                        # Best match: use the widget's own field_id as value_field
                        # e.g. po_number field looking up "po" → value_field = po_number
                        if fid in sample:
                            vf = fid
                        elif list_col_ids and list_col_ids[0] in sample:
                            # Use first LIST column as value_field (most reliable)
                            vf = list_col_ids[0]
                        else:
                            for try_k in ("code", "id"):
                                if try_k in sample: vf = try_k; break
                            else:
                                for f in fields:
                                    if f.endswith(("_id", "_code", "_no")):
                                        vf = f; break
                                else:
                                    if fields: vf = fields[0]
                        for try_n in ("name", "description", "full_name", "title"):
                            if try_n in sample: df = try_n; break
                        else:
                            for f in fields:
                                if f.endswith("_name"):
                                    df = f; break
                            else:
                                if len(fields) > 1: df = fields[1]
                    # Get LIST column widths from the lookup table's grid definition
                    lookup_grid_id = field["lookup"] + "_grid"
                    lcols = None
                    if lookup_grid_id in self.app_grids:
                        all_cols = self.app_grids[lookup_grid_id].get("columns")
                        if all_cols:
                            include = field.get("lookup_include")
                            exclude = field.get("lookup_exclude")
                            lcols = [c for c in all_cols if not c.get("hidden")]
                            if include:
                                lcols = [c for c in lcols if c["id"] in include]
                            if exclude:
                                lcols = [c for c in lcols if c["id"] not in exclude]
                    lookup_w = min(width, avail_w)
                    widget = LookupField(y, wx, lookup_w, ldata,
                                         display_field=df, value_field=vf,
                                         list_columns=lcols)
                    # Store full data backup for CASCADE re-filtering
                    widget._all_lookup_data = list(ldata)
                    # Attach cascade metadata: "source_widget->filter_field"
                    cascade = field.get("lookup_cascade")
                    if cascade:
                        widget._cascade = cascade
                    # Attach explicit fill mapping if lookupfill is a dict
                    lfill = field.get("lookupfill")
                    if isinstance(lfill, dict):
                        widget._fill_map = lfill
                else:
                    default_val = field.get("default", "")
                    if self._default_resolver:
                        default_val = self._default_resolver(default_val)
                    field_rows = field.get("rows", 0)
                    if field_rows > 1:
                        widget = MultiLineInput(y, wx, width,
                                                rows=field_rows,
                                                value=str(default_val))
                    else:
                        widget = TextInput(y, wx, width, value=str(default_val))
                    subtype = field.get("subtype", "")
                    if subtype:
                        widget._subtype = subtype

                # Apply readonly flag to any widget
                if self._is_readonly_for_level(field, self.user_level):
                    widget._readonly = True

                widget._field_id = fid
                # Keep the DSL field ID for storage, but display a readable
                # label in the form without internal underscore separators.
                widget._label = fid.replace("_", " ").title()
                subtype = field.get("subtype", "")
                if subtype == "currency":
                    widget._label = widget._label + " ($)"
                elif subtype == "percentage":
                    widget._label = widget._label + " (%)"
                elif subtype == "email":
                    widget._label = widget._label + " (@)"
                widget._label_x = lx
                self.widgets.append(widget)

                # Multi-line fields consume extra rows in form layout
                extra_rows = getattr(widget, 'height', 1) - 1
                cur_col += span
                if cur_col >= num_cols:
                    cur_col = 0
                    row_offset += 1
                row_offset += extra_rows
        self._wire_item_master_auto_conversion()
        self._wire_formula_fields()
        self._wire_cascade_lookups()

    def _widget_map(self) -> Dict[str, BaseWidget]:
        return {
            getattr(widget, '_field_id', ''): widget
            for widget in self.widgets
            if getattr(widget, '_field_id', '')
        }

    def _wire_formula_fields(self):
        """Mark formula fields readonly and set up re-evaluation on sibling changes."""
        form_fields = {}
        for ref_id, placement in self.layout_def.get("fields", {}).items():
            form_def = self.form_defs.get(ref_id)
            if not form_def or not isinstance(form_def, list):
                continue
            for f in form_def:
                if f.get("formula"):
                    form_fields[f["id"]] = f["formula"]

        if not form_fields:
            return

        widget_map = self._widget_map()
        formula_widgets = {fid: widget_map[fid] for fid in form_fields if fid in widget_map}

        # Mark formula fields readonly
        for w in formula_widgets.values():
            w._readonly = True

        # Store formulas for re-evaluation
        self._field_formulas = form_fields

        # Wire on_change on non-formula fields to trigger re-evaluation
        non_formula = {fid: w for fid, w in widget_map.items() if fid not in form_fields}

        def make_reeval(formula_wids, fmls, wmap):
            def reeval(val):
                # Build current header values from all widgets
                header = {fid: w.value for fid, w in wmap.items()}
                for fid, expr in fmls.items():
                    try:
                        ctx = {'__builtins__': {}, 'abs': abs, 'min': min, 'max': max, 'round': round}
                        for k, v in header.items():
                            try:
                                ctx[k] = float(v) if v not in (None, '') else 0.0
                            except (ValueError, TypeError):
                                ctx[k] = 0.0
                        result = eval(expr, ctx)  # noqa: S307
                        if fid in formula_wids:
                            formula_wids[fid].load(result)
                    except Exception:
                        pass
            return reeval

        reeval_fn = make_reeval(formula_widgets, form_fields, widget_map)
        for w in non_formula.values():
            old_on_change = w.on_change

            def make_chain(old, new):
                def chained(val):
                    if old:
                        old(val)
                    new(val)
                return chained

            w.on_change = make_chain(old_on_change, reeval_fn)

    def _load_widget_value(self, widget: Optional[BaseWidget], value: Any):
        if not widget:
            return
        if hasattr(widget, 'load'):
            widget.load(value)
        else:
            widget.set_value(value)

    def _wire_cascade_lookups(self):
        """Wire CASCADE lookup filtering: when source widget changes, filter target lookup data.

        DSL syntax: lookup:city CASCADE state->state_code
        Meaning: filter city lookup where city.state_code == value of 'state' widget.
        """
        widget_map = self._widget_map()

        for w in self.widgets:
            cascade = getattr(w, '_cascade', None)
            if not cascade or not isinstance(w, LookupField):
                continue
            # Parse "source_field->filter_field_in_lookup"
            if '->' not in cascade:
                continue
            source_id, filter_field = cascade.split('->', 1)
            source_id = source_id.strip()
            filter_field = filter_field.strip()
            source_widget = widget_map.get(source_id)
            if not source_widget:
                continue

            target_widget = w  # the LookupField with CASCADE

            def make_filter(target, ff):
                def _apply_cascade(val):
                    all_data = getattr(target, '_all_lookup_data', target.lookup_data)
                    if val:
                        filtered = [r for r in all_data if str(r.get(ff, "")) == str(val)]
                    else:
                        filtered = list(all_data)
                    target.lookup_data = filtered
                    # Clear current value if it no longer matches
                    if target.value and not any(
                        r.get(target.value_field) == target.value for r in filtered
                    ):
                        target.set_value("")
                return _apply_cascade

            old_on_change = source_widget.on_change
            filter_fn = make_filter(target_widget, filter_field)

            def make_chain(old, new):
                def chained(val):
                    if old:
                        old(val)
                    new(val)
                return chained

            source_widget.on_change = make_chain(old_on_change, filter_fn)

    def _is_item_master_layout(self) -> bool:
        widget_ids = set(self._widget_map())
        required = {'part_no', 'uom', 'pack_qty', 'unit_cost', 'pack_cost'}
        return required.issubset(widget_ids)

    def _wire_item_master_auto_conversion(self):
        if not self._is_item_master_layout():
            return

        watched_fields = (
            'uom', 'pack_uom', 'pack_qty', 'recv_uom', 'issue_uom',
            'unit_cost', 'pack_cost', 'unit_price', 'pack_price',
        )

        for field_id, widget in self._widget_map().items():
            if field_id not in watched_fields:
                continue

            def _handler(_value, changed_field=field_id):
                self._sync_item_master_fields(changed_field)

            widget.on_change = _handler

        self._sync_item_master_fields()

    def _sync_item_master_fields(self, changed_field: Optional[str] = None):
        if self._item_master_syncing or not self._is_item_master_layout():
            return

        widget_map = self._widget_map()

        def _val(field_id: str):
            widget = widget_map.get(field_id)
            return widget.value if widget else None

        def _is_blank(field_id: str) -> bool:
            value = _val(field_id)
            return value is None or str(value).strip() == ''

        def _set(field_id: str, value: Any):
            widget = widget_map.get(field_id)
            if not widget:
                return
            current = widget.value
            if value is None and (current is None or str(current).strip() == ''):
                return
            if isinstance(value, (int, float)) or isinstance(current, (int, float)):
                if abs(_safe_float(current) - _safe_float(value)) < 0.0001:
                    return
            elif str(current or '') == str(value or ''):
                return
            self._load_widget_value(widget, value)

        base_uom = str(_val('uom') or '').strip().upper()
        pack_uom = str(_val('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(_val('pack_qty') or 0)
        factor = pack_qty if pack_qty > 0 else 1.0

        try:
            self._item_master_syncing = True

            recv_uom = str(_val('recv_uom') or '').strip().upper()
            issue_uom = str(_val('issue_uom') or '').strip().upper()
            prefer_pack_recv = bool(
                pack_qty > 1 and pack_uom and base_uom and pack_uom != base_uom
            )

            if base_uom:
                if prefer_pack_recv and (not recv_uom or recv_uom == base_uom):
                    _set('recv_uom', pack_uom)
                elif not recv_uom:
                    _set('recv_uom', base_uom)
                elif not prefer_pack_recv and recv_uom == pack_uom:
                    _set('recv_uom', base_uom)

                if not issue_uom:
                    _set('issue_uom', base_uom)

            pair_rules = {
                'unit_cost': ('pack_cost', factor, 'mul'),
                'pack_cost': ('unit_cost', factor, 'div'),
                'unit_price': ('pack_price', factor, 'mul'),
                'pack_price': ('unit_price', factor, 'div'),
            }

            if changed_field in pair_rules:
                target_field, divisor, mode = pair_rules[changed_field]
                if _is_blank(changed_field):
                    _set(target_field, None)
                else:
                    source_num = _safe_float(_val(changed_field))
                    computed = source_num * divisor if mode == 'mul' else (
                        source_num / divisor if divisor else 0.0
                    )
                    _set(target_field, round(computed, 4))
            else:
                base_pairs = (
                    ('unit_cost', 'pack_cost'),
                    ('unit_price', 'pack_price'),
                )
                for base_field, pack_field in base_pairs:
                    if not _is_blank(base_field):
                        _set(pack_field, round(_safe_float(_val(base_field)) * factor, 4))
                    elif not _is_blank(pack_field):
                        _set(
                            base_field,
                            round(_safe_float(_val(pack_field)) / factor if factor else 0.0, 4),
                        )
        finally:
            self._item_master_syncing = False

    @staticmethod
    def _is_readonly_for_level(definition: Dict, user_level: int = 0) -> bool:
        """Resolve static and user-level read-only rules for a field/column."""
        if definition.get("readonly"):
            return True
        threshold = definition.get("readonly_below")
        if threshold is None:
            return False
        try:
            return int(user_level) < int(threshold)
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _build_col_flags(col: Dict, readonly: bool = False,
                         user_level: int = 0) -> list:
        """Build cxgrid GridColumn flags from a parsed DSL column dict."""
        col_type = col.get("type", "STRING")
        flags = []
        if col_type in ("INT", "FLOAT"):
            flags.append("num")
        if col_type in ("DATE", "DATETIME"):
            flags.append("date")
        if readonly or LayoutRenderer._is_readonly_for_level(col, user_level):
            flags.append("ro")
        if col.get("lookup"):
            flags.append(f"lookup:{col['lookup']}")
        if col.get("lookupfill"):
            lfill = col["lookupfill"]
            if isinstance(lfill, dict):
                # Encode dict as "lookupfill_map:src=>dest,src2=>dest2"
                pairs = ",".join(f"{s}=>{d}" for s, d in lfill.items())
                flags.append(f"lookupfill_map:{pairs}")
            else:
                flags.append(f"lookupfill:{lfill}")
        if col.get("formula"):
            flags.append(f"formula:{col['formula']}")
        if col.get("enum_list"):
            flags.append(f"dropdown:{','.join(col['enum_list'])}")
        return flags

    def _init_grids(self):
        """Create grid instances from grid definitions placed in this layout."""
        for ref_id, placement in self.layout_def.get("fields", {}).items():
            grid_def = self.grid_defs.get(ref_id)
            if not grid_def:
                continue

            data = self.grid_data.get(ref_id, [])
            grid_y = placement.get("y", 0) + self._figlet_h
            grid_x = placement.get("x", 0)
            grid_h = placement.get("height", 10)
            if self._figlet_h:
                grid_h = max(4, grid_h - self._figlet_h)

            if HAS_CXGRID:
                columns = []
                for col in grid_def.get("columns", []):
                    flags = self._build_col_flags(
                        col, readonly=True, user_level=self.user_level)
                    gc = GridColumn(
                        name=col["id"],
                        label=col["id"].replace("_", " ").title(),
                        width=col.get("width", 12),
                        flags=flags
                    )
                    if col.get("hidden"):
                        gc.visible = False
                    columns.append(gc)
                if columns:
                    # Store DSL widths before any config override
                    for c in columns:
                        c._dsl_width = c.width
                    # Apply saved column config (visibility, order — not width)
                    saved = self.column_configs.get(ref_id)
                    if saved:
                        col_map = {c.name: c for c in columns}
                        reordered = []
                        for name, visible, width in saved:
                            if name in col_map:
                                c = col_map.pop(name)
                                c.visible = visible
                                # Keep DSL width for stretch calculation;
                                # stretch will recalculate to fill terminal
                                reordered.append(c)
                        # Append any new columns not in saved config
                        for c in columns:
                            if c.name in col_map:
                                reordered.append(c)
                        columns = reordered
                    show_totals = grid_def.get("show_totals", False)
                    # Limit grid width when UDF panel is present
                    gw = 0
                    if self.udf_panel:
                        gw = self.udf_panel.panel_x - grid_x - 1
                    grid = CxGrid(
                        self.stdscr, columns, data,
                        start_y=grid_y, start_x=grid_x,
                        show_totals=show_totals,
                        max_width=gw
                    )
                    grid._grid_id = ref_id  # track for saving config
                    # Listing grids: restore saved sort or default to first column desc
                    if grid.row_select_mode and columns:
                        saved_sort = self.sort_configs.get(ref_id)
                        if saved_sort:
                            col_name, ascending = saved_sort
                            grid.set_sort(col_name, ascending)
                        else:
                            grid.sort_column = 0
                            grid.sort_ascending = False
                            grid.columns[0].sort_order = "desc"
                            grid._rebuild_view()
                    # Wire conditional row highlighting
                    hl_rules = self.layout_def.get("highlights", [])
                    if hl_rules and grid.row_select_mode:
                        self._wire_highlights(grid, hl_rules)
                    self.cxgrids.append(grid)
            else:
                columns = []
                for col in grid_def.get("columns", []):
                    columns.append({
                        "id": col["id"],
                        "label": col["id"].replace("_", " ").title(),
                        "width": col.get("width", 10)
                    })
                if columns:
                    grid = EditableGrid(
                        y=grid_y, x=grid_x,
                        width=sum(c["width"] for c in columns),
                        height=grid_h,
                        columns=columns, data=data
                    )
                    self.basic_grids.append(grid)

    def _init_buttons(self):
        """Collect button definitions for rendering."""
        for label, btn_def in self.layout_def.get("buttons", {}).items():
            self.buttons.append({
                "label": label,
                "action": btn_def["action"],
                "y": btn_def["y"],
                "x": btn_def["x"]
            })

    def _init_udf(self):
        """Initialize UDF panel if layout has udf_schema."""
        schema_name = self.layout_def.get("udf_schema")
        if not schema_name:
            return
        try:
            max_y, max_x = self.stdscr.getmaxyx()
        except curses.error:
            return
        panel_w = min(40, max(26, max_x // 3))
        panel_x = max_x - panel_w - 1
        panel_y = 1
        panel_h = max_y - 3  # leave room for status bar
        self.udf_panel = UDFPanel(self.stdscr, schema_name,
                                  panel_x, panel_y, panel_w, panel_h)

    def _init_tabs(self):
        """Create tab panel grids from TABS definition."""
        tabs = self.layout_def.get("tabs")
        if not tabs or not tabs.get("items"):
            return
        self.tabs_def = tabs
        for tab_item in tabs["items"]:
            grid_id = tab_item["grid"]
            grid_def = self.grid_defs.get(grid_id)
            data = self.grid_data.get(grid_id, [])
            tab_y = tabs["y"] + 2  # +2 for tab header row + separator
            tab_x = tabs["x"]
            tab_h = tabs["height"] - 2

            if grid_def and grid_def.get("tree_detail") and HAS_TREEGRID:
                # TreeGrid for hierarchical TREE_DETAIL blocks
                tree_cols = []
                is_first = True
                for col in grid_def.get("columns", []):
                    col_type = col.get("type", "STRING")
                    tc = TreeColDef(
                        name=col["id"],
                        label=col["id"].replace("_", " ").title(),
                        width=col.get("width", 12),
                        field_type=col_type,
                        readonly=self._is_readonly_for_level(
                            col, self.user_level),
                        align='right' if col_type in ("INT", "FLOAT") else 'left',
                        lookup=col.get("lookup"),
                        lookup_fill=col.get("lookupfill"),
                        formula=col.get("formula"),
                        is_tree_col=is_first,
                        subtotal=col.get("subtotal", False),
                        noshow_on_parent=col.get("noshow_on_parent", False),
                        enum_list=col.get("enum_list"),
                    )
                    is_first = False
                    tree_cols.append(tc)
                if tree_cols:
                    tg = TreeGrid(
                        self.stdscr, tree_cols,
                        children_key=grid_def.get("children_key", "children"),
                        tree_label_field=grid_def.get("tree_label_field", ""),
                        y0=tab_y, x0=tab_x,
                        visible_rows=tab_h - 2,
                        name=grid_id,
                        lookup_handler=self._make_lookup_handler(),
                    )
                    tg._grid_id = grid_id
                    tg._is_tree = True
                    # Load tree data (nested JSON)
                    if isinstance(data, list):
                        tg.load(data)
                    else:
                        tg.load([])
                    self.tab_grids.append(tg)
                else:
                    self.tab_grids.append(None)
            elif grid_def and HAS_CXGRID:
                columns = []
                for col in grid_def.get("columns", []):
                    flags = self._build_col_flags(
                        col, readonly=False, user_level=self.user_level)
                    columns.append(GridColumn(
                        name=col["id"],
                        label=col["id"].replace("_", " ").title(),
                        width=col.get("width", 12),
                        flags=flags
                    ))
                if columns:
                    # Wire lookup list columns for multi-column picker
                    self._wire_lookup_list_cols(columns)
                    # Apply saved column config
                    saved = self.column_configs.get(grid_id)
                    if saved:
                        col_map = {c.name: c for c in columns}
                        reordered = []
                        for name, visible, width in saved:
                            if name in col_map:
                                c = col_map.pop(name)
                                c.visible = visible
                                c.width = width
                                reordered.append(c)
                        for c in columns:
                            if c.name in col_map:
                                reordered.append(c)
                        columns = reordered
                    show_totals = grid_def.get("show_totals", False)
                    # Limit grid width when UDF panel is present
                    tgw = 0
                    if self.udf_panel:
                        tgw = self.udf_panel.panel_x - tab_x - 1
                    grid = CxGrid(self.stdscr, columns, data,
                                  start_y=tab_y, start_x=tab_x,
                                  show_totals=show_totals,
                                  max_height=tab_h,
                                  max_width=tgw)
                    grid._grid_id = grid_id
                    grid.lookup_handler = self._make_lookup_handler()
                    grid.on_cell_edit = self._make_cell_edit_callback(grid)
                    grid.on_need_new_row = self._make_new_row_callback(grid)
                    grid.on_uom_toggle = self._make_uom_toggle_callback(grid)
                    # Store coedit column for auto-activation
                    coedit_col = tab_item.get("coedit")
                    if coedit_col:
                        grid._auto_coedit = coedit_col
                    self.tab_grids.append(grid)
                else:
                    self.tab_grids.append(None)
            elif grid_def:
                columns = []
                for col in grid_def.get("columns", []):
                    columns.append({
                        "id": col["id"],
                        "label": col["id"].replace("_", " ").title(),
                        "width": col.get("width", 10)
                    })
                if columns:
                    grid = EditableGrid(
                        y=tab_y, x=tab_x,
                        width=sum(c["width"] for c in columns),
                        height=tab_h, columns=columns, data=data
                    )
                    self.tab_grids.append(grid)
                else:
                    self.tab_grids.append(None)
            else:
                self.tab_grids.append(None)

    def _wire_lookup_list_cols(self, columns):
        """Attach lookup_list_cols to grid columns that have a lookup reference."""
        for col in columns:
            if col.lookup:
                lookup_grid_id = col.lookup + "_grid"
                grid_def = self.app_grids.get(lookup_grid_id)
                if grid_def and grid_def.get("columns"):
                    col.lookup_list_cols = [
                        {"id": c["id"],
                         "label": c["id"].replace("_", " ").title(),
                         "width": c.get("width", 12),
                         "numeric": c.get("type") in ("INT", "FLOAT")}
                        for c in grid_def["columns"]
                        if not c.get("hidden")
                    ]

    def _make_lookup_handler(self):
        """Create a lookup_handler function compatible with cxgrid.

        Signature: fn(lookup_name, val, list_all=False)
        - list_all=True  → list of (key, display) tuples
        - list_all=False → display text for a single key value
        """
        lookup_data = self.lookup_data

        def _extract_key_display(item):
            """Find the best key and display fields from a lookup row dict."""
            # Skip internal fields
            skip = {"rowid", "_parent_rowid"}
            fields = [k for k in item if k not in skip]
            # Try common key field names
            key = None
            for try_key in ("code", "id"):
                if try_key in item:
                    key = str(item[try_key]); break
            # Try fields ending with _id or _code or _no
            if key is None:
                for f in fields:
                    if f.endswith(("_id", "_code", "_no")):
                        key = str(item[f]); break
            # Fallback: first field
            if key is None and fields:
                key = str(item[fields[0]])
            if key is None:
                key = str(item.get("rowid", ""))
            # Try common display field names
            display = None
            for try_name in ("name", "description", "full_name", "title"):
                if try_name in item:
                    display = str(item[try_name]); break
            # Try fields ending with _name
            if display is None:
                for f in fields:
                    if f.endswith("_name"):
                        display = str(item[f]); break
            if display is None:
                # Second field if available
                display = str(item[fields[1]]) if len(fields) > 1 else key
            return key, display

        def handler(lookup_name, val, list_all=False, full_record=False):
            items = lookup_data.get(lookup_name, [])
            if list_all:
                if full_record:
                    # Return full record dicts for multi-column picker
                    return [dict(item) for item in items]
                result = []
                for item in items:
                    code, name = _extract_key_display(item)
                    result.append((code, name))
                return result
            else:
                if val is None:
                    return None
                val_str = str(val)
                for item in items:
                    code, name = _extract_key_display(item)
                    if code == val_str:
                        if full_record:
                            return dict(item)
                        return name
                return None

        return handler

    def _detail_uom_profile(self, grid, row: dict):
        grid_id = str(getattr(grid, '_grid_id', '') or '').lower()
        if any(name in grid_id for name in ('po_line', 'grn_line')):
            return ('recv', 'unit_cost', 'base_unit_cost', 'pack_cost')
        if any(name in grid_id for name in ('issue_line', 'qi_line', 'transfer_line')):
            return ('issue', 'unit_cost', 'base_unit_cost', 'pack_cost')
        if any(name in grid_id for name in ('so_line', 'do_line', 'pos_slip_line', 'wo_line')):
            return ('issue', 'unit_price', 'base_unit_price', 'pack_price')
        if 'pick_line' in grid_id:
            return ('issue', None, None, None)
        return None

    @staticmethod
    def _row_qty_fields(row: dict) -> list:
        return [field for field in (
            'quantity', 'order_qty', 'received_qty', 'issue_qty',
            'transfer_qty', 'ship_qty', 'pick_qty', 'qty',
        ) if field in row]

    def _apply_line_price_for_uom(self, grid, row: dict, target_uom=None) -> bool:
        if not isinstance(row, dict):
            return False
        profile = self._detail_uom_profile(grid, row)
        if not profile:
            return False
        _, price_field, base_price_field, pack_price_field = profile
        if not price_field:
            grid.compute_row(row)
            return True
        active_uom = str(target_uom or row.get('uom') or '').strip().upper()
        pack_uom = str(row.get('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(row.get('pack_qty') or 0)
        use_pack = active_uom == pack_uom and pack_qty > 1
        base_price = _safe_float(row.get(base_price_field, row.get(price_field, 0)))
        pack_price = _safe_float(row.get(pack_price_field, 0))
        if use_pack:
            row[price_field] = round(pack_price if pack_price > 0 else base_price * pack_qty, 4)
        else:
            row[price_field] = round(base_price, 4)
        grid.compute_row(row)
        return True

    def _apply_line_uom_defaults(self, grid, row: dict) -> bool:
        if not isinstance(row, dict):
            return False
        profile = self._detail_uom_profile(grid, row)
        if not profile:
            return False
        mode, price_field, base_price_field, pack_price_field = profile
        base_uom = str(row.get('base_uom') or row.get('uom') or '').strip().upper()
        pack_uom = str(row.get('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(row.get('pack_qty') or 0)
        if not base_uom:
            return False
        recv_uom = str(row.get('recv_uom') or '').strip().upper()
        issue_uom = str(row.get('issue_uom') or '').strip().upper()
        if mode == 'recv':
            target_uom = recv_uom or (pack_uom if pack_qty > 1 and pack_uom else base_uom)
        else:
            target_uom = issue_uom or base_uom
        if not target_uom:
            target_uom = base_uom
        row['uom'] = target_uom
        return self._apply_line_price_for_uom(grid, row, target_uom=target_uom)

    def _sync_line_price_memory(self, grid, row: dict, current_uom=None) -> bool:
        if not isinstance(row, dict):
            return False
        profile = self._detail_uom_profile(grid, row)
        if not profile:
            return False
        _, price_field, base_price_field, pack_price_field = profile
        if not price_field or not base_price_field or not pack_price_field:
            return False
        active_uom = str(current_uom or row.get('uom') or '').strip().upper()
        pack_uom = str(row.get('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(row.get('pack_qty') or 0)
        visible_price = _safe_float(row.get(price_field, 0))
        if active_uom and pack_uom and active_uom == pack_uom and pack_qty > 1:
            row[pack_price_field] = round(visible_price, 4)
            row[base_price_field] = round(visible_price / pack_qty if pack_qty else 0.0, 4)
        else:
            row[base_price_field] = round(visible_price, 4)
            row[pack_price_field] = round(visible_price * pack_qty if pack_qty > 1 else visible_price, 4)
        return True

    def _convert_grid_row_uom(self, grid, row: dict, from_uom, to_uom) -> bool:
        if not isinstance(row, dict):
            return False
        base_uom = str(row.get('base_uom') or row.get('uom') or '').strip().upper()
        pack_uom = str(row.get('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(row.get('pack_qty') or 0)
        source_uom = str(from_uom or '').strip().upper()
        target_uom = str(to_uom or '').strip().upper()
        if not base_uom or not pack_uom or pack_qty <= 1:
            return False
        if source_uom == target_uom:
            return False
        valid_pair = {base_uom, pack_uom}
        if source_uom not in valid_pair or target_uom not in valid_pair:
            return False

        for qty_field in self._row_qty_fields(row):
            raw = str(row.get(qty_field, '') or '').strip()
            if not raw:
                continue
            base_qty = _base_qty(row.get(qty_field, 0), source_uom, pack_uom, pack_qty)
            row[qty_field] = round(_display_qty(base_qty, target_uom, pack_uom, pack_qty), 4)

        row['uom'] = target_uom
        return self._apply_line_price_for_uom(grid, row, target_uom=target_uom)

    def _toggle_grid_row_uom(self, grid) -> bool:
        if not grid or not getattr(grid, 'data', None):
            return False
        row = grid.get_current_row() if hasattr(grid, 'get_current_row') else None
        if not isinstance(row, dict):
            return False
        base_uom = str(row.get('base_uom') or row.get('uom') or '').strip().upper()
        pack_uom = str(row.get('pack_uom') or '').strip().upper()
        pack_qty = _safe_float(row.get('pack_qty') or 0)
        if not base_uom or not pack_uom or pack_qty <= 1:
            return False
        current_uom = str(row.get('uom') or '').strip().upper()
        if current_uom == base_uom:
            target_uom = pack_uom
        elif current_uom == pack_uom:
            target_uom = base_uom
        else:
            return False
        if not self._convert_grid_row_uom(grid, row, current_uom, target_uom):
            return False
        if hasattr(grid, 'set_status'):
            grid.set_status(f"UOM: {current_uom} -> {target_uom}")
        return True

    def _handle_grid_uom_shortcut(self, key, grid, grid_editing=False) -> bool:
        """Handle Ctrl+U without taking F7 away from the grid filter."""
        if key != CTRL_U_KEY or grid_editing:
            return False
        if not self._toggle_grid_row_uom(grid) and hasattr(grid, 'set_status'):
            grid.set_status("UOM conversion unavailable for this row")
        return True

    def _make_uom_toggle_callback(self, grid):
        """Create the callback used when Ctrl+U is pressed inside cell editing."""
        def callback():
            self._handle_grid_uom_shortcut(CTRL_U_KEY, grid)
            self._recompute_header()
        return callback

    def _make_cell_edit_callback(self, grid):
        """Create on_cell_edit callback that auto-fills related fields from lookup data.

        When a lookup column value changes, find the full lookup record
        and copy all matching fields into the row.
        """
        lookup_data = self.lookup_data

        def callback(row_idx, field_name, old_value, new_value):
            if row_idx < 0 or row_idx >= len(grid.data):
                return
            row = grid.data[row_idx]
            profile = self._detail_uom_profile(grid, row)
            handled_lookup = False
            # Find which column was edited
            for col in grid.columns:
                if col.store_field == field_name and col.lookup:
                    items = lookup_data.get(col.lookup, [])
                    # Build set of grid column names for matching
                    grid_cols = {c.name for c in grid.columns}
                    skip = {"rowid", "_parent_rowid"}
                    # Find the matching record by checking all non-skip fields
                    for item in items:
                        fields = [k for k in item if k not in skip]
                        # Try to match on the value field
                        matched = False
                        for f in fields:
                            if str(item[f]) == str(new_value):
                                matched = True
                                break
                        if not matched:
                            continue
                        # Copy all matching fields from lookup record to row
                        for lk, lv in item.items():
                            if lk in skip or lk == field_name:
                                continue
                            # Direct match: lookup field name == grid column name
                            if lk in grid_cols:
                                row[lk] = lv
                                continue
                            # Suffix match: grid "unit_cost" ← lookup "cost"
                            for gc in grid_cols:
                                if gc.endswith(lk) and gc != field_name:
                                    row[gc] = lv
                                    break
                        grid.compute_row(row)
                        self._apply_line_uom_defaults(grid, row)
                        handled_lookup = True
                        break
                    break
            if profile:
                _, price_field, base_price_field, pack_price_field = profile
                if field_name == 'uom':
                    self._convert_grid_row_uom(grid, row, old_value, new_value)
                elif price_field and field_name == price_field:
                    self._sync_line_price_memory(grid, row)
                    grid.compute_row(row)
                elif field_name in {base_price_field, pack_price_field, 'pack_uom', 'pack_qty', 'recv_uom', 'issue_uom'} and not handled_lookup:
                    self._apply_line_uom_defaults(grid, row)
            # Re-evaluate COMPUTED header fields
            self._recompute_header()

        return callback

    def _recompute_header(self):
        """Re-evaluate COMPUTED fields from detail grid data and update header widgets."""
        if not self._computed_defs:
            return
        # Collect all detail lines from tab grids
        lines = []
        detail_col_names = []
        for tg in self.tab_grids:
            if tg and HAS_CXGRID and isinstance(tg, CxGrid):
                lines.extend(tg.data)
                if not detail_col_names:
                    detail_col_names = [c.name for c in tg.columns]
        # Build header dict from current widget values
        header = {}
        widget_map = {}
        for w in self.widgets:
            fid = getattr(w, '_field_id', '')
            if fid and hasattr(w, 'value'):
                header[fid] = w.value
                widget_map[fid] = w
        # Evaluate each computed field and update widgets
        for comp in self._computed_defs:
            val = eval_computed_formula(comp["expr"], header, lines, detail_col_names)
            header[comp["name"]] = val  # allow later formulas to reference earlier ones
            if comp["name"] in widget_map:
                widget_map[comp["name"]].load(val)

    def _wire_highlights(self, grid, hl_rules):
        """Wire conditional row highlighting from HIGHLIGHT rules to a grid."""
        style_map = {
            'error':   curses.color_pair(grid.CLR_HL_ERROR),
            'warn':    curses.color_pair(grid.CLR_HL_WARN) | curses.A_BOLD,
            'hilite':  curses.color_pair(grid.CLR_HL_HILITE) | curses.A_BOLD,
            'success': curses.color_pair(grid.CLR_HL_SUCCESS),
            'bold':    curses.A_BOLD,
            'dim':     curses.color_pair(grid.CLR_HL_DIM) | curses.A_DIM,
            # Common aliases used by PyStock and existing scripts.
            'warning': curses.color_pair(grid.CLR_HL_WARN) | curses.A_BOLD,
            'info':    curses.color_pair(grid.CLR_HL_HILITE) | curses.A_BOLD,
            'yellow':  curses.color_pair(grid.CLR_HL_WARN) | curses.A_BOLD,
            'cyan':    curses.color_pair(grid.CLR_HL_HILITE) | curses.A_BOLD,
            'green':   curses.color_pair(grid.CLR_HL_SUCCESS),
            'red':     curses.color_pair(grid.CLR_HL_ERROR),
        }

        def _row_highlight(row):
            for rule in hl_rules:
                try:
                    if _eval_highlight_condition(rule["expr"], row):
                        return style_map.get(rule["style"])
                except Exception:
                    pass
            return None

        grid.row_attr_fn = _row_highlight

    def _filter_pending_delivery(self, ldata: list, src_detail: str,
                                    link_field: str) -> list:
        """Filter lookup records to those with pending delivery.

        Checks source detail rows (e.g. po_line, pk_line): if any line has
        qty > fulfilled, the parent has pending delivery.
        For pk_line: any row with pick_qty > 0 means pending.
        """
        import json as _json
        # Build set of parent rowids that have open lines
        open_parents = set()
        drows = self.db.query(f'SELECT data FROM "{src_detail}"')
        for dr in drows:
            doc = _json.loads(dr.get("data", "{}"))
            pid = doc.get("_parent_rowid")
            if pid is None:
                continue
            # Detect qty field: order_qty or pick_qty
            oq = float(doc.get("order_qty", 0) or 0)
            if oq <= 0:
                oq = float(doc.get("pick_qty", 0) or 0)
            rq = float(doc.get("received_qty", 0) or 0)
            sq = float(doc.get("ship_qty", 0) or 0)
            fulfilled = max(rq, sq)
            if oq > fulfilled + 1e-9:
                open_parents.add(pid)
        return [r for r in ldata if r.get("rowid") in open_parents]

    def _make_new_row_callback(self, grid):
        """Create on_need_new_row callback that adds a blank row with defaults."""
        def callback():
            new_row = {}
            for c in grid.columns:
                if c.numeric:
                    new_row[c.name] = 0
                else:
                    new_row[c.name] = ""
            grid.add_row(new_row)
        return callback

    def _auto_fill_lookup(self, lookup_widget):
        """After a LookupField selection, auto-fill companion fields.

        Finds the full lookup record for the selected value, then fills
        sibling form widgets. Supports fill mapping via lookupfill dict.
        """
        fid = getattr(lookup_widget, '_field_id', '')
        if not fid or not lookup_widget.value:
            return
        # Find the full record for the selected value
        record = None
        for row in lookup_widget.lookup_data:
            if row.get(lookup_widget.value_field) == lookup_widget.value:
                record = row
                break
        if record is None:
            return

        # Check for explicit fill map from field definition
        fill_map = getattr(lookup_widget, '_fill_map', None)
        if fill_map and isinstance(fill_map, dict):
            # Explicit mapping: {src_field: dest_field}
            widget_map = {}
            for w in self.widgets:
                wfid = getattr(w, '_field_id', '')
                if wfid and hasattr(w, 'load'):
                    widget_map[wfid] = w
            for src, dest in fill_map.items():
                if src in record and dest in widget_map:
                    widget_map[dest].load(record[src])
        else:
            # Auto-fill by field name matching
            # Note: readonly widgets ARE filled — they're readonly to the user,
            # not to lookup auto-fill (that's the whole point of ro + fill).
            skip = {"rowid", "_parent_rowid", "status", lookup_widget.value_field}
            for w in self.widgets:
                wfid = getattr(w, '_field_id', '')
                if not wfid or wfid == fid or not hasattr(w, 'load'):
                    continue
                # Direct match
                if wfid in record and wfid not in skip:
                    w.load(record[wfid])
                    continue
                # Suffix match
                for rk, rv in record.items():
                    if rk in skip:
                        continue
                    if rk.endswith(wfid) or wfid.endswith(rk):
                        w.load(rv)
                        break

        # ── Copy detail lines from source document ──
        self._copy_detail_from_lookup(fid, record)

    def _copy_detail_from_lookup(self, field_id: str, record: dict):
        """If the lookup field has a copydetail flag, load source detail rows
        into the current form's tab grid."""
        if not self.db or not self.tab_grids or not self.tabs_def:
            return
        # Find the field definition with copydetail
        field_def = None
        for form_fields in self.form_defs.values():
            if not isinstance(form_fields, list):
                continue
            for f in form_fields:
                if f.get("id") == field_id and "copydetail" in f:
                    field_def = f
                    break
            if field_def:
                break
        if not field_def:
            return
        source_detail = field_def["copydetail"]  # e.g. "po_line"
        source_table = source_detail  # table name matches detail name
        source_rowid = record.get("rowid")
        if source_rowid is None:
            return
        # Load detail rows from source table
        try:
            import json
            rows = self.db.query(f'SELECT data FROM "{source_table}"')
            source_rows = []
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    if doc.get("_parent_rowid") == source_rowid:
                        source_rows.append(doc)
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            return
        if not source_rows:
            return
        # Calculate open balance by checking existing fulfillment documents.
        # For PO→GRN: sum received_qty from grn_line where same PO.
        # For SO→DO: sum ship_qty from do_line where same SO.
        # Approach: find the target detail table, query existing docs that
        # reference the same source, sum the fulfillment qty per key field.
        fulfilled = {}  # {part_key: total_fulfilled_base}
        if self.tab_grids and HAS_CXGRID:
            target_grid = self.tab_grids[0]
            if target_grid is not None:
                # Find fulfillment field in target grid (ship_qty, received_qty, etc.)
                fulfill_field = None
                for c in target_grid.columns:
                    if c.name in ("ship_qty", "received_qty", "delivered_qty"):
                        fulfill_field = c.name
                        break
                # Find key field for matching (part_no, item_code, etc.)
                key_field = None
                for c in target_grid.columns:
                    if c.name in ("part_no", "item_code", "product_code"):
                        key_field = c.name
                        break
                if not key_field and target_grid.columns:
                    key_field = target_grid.columns[0].name
                if fulfill_field and key_field:
                    # Query existing target detail rows that share the same
                    # source document reference (e.g. same so_number)
                    target_grid_id = getattr(target_grid, '_grid_id', '')
                    target_grid_def = self.grid_defs.get(target_grid_id, {})
                    target_table = target_grid_def.get("parent", "").replace("_form", "")
                    if target_table:
                        try:
                            # Find all parent docs referencing same source
                            # (e.g. all delivery headers with so_number=SO-0004)
                            parent_form_id = None
                            for ref_id in self.layout_def.get("fields", {}):
                                if ref_id in self.form_defs:
                                    parent_form_id = ref_id
                                    break
                            parent_table = parent_form_id.replace("_form", "") if parent_form_id else ""
                            if parent_table:
                                lookup_val = record.get(field_def.get("id", ""))
                                if not lookup_val:
                                    # Use the value from the lookup widget
                                    for w in self.widgets:
                                        if getattr(w, '_field_id', '') == field_id:
                                            lookup_val = w.value
                                            break
                                parent_rows = self.db.query(
                                    f'SELECT rowid, data FROM "{parent_table}"')
                                parent_rids = []
                                for pr in parent_rows:
                                    try:
                                        pd = json.loads(pr.get("data", "{}"))
                                        if pd.get(field_id) == lookup_val and "_parent_rowid" not in pd:
                                            parent_rids.append(pr["rowid"])
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                                # Sum fulfilled qty from target detail
                                if parent_rids:
                                    det_rows = self.db.query(
                                        f'SELECT data FROM "{target_table}"')
                                    for dr in det_rows:
                                        try:
                                            dd = json.loads(dr.get("data", "{}"))
                                            if dd.get("_parent_rowid") in parent_rids:
                                                pk = str(dd.get(key_field, ""))
                                                fv = _base_qty(
                                                    dd.get(fulfill_field, 0),
                                                    dd.get('uom'),
                                                    dd.get('pack_uom'),
                                                    dd.get('pack_qty'),
                                                )
                                                fulfilled[pk] = fulfilled.get(pk, 0) + fv
                                        except (json.JSONDecodeError, TypeError, ValueError):
                                            pass
                        except Exception:
                            pass

        # Build open rows: order_qty minus already-fulfilled
        open_rows = []
        # Detect key field in source rows
        key_field_src = None
        for kf in ("part_no", "item_code", "product_code"):
            if source_rows and kf in source_rows[0]:
                key_field_src = kf
                break
        if not key_field_src and source_rows:
            keys = [k for k in source_rows[0] if k != "_parent_rowid"]
            if keys:
                key_field_src = keys[0]

        # Detect quantity field: order_qty, pick_qty, or first numeric-looking qty
        qty_field = None
        for qf in ("order_qty", "pick_qty", "quantity", "qty"):
            if source_rows and qf in source_rows[0]:
                qty_field = qf
                break

        # Rollup: if source has multiple rows per key (e.g. pk_line with
        # per-lot rows), aggregate qty by part_no before building DO lines
        needs_rollup = False
        if key_field_src and qty_field and source_rows:
            seen_keys = set()
            for src in source_rows:
                pk = str(src.get(key_field_src, ""))
                if pk in seen_keys:
                    needs_rollup = True
                    break
                seen_keys.add(pk)

        if needs_rollup:
            # Aggregate by key field — sum qty, keep first row's other fields
            rollup = {}  # key -> merged row
            for src in source_rows:
                pk = str(src.get(key_field_src, ""))
                q = float(src.get(qty_field, 0) or 0)
                if pk in rollup:
                    rollup[pk][qty_field] = float(rollup[pk].get(qty_field, 0) or 0) + q
                else:
                    row_copy = {k: v for k, v in src.items()
                                if k != "_parent_rowid"
                                and k not in ("lot_no", "grn_no", "loc_id",
                                              "storage_rowid", "_storage_rowid")}
                    row_copy[qty_field] = q
                    rollup[pk] = row_copy
            source_rows = list(rollup.values())

        for src in source_rows:
            oq_raw = float(src.get("order_qty", 0) or 0)
            # Fallback: use pick_qty or other qty field as order_qty equivalent
            if oq_raw <= 0 and qty_field and qty_field != "order_qty":
                oq_raw = float(src.get(qty_field, 0) or 0)
            pk = str(src.get(key_field_src, "")) if key_field_src else ""
            already = fulfilled.get(pk, 0)
            oq_base = _base_qty(
                oq_raw,
                src.get('uom'),
                src.get('pack_uom'),
                src.get('pack_qty'),
            )
            balance_base = oq_base - already
            if balance_base > 1e-9:
                row_copy = dict(src)
                balance = _display_qty(
                    balance_base,
                    src.get('uom'),
                    src.get('pack_uom'),
                    src.get('pack_qty'),
                )
                row_copy["order_qty"] = balance
                # Pre-fill fulfillment fields with open balance
                for ff in ("received_qty", "ship_qty", "delivered_qty"):
                    if ff in row_copy:
                        row_copy[ff] = balance
                open_rows.append(row_copy)
            elif oq_base <= 0 and not already:
                # No order_qty — copy as-is
                open_rows.append(dict(src))
        if not open_rows:
            return
        # Find the first tab grid and populate it
        if HAS_CXGRID and self.tab_grids:
            target_grid = self.tab_grids[0]
            if target_grid is None:
                return
            # Clear existing data
            target_grid.data.clear()
            target_grid.cursor_row = 0
            # Map source columns to target columns by name
            # Alias map for cross-document field compatibility
            _alias = {
                "pick_qty": ("ship_qty", "order_qty", "quantity"),
                "ship_qty": ("order_qty", "pick_qty"),
                "order_qty": ("ship_qty",),
                "received_qty": ("order_qty",),
            }
            for src_row in open_rows:
                new_row = {}
                for c in target_grid.columns:
                    if c.name in src_row:
                        new_row[c.name] = src_row[c.name]
                    else:
                        # Try alias mapping
                        mapped = False
                        for src_name, targets in _alias.items():
                            if c.name in targets and src_name in src_row:
                                new_row[c.name] = src_row[src_name]
                                mapped = True
                                break
                        if not mapped:
                            new_row[c.name] = 0 if c.numeric else ""
                target_grid.add_row(new_row)

    def get_values(self) -> Dict[str, Any]:
        """Collect current values from all widgets (including UDF)."""
        vals = {}
        for w in self.widgets:
            fid = getattr(w, '_field_id', None)
            if fid:
                vals[fid] = w.value
        if self.udf_panel:
            udf_vals = self.udf_panel.get_values()
            if udf_vals:
                vals["udf"] = udf_vals
        return vals

    def load_values(self, data: Dict[str, Any]):
        """Load values from a data dict into all form widgets (for editing)."""
        prior_sync_state = self._item_master_syncing
        self._item_master_syncing = True
        try:
            for w in self.widgets:
                fid = getattr(w, '_field_id', None)
                if fid and fid in data:
                    w.load(data[fid])
        finally:
            self._item_master_syncing = prior_sync_state
        self._sync_item_master_fields()
        if self.udf_panel and "udf" in data:
            self.udf_panel.load_values(data["udf"])

    def get_tab_grid_data(self) -> Dict[str, List[Dict]]:
        """Return current data from all tab grids, keyed by grid_id."""
        result = {}
        if not self.tabs_def:
            return result
        for i, tab_item in enumerate(self.tabs_def.get("items", [])):
            grid_id = tab_item.get("grid", "")
            if i < len(self.tab_grids) and self.tab_grids[i] is not None:
                g = self.tab_grids[i]
                if HAS_TREEGRID and isinstance(g, TreeGrid):
                    result[grid_id] = g.get_data()
                elif HAS_CXGRID and isinstance(g, CxGrid):
                    result[grid_id] = list(g.data)
                else:
                    result[grid_id] = list(getattr(g, 'data', []))
        return result

    def _save_column_config(self, grid):
        """Capture column config from a grid after column config dialog."""
        grid_id = getattr(grid, '_grid_id', None)
        if grid_id:
            self.column_configs[grid_id] = [
                (c.name, c.visible, c.width) for c in grid.columns
            ]

    def get_sort_configs(self):
        """Capture current sort state from all grids for session persistence."""
        result = {}
        for grid in self.cxgrids:
            grid_id = getattr(grid, '_grid_id', None)
            if grid_id and grid.sort_column is not None:
                col = grid.columns[grid.sort_column]
                result[grid_id] = (col.name, grid.sort_ascending)
        return result

    def get_selected_row(self) -> Optional[Dict]:
        """Return the data dict of the currently selected grid row."""
        if self.cxgrids:
            grid = self.cxgrids[0]
            return grid._view_row(grid.row)
        return None

    def _is_listing_layout(self):
        """Check if this is a listing grid layout (no form widgets, no menu)."""
        if self.layout_def.get("center_menu") or self.layout_def.get("top_menus"):
            return False
        if self.layout_def.get("tabs"):
            return False
        # Listing layouts have grid fields but no form widgets
        has_grid = bool(self.cxgrids or self.basic_grids)
        has_form = bool(self.widgets)
        return has_grid and not has_form

    def _calc_figlet_height(self):
        """Calculate figlet banner height for listing pages."""
        try:
            import pyfiglet
        except ImportError:
            return 0
        # Only for listing layouts (grid-only, no menus/tabs/forms)
        if self.layout_def.get("center_menu") or self.layout_def.get("top_menus"):
            return 0
        if self.layout_def.get("tabs"):
            return 0
        # Check fields reference grids, not forms
        fields = self.layout_def.get("fields", {})
        has_grid_field = any(fid in self.grid_defs for fid in fields)
        has_form_field = any(fid in self.form_defs for fid in fields)
        if not has_grid_field or has_form_field:
            return 0
        title = self.layout_def.get("title", "")
        if not title:
            return 0
        h, w = self.stdscr.getmaxyx()
        try:
            fig = pyfiglet.figlet_format(title, font="standard", width=w - 4)
        except Exception:
            return 0
        lines = [ln for ln in fig.rstrip("\n").split("\n") if ln.strip()]
        return min(len(lines), h // 4)

    def _draw_figlet(self):
        """Render figlet title banner at top of listing pages."""
        if not self._figlet_h:
            return
        try:
            import pyfiglet
        except ImportError:
            return
        h, w = self.stdscr.getmaxyx()
        title = self.layout_def.get("title", "")
        try:
            fig = pyfiglet.figlet_format(title, font="standard", width=w - 4)
        except Exception:
            return
        lines = [ln for ln in fig.rstrip("\n").split("\n") if ln.strip()]
        attr = curses.color_pair(CLR_FORM_BORDER) | curses.A_BOLD
        for i in range(min(len(lines), self._figlet_h)):
            cx = max(0, (w - len(lines[i])) // 2)
            try:
                self.stdscr.addstr(i, cx, lines[i][:w - 1], attr)
            except curses.error:
                pass

    def _get_effective_rect(self):
        """Return layout rectangle, expanded to terminal size."""
        rect = dict(self.layout_def["position"])  # copy
        max_y, max_x = self.stdscr.getmaxyx()
        # Always expand to fill terminal (grids and tabs extend beyond fixed DSL sizes)
        rect["width"] = max_x
        rect["height"] = max_y
        return rect

    def _draw_frame(self):
        rect = self._get_effective_rect()
        border_attr = curses.color_pair(CLR_FORM_BORDER)
        # Figlet banner on listing pages
        if self._figlet_h:
            self._draw_figlet()
        frame_y = rect["y"] + self._figlet_h
        if self.layout_def.get("border", 0):
            try:
                self.stdscr.attron(border_attr)
                rectangle(self.stdscr, frame_y, rect["x"],
                          rect["y"] + rect["height"] - 1, rect["x"] + rect["width"] - 1)
                self.stdscr.attroff(border_attr)
            except curses.error:
                pass
        title = self.layout_def.get("title", "")
        if title and "top_menu" not in self.layout_def:
            if not self._figlet_h:
                try:
                    self.stdscr.addstr(frame_y, rect["x"] + 2, f" {title} ",
                                       curses.A_BOLD | border_attr)
                except curses.error:
                    pass
        self._draw_crud_tabs(frame_y)

    def _draw_crud_tabs(self, frame_y: int):
        """Draw the top-level List/Form selector for a TABBED CRUD form."""
        tabs = self.layout_def.get("crud_tabs")
        if not tabs:
            return
        labels = tabs.get("labels") or ["List", "Form and Edit"]
        active = 0 if tabs.get("active") == "list" else 1
        x = self.layout_def.get("position", {}).get("x", 0) + 2
        y = frame_y + 1
        for index, label in enumerate(labels[:2]):
            text = f" {label} "
            attr = curses.A_BOLD | curses.A_REVERSE \
                if index == active else curses.A_DIM
            try:
                self.stdscr.addstr(y, x, text, attr)
            except curses.error:
                pass
            x += len(text) + 1

    def _draw_widgets(self):
        label_attr = curses.color_pair(CLR_FORM_LABEL)
        for w in self.widgets:
            label = getattr(w, '_label', '')
            label_x = getattr(w, '_label_x', w.x)
            if label:
                try:
                    padded = f"{label}:".rjust(w.x - label_x - 1)
                    self.stdscr.addstr(w.y, label_x, padded, label_attr)
                except curses.error:
                    pass
            w.draw(self.stdscr)

    def _draw_sections(self):
        for s in self.sections:
            s.draw(self.stdscr)

    def _draw_tabs(self, focused: bool = False):
        if not self.tabs_def:
            return
        tabs = self.tabs_def
        ty = tabs["y"]
        tx = tabs["x"]
        # Draw focus indicator
        if focused:
            try:
                self.stdscr.addstr(ty, max(tx - 2, 0), ">", curses.A_BOLD)
            except curses.error:
                pass
        # Draw tab headers
        cx = tx
        for i, item in enumerate(tabs["items"]):
            label = f" {item['label']} "
            if i == self.active_tab:
                attr = curses.A_BOLD | curses.color_pair(CLR_HEADER)
            elif focused:
                attr = curses.A_NORMAL
            else:
                attr = curses.A_DIM
            try:
                self.stdscr.addstr(ty, cx, label, attr)
            except curses.error:
                pass
            cx += len(label) + 1
        # Draw separator line under tabs
        rect = self.layout_def.get("position", {})
        sep_w = rect.get("width", 60) - tx * 2 - 2
        try:
            self.stdscr.addstr(ty + 1, tx, "\u2500" * sep_w)
        except curses.error:
            pass
        # Draw active tab's grid
        if 0 <= self.active_tab < len(self.tab_grids):
            g = self.tab_grids[self.active_tab]
            if g:
                if HAS_TREEGRID and isinstance(g, TreeGrid):
                    g.draw()
                elif HAS_CXGRID and isinstance(g, CxGrid):
                    g.draw()
                else:
                    g.draw(self.stdscr)

    def _draw_grids(self):
        for g in self.cxgrids:
            g.draw()
        for g in self.basic_grids:
            g.draw(self.stdscr)

    def _draw_buttons(self, focused_btn_idx: int = -1):
        for i, btn in enumerate(self.buttons):
            try:
                label = f"[ {btn['label']} ]"
                if i == focused_btn_idx:
                    self.stdscr.addstr(btn["y"], btn["x"], label, curses.A_BOLD | curses.A_REVERSE)
                else:
                    self.stdscr.addstr(btn["y"], btn["x"], label)
            except curses.error:
                pass

    def _draw_hotkey_bar(self, context: str = "form"):
        """Draw F-key status bar at bottom of screen using StatusLine."""
        has_workflow = bool(self.layout_def.get("workflow"))
        wf = self.layout_def.get("workflow", {})

        has_udf = self.udf_panel is not None and bool(self.udf_panel.widgets)
        has_detail_tabs = bool(self.tabs_def)
        has_lookup = any(isinstance(w, LookupField) for w in self.widgets)
        has_lookup = has_lookup or any(
            any(getattr(col, "lookup", None)
                for col in getattr(grid, "columns", []))
            for grid in self.cxgrids
        )

        if context in ("form", "form_edit", "tab_grid"):
            self.status_line.set_capabilities(
                lookup=has_lookup,
                workflow=has_workflow,
                focus=has_udf or has_detail_tabs,
                udf_edit=has_udf and context != "tab_grid",
                uom=context == "tab_grid",
                filter=context == "tab_grid",
                columns=context == "tab_grid",
                theme=True,
                save=True,
                print=context != "tab_grid",
                b2b=context != "tab_grid",
                back=True,
            )
            self.status_line.set_f5_label(
                "Actions" if wf.get("actions") else "Post")
        else:
            self.status_line.set_page(context)

        if context == "form_edit":
            rowid = ""
            if self.edit_context and self.edit_context.get("rowid"):
                rowid = f" #{self.edit_context['rowid']}"
            info = f"Editing{rowid}" if rowid else ""
            if self.layout_def.get("crud_tabs"):
                info = (info + "  " if info else "") + "PgUp: List"
            self.status_line.set_info(info)
        else:
            info = "PgUp: List" if self.layout_def.get("crud_tabs") else ""
            self.status_line.set_info(info)

        self.status_line.draw()

    def draw_all(self):
        self.stdscr.clear()
        self._draw_frame()
        if "top_menu" in self.layout_def:
            self.menu._draw_top_bar()
        self._draw_sections()
        self._draw_widgets()
        self._draw_grids()
        self._draw_tabs()
        self._draw_buttons()
        if self.udf_panel:
            self.udf_panel.draw()
        self.stdscr.refresh()

    def run_menu(self, mode: str = "auto") -> str:
        if mode == "auto":
            has_menu = "top_menus" in self.layout_def or "center_menu" in self.layout_def
            has_grid = bool(self.cxgrids) or bool(self.basic_grids)
            has_form = bool(self.widgets)

            if has_menu and not has_form and not has_grid:
                mode = "hybrid" if "top_menus" in self.layout_def else "center"
                return self.menu.run(default_mode=mode)
            elif has_grid and not has_form:
                return self._run_grid_loop()
            elif has_form or self.buttons:
                return self._run_form_loop()
            elif has_menu:
                mode = "hybrid" if "top_menus" in self.layout_def else "center"
                return self.menu.run(default_mode=mode)
            else:
                # Empty layout
                self.draw_all()
                while True:
                    key = self.stdscr.getch()
                    if key == 27:
                        return ""
        return self.menu.run(default_mode=mode)

    def _set_focus(self, idx: int):
        for i, w in enumerate(self.widgets):
            w.focused = (i == idx)

    def _export_form_pdf(self):
        """Export current form to PDF via Ctrl+P."""
        from .form_pdf import export_form_pdf, show_print_dialog

        # Gather header values from widgets
        record = {}
        if self.edit_context and self.edit_context.get("data"):
            record.update(self.edit_context["data"])
        for w in self.widgets:
            fid = getattr(w, 'field_id', '') or getattr(w, '_field_id', '')
            if fid:
                record[fid] = w.value

        # Gather detail grid data from tabs
        detail_grids = []
        if self.tabs_def:
            for ti, tab_item in enumerate(self.tabs_def.get("items", [])):
                grid_id = tab_item.get("grid", "")
                label = tab_item.get("label", "")
                grid_def = self.app_grids.get(grid_id, {})
                columns = grid_def.get("columns", [])
                show_totals = grid_def.get("show_totals", False)
                data = []
                if ti < len(self.tab_grids) and self.tab_grids[ti]:
                    tg = self.tab_grids[ti]
                    if HAS_CXGRID and isinstance(tg, CxGrid):
                        data = list(tg.data)
                    elif hasattr(tg, 'data'):
                        data = list(tg.data)
                detail_grids.append({
                    "label": label,
                    "columns": columns,
                    "data": data,
                    "show_totals": show_totals,
                })

        title = self.layout_def.get("title", "Document")

        # Show print options dialog
        form_name = title.lower().replace(' ', '_')
        dlg_result = show_print_dialog(self.stdscr, detail_grids, form_name)
        if dlg_result is None:
            self.stdscr.touchwin()
            self.stdscr.refresh()
            return
        detail_grids, landscape = dlg_result

        # Business settings
        settings = {}
        if self._default_resolver:
            pass
        if self.db:
            try:
                rows = self.db.query(
                    'SELECT rowid, data FROM "business_settings"')
                if rows:
                    import json
                    settings = json.loads(rows[0].get("data", "{}"))
            except Exception:
                pass

        computed_defs = self.layout_def.get("computed")

        # Find form fields from form_defs
        form_fields = []
        header_form = self.layout_def.get("header_form", "")
        if header_form and header_form in self.form_defs:
            form_fields = self.form_defs[header_form]
        else:
            for ref_id in self.layout_def.get("fields", {}):
                if ref_id in self.form_defs:
                    form_fields = self.form_defs[ref_id]
                    break

        result = export_form_pdf(
            form_fields, record, detail_grids, settings, title,
            computed_defs=computed_defs, landscape=landscape)

        # Restore curses state after PDF generation
        self.stdscr.touchwin()
        self.stdscr.refresh()

        # Show result in status
        if self.status_line:
            if result and not result.startswith("reportlab") and not result.startswith("PDF save"):
                import os as _os
                self.status_line.set_info(f"PDF: {_os.path.basename(result)}")
            else:
                self.status_line.set_info(str(result))

    def _hide_cursor(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    def _run_form_loop(self) -> str:
        """Input loop for layouts with forms/grids/buttons.

        Focus zones (in order):
          0 .. len(widgets)-1          = form widgets
          len(widgets)                 = tab bar (if tabs exist)
          len(widgets)+has_tabs .. end = buttons
        """
        has_tabs = 1 if self.tabs_def else 0
        tab_focus_idx = len(self.widgets)  # index of the tab-bar zone
        total_items = len(self.widgets) + has_tabs + len(self.buttons)
        if total_items == 0:
            self.draw_all()
            while True:
                key = self.stdscr.getch()
                if key == 27:
                    return ""

        focus_idx = 0
        prev_focus_idx = -1
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS |
                             getattr(curses, "REPORT_MOUSE_POSITION", 0))
        except curses.error:
            pass

        def cycle_focus_zone():
            """Cycle F6 focus through header, detail grid, and UDF zones."""
            udf_available = bool(self.udf_panel and self.udf_panel.widgets)
            on_tabs = has_tabs and focus_idx == tab_focus_idx

            if self._udf_focused:
                self._udf_focused = False
                self.udf_panel.focused = False
                return 0
            if on_tabs:
                if udf_available:
                    self._udf_focused = True
                    self.udf_panel.focused = True
                    return focus_idx
                return 0
            if has_tabs:
                return tab_focus_idx
            if udf_available:
                self._udf_focused = True
                self.udf_panel.focused = True
            return focus_idx

        def handle_mouse(mx, my, bstate):
            """Route a mouse event to form, tabs, UDF, or active detail grid."""
            nonlocal focus_idx
            click = (getattr(curses, "BUTTON1_CLICKED", 0) |
                     getattr(curses, "BUTTON1_PRESSED", 0))
            if bstate & (getattr(curses, "BUTTON4_PRESSED", 0) |
                         getattr(curses, "BUTTON5_PRESSED", 0)):
                if on_tabs and 0 <= self.active_tab < len(self.tab_grids):
                    grid = self.tab_grids[self.active_tab]
                    if grid and HAS_CXGRID and isinstance(grid, CxGrid):
                        grid.handle_mouse(bstate, mx, my)
                return
            if not (bstate & click):
                return

            if self.udf_panel:
                px, py = self.udf_panel.panel_x, self.udf_panel.panel_y
                pw, ph = self.udf_panel.panel_w, self.udf_panel.panel_h
                if px <= mx < px + pw and py <= my < py + ph:
                    self._udf_focused = bool(self.udf_panel.widgets)
                    self.udf_panel.focused = self._udf_focused
                    return

            if self.tabs_def:
                tabs = self.tabs_def
                if my == tabs["y"]:
                    x = tabs["x"]
                    for i, item in enumerate(tabs["items"]):
                        label_w = len(f" {item['label']} ")
                        if x <= mx < x + label_w:
                            self.active_tab = i
                            focus_idx = tab_focus_idx
                            self._udf_focused = False
                            return
                        x += label_w + 1

            if on_tabs and 0 <= self.active_tab < len(self.tab_grids):
                grid = self.tab_grids[self.active_tab]
                if grid and HAS_CXGRID and isinstance(grid, CxGrid):
                    grid.handle_mouse(bstate, mx, my)
                    return

            for i, widget in enumerate(self.widgets):
                wx = getattr(widget, "x", -1)
                wy = getattr(widget, "y", -1)
                ww = getattr(widget, "width", 0)
                wh = getattr(widget, "height", 1)
                if wx <= mx < wx + ww and wy <= my < wy + wh:
                    self._udf_focused = False
                    if self.udf_panel:
                        self.udf_panel.focused = False
                    focus_idx = i
                    return

        while True:
            if self._udf_focused:
                self._set_focus(-1)  # defocus all main widgets
            else:
                self._set_focus(focus_idx)
            on_tabs = has_tabs and focus_idx == tab_focus_idx

            # Auto-add first row when entering an empty detail grid
            if on_tabs and prev_focus_idx != focus_idx:
                if 0 <= self.active_tab < len(self.tab_grids):
                    ag = self.tab_grids[self.active_tab]
                    if ag and HAS_CXGRID and isinstance(ag, CxGrid) and not ag.data:
                        new_row = {}
                        for c in ag.columns:
                            new_row[c.name] = 0 if c.numeric else ""
                        ag.add_row(new_row)
                    # Auto-enter CoEdit mode if configured
                    if ag and HAS_CXGRID and isinstance(ag, CxGrid):
                        auto_ce = getattr(ag, '_auto_coedit', '')
                        if auto_ce and not ag.coedit_mode and ag.data:
                            ag.coedit(auto_ce)
            # TreeGrid: hand off to its own event loop when tab focused
            if on_tabs and HAS_TREEGRID:
                if 0 <= self.active_tab < len(self.tab_grids):
                    ag = self.tab_grids[self.active_tab]
                    if isinstance(ag, TreeGrid):
                        # Draw everything first, then let TreeGrid take over
                        self.stdscr.clear()
                        self._draw_frame()
                        self._draw_sections()
                        self._draw_widgets()
                        self._draw_grids()
                        self._draw_tabs(focused=True)
                        self._draw_buttons(-1)
                        self._draw_hotkey_bar("tree_grid")
                        self.stdscr.refresh()
                        self._hide_cursor()
                        tree_data, exit_key = ag.run()
                        if exit_key == curses.KEY_F10:
                            return "__save__"
                        elif exit_key == 27:
                            # ESC from tree — go back to form
                            focus_idx = 0
                        elif exit_key == curses.KEY_F6:
                            focus_idx = cycle_focus_zone()
                        elif exit_key == curses.KEY_BTAB or exit_key == 353:
                            focus_idx = (focus_idx - 1) % total_items
                        elif exit_key == 9:
                            focus_idx = (focus_idx + 1) % total_items
                        elif exit_key == curses.KEY_F3:
                            self._hide_cursor()
                            return "__new__"
                        elif exit_key == curses.KEY_F5:
                            if self.layout_def.get("workflow"):
                                self._hide_cursor()
                                return "__post__"
                        prev_focus_idx = focus_idx
                        continue
            prev_focus_idx = focus_idx
            btn_focus = -1
            if focus_idx >= len(self.widgets) + has_tabs:
                btn_focus = focus_idx - len(self.widgets) - has_tabs

            self.stdscr.clear()
            self._draw_frame()
            self._draw_sections()
            self._draw_widgets()
            self._draw_grids()
            self._draw_tabs(focused=on_tabs)
            self._draw_buttons(btn_focus)
            if self.udf_panel:
                self.udf_panel.draw()

            # Focus indicator arrow for widgets (skip when UDF has focus)
            if not self._udf_focused and focus_idx < len(self.widgets):
                w = self.widgets[focus_idx]
                try:
                    label_x = getattr(w, '_label_x', w.x)
                    self.stdscr.addstr(w.y, max(label_x - 2, 0), ">", curses.A_BOLD)
                except curses.error:
                    pass

            # Hotkey bar
            if on_tabs:
                bar_ctx = "tab_grid"
            elif self.edit_context:
                bar_ctx = "form_edit"
            else:
                bar_ctx = "form"
            self._draw_hotkey_bar(bar_ctx)

            # Position terminal cursor
            if self._udf_focused and self.udf_panel:
                # Place cursor in UDF panel's active widget
                cy, cx = self.udf_panel.cursor_pos()
                try:
                    curses.curs_set(1)
                    self.stdscr.move(cy, cx)
                except curses.error:
                    pass
            elif focus_idx < len(self.widgets):
                w = self.widgets[focus_idx]
                cursor_x = w.cursor_x()
                cursor_y = w.cursor_y() if hasattr(w, 'cursor_y') else w.y
                try:
                    curses.curs_set(1)
                    self.stdscr.move(cursor_y, cursor_x)
                except curses.error:
                    pass
            else:
                self._hide_cursor()

            self.stdscr.refresh()

            # CoEdit tick — starts inline edit after first draw
            if on_tabs and 0 <= self.active_tab < len(self.tab_grids):
                ag = self.tab_grids[self.active_tab]
                if ag and HAS_CXGRID and isinstance(ag, CxGrid):
                    ag.coedit_tick()

            key = self.stdscr.getch()

            if key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    handle_mouse(mx, my, bstate)
                except curses.error:
                    pass
                continue

            # UDF panel has focus — route keys there (except F-keys)
            if self._udf_focused and self.udf_panel:
                if key == curses.KEY_F6:
                    focus_idx = cycle_focus_zone()
                    continue
                elif key == curses.KEY_F7:
                    self.udf_panel.schema_editor()
                    continue
                elif key == curses.KEY_F10 or key == 19:
                    self._udf_focused = False
                    self.udf_panel.focused = False
                    self._hide_cursor()
                    return "__save__"
                elif key == 27:
                    self._udf_focused = False
                    self.udf_panel.focused = False
                    continue
                else:
                    self.udf_panel.handle_key(key)
                    continue

            if key == 27:  # ESC — but might be start of escape sequence
                # If active tab grid is in filter mode, pass ESC to cancel filter
                if on_tabs and 0 <= self.active_tab < len(self.tab_grids):
                    ag = self.tab_grids[self.active_tab]
                    if ag and HAS_CXGRID and isinstance(ag, CxGrid) and ag.filter_mode:
                        ag.handle_input(key)
                        continue
                self.stdscr.nodelay(True)
                next_key = self.stdscr.getch()
                self.stdscr.nodelay(False)
                if next_key == -1:
                    # Real ESC press (no follow-up byte)
                    self._hide_cursor()
                    return ""
                elif next_key in (ord('c'), ord('C')) and on_tabs:
                    # Alt+C fallback on tab grid — open column config
                    if 0 <= self.active_tab < len(self.tab_grids):
                        ag = self.tab_grids[self.active_tab]
                        if ag and HAS_CXGRID and isinstance(ag, CxGrid):
                            ag._open_column_config()
                            self._save_column_config(ag)
                else:
                    # Other escape sequence — ignore
                    continue
            elif key == curses.KEY_F8 and on_tabs:  # F8 — column config
                if 0 <= self.active_tab < len(self.tab_grids):
                    ag = self.tab_grids[self.active_tab]
                    if ag and HAS_CXGRID and isinstance(ag, CxGrid):
                        ag._open_column_config()
                        self._save_column_config(ag)
                continue
            elif key == curses.KEY_F6:  # F6 cycle header/detail/UDF focus
                if self.layout_def.get("crud_tabs") and not self.tabs_def:
                    self._hide_cursor()
                    return "__crud_list__"
                focus_idx = cycle_focus_zone()
                continue
            elif key == curses.KEY_PPAGE and self.layout_def.get("crud_tabs"):
                self._hide_cursor()
                return "__crud_list__"
            elif key == curses.KEY_F7 and self.udf_panel and not on_tabs:  # F7 UDF Schema
                self.udf_panel.schema_editor()
                continue
            elif key == curses.KEY_F10 or key == 19:  # F10 or Ctrl+S Save
                self._hide_cursor()
                return "__save__"
            elif key == curses.KEY_F3:  # F3 New
                self._hide_cursor()
                return "__new__"
            elif key == curses.KEY_F5:  # F5 Post/Workflow
                if self.layout_def.get("workflow"):
                    self._hide_cursor()
                    return "__post__"
            elif key == 2:  # Ctrl+B — B2B Send Online
                self._hide_cursor()
                return "__b2b_send__"
            elif key == 16:  # Ctrl+P — Print to PDF
                try:
                    self._export_form_pdf()
                except Exception as e:
                    import traceback
                    try:
                        with open("errors.log", "a") as _ef:
                            traceback.print_exc(file=_ef)
                    except Exception:
                        pass
                    if self.status_line:
                        self.status_line.set_info(f"Print error: {e}")
                continue
            elif key == curses.KEY_F4:  # F4 Lookup on current field
                if on_tabs:
                    # Pass F4 to the active tab grid for column lookup
                    if 0 <= self.active_tab < len(self.tab_grids):
                        ag = self.tab_grids[self.active_tab]
                        if ag and HAS_CXGRID and isinstance(ag, CxGrid):
                            ag.handle_input(key)
                            if ag.save_requested:
                                ag.save_requested = False
                                self._hide_cursor()
                                return "__save__"
                elif focus_idx < len(self.widgets):
                    w = self.widgets[focus_idx]
                    if isinstance(w, LookupField):
                        old_val = w.value
                        w.handle_key(curses.KEY_F4)
                        if w.value != old_val:
                            self._auto_fill_lookup(w)
            elif on_tabs:
                # Tab bar is focused — pass keys to active tab grid for editing
                active_grid = None
                if 0 <= self.active_tab < len(self.tab_grids):
                    active_grid = self.tab_grids[self.active_tab]

                grid_editing = active_grid and (getattr(active_grid, 'edit_mode', False) or getattr(active_grid, 'filter_mode', False))
                num_tabs = len(self.tabs_def.get("items", []))

                if key == 5 and active_grid and HAS_CXGRID and isinstance(active_grid, CxGrid) and not grid_editing:
                    # Ctrl+E — toggle CoEdit on current column
                    if active_grid.coedit_mode:
                        active_grid.exit_coedit()
                    else:
                        col_name = active_grid.columns[active_grid.col].name
                        active_grid.coedit(col_name)
                elif (key == curses.KEY_BTAB or key == 353) and not grid_editing:
                    # Shift-Tab always leaves the tab zone (go back to form)
                    focus_idx = (focus_idx - 1) % total_items
                elif key == 9 and not grid_editing:  # Tab
                    if num_tabs > 1:
                        self.active_tab = (self.active_tab + 1) % num_tabs
                    else:
                        focus_idx = (focus_idx + 1) % total_items
                elif key in (curses.KEY_IC, curses.KEY_F3) and active_grid and HAS_CXGRID and isinstance(active_grid, CxGrid):
                    # Insert or F3 — add blank row seeded with defaults
                    new_row = {}
                    for c in active_grid.columns:
                        if c.numeric:
                            new_row[c.name] = 0
                        else:
                            new_row[c.name] = ""
                    active_grid.add_row(new_row)
                    self._recompute_header()
                elif (active_grid and HAS_CXGRID and isinstance(active_grid, CxGrid)
                      and self._handle_grid_uom_shortcut(key, active_grid, grid_editing)):
                    self._recompute_header()
                elif active_grid and HAS_CXGRID and isinstance(active_grid, CxGrid):
                    # Pass all other keys to the grid (navigation, editing, F4, etc.)
                    old_row_count = len(active_grid.data)
                    active_grid.handle_input(key)
                    if active_grid.save_requested:
                        active_grid.save_requested = False
                        self._hide_cursor()
                        return "__save__"
                    # Recompute header after any grid interaction that may
                    # change data (cell edit, row delete, lookup fill, etc.)
                    if self._computed_defs:
                        self._recompute_header()
                    elif len(active_grid.data) != old_row_count:
                        self._recompute_header()
            elif key == 9:  # Tab
                focus_idx = (focus_idx + 1) % total_items
            elif key == curses.KEY_BTAB or key == 353:  # Shift-Tab
                focus_idx = (focus_idx - 1) % total_items
            elif key == curses.KEY_DOWN:
                if focus_idx < len(self.widgets):
                    w = self.widgets[focus_idx]
                    if isinstance(w, MultiLineInput) and w.handle_key(key):
                        pass  # navigated within multi-line
                    else:
                        focus_idx = (focus_idx + 1) % total_items
                else:
                    focus_idx = (focus_idx + 1) % total_items
            elif key == curses.KEY_UP:
                if focus_idx < len(self.widgets):
                    w = self.widgets[focus_idx]
                    if isinstance(w, MultiLineInput) and w.handle_key(key):
                        pass  # navigated within multi-line
                    else:
                        focus_idx = (focus_idx - 1) % total_items
                else:
                    focus_idx = (focus_idx - 1) % total_items
            elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                if 0 <= btn_focus < len(self.buttons):
                    self._hide_cursor()
                    return self.buttons[btn_focus]["action"]
                elif focus_idx < len(self.widgets):
                    w = self.widgets[focus_idx]
                    # MultiLineInput handles Enter internally (moves between lines)
                    if isinstance(w, MultiLineInput) and w.handle_key(key):
                        pass  # stayed within multi-line group
                    else:
                        focus_idx = (focus_idx + 1) % total_items
            elif key == ord(' ') and focus_idx < len(self.widgets):
                # Space on Combobox/LookupField opens popup; otherwise pass to widget
                w = self.widgets[focus_idx]
                if isinstance(w, LookupField):
                    old_val = w.value
                    w.handle_key(key)
                    if w.value != old_val:
                        self._auto_fill_lookup(w)
                elif isinstance(w, Combobox):
                    w.handle_key(key)
                else:
                    w.handle_key(key)
            elif focus_idx < len(self.widgets):
                w = self.widgets[focus_idx]
                if isinstance(w, LookupField):
                    old_val = w.value
                    w.handle_key(key)
                    if w.value != old_val:
                        self._auto_fill_lookup(w)
                else:
                    w.handle_key(key)

    def _run_grid_loop(self) -> str:
        """Input loop for layouts with a cxgrid and buttons."""
        if self.cxgrids:
            grid = self.cxgrids[0]
            while True:
                self.stdscr.clear()
                self._draw_frame()
                grid.draw()
                self._draw_buttons()

                # Row count + view label + hotkey bar
                try:
                    max_y, max_x = self.stdscr.getmaxyx()
                    row_count = len(grid.data)
                    info = f"Rows: {row_count}"
                    if self.layout_def.get("crud_tabs"):
                        info += "  [Tab: Form and Edit]  [PgUp/PgDn: Page]"
                    # Show current LISTVIEW label if defined
                    grid_id = getattr(grid, '_grid_id', '')
                    gdef = self.grid_defs.get(grid_id, {})
                    views = gdef.get("list_views", [])
                    if views:
                        vi = getattr(grid, '_view_idx', 0)
                        vlabel = views[vi]["label"] if vi < len(views) else ""
                        info += f"  [F2: {vlabel}]"
                    self.stdscr.addstr(max_y - 2, 1, info, curses.A_DIM)
                except curses.error:
                    pass
                self._draw_hotkey_bar("grid")

                self.stdscr.refresh()
                key = self.stdscr.getch()

                if key == 27:
                    # Check if cxgrid wants to handle ESC (e.g., coedit/filter mode)
                    if grid.coedit_mode or grid.edit_mode or grid.filter_mode:
                        grid.handle_input(key)
                    else:
                        # Check for Alt+key sequences before treating as plain ESC
                        self.stdscr.nodelay(True)
                        next_key = self.stdscr.getch()
                        self.stdscr.nodelay(False)
                        if next_key == -1:
                            return ""  # Real ESC
                        elif next_key in (ord('c'), ord('C')):
                            grid._open_column_config()
                            self._save_column_config(grid)
                        elif next_key == curses.KEY_LEFT:
                            grid.scroll_left()
                        elif next_key == curses.KEY_RIGHT:
                            grid.scroll_right()
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    # Edit selected row
                    if grid.data:
                        return "__edit__"
                elif key == curses.KEY_F3:
                    return "__new__"
                elif key == 9 and self.layout_def.get("crud_tabs"):
                    # Tab switches to the Form and Edit tab. PgUp/PgDn stay
                    # with the grid so Page Up/Down page through the list.
                    return "__crud_form__"
                elif key == curses.KEY_F8:  # F8 — column config
                    grid._open_column_config()
                    self._save_column_config(grid)
                elif key == curses.KEY_F2:  # F2 — cycle list view
                    grid_id = getattr(grid, '_grid_id', '')
                    gdef = self.grid_defs.get(grid_id, {})
                    views = gdef.get("list_views", [])
                    if views:
                        return "__cycle_view__"
                elif key == 4:  # Ctrl+D delete
                    if grid.data and grid._view_indices:
                        row = grid._view_row(grid.row)
                        if row and row.get("rowid") is not None:
                            if self._confirm_action("Delete selected row? (Y/N)",
                                                    title="Confirm Delete"):
                                grid.delete_current_row()
                                return ("__delete__", row["rowid"])
                else:
                    grid.handle_input(key)
        else:
            # Basic grid fallback
            self.draw_all()
            while True:
                key = self.stdscr.getch()
                if key == 27:
                    return ""
                elif key == curses.KEY_F3:
                    return "__new__"
                elif key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
                    return "__edit__"
