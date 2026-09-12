# Canonical DSL

The compact DSL is the canonical authoring dialect for new scripts. It is
parsed into the same application-definition model used by the runtime; scripts
are not rewritten or migrated.

## Metadata

```text
APP "Application Name" VERSION "1.0"
DATASOURCE "database_name" ADAPTER "sqlite"
```

Supported adapters are `sqlite`, `postgres`, and `firebird`.

## Compact Forms

Forms use an unquoted name and close with `END`:

```text
FORM customer TITLE "Customers" COLS 2
  name | 40 | req span:2
  kind | 12 | opts:Retail,Wholesale
  LIST name:30 kind:12
END
```

Fields and detail columns use `id | width | flags`. Current flags include
`req`, `num`, `date`, `ro`, `upper`, `prefix:...`, `span:N`, `rows:N`,
`default:...`, `opts:A,B`, `lookup:name`, `fill:...`, `copydetail:name`,
`filter:key=value`, `formula:...`, and `readonly_below:N`.

`readonly_below:N` makes a form field or detail column read-only when the
current user's numeric level is lower than `N`. Missing user context is treated
as guest level `0` so protected fields fail safe:

```text
  unit_cost | 12 | num readonly_below:50
  DETAIL "Order Lines" AS po_line
    discount | 8 | num readonly_below:50
  END DETAIL
```

The runner initially reads `DBNOCODE_USER` and `DBNOCODE_USER_LEVEL`. A login
screen can call `ScriptRunner.set_current_user(name, level)` to replace this
session context. This controls editing in the TUI; server-side authorization is
still required to protect the database from other clients.

Reusable field groups can be declared once and inserted into forms:

```text
FIELDSET contact_info
  email | 30 |
  phone | 15 |
  address | 50 | span:2 rows:3
END FIELDSET

FORM vendor TITLE "Vendor Master" COLS 2
  vendor_code | 10 | req prefix:VEN
  vendor_name | 40 | req span:2
  USE contact_info
  LIST vendor_code:10 vendor_name:30 phone:15
END
```

`DETAIL`, `TREE_DETAIL`, `LIST`, `COMPUTED`, `SCRIPT`, `WORKFLOW`, `ON SAVE`,
`MENU`, `REPORT`, `SUBROUTINE`, and `RESET` are also supported. See
`grammar.ebnf` for the current boundary syntax.

## Mapped Stock Operations

Inventory scripts can map document-specific fields into the standard stock
engine. `LINES` processes every detail row as `line`; `ITEM` and `QTY` are
required. Several mappings may share a row when separated by `|`.

```text
SCRIPT post
  STOCK RECEIVE
    LINES lines | ITEM line.part_no | QTY line.received_qty | UOM line.uom
    PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
    DATE grn_date | DOC grn_no | WAREHOUSE warehouse
    COST line.unit_cost | LOT line.lot_no | TYPE "RECV"
  END STOCK
ENDSCRIPT
```

Operations are `RECEIVE`, `ISSUE`, `ALLOCATE`, and `TRANSFER`. Add `REVERSE`
to reverse the movement. The engine performs pack conversion, stock balance
recalculation, lot movement, and stock-card creation in the script transaction.
`RESULT variable_name` exposes the converted base quantity for subsequent
document-specific updates. `DEMAND` links an issue to allocated demand, and
`LOTS FALSE` disables lot movement when a document intentionally does not use
lot tracking.

## Generated IDs

For `FORM foo`, the parser generates `foo_form`, `foo_grid`, `add_foo`,
`list_foo`, `goto_foo_add`, `goto_foo_list`, and `goto_foo`. A detail named
`foo_line` generates `foo_line_form` and `foo_line_grid`.

## Aliases and Legacy Input

`GROUP` is accepted as an alias for `PULLDOWN` inside menus. Menu targets such
as `name`, `name.list`, `name.add`, `EXIT`, `RESET`, `REPORTS`, `SETTINGS`,
`SWITCH`, and `report:name` remain supported. The verbose DSL remains a legacy
compatibility input. It is parsed but is not the canonical authoring format;
existing scripts are not migrated.

Semantic validation runs after parsing and before execution. Diagnostics use
structured codes and `line=0` when the parsed definition has no source mapping.
