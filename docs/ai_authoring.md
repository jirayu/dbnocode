# AI Authoring Guide — dbnocode DSL

This file is the authoritative prompt-prefix for AI tools generating `.dsl` files.
Paste it before any AI prompt. After generating, validate with:

    python main.py --validate-only your.dsl

---

## EXACT BLOCK TERMINATORS — memorize these, they are NOT negotiable

| Block opened by | Closed by |
|---|---|
| `FORM name ...` | `END` |
| `FIELDSET name` | `END FIELDSET` |
| `DETAIL "label" AS name` | `END DETAIL` |
| `TREE_DETAIL "label" AS name` | `END TREE_DETAIL` |
| `COMPUTED` | `END COMPUTED` |
| `HIGHLIGHT` | `END HIGHLIGHT` |
| `SCRIPT name` | `ENDSCRIPT` (one word, no space) |
| `SUBROUTINE name` | `ENDSUBROUTINE` (one word, no space) |
| `WORKFLOW ON field` | `END WORKFLOW` |
| `ON SAVE` | `END SAVE` |
| `STOCK RECEIVE/ISSUE/ALLOCATE/TRANSFER` | `END STOCK` |
| `MENU "name"` | `END` |
| `GROUP "name"` or `PULLDOWN "name"` | `END` |
| `CENTER` | `END CENTER` |
| `COLUMNS` (in REPORT) | `END COLUMNS` |
| `REPORT name` | `END REPORT` |
| `RESET` | `END RESET` |
| `IF cond` | `END` (not `END IF`) |
| `FOREACH x IN y` | `END` (not `END FOREACH`) |

---

## NEVER USE — common AI hallucinations that fail validation

```
# WRONG: END FORM, END SCRIPT, END SUBROUTINE, END IF, END FOREACH
# WRONG: ENDFORM, END_FORM, /FORM
# WRONG: REQUIRED (flag is: req)
# WRONG: READONLY (flag is: ro)
# WRONG: NULLABLE, OPTIONAL, UNIQUE (these don't exist)
# WRONG: TYPE "TEXT", TYPE "NUMBER" (no TYPE keyword on fields)
# WRONG: STRING, INTEGER, BOOLEAN as flags (they don't exist)
# WRONG: default:"value" — quotes not allowed in default: value
# WRONG: opts: ["A","B"] — opts uses comma-separated no-quotes: opts:A,B
# WRONG: FORM name { ... } — curly braces never used
# WRONG: END GROUP (group closes with just END)
# WRONG: CONSTRAINT, FOREIGN KEY, INDEX — no DDL allowed
# WRONG: lookup:form.field — lookup takes form name only, no dot
# WRONG: fill:form.field — fill takes field id only, no dot or form name
# WRONG: prefix:"WH" — no quotes: prefix:WH
# WRONG: CALCULATED, FORMULA keyword at block level — use COMPUTED block
# WRONG: VALIDATE keyword — use validator flags (req, etc.)
# WRONG: TRIGGER keyword — use SCRIPT + WORKFLOW
# WRONG: RELATIONSHIP, JOIN, REFERENCE — no relational keywords
# WRONG: cascade: (as a flag) — correct: CASCADE inside lookup: flag
```

---

## VALID FIELD FLAGS — complete list

```
req          required field (highlights if empty on save)
num          numeric input
date         date input YYYY-MM-DD
time         time HH:MM:SS
timestamp    YYYY-MM-DD HH:MM:SS
uuid         UUID format
json         JSON object/array
email        email format validation
phone        phone number
currency     numeric + 2dp + currency symbol
percentage   numeric + 2dp + %
color        hex color #RRGGBB
ro           read-only (display only)
upper        force uppercase input
prefix:ABC   auto-generate code: ABC + YYMM + seq (no quotes, no colon before value)
span:2       field spans 2 columns (use for wide fields)
rows:3       multi-line text input (N lines tall)
default:val  default value — NO quotes around val, NO spaces in val
opts:A,B,C   dropdown options — comma-separated, no quotes, no spaces
lookup:form                    pick from form's list
lookup:form INCLUDE f1,f2      picker shows only these columns
lookup:form EXCLUDE f1,f2      picker hides these columns
lookup:f1|f2                   pick from multiple forms (union)
lookup:form CASCADE src->tgt   filter picker by sibling field value
fill:fieldid                   fill this field from lookup result
filter:k=v                     filter lookup rows
formula:expr                   computed display-only value
readonly_below:N               lock field for users with level < N
```

---

## FIELD SYNTAX

```
field_id | width | flag flag flag
```

- `width` is display width only (integer), may be blank for auto
- flags are space-separated, no commas between them
- flags with values use colon with NO space: `default:Draft`, `prefix:ORD`

```
// Good examples:
grn_no      | 12 | req prefix:GRN
grn_date    | 12 | date
part_name   | 40 | req span:2
unit_cost   | 12 | num default:0 readonly_below:50
warehouse   | 12 | lookup:warehouse fill:warehouse_name
notes       | 60 | span:2 rows:3
status      |    | opts:Draft,Posted,Void default:Draft
```

---

## SCRIPT INSTRUCTIONS — complete list

```
SET var = expression
IF condition ... END
FOREACH var IN list ... END
FETCH var FROM table WHERE field = expr
UPDATE table SET field = expr, field = expr WHERE field = expr
INSERT table SET field = expr, field = expr
DELETE table WHERE field = expr
CALL subroutine_name(arg1, arg2)
OK "message"        -- show success popup and exit script
FAIL "message"      -- show error popup and stop script
COUNTROWS("table", "field", value)   -- count matching rows
```

Variables available in scripts:
- Named form fields (e.g. `grn_no`, `warehouse`)
- `lines` — current detail rows (list)
- `line` — current row inside FOREACH
- `header` — the saved parent record (in ON SAVE)
- `_last_rowid` — rowid of last INSERT
- `_rowid` — rowid of fetched record

Expressions: `+ - * / ( )`, comparisons `= != < > <= >=`, `AND OR NOT`,
string concat with `+`, `STR(x)`, `NUM(x)`, `TODAY`, `COUNT(list)`,
`SUM(list, field)`, `MIN/MAX/ABS`.

### Complete built-in function list

```
-- Math
ABS(x)  ROUND(x, dp)  CEIL(x)  FLOOR(x)  MIN(a,b)  MAX(a,b)  DIV(a,b)

-- String
UPPER(s)  LOWER(s)  LEN(s)  LEFT(s,n)  RIGHT(s,n)  TRIM(s)
REPLACE(s,old,new)  LPAD(s,n,ch)  CONCAT(a,b,...)

-- Type conversion
STR(x)  NUM(x)  ISNULL(v,default)  ISBLANK(v)

-- Date
TODAY  NOW  YEAR(d)  MONTH(d)  DAY(d)  QUARTER(d)  YEARMONTH(d)
DAYOFWEEK(d)  DATEDIFF(d1,d2)  DATEADD(d,n,"days"|"months"|"years")

-- Aggregation (over detail lines)
COUNT(list)  SUM(list,field)  SUMIF(list,field,cond_field,cond_val)

-- Lookup / reference
LOOKUPVALUE("table","key_field",key,"return_field")
LOOKUPRANGE("table","key","low_field","high_field","return_field")
LOOKUPSTEP("table","key","step_field","return_field")
COUNTROWS("table","field",value)
NEXTDOCNO("table","field","prefix")

-- Document / stock
BOMEXPLODE(bom_no, qty)
BOMROLLUP(bom_no)
FIFOOUT(warehouse, part_no, qty)
BASEQTY(qty, pack_uom)
DISPLAYQTY(base_qty, pack_uom)

-- Settings
SETTING("key")

-- WMS (implemented as Python helpers — see note below)
PUTAWAY_ALLOCATE(warehouse, grn_lines, grn_no)
PICKING_ALLOCATE(warehouse, do_lines)
PICKING_CONFIRM(warehouse, pick_lines)
```

---

## DSL SCRIPT FIRST — NO PYTHON FALLBACK

**This is the most important rule for AI tools.**

The DSL Script engine handles loops, conditionals, DB reads/writes, aggregations,
lookups, and multi-step transactions. Do NOT suggest adding Python code unless the
task provably requires something the engine cannot do.

### DSL Script CAN handle — do not escalate to Python

```
-- Multi-step transaction with condition
SCRIPT post_invoice
  IF status = "Posted"
    FAIL "Already posted."
  END
  SET _cnt = COUNTROWS("inv_line", "_parent_rowid", _rowid)
  IF _cnt = 0
    FAIL "No lines."
  END
  FOREACH ln IN lines
    FETCH it FROM item WHERE _rowid = ln.item_rowid
    SET new_oh = it.on_hand - ln.qty
    UPDATE item SET on_hand = new_oh WHERE _rowid = ln.item_rowid
    INSERT inv_posting SET doc_no = inv_no, part_no = ln.part_no, qty = ln.qty
  END
  OK "Posted " + STR(_cnt) + " lines."
ENDSCRIPT

-- Running total with lookup
SCRIPT calc_total
  SET total = 0
  FOREACH ln IN lines
    SET p = LOOKUPVALUE("price_list","part_no",ln.part_no,"price")
    SET total = total + (ln.qty * p)
  END
  UPDATE so SET grand_total = total WHERE _rowid = header._rowid
ENDSCRIPT

-- Date arithmetic and FIFO
SCRIPT issue_stock
  FETCH avail FROM warehouse_storage
    WHERE part_no = part_no AND balance > 0
  IF avail.balance < qty
    FAIL "Insufficient stock."
  END
  SET issued = FIFOOUT(warehouse, part_no, qty)
  OK "Issued: " + STR(issued)
ENDSCRIPT
```

### When Python IS allowed — strict criteria, ALL must be true

Python helper functions in `dsl_lib/script_engine.py` are acceptable ONLY when:

1. The algorithm requires **sorting + mutable state tracking across iterations**
   that cannot be expressed with FOREACH (e.g. CBM bin-packing across locations)
2. The algorithm needs to **read and write many rows atomically** in a way that
   would require dozens of nested FOREACH/FETCH blocks
3. The logic is **domain-specific and reusable** across many scripts (e.g.
   PUTAWAY_ALLOCATE, PICKING_ALLOCATE, FIFOOUT)

If only ONE of these is true, write it in DSL Script.

### NEVER write Python for these — DSL handles them

```
// Totalling lines                          → SUM(lines, field)
// Counting records                         → COUNTROWS("table","field",val)
// Looking up a value from another table    → LOOKUPVALUE(...)
// Updating a field based on a lookup       → FETCH + SET + UPDATE
// Running a calculation across lines       → FOREACH + SET + arithmetic
// Date comparisons                         → DATEDIFF, DATEADD, TODAY
// Conditional posting                      → IF + FAIL / OK
// Cascading inserts (header + detail rows) → INSERT + _last_rowid + FOREACH INSERT
// Auto-numbering                           → NEXTDOCNO(...)
// Reversing a stock movement               → STOCK RECEIVE REVERSE
// BOM explosion                            → BOMEXPLODE(bom_no, qty)
// FIFO stock deduction                     → FIFOOUT(warehouse, part_no, qty)
```

### Decision flowchart

```
Can DSL Script express the logic with FOREACH, IF, FETCH, UPDATE, INSERT?
  YES → write it in DSL Script inside a SCRIPT block
  NO  → Does it require sorting + stateful allocation (bin-packing, FIFO across
        multiple storage records, capacity planning)?
          YES → add a named Python function to script_engine.py _eval_func,
                expose as a DSL keyword, document in this file
          NO  → go back and try harder with DSL Script
```

---

## WORKFLOW SYNTAX

```
WORKFLOW ON status
  Draft > Posted : script_name
  Posted > Void  : void_script
  ACTION "Button Label" : script_name
END WORKFLOW
```

- `transition`: `from_value > to_value : script_name`
- `ACTION`: shows as F5 menu item, runs script, does NOT change status
- `---` adds a separator in F5 menu
- `script_name` must be defined in a `SCRIPT` block in same form

---

## COMPUTED BLOCK

```
COMPUTED
  subtotal    = SUM(lines, amount)
  tax_amt     = subtotal * tax_rate / 100
  grand_total = subtotal + tax_amt
END COMPUTED
```

- Field names in COMPUTED must exist as `ro` fields in the form
- Evaluated top-to-bottom; later expressions can reference earlier results
- `SUM(lines, fieldname)` sums a detail column
- `COUNT(lines)` counts detail rows

---

## MINIMAL COMPLETE EXAMPLE

```dsl
APP "My App" VERSION "1.0"
DATASOURCE "my_db" ADAPTER "sqlite"

FORM customer TITLE "Customers" COLS 2
  SECTION "Identity"
  cust_code    | 12 | req prefix:CST
  cust_name    | 40 | req span:2
  SECTION "Contact"
  phone        | 15 |
  email        | 30 |
  credit_limit | 12 | num default:0
  LIST cust_code:12 cust_name:30 phone:15 credit_limit:12
END

FORM invoice TITLE "Invoices" COLS 2
  SECTION "Header"
  inv_no       | 14 | req prefix:INV
  inv_date     | 12 | date
  cust_code    | 12 | lookup:customer fill:cust_name
  cust_name    | 40 | ro span:2
  status       |    | opts:Draft,Posted,Void default:Draft
  SECTION "Totals"
  subtotal     | 14 | num ro
  grand_total  | 14 | num ro
  COMPUTED
    subtotal    = SUM(lines, amount)
    grand_total = subtotal
  END COMPUTED
  DETAIL "Invoice Lines" AS inv_line
    part_no  | 14 | lookup:item fill:part_name
    part_name| 30 | ro
    qty      | 10 | num
    price    | 12 | num
    amount   | 14 | num ro formula:qty*price
  END DETAIL
  SCRIPT post
    IF status = "Posted"
      FAIL "Already posted."
    END
    OK "Invoice posted."
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    ACTION "Preview" : post
  END WORKFLOW
  LIST inv_no:14 inv_date:12 cust_name:30 status:10 grand_total:14
END

MENU "Main" PULLDOWN
  GROUP "Sales"
    "Customers" => customer
    "Invoices"  => invoice
    ---
    "Exit"      => EXIT
  END
  CENTER
    "1. Customers" => customer
    "2. Invoices"  => invoice
    "3. Exit"      => EXIT
  END CENTER
END
```

---

## RESET BLOCK

```
RESET
  CLEAR table1, table2, table3
  UPDATE item SET on_hand = 0, allocated = 0
END RESET
```

---

## REPORT

```
REPORT low_stock TITLE "Low Stock Report"
  SOURCE item
  ORDER on_hand ASC
  WHERE on_hand < 10
  COLUMNS
    part_no   | "Part No"  | 12
    part_name | "Name"     | 40
    on_hand   | "On Hand"  | 10 | num
  END COLUMNS
  TOTALS on_hand
END REPORT
```

---

## MENU TARGETS — exhaustive list

```
form_name          opens the form's default browse list
form_name.list     explicit browse list
form_name.add      opens blank add form
EXIT               quit app
RESET              run RESET block (with confirmation)
REPORTS            reports picker
SETTINGS           business settings editor
EXCEL.IMPORT       import Excel workbook
SWITCH             switch company/database
report:name        run a specific REPORT directly
```

---

## QUICK CHECKLIST before submitting DSL

- [ ] Every `FORM` closes with `END` (not `END FORM`)
- [ ] Every `SCRIPT` closes with `ENDSCRIPT` (not `END SCRIPT`)
- [ ] Every `SUBROUTINE` closes with `ENDSUBROUTINE`
- [ ] Every `DETAIL` closes with `END DETAIL`
- [ ] Every `IF` closes with `END` (not `END IF`)
- [ ] Every `FOREACH` closes with `END` (not `END FOREACH`)
- [ ] Field flags use `req` not `required`, `ro` not `readonly`
- [ ] `opts:` values have no quotes: `opts:Draft,Posted`
- [ ] `prefix:` has no quotes: `prefix:GRN`
- [ ] `default:` has no quotes and no spaces: `default:0.10`
- [ ] `WORKFLOW` script names match a `SCRIPT` in same form
- [ ] `LIST` references only fields that exist in the same `FORM`
- [ ] Run `python main.py --validate-only your.dsl` — fix all errors first
