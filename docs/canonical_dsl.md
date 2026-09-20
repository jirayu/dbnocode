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

Add `TABBED` to present the generated searchable list and CRUD editor as two
views of one screen. Enter on a list row opens it in **Form and Edit**, F3 opens
a blank record, Tab opens the form tab, Page Up/Page Down page through the list,
and F6 or Page Up on the form tab returns to the list tab:

```text
FORM customer TITLE "Customers" COLS 2 TABBED
  customer_code | 12 | req
  name          | 40 | req
  LIST customer_code:12 name:40
END
```

`TABBED` is for ordinary CRUD forms and cannot be combined with `ENTRY`, whose
full-screen detail-grid workflow has its own navigation model.

Fields and detail columns use `id | width | flags`. The `width` controls the UI input/display size only and does not limit the actual database field size. For text fields, the underlying storage uses appropriate types (TEXT, VARCHAR, etc.) that can accommodate variable-length data regardless of the specified width. Width may be blank for auto-sizing in lists.

Current flags include
`req`, `num`, `date`, `time`, `timestamp`, `uuid`, `json`, `email`, `phone`,
`currency`, `percentage`, `color`, `ro`, `upper`, `prefix:...`, `span:N`,
`rows:N`, `default:...`, `opts:A,B`, `lookup:name`, `fill:...`,
`copydetail:name`, `filter:key=value`, `formula:...`, and `readonly_below:N`.
The `formula` flag can be used to create read-only calculated fields. Formulas can include arithmetic operations, function calls, and references to other fields. For example, `formula:FORMAT(CURRENCY, value)` formats a numeric value as currency.

Extended field types provide built-in validation without requiring regular expressions:
- `time` - HH:MM:SS format (24-hour)
- `timestamp` - YYYY-MM-DD HH:MM:SS
- `uuid` - Standard UUID format (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
- `json` - Valid JSON object/array
- `email` - Standard email format
- `phone` - Phone number (with optional country code)
- `currency` - Numeric with 2 decimal places + currency symbol
- `percentage` - Numeric with 2 decimal places + % sign
- `color` - Hex color (#RRGGBB or #RGB)

Example usage:
```text
FORM event TITLE "Event Scheduler" COLS 2
  event_id   | 10 | req prefix:EVT
  start_time |  8 | time
  end_time   |  8 | time
  created_at | 19 | timestamp
  attendee_uuid | 36 | uuid
  config_json  | 50 | json
  contact_email| 30 | email
  phone_num   | 15 | phone
  price       | 10 | currency
  discount    |  6 | percentage
  bg_color    |  7 | color
LIST event_id:10 start_time:8 end_time:8 created_at:19
END
```

Enhanced lookup syntax allows controlling which fields are returned from the target form and creating dynamic relationships:
- `lookup:target_form` - Returns all fields (original behavior)
- `lookup:target_form INCLUDE f1,f2,f3` - Returns only specified fields
- `lookup:target_form EXCLUDE f1,f2` - Returns all fields except specified ones
- `lookup:target_form (f1 AS alias1, f2, f3)` - Returns fields with optional renaming
- `lookup:form1|form2|form3` - Returns values from multiple forms (union)
- `lookup:target_form CASCADE source_field->target_field` - Dynamically filters target based on source field value

Example usage:
```text
FORM order TITLE "Order Entry" COLS 2
  order_id   | 10 | req prefix:ORD
  customer   | 10 | req lookup:customer INCLUDE cust_code,cust_name,email
  ship_to    | 10 | lookup:address EXCLUDE internal_notes,temp_flags
  sales_rep  | 10 | lookup:employee (emp_id AS rep_id, emp_name AS rep_name, dept)
  region     | 10 | lookup:sales_region|territory  // Multiple sources
  state      | 10 | lookup:state CASCADE country->country_code  // Cascade lookup
  LIST order_id:10 customer:30 ship_to:30 sales_rep:20 region:10 state:10
END
```

Cascade lookups work by automatically updating the lookup's filter when the source field changes. For example, when `country` changes, the `state` lookup will only show states matching the selected country's `country_code`.

Self-referencing lookups for hierarchies work with standard lookup syntax combined with filtering:
```text
FORM category TITLE "Category Hierarchy" COLS 2
  category_name | 30 | req
  parent_category | 20 | lookup:category filter:_rowid != header._rowid
END
```

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
    LINES lines | ITEM line.part_no | QTY line.qty | UOM line.uom
    PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
    DATE grn_date | DOC grn_no | WAREHOUSE warehouse
    COST line.unit_cost | LOT line.lot_no | TYPE "RECV"
  END STOCK
ENDSCRIPT
```

Operations are `RECEIVE`, `ISSUE`, `ALLOCATE`, and `TRANSFER`. Add `REVERSE`
to reverse the movement. The engine performs pack conversion, stock balance
recalculation, lot movement, and stroke-card creation in the script transaction.
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
`EXCEL.IMPORT`,
`SWITCH`, and `report:name` remain supported. The verbose DSL remains a legacy
compatibility input. It is parsed but is not the canonical authoring format;
existing scripts are not migrated.

Semantic validation runs after parsing and before execution. Diagnostics use
structured codes and `line=0` when the parsed definition has no source mapping.