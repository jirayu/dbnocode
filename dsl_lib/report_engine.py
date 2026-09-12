"""Report Engine — pandas-powered data transformation with dict fallback.

Functions: load_report_data, apply_where, apply_runtime_filters,
           apply_formulas, apply_order, build_listing, build_summary,
           build_crosstab.
"""
from datetime import date, datetime
import json
import re
from typing import List, Dict, Any, Tuple, Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ── Data loading ────────────────────────────────────────────────────

def load_report_data(db, source: str, mode: str = "docs") -> List[dict]:
    """Load rows from a table (JSON memo storage).

    mode='docs'  — one row per document (default, backward compatible)
    mode='lines' — master-detail expansion: one row per line item,
                   header fields merged into each line row
    """
    if not db:
        return []
    try:
        rows = db.query(f'SELECT rowid, data FROM "{source}"')
    except Exception:
        return []

    docs = []
    child_docs = []
    for row in rows:
        try:
            doc = json.loads(row.get("data", "{}"))
            doc["_rowid"] = row.get("rowid")
            if doc.get("_parent_rowid") is not None:
                child_docs.append(doc)
                continue
            # Skip empty records (no real data keys)
            real_keys = [k for k in doc
                         if k != "rowid" and not k.startswith("_")]
            if not real_keys:
                continue
            # Flatten UDF fields into top-level so reports can reference them
            udf = doc.pop("udf", None)
            if isinstance(udf, dict):
                for uk, uv in udf.items():
                    if uk not in doc:  # don't overwrite real fields
                        doc[uk] = uv
            docs.append(doc)
        except (json.JSONDecodeError, TypeError):
            pass

    if mode != "lines":
        return docs

    # A report may use the physical child table as its source (for example
    # so_line) rather than a parent document containing an embedded `lines`.
    if child_docs:
        parent_tables = [source[:-5]] if source.endswith("_line") else []
        if source == "explosion_line":
            parent_tables.insert(0, "bom_explosion")
        parents = {}
        for parent_table in parent_tables:
            try:
                for parent_row in db.query(
                        f'SELECT rowid, data FROM "{parent_table}"'):
                    parent = json.loads(parent_row.get("data", "{}"))
                    parents[parent_row.get("rowid")] = parent
            except Exception:
                continue
        result = []
        for child in child_docs:
            merged = dict(parents.get(child.get("_parent_rowid"), {}))
            merged.update(child)
            result.append(merged)
        return result

    # Lines mode: expand each doc's "lines" array into separate rows
    result = []
    for doc in docs:
        header = {k: v for k, v in doc.items() if k != "lines"}
        lines = doc.get("lines")
        if not isinstance(lines, list) or not lines:
            result.append(header)
            continue
        for ln in lines:
            if isinstance(ln, dict):
                merged = dict(header)
                merged.update(ln)
                result.append(merged)
    return result


# ── WHERE filtering ─────────────────────────────────────────────────

_WHERE_RE = re.compile(
    r'(\w+)\s*(=|!=|<>|>=|<=|>|<|LIKE)\s*(.+)',
    re.I
)
_BETWEEN_RE = re.compile(r'^BETWEEN\s+(.+?)\s+AND\s+(.+)$', re.I)


def apply_where(rows: List[dict], where_str: str,
                params: Optional[dict] = None) -> List[dict]:
    """Filter rows by WHERE clause with {param} substitution."""
    if not where_str or not rows:
        return rows
    # Substitute {param} tokens
    if params:
        for k, v in params.items():
            where_str = where_str.replace(f'{{{k}}}', str(v))
    # Parse AND-separated clauses
    clauses = re.split(r'\s+AND\s+', where_str, flags=re.I)
    parsed = []
    for clause in clauses:
        m = _WHERE_RE.match(clause.strip())
        if m:
            field = m.group(1)
            op = m.group(2).upper()
            val = m.group(3).strip().strip("'\"")
            parsed.append((field, op, val))
    if not parsed:
        return rows
    result = []
    for row in rows:
        if _row_matches(row, parsed):
            result.append(row)
    return result


def apply_runtime_filters(rows: List[dict], filter_map: Dict[str, str],
                          columns: Optional[List[dict]] = None) -> List[dict]:
    """Apply interactive per-column report filters.

    Supports text contains plus explicit operators such as `=`, `!=`, `<>`,
    `>`, `>=`, `<`, `<=`, and inclusive ranges using `BETWEEN a AND b`
    or `a..b`.
    """
    if not rows or not filter_map:
        return rows
    col_map = {c.get("id"): c for c in (columns or []) if c.get("id")}
    active = {k: (v or "").strip() for k, v in filter_map.items()
              if (v or "").strip()}
    if not active:
        return rows
    result = []
    for row in rows:
        ok = True
        for col_id, filter_text in active.items():
            col_def = col_map.get(col_id, {})
            if not matches_runtime_filter(
                    row.get(col_id, ""), filter_text,
                    col_def.get("type") == "FLOAT"):
                ok = False
                break
        if ok:
            result.append(row)
    return result


def _row_matches(row: dict, clauses: list) -> bool:
    for field, op, expected in clauses:
        actual = row.get(field, "")
        if actual is None:
            actual = ""
        if not _compare(actual, op, expected):
            return False
    return True


def _compare(actual, op: str, expected: str) -> bool:
    # Try numeric comparison
    try:
        a = float(str(actual).replace(",", ""))
        e = float(expected.replace(",", ""))
        if op == "=":
            return a == e
        elif op in ("!=", "<>"):
            return a != e
        elif op == ">":
            return a > e
        elif op == ">=":
            return a >= e
        elif op == "<":
            return a < e
        elif op == "<=":
            return a <= e
    except (ValueError, TypeError):
        pass
    # String comparison
    a_str = str(actual)
    if op == "=":
        return a_str == expected
    elif op in ("!=", "<>"):
        return a_str != expected
    elif op == "LIKE":
        pattern = expected.replace("%", ".*").replace("_", ".")
        return bool(re.match(pattern, a_str, re.I))
    return a_str == expected


def matches_runtime_filter(actual, filter_text: str,
                           numeric_hint: bool = False) -> bool:
    """Evaluate one interactive filter against one value."""
    ft = (filter_text or "").strip()
    if not ft:
        return True
    if actual is None:
        actual = ""

    between = _parse_between_filter(ft)
    if between is not None:
        lower, upper = between
        return (_compare_runtime(actual, ">=", lower, numeric_hint)
                and _compare_runtime(actual, "<=", upper, numeric_hint))

    for op in (">=", "<=", "!=", "<>", ">", "<", "="):
        if ft.startswith(op):
            return _compare_runtime(actual, op, ft[len(op):].strip(),
                                    numeric_hint)

    if numeric_hint:
        return _compare_runtime(actual, "=", ft, numeric_hint=True)
    return ft.lower() in str(actual).lower()


def _parse_between_filter(filter_text: str):
    m = _BETWEEN_RE.match(filter_text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if ".." in filter_text:
        left, right = filter_text.split("..", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    return None


def _compare_runtime(actual, op: str, expected: str,
                     numeric_hint: bool = False) -> bool:
    """Comparison helper for interactive report filters."""
    a_num = _to_number(actual)
    e_num = _to_number(expected)
    if numeric_hint and a_num is not None and e_num is not None:
        return _eval_compare(a_num, op, e_num)

    a_date = _to_date(actual)
    e_date = _to_date(expected)
    if a_date is not None and e_date is not None:
        return _eval_compare(a_date, op, e_date)

    if a_num is not None and e_num is not None:
        return _eval_compare(a_num, op, e_num)

    a_str = str(actual).strip()
    e_str = str(expected).strip()
    if op == "=":
        return a_str.lower() == e_str.lower()
    if op in ("!=", "<>"):
        return a_str.lower() != e_str.lower()
    return _eval_compare(a_str.lower(), op, e_str.lower())


def _eval_compare(left, op: str, right) -> bool:
    if op == "=":
        return left == right
    if op in ("!=", "<>"):
        return left != right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def _to_number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _to_date(value):
    text = str(value).strip()
    if not text:
        return None
    for parser in (date.fromisoformat, datetime.fromisoformat):
        try:
            parsed = parser(text)
            return parsed.date() if isinstance(parsed, datetime) else parsed
        except (ValueError, TypeError):
            continue
    return None


# ── Formula evaluation ──────────────────────────────────────────────

def _IF(cond, true_val, false_val=0):
    return true_val if cond else false_val

def _ISBLANK(v):
    return str(v).strip() == '' or v is None

_FORMULA_GLOBALS = {
    '__builtins__': {},
    'IF': _IF, 'ROUND': round, 'MAX': max, 'MIN': min,
    'ABS': abs, 'INT': int, 'ISBLANK': _ISBLANK,
    'FLOOR': lambda x: int(x),
    'CEIL': lambda x: int(x) + (1 if x % 1 else 0),
    # lowercase aliases
    'round': round, 'max': max, 'min': min, 'abs': abs, 'int': int,
}


def apply_formulas(rows: List[dict], columns: List[dict]) -> List[dict]:
    """Evaluate formula columns on each row (left-to-right so chaining works)."""
    formula_cols = [(c["id"], c["formula"]) for c in columns if c.get("formula")]
    if not formula_cols:
        return rows
    for row in rows:
        for col_id, formula in formula_cols:
            row[col_id] = _eval_formula(formula, row)
    return rows


def _eval_formula(formula: str, row: dict):
    """Evaluate a formula expression with spreadsheet-style functions.

    Available: IF(cond, true, false), ROUND, MAX, MIN, ABS, INT,
               FLOOR, CEIL, ISBLANK
    Example: 'IF(discount > 0, qty * price * (1 - discount/100), qty * price)'
    """
    ns = dict(_FORMULA_GLOBALS)
    for k, v in row.items():
        if k.startswith("_"):
            continue
        try:
            ns[k] = float(str(v).replace(",", "")) if v not in (None, "") else 0.0
        except (ValueError, TypeError):
            ns[k] = 0.0
    try:
        result = eval(formula, ns)
        if isinstance(result, bool):
            return 1 if result else 0
        return result
    except ZeroDivisionError:
        return 0
    except Exception:
        return 0


# ── Sorting ─────────────────────────────────────────────────────────

def apply_order(rows: List[dict], order_str: str) -> List[dict]:
    """Sort rows by comma-separated field names (- prefix for desc)."""
    if not order_str or not rows:
        return rows
    fields = []
    for f in order_str.split(","):
        f = f.strip()
        if f.startswith("-"):
            fields.append((f[1:], False))
        else:
            fields.append((f, True))

    def sort_key(row):
        keys = []
        for fname, asc in fields:
            val = row.get(fname, "")
            if val is None:
                val = ""
            try:
                keys.append(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                keys.append(str(val).lower())
        return keys

    try:
        # Stable right-to-left sorts support mixed ascending/descending fields.
        result = list(rows)
        for index, (fname, asc) in reversed(list(enumerate(fields))):
            result.sort(key=lambda row, i=index: sort_key(row)[i],
                        reverse=not asc)
        return result
    except TypeError:
        return rows


# ── Listing with control breaks ─────────────────────────────────────

def build_listing(rows: List[dict], groups: List[dict],
                  totals: Optional[dict],
                  sum_fields: set) -> List[tuple]:
    """Build display list with group headers, subtotals, grand total.

    Returns list of tuples:
      ("data", row_dict)
      ("group_header", label_str, level)
      ("group_summary", agg_dict, label_str, level)
      ("grand_total", agg_dict)
    """
    if not rows:
        return []
    if not groups:
        # No grouping — just data + optional grand total
        display = [("data", row) for row in rows]
        if totals and sum_fields:
            agg = _compute_agg(rows, sum_fields)
            display.append(("grand_total", agg))
        return display

    # Get all group field lists (outermost first)
    group_fields = groups[0]["fields"] if groups else []

    display = []
    prev_key = None
    group_rows = []

    for row in rows:
        cur_key = tuple(str(row.get(g, "")) for g in group_fields)
        if prev_key is not None and cur_key != prev_key:
            # Emit subtotal for previous group
            if sum_fields:
                agg = _compute_agg(group_rows, sum_fields)
                label = _format_group_label(group_fields, prev_key,
                                            groups[0].get("label", "Subtotal"))
                display.append(("group_summary", agg, label, 0))
            group_rows = []
            # Emit header for new group
            header_label = _format_group_header(group_fields, cur_key)
            display.append(("group_header", header_label, 0))
        elif prev_key is None:
            header_label = _format_group_header(group_fields, cur_key)
            display.append(("group_header", header_label, 0))

        display.append(("data", row))
        group_rows.append(row)
        prev_key = cur_key

    # Final group subtotal
    if group_rows and sum_fields:
        agg = _compute_agg(group_rows, sum_fields)
        label = _format_group_label(group_fields, prev_key,
                                    groups[0].get("label", "Subtotal"))
        display.append(("group_summary", agg, label, 0))

    # Grand total
    if totals and sum_fields:
        agg = _compute_agg(rows, sum_fields)
        display.append(("grand_total", agg))

    return display


def _compute_agg(rows: List[dict], sum_fields: set) -> dict:
    """Compute sum aggregation for the given fields."""
    if HAS_PANDAS and rows:
        try:
            df = pd.DataFrame(rows)
            agg = {}
            for f in sum_fields:
                if f in df.columns:
                    agg[f] = pd.to_numeric(df[f], errors='coerce').fillna(0).sum()
                else:
                    agg[f] = 0
            return agg
        except Exception:
            pass
    # Fallback
    agg = {f: 0.0 for f in sum_fields}
    for row in rows:
        for f in sum_fields:
            try:
                agg[f] += float(str(row.get(f, 0) or 0).replace(",", ""))
            except (ValueError, TypeError):
                pass
    return agg


def _format_group_header(fields: list, key_tuple: tuple) -> str:
    parts = []
    for i, f in enumerate(fields):
        val = key_tuple[i] if i < len(key_tuple) else ""
        parts.append(f"{f}: {val}")
    return " | ".join(parts)


def _format_group_label(fields: list, key_tuple: tuple, label: str) -> str:
    return label


# ── Summary view ────────────────────────────────────────────────────

def build_summary(rows: List[dict], group_key: str,
                  sum_fields: List[str]) -> Tuple[List[str], List[dict]]:
    """Group rows by group_key, aggregate sum_fields.

    Returns (column_names, result_rows).
    """
    if not rows or not group_key:
        return [], []

    if HAS_PANDAS:
        try:
            df = pd.DataFrame(rows)
            if group_key not in df.columns:
                return [], []
            for sf in sum_fields:
                if sf in df.columns:
                    df[sf] = pd.to_numeric(df[sf], errors='coerce').fillna(0)
            grp = df.groupby(group_key, dropna=False)
            result = []
            for gk, gdf in grp:
                row = {"_group": str(gk or ""), "_count": len(gdf)}
                for sf in sum_fields:
                    row[sf] = float(gdf[sf].sum()) if sf in gdf.columns else 0
                result.append(row)
            # Grand total
            grand = {"_group": "TOTAL", "_count": len(df)}
            for sf in sum_fields:
                grand[sf] = float(df[sf].sum()) if sf in df.columns else 0
            result.append(grand)
            col_names = ["_group", "_count"] + list(sum_fields)
            return col_names, result
        except Exception:
            pass

    # Fallback
    groups = {}
    for r in rows:
        k = str(r.get(group_key, "") or "")
        if k not in groups:
            groups[k] = {"_group": k, "_count": 0}
            for sf in sum_fields:
                groups[k][sf] = 0.0
        groups[k]["_count"] += 1
        for sf in sum_fields:
            try:
                groups[k][sf] += float(str(r.get(sf, 0) or 0).replace(",", ""))
            except (ValueError, TypeError):
                pass
    result = sorted(groups.values(), key=lambda x: x["_group"])
    grand = {"_group": "TOTAL", "_count": len(rows)}
    for sf in sum_fields:
        grand[sf] = sum(g[sf] for g in result)
    result.append(grand)
    col_names = ["_group", "_count"] + list(sum_fields)
    return col_names, result


# ── Crosstab view ───────────────────────────────────────────────────

def build_crosstab(rows: List[dict], row_field: str, col_field: str,
                   val_field: str, agg: str = "sum",
                   row_total: bool = True,
                   col_total: bool = True) -> Tuple[List[str], List[dict]]:
    """Build a crosstab (pivot table).

    Returns (column_names, result_rows).
    """
    if not rows or not row_field or not col_field or not val_field:
        return [], []

    if HAS_PANDAS:
        try:
            df = pd.DataFrame(rows)
            if row_field not in df.columns or col_field not in df.columns:
                return [], []
            df[val_field] = pd.to_numeric(df[val_field], errors='coerce').fillna(0)
            pt = pd.pivot_table(
                df, values=val_field, index=row_field,
                columns=col_field, aggfunc=agg, fill_value=0,
                margins=row_total or col_total,
                margins_name="TOTAL"
            )
            # Convert to list of dicts
            result = []
            col_names_raw = [str(c) for c in pt.columns]
            for idx, row_data in pt.iterrows():
                r = {"_row": str(idx)}
                for c in col_names_raw:
                    r[c] = float(row_data[c]) if c != "TOTAL" or row_total else 0
                result.append(r)
            # Remove TOTAL row if col_total is False
            if not col_total and result and result[-1]["_row"] == "TOTAL":
                result = result[:-1]
            # Remove TOTAL column if row_total is False
            if not row_total and "TOTAL" in col_names_raw:
                col_names_raw.remove("TOTAL")
                for r in result:
                    r.pop("TOTAL", None)
            col_names = ["_row"] + col_names_raw
            return col_names, result
        except Exception:
            pass

    # Fallback — manual crosstab
    col_vals = sorted({str(r.get(col_field, "") or "") for r in rows})
    agg_map = {}
    for r in rows:
        rk = str(r.get(row_field, "") or "")
        ck = str(r.get(col_field, "") or "")
        try:
            v = float(str(r.get(val_field, 0) or 0).replace(",", ""))
        except (ValueError, TypeError):
            v = 0
        if rk not in agg_map:
            agg_map[rk] = {}
        agg_map[rk][ck] = agg_map[rk].get(ck, 0) + v

    result = []
    for rk in sorted(agg_map.keys()):
        row = {"_row": rk}
        rtotal = 0
        for ck in col_vals:
            val = agg_map[rk].get(ck, 0)
            row[ck] = val
            rtotal += val
        if row_total:
            row["TOTAL"] = rtotal
        result.append(row)

    # Grand total row
    if col_total:
        grand = {"_row": "TOTAL"}
        gtotal = 0
        for ck in col_vals:
            cs = sum(agg_map[rk].get(ck, 0) for rk in agg_map)
            grand[ck] = cs
            gtotal += cs
        if row_total:
            grand["TOTAL"] = gtotal
        result.append(grand)

    col_names = ["_row"] + col_vals
    if row_total:
        col_names.append("TOTAL")
    return col_names, result
