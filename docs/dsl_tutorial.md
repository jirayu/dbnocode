# DSL Tutorial — Build a TUI app without code

dbnocode reads a `.dsl` text file, validates it, and renders a full
Python/curses application bound to a database. This tutorial uses the
**compact DSL** (the canonical dialect — see `docs/canonical_dsl.md` and
`docs/grammar.ebnf`).

## 1. Run it

    python main.py                                 # uses scripts/main.dsl
    python main.py scripts/02_membership.dsl      # a specific script
    python main.py --validate-only scripts/04_inventory_compact.dsl

`--validate-only` checks syntax + semantics and exits (no TUI). If you see
`Valid: N forms, M grids`, your script parsed.

To bootstrap an application from an Excel workbook, import it once and run the
generated compact DSL:

    python main.py --import-excel inventory.xlsx
    python main.py scripts/inventory.dsl

Each worksheet table becomes a classic CRUD form backed by a preloaded local
SQLite database — the same list/form layout as hand-authored forms, so no
tab-strip navigation is involved. The browse list is searchable: Enter edits the
selected row, F3 creates a row, and Ctrl+D deletes it. Each row opens into the
normal F10 form editor.

You can also import into an app you are already running: open the **System** →
**Import Excel** menu action and type the workbook path. When the app has a host
script and a reachable database, the importer stores the generated forms beside
that script (in `<script>.imports/`), registers them in `<script>.imports.json`,
and seeds the workbook rows into the app's own database. On the next launch the
forms appear under an **Imported Excel** menu section alongside your existing
screens — the workbook becomes part of the same build, not a separate app.
Re-importing a workbook whose forms already exist asks whether to replace them
or stop first, so an existing form is never overwritten by accident. When an
import turns out wrong, use **System → Delete Imported Excel** to pick it from
the list: the sidecar is unregistered and deleted and its data tables are
dropped.
Without a host script or usable database (e.g. when launched ad hoc from an
EXE), it falls back to generating a standalone project as above.

> Tip: run your terminal in a UTF-8 locale so box-drawing glyphs render
> correctly (see `REVIEW.md` for details).

## 2. Minimal script

    APP "My App" VERSION "1.0"
    DATASOURCE "myapp_db" ADAPTER "sqlite"

    FORM customer TITLE "Customers" COLS 2
      name  | 40 | req span:2
      kind  | 12 | opts:Retail,Wholesale
      LIST name:30 kind:12
    END

That is a complete, runnable app: a `customer` table, an entry form, and a
browsable list. The parser auto-generates the screens/actions you need
(see §9).

## 3. Anatomy

- `APP` / `DATASOURCE` — metadata + database (`sqlite`, `postgres`, `firebird`).
- `FORM name ... END` — a data entry screen + its table.
- `FIELDSET name ... END` — reusable field group, inserted with `USE name`.
- `MENU ... END` — navigation (top bar + center launcher).
- `REPORT` / `SCRIPT` / `SUBROUTINE` / `WORKFLOW` / `ON SAVE` — behavior.

`// comment` is allowed anywhere (and `INCLUDE "other.dsl"` merges files).

## 4. Fields

Syntax:  `field_id | width | flags`

The `width` controls the UI input/display size only and does not limit the actual database field size. For text fields, the underlying storage uses appropriate types (TEXT, VARCHAR, etc.) that can accommodate variable-length data regardless of the specified width. Width may be blank for auto-sizing in lists.

      part_no   | 12 | req prefix:ITM
      part_name | 40 | req span:2
      specs     | 50 | span:2 rows:3
      category  |    | opts:Raw Material,Finished Good
      on_hand   |    | num ro
      cbm       |    | num default:0.10

Flags:

| flag | meaning |
|------|---------|
| `req` | required |
| `num` | numeric |
| `date` | date (YYYY-MM-DD) |
| `ro` | read-only |
| `upper` | uppercase |
| `prefix:TXT` | auto code prefix |
| `span:N` | column span (2 = full width) |
| `rows:N` | multi-line height |
| `default:val` | default value |
| `opts:A,B,C` | dropdown options |
| `lookup:form` | pick from another form |
| `lookup:form INCLUDE f1,f2` | show only these columns in picker |
| `lookup:form EXCLUDE f1,f2` | hide these columns from picker |
| `lookup:form CASCADE src->field` | filter lookup by sibling field value (e.g. city filtered by selected state) |
| `fill:...` | prefill from lookup |
| `filter:k=v` | filter lookup rows |
| `formula:...` | computed value (display only). Can include formatting functions like `FORMAT(CURRENCY, value)`. |
| `readonly_below:N` | read-only unless user level >= N (fails safe to guest) |

Width may be blank (auto-sized in the list).

## 5. Sections, details, lists

    FORM item TITLE "Item Master" COLS 2
      SECTION "Item Information"
        part_no   | 12 | req prefix:ITM
        part_name | 40 | req span:2
      SECTION "Stock Levels"
        on_hand   |    | num
      DETAIL "Stock Lots" AS item_lot
        lot_no    | 12 | ro
        on_hand   |    | num ro
      END DETAIL
      LIST part_no:12 part_name:40 on_hand:10
    END

- `SECTION "Title"` — visual group header.
- `DETAIL "Label" AS child` — child table linked by `_parent_rowid`.
- `LIST f:w ...` — which columns show in the browse grid (w = width).

## 6. Fieldsets (reuse)

    FIELDSET contact_info
      email  | 30 |
      phone  | 15 |
      address| 50 | span:2 rows:3
    END FIELDSET

    FORM vendor TITLE "Vendors" COLS 2
      vendor_code | 10 | req prefix:VEN
      USE contact_info
      LIST vendor_code:10 email:30 phone:15
    END

## 7. Scripts & expressions

A `SCRIPT ... ENDSCRIPT` block is executed by a tiny safe interpreter (no
Python `eval`). It reads/writes tables by name:

    SUBROUTINE stock_recalc(rowid, delta)
      FETCH it FROM item WHERE _rowid = rowid
      SET oh = it.on_hand + delta
      UPDATE item SET on_hand = oh WHERE _rowid = rowid
    ENDSUBROUTINE

    SCRIPT post_grn
      FETCH row FROM grn WHERE _rowid = header._rowid
      STOCK RECEIVE
        LINES lines | ITEM line.part_no | QTY line.qty | UOM line.uom
        DATE grn_date | DOC grn_no | WAREHOUSE wh
        COST line.unit_cost | LOT line.lot_no | TYPE "RECV"
      END STOCK
    ENDSCRIPT

Instructions: `FETCH v FROM tbl WHERE f = expr`, `SET v = expr`,
`UPDATE tbl SET f = expr WHERE ...`, `INSERT tbl SET f = v`,
`DELETE tbl WHERE ...`, `IF cond ... END IF`, `FOREACH ...`, `CALL name(args)`.
Expressions support `+ - * /`, comparisons, `MIN/MAX/ABS`, string ops, and
`TODAY`. `header` refers to the current form's saved record; `lines` to its
detail rows.

## 8. Stock operations

Map document rows into the stock engine:

    STOCK RECEIVE
      LINES lines | ITEM line.part_no | QTY line.qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE doc_date | DOC doc_no | WAREHOUSE wh
      COST line.unit_cost | LOT line.lot_no | TYPE "RECV"
    END STOCK

Operations: `RECEIVE | ISSUE | ALLOCATE | TRANSFER`; add `REVERSE` to undo.
The engine does pack conversion, balance recalc, lot movement, and
stroke-card posting inside one transaction. `RESULT var` exposes the base qty.

## 9. Generated screens & actions

For `FORM foo` the runtime creates:
  `foo_form`, `foo_grid`, `add_foo`,
  `list_foo`, `goto_foo_add`, `goto_foo_list`, and `goto_foo`.
A detail `foo_line` adds `foo_line_form` and `foo_line_grid`.

You rarely name these yourself — menus and `ON SAVE` reference them.

## 10. Menus

    MENU "Main" PULLDOWN
      GROUP "File"
        "Customers"  => customer      HOTKEY "F2"
        "Items"      => item.list     HOTKEY "F3"
        "---"
        "Exit"       => EXIT          HOTKEY "ESC"
      END GROUP
      CENTER
        "1. Customers" => customer
        "2. Exit"      => EXIT
      END CENTER
    END

Targets: `name`, `name.list`, `name.add`, `EXIT`, `RESET`, `REPORTS`,
`SETTINGS`, `EXCEL.IMPORT`, `SWITCH`, `report:name`, `B2B.INBOX`, `B2B.OUTBOX`.
(`GROUP` is an alias for `PULLDOWN`.)

## 11. ON SAVE / WRITE (auto-posting)

    FORM grn TITLE "Goods Receipt" COLS 2
      grn_no   | 12 | req prefix:GRN
      grn_date | 12 | date
      DETAIL "Lines" AS grn_line
        part_no | 12 | req lookup:item
        qty     | 10 | num
      END DETAIL
      ON SAVE
        IF header.grn_no
          WRITE grn_line FROM lines
        END WRITE
        CALL post_grn()
      END SAVE

`ON SAVE` runs after a successful header save. `WRITE child FROM lines`
copies detail rows. `header` = saved parent record.

## 12. Reports

    REPORT stock_on_hand TITLE "Stock Report"
      SOURCE item
      ORDER on_hand DESC
      WHERE category = "Finished Good"
      COLUMNS
        part_no   | "Part"   | 12
        part_name | "Name"   | 40
        on_hand   | "On Hand"| 10 | num
      END COLUMNS
      TOTALS on_hand
    END REPORT

Modes: listing / summary / crosstab. `WHERE` supports `{param}` substitution
from report prompts.

## 13. Users & levels

The runner reads `DBNOCODE_USER` / `DBNOCODE_USER_LEVEL` (default guest = 0).
A login screen can call `ScriptRunner.set_current_user(name, level)`.
`readonly_below:N` then locks sensitive fields (e.g. `unit_cost`) for
low-level users — failing safe to read-only.

## 14. Validate before you run

    python main.py --validate-only your.dsl

The validator catches unbalanced blocks, bad hotkeys, and undeclared menu
targets. Semantic validation catches unknown fields/actions. Fixes are
printed per line.

## 15. Common pitfalls

- A field with a space in a `default:` or `filter:` value must avoid spaces,
  or the tokenizer splits it.
- Compact scripts need at least one `FORM` for dialect detection; a
  menu-only file is detected as verbose and will fail to parse.
- Keep `opts:` lists comma-separated with no trailing comma.
- Colors/theming and terminal locale are handled by the runtime — see
  `REVIEW.md` for known limitations (e.g. set your terminal to UTF-8).