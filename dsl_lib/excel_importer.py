"""Import an Excel workbook as a compact-DSL, tabbed CRUD application.

The importer intentionally targets dbnocode's native storage model: one JSON
document per SQLite row.  It does not retain a runtime dependency on the source
workbook after the generated script and database have been created.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any


_PRIVATE_SHEET = re.compile(r"^_")
_DEFAULT_SHEET = re.compile(r"^(sheet|tabelle|feuil|hoja|foglio)\d*$", re.I)
_TEXT_HINT = re.compile(
    r"sku|code|barcode|zip|postal|phone|fax|mobile|tel|part.?no|item.?no|"
    r"serial|reference|account.?no",
    re.I,
)
_RESERVED_TABLES = {
    "business_settings", "wh_locations", "b2b_inbox", "b2b_outbox"
}
_RESERVED_FIELDS = {"rowid", "_parent_rowid"}


@dataclass
class ImportedForm:
    name: str
    title: str
    fields: list[dict[str, Any]]
    rows: list[dict[str, Any]]


@dataclass
class ExcelImportResult:
    script_path: Path
    database_path: Path
    forms: int
    rows: int


@dataclass
class ExcelDefinitionResult:
    script_path: Path
    forms_data: list[ImportedForm]

    @property
    def forms(self) -> int:
        return len(self.forms_data)

    @property
    def rows(self) -> int:
        return sum(len(form.rows) for form in self.forms_data)


def slugify(value: Any, fallback: str = "field") -> str:
    """Turn arbitrary cell text into a stable field/form identifier.

    Latin text is decomposed and reduced to ASCII (``São Paulo`` →
    ``sao_paulo``); non-Latin headers (Thai, CJK, Arabic...) keep their scripts
    verbatim, including combining marks, so ``การส่งซื้อ`` stays ``การส่งซื้อ``.
    Without this, sheets with non-Latin headers collapse to a single ``field``
    and the true header row is mistaken for data.
    """

    def _keep(text: str) -> str:
        kept = []
        for ch in text:
            if ch == "_" or ch.isalnum() or unicodedata.category(ch) == "Mn":
                kept.append(ch)
            elif kept:
                kept.append("_")
        return re.sub(r"_+", "_", "".join(kept)).strip("_")

    original = str(value or "").strip()
    ascii_slug = _keep(unicodedata.normalize("NFKD", original)).lower()
    text = ascii_slug if ascii_slug else _keep(original).lower()
    if not text:
        return fallback
    if text[0].isdigit():
        text = "col_" + text
    return text


def _dedupe(value: str, used: set[str]) -> str:
    if value not in used:
        used.add(value)
        return value
    suffix = 2
    while f"{value}_{suffix}" in used:
        suffix += 1
    value = f"{value}_{suffix}"
    used.add(value)
    return value


def _field_name(value: Any, used: set[str]) -> str:
    name = slugify(value)
    if name in _RESERVED_FIELDS:
        name = "source_" + name.lstrip("_")
    return _dedupe(name, used)


def _form_name(value: Any, used: set[str]) -> str:
    name = slugify(value, "sheet")
    if name in _RESERVED_TABLES:
        name = "imported_" + name
    return _dedupe(name, used)


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _cell_fill(cell) -> str:
    fill = getattr(cell, "fill", None)
    if not fill or fill.patternType != "solid":
        return ""
    rgb = getattr(fill.fgColor, "rgb", "")
    return str(rgb or "").upper()[-6:]


def _infer_type(label: str, values: list[Any], number_formats: list[str]) -> str:
    try:
        from openpyxl.styles.numbers import is_date_format
    except ImportError as exc:  # pragma: no cover - handled by the CLI too
        raise RuntimeError("Excel import requires openpyxl>=3.1") from exc
    if any(fmt and is_date_format(fmt) for fmt in number_formats):
        return "date"
    if _TEXT_HINT.search(label):
        return "text"
    samples = [v for v in values if v not in (None, "")]
    if any(isinstance(v, (datetime, date)) for v in samples):
        return "date"
    numeric = [v for v in samples if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if samples and len(numeric) == len(samples):
        return "number"
    return "text"


def _table_header(ws) -> tuple[int, list[int]] | None:
    """Choose the most plausible rectangular header row in a worksheet."""
    max_row = min(ws.max_row or 0, 100)
    max_col = min(ws.max_column or 0, 80)
    best = None
    for row_no in range(1, max_row + 1):
        columns = [
            col for col in range(1, max_col + 1)
            if isinstance(ws.cell(row_no, col).value, str)
            and ws.cell(row_no, col).value.strip()
        ]
        if len(columns) < 2:
            continue
        # Blue/yellow cells are form values, not table headings. Without this
        # guard a label/value pair such as "Order Number | SO-1" can outscore
        # the real line-item header farther down the sheet.
        if any(_cell_fill(ws.cell(row_no, col)) in {"CCE5FF", "FFFF99"}
               for col in columns):
            continue
        normalized = [slugify(ws.cell(row_no, col).value) for col in columns]
        if len(set(normalized)) < max(2, len(normalized) // 2):
            continue
        populated = 0
        for probe_row in range(row_no + 1, min(max_row, row_no + 12) + 1):
            count = sum(ws.cell(probe_row, col).value not in (None, "") for col in columns)
            if count >= max(1, len(columns) // 2):
                populated += 1
        # Prefer rows followed by data, then wider rows, then earlier rows.
        score = populated * 100 + len(columns) * 2 - row_no
        if best is None or score > best[0]:
            best = (score, row_no, columns)
    if not best or best[0] < 4:
        return None
    return best[1], best[2]


def _read_table(ws, header_row: int, columns: list[int]) -> ImportedForm:
    used: set[str] = set()
    fields = []
    for col in columns:
        label = str(ws.cell(header_row, col).value).strip()
        fields.append({
            "name": _field_name(label, used),
            "label": label,
            "column": col,
        })

    raw_rows: list[tuple[int, list[Any]]] = []
    blanks = 0
    last_row = min(ws.max_row or header_row, header_row + 5000)
    for row_no in range(header_row + 1, last_row + 1):
        values = [ws.cell(row_no, item["column"]).value for item in fields]
        if all(value in (None, "") for value in values):
            blanks += 1
            if blanks >= 3:
                break
            continue
        blanks = 0
        raw_rows.append((row_no, values))

    for index, field in enumerate(fields):
        values = [row[index] for _, row in raw_rows]
        formats = [
            ws.cell(row_no, field["column"]).number_format
            for row_no, _ in raw_rows[:20]
        ]
        field["type"] = _infer_type(field["label"], values[:20], formats)
        display_lengths = [len(str(value)) for value in values[:100] if value not in (None, "")]
        field["width"] = min(50, max(8, len(field["label"]), *(display_lengths or [0])))

    records = [
        {field["name"]: _json_value(value) for field, value in zip(fields, row)}
        for _, row in raw_rows
    ]
    return ImportedForm(slugify(ws.title, "sheet"), ws.title, fields, records)


def _read_styled_form(ws) -> ImportedForm | None:
    """Read NocodeXL-style blue/yellow cells when a sheet has no table."""
    used: set[str] = set()
    fields, record = [], {}
    for row in ws.iter_rows(max_row=min(ws.max_row or 1, 200),
                            max_col=min(ws.max_column or 1, 60)):
        for cell in row:
            fill = _cell_fill(cell)
            if fill not in {"CCE5FF", "FFFF99"}:
                continue
            label_value = None
            if cell.column > 1:
                label_value = ws.cell(cell.row, cell.column - 1).value
            if not isinstance(label_value, str) or not label_value.strip():
                label_value = ws.cell(max(1, cell.row - 1), cell.column).value
            label = str(label_value or f"Field {cell.coordinate}").strip().rstrip(":")
            name = _field_name(label, used)
            field_type = _infer_type(label, [cell.value], [cell.number_format])
            fields.append({
                "name": name, "label": label, "type": field_type,
                "width": min(50, max(8, len(label), len(str(cell.value or "")))),
                "readonly": fill == "FFFF99",
                "required": bool(getattr(cell.font, "bold", False)),
                "_source_row": cell.row,
            })
            record[name] = _json_value(cell.value)
    if not fields:
        return None
    rows = [record] if any(value not in (None, "") for value in record.values()) else []
    return ImportedForm(slugify(ws.title, "sheet"), ws.title, fields, rows)


def analyze_workbook(path: str | Path) -> list[ImportedForm]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError(
            "Excel import requires openpyxl>=3.1; run pip install -r requirements.txt"
        ) from exc
    workbook_path = Path(path).resolve()
    if workbook_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("Excel import supports .xlsx and .xlsm workbooks")
    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    try:
        forms: list[ImportedForm] = []
        used_names: set[str] = set()
        for ws in workbook.worksheets:
            if _PRIVATE_SHEET.match(ws.title):
                continue
            if _DEFAULT_SHEET.match(ws.title) and (ws.max_row or 1) <= 1 \
                    and (ws.max_column or 1) <= 1 and ws["A1"].value in (None, ""):
                continue
            detected = _table_header(ws)
            styled = _read_styled_form(ws)
            if styled:
                styled.name = _form_name(styled.name, used_names)
                forms.append(styled)
                # Invoice-style sheets may contain a styled header form and a
                # separate line table farther down. Preserve both datasets.
                last_styled_row = max(
                    field.get("_source_row", 0) for field in styled.fields)
                if detected and detected[0] > last_styled_row + 1:
                    table = _read_table(ws, *detected)
                    table.name = _form_name(f"{styled.name}_lines", used_names)
                    table.title = f"{ws.title} Lines"
                    forms.append(table)
                continue
            if detected:
                form = _read_table(ws, *detected)
                form.name = _form_name(form.name, used_names)
                forms.append(form)
        if not forms:
            raise ValueError("No importable tables or styled form fields were found")
        return forms
    finally:
        workbook.close()


def _quoted(value: str) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace('"', "'")


def generate_dsl(app_name: str, datasource: str,
                 forms: list[ImportedForm]) -> str:
    lines = [
        f'APP "{_quoted(app_name)}" VERSION "1.0"',
        f'DATASOURCE "{_quoted(datasource)}" ADAPTER "sqlite"',
        "",
    ]
    names = {form.name for form in forms}
    for form in forms:
        # Classic (non-TABBED) CRUD form so imported sheets render like the
        # app's existing forms: a browse list page and a separate add/edit
        # page. No tab-strip navigation required.
        lines.append(f'FORM {form.name} TITLE "{_quoted(form.title)}" COLS 2')
        for field in form.fields:
            flags = []
            if field.get("type") == "number":
                flags.append("num")
            elif field.get("type") == "date":
                flags.append("date")
            if field.get("required"):
                flags.append("req")
            if field.get("readonly"):
                flags.append("ro")
            if field["name"].endswith("_id"):
                base = field["name"][:-3]
                lookup = next((name for name in names
                               if name in {base, base + "s", base + "es"}), None)
                if lookup and lookup != form.name:
                    flags.append(f"lookup:{lookup}")
            lines.append(
                f'  {field["name"]} | {field["width"]} | {" ".join(flags)}'.rstrip()
            )
        visible = [f'{field["name"]}:{min(24, field["width"])}'
                   for field in form.fields]
        lines.append("  LIST " + " ".join(visible))
        lines.extend(["END", ""])

    lines.extend([f'MENU "{_quoted(app_name)}"', '  GROUP "Imported Sheets"'])
    for form in forms:
        lines.append(f'    "{_quoted(form.title)}" => {form.name}')
    lines.extend(['    ---', '    "Exit" => EXIT HOTKEY ESC', "  END", "  CENTER"])
    for index, form in enumerate(forms, 1):
        lines.append(f'    "{index}. {_quoted(form.title)}" => {form.name}')
    lines.extend([f'    "{len(forms) + 1}. Exit" => EXIT', "  END", "END", ""])
    return "\n".join(lines)


def _validate_generated_dsl(text: str):
    from .compact_parser import CompactDSLParser
    from .validator import DSLValidator, validate_app_definition

    result = DSLValidator().validate(text, compact=True)
    if not result.valid:
        raise RuntimeError("Generated DSL failed validation: " + "; ".join(
            error.message for error in result.errors))
    app = CompactDSLParser(text).parse()
    semantic = validate_app_definition(app)
    if not semantic.valid:
        raise RuntimeError("Generated DSL failed semantic validation: " + "; ".join(
            error.message for error in semantic.errors))


def merge_imported_app(current: dict, imported: dict) -> list[str]:
    """Merge generated Excel forms into an already parsed application.

    The current application's metadata, datasource, and main menu remain
    authoritative. Imported form/list layouts and actions are added in place,
    and their list actions are exposed under an ``Imported Excel`` menu group.
    Returns the imported base form names.
    """
    form_names = [
        form_id.removesuffix("_form")
        for form_id in (imported.get("forms") or {})
        if form_id.endswith("_form")
    ]
    for section in ("forms", "grids", "reports", "subroutines"):
        current.setdefault(section, {}).update(imported.get(section) or {})
    current.setdefault("layouts", {}).update({
        key: value for key, value in (imported.get("layouts") or {}).items()
        if key != "main_menu"
    })
    imported_actions = {
        key: value for key, value in (imported.get("actions") or {}).items()
        if key.startswith("goto_") and key not in {"goto_main"}
    }
    current.setdefault("actions", {}).update(imported_actions)

    menu_items = []
    for name in form_names:
        action = f"goto_{name}_list"
        if action not in current["actions"]:
            continue
        title = (current["layouts"].get(f"add_{name}") or {}).get(
            "title", name.replace("_", " ").title())
        menu_items.append({"label": title, "action": action})

    main_menu = current.get("layouts", {}).get("main_menu")
    if not main_menu:
        return form_names
    imported_action_names = {item["action"] for item in menu_items}
    center = main_menu.get("center_menu")
    if center is not None:
        center[:] = [
            item for item in center
            if item.get("action") not in imported_action_names
        ]
        if not any(item.get("section") and item.get("label") == "Imported Excel"
                   for item in center):
            center.append({"label": "Imported Excel", "section": True})
        center.extend(dict(item) for item in menu_items)

    top_menus = main_menu.get("top_menus")
    if top_menus is not None:
        group = next((item for item in top_menus
                      if item.get("label") == "Imported Excel"), None)
        if group is None:
            group = {"label": "Imported Excel", "items": []}
            top_menus.append(group)
        group["items"] = [
            item for item in group.get("items", [])
            if item.get("action") not in imported_action_names
        ] + [dict(item) for item in menu_items]
        current["top_menus"] = top_menus
    return form_names


def _registry_path(script_path: str | Path) -> Path:
    script = Path(script_path).resolve()
    return script.with_name(script.name + ".imports.json")


def registered_form_names(script_path: str | Path) -> set[str]:
    """Return the base form names currently registered for ``script_path``.

    Used to warn before a re-import would overwrite an already-present form.
    """
    registry = _registry_path(script_path)
    names: set[str] = set()
    if not registry.exists():
        return names
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return names
    from .compact_parser import CompactDSLParser
    for stored in payload.get("imports") or []:
        imported_path = Path(stored)
        if not imported_path.is_absolute():
            imported_path = registry.parent / imported_path
        try:
            imported = CompactDSLParser(
                imported_path.read_text(encoding="utf-8")).parse()
        except Exception:
            continue
        for form_id in (imported.get("forms") or {}):
            if form_id.endswith("_form"):
                names.add(form_id[:-5])
    return names


def list_registered_imports(script_path: str | Path) -> list[dict[str, Any]]:
    """Describe every registered Excel import for ``script_path``.

    Each entry carries ``stored`` (registry key), ``path`` (resolved sidecar),
    ``forms`` (base form names) and ``error`` (when the sidecar failed to
    parse).
    """
    registry = _registry_path(script_path)
    if not registry.exists():
        return []
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    from .compact_parser import CompactDSLParser
    items: list[dict[str, Any]] = []
    for stored in payload.get("imports") or []:
        imported_path = Path(stored)
        if not imported_path.is_absolute():
            imported_path = registry.parent / imported_path
        forms, error = [], None
        try:
            imported = CompactDSLParser(
                imported_path.read_text(encoding="utf-8")).parse()
        except Exception as exc:
            error = str(exc).splitlines()[0] if str(exc) else "unreadable"
            imported = None
        if imported is not None:
            forms = [
                form_id[:-5] for form_id in (imported.get("forms") or {})
                if form_id.endswith("_form")
            ]
        items.append({
            "stored": stored,
            "path": imported_path,
            "forms": forms,
            "error": error,
        })
    return items


def unregister_import(script_path: str | Path,
                      stored: str) -> Path | None:
    """Remove ``stored`` from the import registry.

    Returns the resolved sidecar path (whether or not the file still exists),
    or None when there was nothing to remove.
    """
    registry = _registry_path(script_path)
    if not registry.exists():
        return None
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    entries = list(payload.get("imports") or [])
    if stored not in entries:
        return None
    entries.remove(stored)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text(json.dumps({"imports": entries}, indent=2),
                         encoding="utf-8")
    temporary.replace(registry)
    resolved = Path(stored)
    if not resolved.is_absolute():
        resolved = registry.parent / resolved
    return resolved


def drop_import_tables(db, form_names: list[str]):
    """Drop imported form tables from the caller's database adapter."""
    for name in form_names:
        try:
            db.execute(f'DROP TABLE IF EXISTS "{name}"', ())
        except Exception as exc:
            raise RuntimeError(
                f"Cannot drop table '{name}': {exc}") from exc


def register_import(script_path: str | Path,
                    imported_script: str | Path) -> Path:
    """Persist one generated import beside its host DSL script."""
    registry = _registry_path(script_path)
    entries = []
    if registry.exists():
        try:
            payload = json.loads(registry.read_text(encoding="utf-8"))
            entries = list(payload.get("imports") or [])
        except (OSError, ValueError, TypeError):
            entries = []
    imported_path = Path(imported_script).resolve()
    try:
        stored = str(imported_path.relative_to(registry.parent))
    except ValueError:
        stored = str(imported_path)
    if stored not in entries:
        entries.append(stored)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text(json.dumps({"imports": entries}, indent=2),
                         encoding="utf-8")
    temporary.replace(registry)
    return registry


def load_registered_imports(current: dict, script_path: str | Path) -> list[str]:
    """Merge every available sidecar-registered Excel definition at startup."""
    registry = _registry_path(script_path)
    if not registry.exists():
        return []
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return [f"Cannot read Excel import registry {registry}: {exc}"]
    warnings = []
    from .compact_parser import CompactDSLParser
    for stored in payload.get("imports") or []:
        imported_path = Path(stored)
        if not imported_path.is_absolute():
            imported_path = registry.parent / imported_path
        try:
            imported = CompactDSLParser(
                imported_path.read_text(encoding="utf-8")).parse()
            merge_imported_app(current, imported)
        except Exception as exc:
            warnings.append(f"Cannot load Excel import {imported_path}: {exc}")
    return warnings


def write_import_definition(
        path: str | Path, script_path: str | Path,
        datasource: str = "current_database", force: bool = False,
        forms: list[ImportedForm] | None = None) -> ExcelDefinitionResult:
    """Write validated imported form definitions without creating a database."""
    source = Path(path).resolve()
    imported_forms = forms if forms is not None else analyze_workbook(source)
    script = Path(script_path).resolve()
    if script.exists() and not force:
        raise FileExistsError(
            f"Refusing to overwrite existing output: {script}; use force=True")
    text = generate_dsl(source.stem, datasource, imported_forms)
    _validate_generated_dsl(text)
    script.parent.mkdir(parents=True, exist_ok=True)
    temporary = script.with_name(script.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(script)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return ExcelDefinitionResult(script, imported_forms)


def import_workbook(path: str | Path, script_path: str | Path | None = None,
                    database_path: str | Path | None = None,
                    force: bool = False) -> ExcelImportResult:
    source = Path(path).resolve()
    forms = analyze_workbook(source)
    app_slug = slugify(source.stem, "excel_app")
    script = Path(script_path or Path("scripts") / f"{app_slug}.dsl").resolve()
    database = Path(database_path or f"{app_slug}.db").resolve()
    if database.suffix.lower() != ".db":
        database = database.with_suffix(".db")
    existing = [item for item in (script, database) if item.exists()]
    if existing and not force:
        joined = ", ".join(str(item) for item in existing)
        raise FileExistsError(f"Refusing to overwrite existing output: {joined}; use --force")

    datasource = str(database.with_suffix(""))
    text = generate_dsl(source.stem, datasource, forms)
    _validate_generated_dsl(text)
    script.parent.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)

    script_tmp = script.with_name(script.name + ".tmp")
    database_tmp = database.with_name(database.name + ".tmp")
    try:
        script_tmp.write_text(text, encoding="utf-8")
        if database_tmp.exists():
            database_tmp.unlink()
        connection = sqlite3.connect(database_tmp)
        try:
            for form in forms:
                connection.execute(f'CREATE TABLE "{form.name}" (data TEXT)')
                connection.executemany(
                    f'INSERT INTO "{form.name}" (data) VALUES (?)',
                    [(json.dumps(row, ensure_ascii=False),) for row in form.rows],
                )
            connection.commit()
        finally:
            connection.close()
        database_tmp.replace(database)
        script_tmp.replace(script)
    except Exception:
        for temporary in (script_tmp, database_tmp):
            if temporary.exists():
                temporary.unlink()
        raise
    return ExcelImportResult(
        script, database, len(forms), sum(len(form.rows) for form in forms)
    )


def _seed_rows(db, forms: list[ImportedForm]):
    """Insert workbook rows into the caller's database adapter.

    Re-importing a workbook replaces that workbook's rows instead of
    appending duplicates. Table scaffolding follows dbnocode's storage model
    (one JSON document per row) so the next startup's ``_ensure_tables`` finds
    the same shape for tables it creates.
    """
    try:
        existing = set(db.get_table_names())
    except Exception:
        existing = set()
    for form in forms:
        table = form.name
        if table in existing:
            try:
                db.execute(f'DELETE FROM "{table}"', ())
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot reset table '{table}': {exc}") from exc
        try:
            db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (data TEXT)', ())
        except Exception as exc:
            raise RuntimeError(
                f"Cannot create table '{table}': {exc}") from exc
        for row in form.rows:
            db.execute(
                f'INSERT INTO "{table}" (data) VALUES (?)',
                (json.dumps(row, ensure_ascii=False),),
            )


def import_into_app(path: str | Path, script_path: str | Path,
                    db=None) -> ExcelImportResult:
    """Import a workbook as forms of the app hosted at ``script_path``.

    The generated definition is stored as a validated sidecar DSL beside the
    host script and registered in ``<script>.imports.json``. ``main.py`` merges
    every registered import at startup, so the forms appear in the host app's
    own menu and database on the next launch. The caller's database adapter can
    be seeded immediately so the imported rows are present even before that
    restart.
    """
    source = Path(path).resolve()
    forms = analyze_workbook(source)
    app_slug = slugify(source.stem, "excel_app")
    host = Path(script_path).resolve()
    side_dir = host.parent / (host.stem + ".imports")
    script = (side_dir / f"{app_slug}.dsl").resolve()
    text = generate_dsl(source.stem, "current_database", forms)
    _validate_generated_dsl(text)
    side_dir.mkdir(parents=True, exist_ok=True)
    temporary = script.with_name(script.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(script)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    if db is not None:
        _seed_rows(db, forms)
    register_import(host, script)
    return ExcelImportResult(
        script, None, len(forms), sum(len(form.rows) for form in forms)
    )
