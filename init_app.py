"""
dbNoCode DSL Project Generator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A curses wizard that scaffolds a new standalone project folder
with the full DSL engine, docs, starter scripts, and a README guide.

Usage:
    python init_app.py
"""
import curses
import os
import re
import shutil
import sys
from pathlib import Path

# ── Source root ──────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    SRC = Path(sys._MEIPASS)
else:
    SRC = Path(__file__).resolve().parent

# ── Files to copy verbatim ──────────────────────────────────────────────────
COPY_ENGINE = [
    # Root — runtime
    'main.py',
    # DSL engine (complete — user never touches these)
    'dsl_lib/__init__.py',
    'dsl_lib/adapters.py',
    'dsl_lib/compact_parser.py',
    'dsl_lib/parser.py',
    'dsl_lib/report_engine.py',
    'dsl_lib/runner.py',
    'dsl_lib/script_engine.py',
    'dsl_lib/validator.py',
    # TUI components (complete — user never touches these)
    'tui/__init__.py',
    'tui/cxgrid.py',
    'tui/cxreport.py',
    'tui/full_grid.py',
    'tui/layout.py',
    'tui/menu.py',
    'tui/themes.py',
    'tui/statusline.py',
    'tui/udf_panel.py',
    'tui/pos_screen.py',
    'tui/warehouse_grid.py',
    'tui/tree_grid.py',
    'tui/widgets.py',
    # DSL documentation
    'docs/bnf_gramma.txt',
    'docs/tutorial.txt',
]

# Example .dsl scripts — starting points for new apps
STARTER_SCRIPTS = [
    'scripts/01_main_menu_compact.dsl',     # simplest: main menu only
    'scripts/02_membership_compact.dsl',    # master data with details
    'scripts/04_inventory_compact.dsl',     # full ERP: PO, GRN, SO, reports
    'scripts/05_sales_order.dsl',           # sales workflow with reports
    'scripts/main.dsl',                     # modular full ERP entry point
    'scripts/modules/02_master_data.dsl',
    'scripts/modules/03_warehouse.dsl',
    'scripts/modules/04_web_pos.dsl',
    'scripts/modules/05_purchasing.dsl',
    'scripts/modules/06_stock_operations.dsl',
    'scripts/modules/07_sales.dsl',
    'scripts/modules/08_bom.dsl',
    'scripts/modules/09_reports.dsl',
    'scripts/modules/10_menu.dsl',
]


# ── Template generators ─────────────────────────────────────────────────────

def _gen_starter_dsl(cfg: dict) -> str:
    """Generate a minimal starter DSL script."""
    return f'''\
APP "{cfg['app_title']}" VERSION "1.0"

DATASOURCE "{cfg['db_name']}" ADAPTER "sqlite"

// ── Master Data ──

FORM item TITLE "Item Master"
  item_code   | 15 | req
  description | 40 | req
  unit        |    | opts:PCS,KG,LTR,SET
  unit_price  |    | num
  on_hand     |    | num ro
  is_active   |    | default:true
  LIST item_code:15 description:35 unit:8 unit_price:12 on_hand:10
END

FORM customer TITLE "Customer"
  cust_code  | 10 | req prefix:CUS
  cust_name  | 40 | req
  phone      | 15 |
  email      | 30 |
  address    |    | span:2
  LIST cust_code:12 cust_name:35 phone:15 email:25
END

// ── Menu ──

MENU "{cfg['app_title']}"
  GROUP "Master Data"
    "Items" => item HOTKEY F2
    "Customers" => customer
  END
  GROUP "System"
    "Exit" => EXIT
  END
END
'''


def _gen_requirements() -> str:
    return '''\
windows-curses>=2.3.0; sys_platform == "win32"
reportlab>=4.0
'''


def _gen_run_bat(cfg: dict) -> str:
    return f'''\
@echo off
title {cfg['app_title']}
color 0A
if exist dsl_tui_app.exe (
    dsl_tui_app.exe scripts\\my_app.dsl
) else (
    python main.py scripts\\my_app.dsl
)
'''


def _gen_readme(cfg: dict) -> str:
    return f'''\
# {cfg['app_title']}

## Quick Reference
- **Run**: `run.bat` or `dsl_tui_app.exe scripts/my_app.dsl`
- **Alt**: `python main.py scripts/my_app.dsl` (if Python installed)
- **DB**: SQLite (auto-created on first run, local file)
- **Default**: No login required — app opens directly

## How to Create New Screens

This project uses the **dbNoCode DSL engine**. All screens are defined
in a single `.dsl` text file — no Python coding required.

### Reference docs
1. **`docs/bnf_gramma.txt`** — formal BNF grammar
2. **`docs/tutorial.txt`** — step-by-step tutorial with examples
3. An existing sample script from `scripts/` for reference

### To add a screen
1. Edit `scripts/my_app.dsl` — add forms, menus, and reports
2. Done — run with `python main.py scripts/my_app.dsl`

**DO NOT modify Python files.** The engine runs `.dsl` scripts directly.
Database tables are auto-created on first use.

### DSL Capabilities
- **Forms**: Header fields + detail grids with tabs, lookups, combos
- **Lists**: Sortable, filterable grids with F7 filter row
- **Reports**: Listing with groups/subtotals, summary, crosstab, PDF export
- **Workflows**: Status transitions with script actions (Draft→Posted→Void)
- **Scripts**: SET, IF/ELSE, FOREACH, FETCH, UPDATE, INSERT, DELETE
- **Menus**: Center menu or pulldown top-bar menus
- **Entry mode**: Full-screen grid-only forms for quick data entry
- **Doc numbering**: Sequential (PREFIX-NNNNN) or monthly (PREFIX-YYMMNNNNN)

### Sample scripts included (for inspiration):
- `scripts/01_main_menu_compact.dsl` — simplest app: center menu only
- `scripts/02_membership_compact.dsl` — master data with detail grid
- `scripts/04_inventory_compact.dsl` — full ERP: PO, GRN, SO, stock, reports
- `scripts/main.dsl` — modular full ERP entry point (run with `python main.py`)
- `scripts/05_sales_order.dsl` — sales workflow with reports

## Project Structure
```
{cfg['folder_name']}/
├── main.py              Entry point — runs DSL scripts
├── run.bat              Quick launcher
├── requirements.txt     Python dependencies
├── README.md            ★ This file — quick reference
├── docs/
│   ├── bnf_gramma.txt   ★ Grammar reference
│   └── tutorial.txt     ★ Step-by-step tutorial
├── dsl_lib/             DSL engine (do not modify)
├── tui/                 TUI components (do not modify)
└── scripts/             ★ DSL scripts (create/edit here)
    └── my_app.dsl       Your application script
```

## Keyboard Shortcuts
- **Menu**: Arrow keys, Enter, ESC, hotkeys (F2-F4)
- **Form**: Tab/Shift+Tab fields, F4 lookup, F5 workflow, F10 save, ESC back
- **List**: Enter edit, F3 new, F6 sort, F7 filter, F8 columns, Ctrl+D delete
- **Report**: F2 listing, F3 summary, F6 crosstab, F10 PDF, ESC back

## Rules
1. All screens are defined in `.dsl` files ONLY — do NOT create Python modules
2. One `.dsl` file = one complete application (forms, menus, reports)
3. Database tables are auto-created — no schema setup needed
4. Detail rows are stored as separate records with `_parent_rowid` link
'''


# ── Project generator ────────────────────────────────────────────────────────

def create_project(project_path) -> str:
    """Public entry point for scaffolding a project.

    Accepts either a path-like (Path/str) or a full cfg dict. Wraps
    `_generate_project`. Returns an error message string ('' on success).
    Exists so external tools (e.g. gui_init_builder) can import a stable
    public name without depending on the private `_generate_project`.
    """
    if isinstance(project_path, dict):
        cfg = project_path
    else:
        cfg = {'project_path': str(project_path)}
    return _generate_project(cfg)


def _generate_project(cfg: dict) -> str:
    """Create project folder and populate it. Returns error message or ''."""
    dest = Path(cfg['project_path'])

    try:
        # Create directories
        for d in ['dsl_lib', 'tui', 'docs', 'scripts']:
            (dest / d).mkdir(parents=True, exist_ok=True)

        # Copy engine files
        for rel in COPY_ENGINE:
            src_file = SRC / rel
            dst_file = dest / rel
            if src_file.is_file():
                shutil.copy2(src_file, dst_file)

        # Copy starter scripts (always overwrite samples, they're reference)
        for rel in STARTER_SCRIPTS:
            src_file = SRC / rel
            dst_file = dest / rel
            if src_file.is_file():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)

        # Copy pre-built runtime exe if available (so users don't need Python)
        for exe_name in ['dsl_tui_app.exe']:
            exe_src = SRC / exe_name
            if not exe_src.is_file():
                exe_src = SRC / 'dist' / exe_name
            if exe_src.is_file():
                shutil.copy2(exe_src, dest / exe_name)

        # Generate customised files
        # Only create my_app.dsl if it doesn't exist (preserve user edits)
        my_app = dest / 'scripts' / 'my_app.dsl'
        if not my_app.exists():
            my_app.write_text(_gen_starter_dsl(cfg), encoding='utf-8')
        # Always overwrite these (engine-related, safe to refresh)
        (dest / 'requirements.txt').write_text(
            _gen_requirements(), encoding='utf-8')
        (dest / 'run.bat').write_text(
            _gen_run_bat(cfg), encoding='utf-8')
        (dest / 'README.md').write_text(
            _gen_readme(cfg), encoding='utf-8')

        return ''
    except Exception as e:
        return f"Error: {e}"


# ── Curses wizard ────────────────────────────────────────────────────────────

def _folder_to_db_name(path_str: str) -> str:
    """Derive a safe DB name from the folder path."""
    name = Path(path_str).name.lower()
    name = re.sub(r'[^a-z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name or 'myapp'


def _folder_to_title(path_str: str) -> str:
    """Derive a default app title from the folder name."""
    name = Path(path_str).name
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = name.replace('_', ' ').replace('-', ' ')
    return name.title()


class _Field:
    """Simple input field for the wizard."""
    def __init__(self, label: str, default: str = '', required: bool = False):
        self.label = label
        self.value = default
        self.required = required
        self.cursor = len(default)


def _draw_wizard(stdscr, fields: list, current: int, status: str = '',
                 db_name_preview: str = ''):
    """Draw the wizard form."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    # Title
    title = ' dbNoCode Project Generator '
    tx = max(0, (w - len(title)) // 2)
    try:
        stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
        stdscr.addstr(1, tx, title)
        stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)
    except curses.error:
        pass

    # Fields
    label_w = max(len(f.label) for f in fields) + 2
    start_y = 4
    for i, f in enumerate(fields):
        y = start_y + i * 2
        if y >= h - 4:
            break
        attr = curses.A_BOLD if i == current else curses.A_NORMAL
        try:
            stdscr.addstr(y, 4, f'{f.label}:', attr)
            vx = 4 + label_w + 1
            max_vw = w - vx - 4
            display = f.value[:max_vw]
            if i == current:
                cx = min(f.cursor, len(display))
                before = display[:cx]
                after = display[cx:]
                if before:
                    stdscr.addstr(y, vx, before)
                cursor_ch = after[0] if after else ' '
                stdscr.addstr(y, vx + cx, cursor_ch, curses.A_REVERSE)
                if len(after) > 1:
                    stdscr.addstr(y, vx + cx + 1, after[1:])
                used = len(display) + (1 if not after else 0)
                pad = max_vw - used
                if pad > 0:
                    stdscr.addstr(y, vx + used, '_' * pad, curses.A_DIM)
            else:
                stdscr.addstr(y, vx, display or '(empty)',
                              curses.A_DIM if not display else curses.A_NORMAL)
        except curses.error:
            pass

    # DB name preview
    info_y = start_y + len(fields) * 2 + 1
    if db_name_preview and info_y < h - 3:
        try:
            stdscr.addstr(info_y, 4, f'DB file: {db_name_preview}.db',
                          curses.A_DIM)
        except curses.error:
            pass

    # Status bar
    if status:
        try:
            stdscr.addstr(h - 3, 4, status, curses.A_BOLD)
        except curses.error:
            pass

    # Key hints
    try:
        hints = '  [F2] Create project    [Tab/Enter] Next field    [ESC] Cancel'
        stdscr.addstr(h - 1, 2, hints[:w - 3], curses.A_DIM)
    except curses.error:
        pass

    stdscr.refresh()


def _show_result(stdscr, cfg: dict, error: str):
    """Show success or error screen, wait for keypress."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    if error:
        try:
            stdscr.addstr(2, 4, 'Project creation failed!', curses.A_BOLD)
            stdscr.addstr(4, 4, error)
            stdscr.addstr(h - 1, 4, 'Press any key to exit.', curses.A_DIM)
        except curses.error:
            pass
    else:
        lines = [
            ('Project ready!', curses.A_BOLD),
            ('', 0),
            (f"  Path: {cfg['project_path']}", curses.A_NORMAL),
            (f"  DB:   {cfg['db_name']}.db (auto-created on first run)",
             curses.A_NORMAL),
            ('', 0),
            ('Next steps:', curses.A_BOLD),
            (f"  1. cd {cfg['project_path']}", curses.A_NORMAL),
            ('  2. run.bat  (or: dsl_tui_app.exe scripts/my_app.dsl)',
             curses.A_NORMAL),
            ('', 0),
            ('To add new screens:', curses.A_BOLD),
            ('  1. Edit scripts/my_app.dsl — add forms, menus, reports.',
             curses.A_NORMAL),
            ('  2. See docs/tutorial.txt for the DSL reference.',
             curses.A_NORMAL),
            ('  3. See scripts/04_inventory_compact.dsl for a full example.',
             curses.A_NORMAL),
            ('', 0),
            ('  Or run scripts/main.dsl for the modular full example.',
             curses.A_DIM),
        ]
        for i, (text, attr) in enumerate(lines):
            y = 2 + i
            if y >= h - 2:
                break
            try:
                stdscr.addstr(y, 4, text, attr)
            except curses.error:
                pass
        try:
            stdscr.addstr(h - 1, 4, 'Press any key to exit.', curses.A_DIM)
        except curses.error:
            pass

    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key == curses.KEY_MOUSE:
            try:
                curses.getmouse()
            except curses.error:
                pass
            continue
        break


def _wizard(stdscr):
    """Main wizard loop."""
    curses.curs_set(0)
    curses.use_default_colors()

    fields = [
        _Field('Project folder', '', required=True),
        _Field('App title', ''),
        _Field('Company name', ''),
    ]
    current = 0
    status = ''

    while True:
        path_val = fields[0].value.strip()
        db_name = _folder_to_db_name(path_val) if path_val else ''
        default_title = _folder_to_title(path_val) if path_val else ''

        _draw_wizard(stdscr, fields, current, status, db_name)

        key = stdscr.getch()

        if key == curses.KEY_MOUSE:
            try:
                curses.getmouse()
            except curses.error:
                pass
            continue

        if key == 27:
            return

        if key in (9, 10, 13, curses.KEY_DOWN):
            if current == 0 and path_val:
                if not fields[1].value.strip():
                    fields[1].value = default_title
                    fields[1].cursor = len(fields[1].value)
                if not fields[2].value.strip():
                    fields[2].value = default_title
                    fields[2].cursor = len(fields[2].value)
            current = (current + 1) % len(fields)
            status = ''
            continue

        if key in (curses.KEY_BTAB, curses.KEY_UP, 353):
            current = (current - 1) % len(fields)
            status = ''
            continue

        if key == curses.KEY_F2:
            if not path_val:
                status = 'Project folder is required.'
                current = 0
                continue
            if not fields[1].value.strip():
                fields[1].value = default_title
            if not fields[2].value.strip():
                fields[2].value = fields[1].value

            cfg = {
                'project_path': path_val,
                'folder_name': Path(path_val).name,
                'app_title': fields[1].value.strip(),
                'company': fields[2].value.strip(),
                'db_name': db_name,
            }

            error = _generate_project(cfg)
            _show_result(stdscr, cfg, error)
            return

        # Text editing
        f = fields[current]
        if key in (curses.KEY_BACKSPACE, 8, 127):
            if f.cursor > 0:
                f.value = f.value[:f.cursor - 1] + f.value[f.cursor:]
                f.cursor -= 1
        elif key == curses.KEY_DC:
            if f.cursor < len(f.value):
                f.value = f.value[:f.cursor] + f.value[f.cursor + 1:]
        elif key == curses.KEY_LEFT:
            f.cursor = max(0, f.cursor - 1)
        elif key == curses.KEY_RIGHT:
            f.cursor = min(len(f.value), f.cursor + 1)
        elif key == curses.KEY_HOME:
            f.cursor = 0
        elif key == curses.KEY_END:
            f.cursor = len(f.value)
        elif 32 <= key <= 126:
            ch = chr(key)
            f.value = f.value[:f.cursor] + ch + f.value[f.cursor:]
            f.cursor += 1


def main():
    """Entry point."""
    if not (SRC / 'dsl_lib' / 'runner.py').is_file():
        print("Error: DSL engine files not found.")
        if getattr(sys, 'frozen', False):
            print(f"  Bundle path: {SRC}")
        else:
            print(f"  Run this from the dbNoCode root directory.")
            print(f"  Expected: {SRC / 'dsl_lib' / 'runner.py'}")
        sys.exit(1)

    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')

    curses.wrapper(_wizard)


if __name__ == '__main__':
    main()
