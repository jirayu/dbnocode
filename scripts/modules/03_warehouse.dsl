// ── Warehouse Storage ──
FORM warehouse_storage TITLE "Warehouse Storage" COLS 2
  SECTION "Item"
  part_no       | 20 | upper ro
  part_name     | 36 | ro span:2
  warehouse     | 16 | ro
  location      | 16 | ro
  SECTION "Quantities"
  on_hand       | 12 | num ro
  picked        | 12 | num ro
  balance       | 12 | num ro
  allocated     | 12 | num ro
  available     | 12 | num ro
  SECTION "Reference"
  lot_no        | 20 | ro
  grn_no        | 20 | ro
  pa_no         | 20 | ro
  entry_date    | 12 | date ro
  uom           | 10 | ro
  customer      | 16 | lookup:customer
  customer_name | 36 | span:2
  LISTVIEW
    "View All"          | *
    "View with Balance" | balance > 0
  END LISTVIEW
  LIST part_no:14 part_name:28 warehouse:10 location:10 on_hand:8 picked:8 balance:8 lot_no:14 customer:12
END

// ══════════════════════════════════════════
// PUT-AWAY LIST (auto-generated from GRN)
// ══════════════════════════════════════════

FORM put_away TITLE "Put-Away List" COLS 2
  SECTION "Put-Away"
  pa_no         | 16 | req prefix:PA:YYMM ro
  pa_date       | 12 | date ro
  grn_no        | 16 | ro
  warehouse     | 12 | ro
  for_customer  | 12 | ro
  customer_name | 30 | ro span:2
  status        |    | ro default:Draft
  DETAIL "Put-Away Lines" AS pa_line
    part_no     | 14 | ro
    part_name   | 24 | ro
    qty         |  8 | num ro
    cbm_per_unit| 10 | num ro
    total_cbm   | 10 | num ro
    loc_id      | 12 | ro
    level       |  6 | num ro
    lot_no      | 12 | ro
  END DETAIL
  SCRIPT confirm_pa
    OK "Put-Away confirmed"
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Confirmed : confirm_pa
  END WORKFLOW
  LIST pa_no:18 pa_date:12 grn_no:18 for_customer:12 customer_name:24 warehouse:10 status:10
END

// ══════════════════════════════════════════
// PICKING LIST (auto-generated from DO)
// ══════════════════════════════════════════

FORM picking_list TITLE "Picking List" COLS 2
  SECTION "Picking"
  pk_no         | 16 | req prefix:PK:YYMM ro
  pk_date       | 12 | date ro
  do_no         | 16 | ro
  cust_code     | 12 | ro
  cust_name     | 36 | ro span:2
  warehouse     | 12 | ro
  status        |    | ro default:Draft
  DETAIL "Pick Lines" AS pk_line
    part_no     | 14 | ro
    part_name   | 24 | ro
    pick_qty    |  8 | num ro
    uom         |  6 | ro
    lot_no      | 14 | ro
    grn_no      | 14 | ro
    loc_id      | 12 | ro
    pack_uom    |  6 | ro hidden
    pack_qty    |  8 | num ro hidden
    pick_base_qty | 10 | num ro hidden
    storage_rowid | 10 | ro hidden
  END DETAIL
  SCRIPT confirm_pick
    SET updated = PICKING_CONFIRM(warehouse, lines)
    OK "Picking confirmed. " + STR(updated) + " storage rows updated."
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Confirmed : confirm_pick
  END WORKFLOW
  LIST pk_no:14 pk_date:12 do_no:14 cust_name:24 warehouse:10 status:10
END
