APP "Inventory Management" VERSION "1.0"

DATASOURCE "inventory_db" ADAPTER "sqlite"

// ══════════════════════════════════════════
// STOCK SUBROUTINES
// ══════════════════════════════════════════

// Recalculate stock fields after on_hand changes (GRN, Issue, DO).
// rowid = item._rowid, delta = qty change (+receive, -issue)
SUBROUTINE stock_recalc(rowid, delta)
  FETCH _sr_item FROM item WHERE _rowid = rowid
  SET _sr_oh = _sr_item.on_hand + delta
  SET _sr_demand = _sr_item.allocated + _sr_item.back_order
  SET _sr_alloc = MIN(_sr_oh, _sr_demand)
  SET _sr_bo = _sr_demand - _sr_alloc
  SET _sr_avail = _sr_oh - _sr_alloc
  UPDATE item SET on_hand = _sr_oh, allocated = _sr_alloc, back_order = _sr_bo, available = _sr_avail WHERE _rowid = rowid
ENDSUBROUTINE

// Recalculate after demand changes (SO post/void). on_hand unchanged.
// rowid = item._rowid, delta = demand change (+order_qty or -order_qty)
SUBROUTINE stock_adjust_demand(rowid, delta)
  FETCH _sd_item FROM item WHERE _rowid = rowid
  SET _sd_demand = _sd_item.allocated + _sd_item.back_order + delta
  SET _sd_demand = MAX(0, _sd_demand)
  SET _sd_alloc = MIN(_sd_item.on_hand, _sd_demand)
  SET _sd_bo = _sd_demand - _sd_alloc
  SET _sd_avail = _sd_item.on_hand - _sd_alloc
  UPDATE item SET allocated = _sd_alloc, back_order = _sd_bo, available = _sd_avail WHERE _rowid = rowid
ENDSUBROUTINE

// Recalculate after both on_hand AND demand change (DO ship/void).
// rowid = item._rowid, oh_delta = on_hand change, demand_delta = demand change
SUBROUTINE stock_recalc_both(rowid, oh_delta, demand_delta)
  FETCH _sb_item FROM item WHERE _rowid = rowid
  SET _sb_oh = _sb_item.on_hand + oh_delta
  SET _sb_demand = _sb_item.allocated + _sb_item.back_order + demand_delta
  SET _sb_demand = MAX(0, _sb_demand)
  SET _sb_alloc = MIN(_sb_oh, _sb_demand)
  SET _sb_bo = _sb_demand - _sb_alloc
  SET _sb_avail = _sb_oh - _sb_alloc
  UPDATE item SET on_hand = _sb_oh, allocated = _sb_alloc, back_order = _sb_bo, available = _sb_avail WHERE _rowid = rowid
ENDSUBROUTINE

// ══════════════════════════════════════════
// REFERENCE TABLES
// ══════════════════════════════════════════

// ── Item Master ──
FORM item TITLE "Item Master" COLS 2
  SECTION "Item Information"

  part_no    | 12 | req prefix:ITM
  part_name  | 40 | req span:2
  code_name  | 20 | 
  category   |    | opts:Raw Material,Finished Good,Component,Consumable,Service
  part_type  | 10 |
  part_level | 10 |
  uom        |    | opts:EA,PC,KG,M,L,BOX,SET,HR
  pack_uom   |    | opts:EA,PC,KG,M,L,BOX,SET,HR
  pack_qty   |    | num
  recv_uom   |    | opts:EA,PC,KG,M,L,BOX,SET,HR
  issue_uom  |    | opts:EA,PC,KG,M,L,BOX,SET,HR
  barcode_tag| 20 |
  unit_cost  |    | num
  pack_cost  |    | num
  unit_price |    | num
  pack_price |    | num
  cbm        |    | num default:0.10
  SECTION "Stock Levels"

  min_qty    |    | num 
  max_qty    |    | num 
  reorder_qty|    | num 
  moq        |    | num
  lead_days  |    | num
  on_hand    |    | num
  on_order   |    | num
  allocated  |    | num ro
  back_order |    | num ro
  available  |    | num ro
  specs      | 50 | span:2 rows:3
  DETAIL "Stock Lots" AS item_lot
    entry_date  | 12 | date ro
    doc_no      | 14 | ro
    lot_no      | 12 | ro
    warehouse   |  8 | ro
    on_hand     | 10 | num ro
    uom         |  5 | ro
    unit_cost   | 10 | num ro
  END DETAIL
  DETAIL "Stock Card" AS item_card
    trans_date  | 12 | date ro
    trans_type  |  8 | ro
    doc_no      | 14 | ro
    receive_qty | 10 | num ro
    issue_qty   | 10 | num ro
    balance     | 12 | num ro
    unit_cost   | 10 | num ro
  END DETAIL
  ON SAVE
    IF header.on_hand
      WRITE item_card
        trans_date  = TODAY
        trans_type  = "INIT"
        doc_no      = header.part_no
        receive_qty = header.on_hand
        issue_qty   = 0
        balance     = header.on_hand
        unit_cost   = header.unit_cost
      END WRITE
      WRITE item_lot
        entry_date  = TODAY
        doc_no      = header.part_no
        lot_no      = "INIT"
        warehouse   = ""
        on_hand     = header.on_hand
        uom         = header.uom
        unit_cost   = header.unit_cost
      END WRITE
      UPDATE item.available = header.on_hand
    END IF
  END SAVE
  UDF "udf_items"
  LIST part_no:12 part_name:30 category:14 code_name:20 uom:5 on_hand:10 on_order:10 allocated:10 back_order:10 available:10
END

FIELDSET party_contact
  b2b_host     | 24 |
  b2b_port     |  8 | num default:8085
  contact      | 30 |
  phone        | 15 |
  email        | 30 |
  address      | 50 | span:2 rows:3
  online_id    | 24 | ro
END FIELDSET

// ── Vendor/Supplier ──
FORM vendor TITLE "Vendor Master" COLS 2
  vendor_code  | 10 | req prefix:VEN
  vendor_name  | 40 | req span:2
  USE party_contact
  LIST vendor_code:10 vendor_name:30 contact:20 phone:15
END

// ── Customer ──
FORM customer TITLE "Customer Master" COLS 2
  cust_code    | 10 | req prefix:CUS
  cust_name    | 40 | req span:2
  USE party_contact
  credit_limit |    | num
  LIST cust_code:10 cust_name:30 contact:20 phone:15 credit_limit:12
END

// ── Warehouse ──
FORM warehouse TITLE "Warehouse Info" COLS 2
  SECTION "Warehouse"
  whid            | 12 | req prefix:WH
  warehouse_type  |    | opts:SITE,VIRTUAL,DAMAGE,HOLD,3RDPT,DOCK,WIP
  online_id       | 24 |
  warehouse_name  | 55 | req span:2
  SECTION "Capacity"
  total_cbm       | 12 | num
  used_cbm        | 12 | num ro
  available_cbm   | 12 | num ro
  SECTION "Location Grid Settings"
  wh_rows         |  6 | num default:8
  wh_cols         |  6 | num default:10
  wh_levels       |  6 | num default:3
  default_cbm     | 10 | num default:150
  zone_map        | 40 | default:1-5:A,6-8:B,9-10:C
  LIST whid:12 warehouse_name:30 warehouse_type:10 total_cbm:10 wh_rows:6 wh_cols:6 wh_levels:6
END

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

// ── Web Order Entry (CoEdit) ──
FORM wo TITLE "Web Order" ENTRY COLS 2
  SECTION "Web Order"

  wo_number    | 18 | req prefix:WO:YYMM
  wo_date      |    | date
  cust_code    | 10 | lookup:customer fill:cust_name
  cust_name    | 40 | ro
  warehouse    |  8 | lookup:warehouse default:@setting.default_warehouse
  status       |    | ro default:Draft

  SECTION "Summary"
  total_qty    |  8 | num ro
  total_amount | 14 | num ro
  item_count   |  6 | num ro
  DETAIL "Order Lines" AS wo_line TOTALS
    part_no    | 12 | ro
    part_name  | 25 | ro
    category   | 14 | ro
    uom        |  5 | ro
    wh_balance |  8 | num ro
    unit_price | 10 | num ro
    order_qty  | 10 | num
    amount     | 14 | num ro formula:order_qty*unit_price
    base_uom   |  6 | ro hidden
    pack_uom   |  6 | ro hidden
    pack_qty   |  8 | num ro hidden
    issue_uom  |  6 | ro hidden
    base_unit_price | 10 | num ro hidden
    pack_price | 10 | num ro hidden
    PREFILL item filter:on_hand>0
    COEDIT order_qty
  END DETAIL
  COMPUTED
    total_qty    = SUM(lines, order_qty)
    total_amount = SUM(lines, amount)
    item_count   = COUNT(lines)
  END COMPUTED
  SCRIPT post
    OK "Web Order confirmed"
  ENDSCRIPT
  SCRIPT void
    OK "Web Order voided"
  ENDSCRIPT
  SCRIPT generate_pk
    SET _pk_count = COUNTROWS("picking_list", "do_no", wo_number)
    IF _pk_count > 0
      FAIL "Picking List already generated for this Web Order."
    END
    IF ISBLANK(warehouse)
      FAIL "Select a warehouse before generating picking list."
    END
    SET pick_lines = PICKING_ALLOCATE(warehouse, lines)
    IF COUNT(pick_lines) = 0
      FAIL "No items with balance > 0 in warehouse storage."
    END
    SET _pk_no = NEXTDOCNO("picking_list", "pk_no", "PK", "YYMM")
    INSERT picking_list SET pk_no = _pk_no, pk_date = wo_date, do_no = wo_number, cust_code = cust_code, cust_name = cust_name, warehouse = warehouse, status = "Draft"
    SET _pk_rowid = _last_rowid
    FOREACH ln IN pick_lines
      INSERT pk_line SET part_no = ln.part_no, part_name = ln.part_name, pick_qty = ln.pick_qty, uom = ln.uom, lot_no = ln.lot_no, grn_no = ln.grn_no, loc_id = ln.loc_id, pack_uom = ln.pack_uom, pack_qty = ln.pack_qty, pick_base_qty = ln.pick_base_qty, storage_rowid = ln._storage_rowid, _parent_rowid = _pk_rowid
    END
    OK "Picking List " + STR(_pk_no) + " created with " + STR(COUNT(pick_lines)) + " lines."
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Confirmed : post
    Confirmed > Void  : void
    ACTION "Generate Picking" : generate_pk
  END WORKFLOW
  HIGHLIGHT
    status == "Void"      | error
    status == "Confirmed" | success
  END HIGHLIGHT
  LIST wo_number:18 wo_date:12 cust_code:10 cust_name:25 total_amount:14 status:10
END

// ══════════════════════════════════════════
// POS SLIP (Point of Sale)
// ══════════════════════════════════════════

FORM pos_slip TITLE "POS Slip" COLS 2
  SECTION "Sale Info"
  slip_no       | 12 | req prefix:POS
  issue_date    | 12 | date default:TODAY
  payment       |    | opts:CASH,TRANSFER,CREDIT-CARD,SCANNED
  card_no       | 20 |
  customer_id   |    | lookup:customer fill:cust_name=>customer_name
  customer_name | 30 | ro
  status        | 10 | ro default:Draft
  SECTION "Totals"
  subtotal      | 14 | num ro
  tax_rate      |  6 | num default:@setting.tax_rate_pct
  tax_amt       | 14 | num ro
  grand_total   | 14 | num ro
  DETAIL "Sale Lines" AS pos_slip_line TOTALS
    part_no     | 14 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,issue_uom,unit_price=>base_unit_price,pack_price
    part_name   | 24 | ro
    quantity    |  8 | num
    uom         |  5 | ro
    unit_price  | 10 | num
    line_total  | 12 | num ro formula:quantity*unit_price
    base_uom    |  6 | ro hidden
    pack_uom    |  6 | ro hidden
    pack_qty    |  8 | num ro hidden
    issue_uom   |  6 | ro hidden
    base_unit_price | 10 | num ro hidden
    pack_price  | 10 | num ro hidden
  END DETAIL
  COMPUTED
    subtotal    = SUM(lines, line_total)
    tax_amt     = subtotal * tax_rate / 100
    grand_total = subtotal + tax_amt
  END COMPUTED
  LIST slip_no:12 issue_date:12 payment:12 customer_name:25 grand_total:14 status:10
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _pos_qty = BASEQTY(line.quantity, line.uom, line.pack_uom, line.pack_qty)
      IF item.on_hand < _pos_qty
        FAIL "Insufficient stock: " + line.part_no + " (have " + STR(item.on_hand) + ", need " + STR(_pos_qty) + ")"
      END
      CALL stock_recalc(item._rowid, 0 - _pos_qty)
      INSERT item_card SET trans_date = issue_date, trans_type = "POS", doc_no = slip_no, receive_qty = 0, issue_qty = _pos_qty, balance = item.on_hand - _pos_qty, unit_cost = line.unit_price, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
  END WORKFLOW
END

// ══════════════════════════════════════════
// PURCHASE ORDER
// ══════════════════════════════════════════

FORM po TITLE "Purchase Order" COLS 2
  SECTION "Purchase Order"
  po_number   | 18 | req prefix:PO:YYMM
  po_date     |    | date
  order_no    | 20 |  
  vendor_code | 10 | lookup:vendor fill:vendor_name
  vendor_name | 40 |
  warehouse   |  8 | lookup:warehouse  
  status      |    | ro default:Draft
  notes       | 50 | span:2 rows:2
  SECTION "Totals"
  subtotal    | 14 | num ro
  tax_rate    |  6 | num default:@setting.tax_rate_pct
  tax_amt     | 14 | num ro
  grand_total | 14 | num ro
  DETAIL "Order Lines" AS po_line TOTALS
    part_no      | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,recv_uom,unit_cost=>base_unit_cost,pack_cost
    part_name    | 25 | ro
    uom          |  6 | ro
    order_qty    | 10 | num
    unit_cost    | 12 | num
    amount       | 14 | num formula:order_qty*unit_cost
    received_qty | 10 | num ro
    base_uom     |  6 | ro hidden
    pack_uom     |  6 | ro hidden
    pack_qty     |  8 | num ro hidden
    recv_uom     |  6 | ro hidden
    base_unit_cost | 10 | num ro hidden
    pack_cost    | 10 | num ro hidden
  END DETAIL
  COMPUTED
    subtotal    = SUM(lines, amount)
    tax_amt     = subtotal * tax_rate / 100
    grand_total = subtotal + tax_amt
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _po_qty = BASEQTY(line.order_qty, line.uom, line.pack_uom, line.pack_qty)
      UPDATE item SET on_order = item.on_order + _po_qty WHERE _rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _po_qty = BASEQTY(line.order_qty, line.uom, line.pack_uom, line.pack_qty)
      UPDATE item SET on_order = item.on_order - _po_qty WHERE _rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
  END WORKFLOW
  LIST po_number:18 po_date:12 vendor_code:10 vendor_name:25 order_no:20 status:10
END

// ══════════════════════════════════════════
// GOODS RECEIPT NOTE
// ══════════════════════════════════════════

FORM grn TITLE "Goods Receipt" COLS 2
  SECTION "Goods Receipt"
  grn_no      | 18 | req prefix:GRN:YYMM
  grn_date    |    | date
  po_number   | 18 | lookup:po filter:status=Posted fill:vendor_name copydetail:po_line
  vendor_name | 40 |
  order_no    | 20 |
  warehouse   |  8 | lookup:warehouse
  for_customer|    | lookup:customer fill:cust_name=>customer_name
  customer_name| 30| ro
  status      |    | ro default:Draft
  remarks     | 50 | span:2 rows:2
  SECTION "Totals"
  subtotal    | 14 | num ro
  tax_rate    |  6 | num default:@setting.tax_rate_pct
  tax_amt     | 14 | num ro
  grand_total | 14 | num ro
  DETAIL "Receipt Lines" AS grn_line TOTALS
    part_no      | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,recv_uom,unit_cost=>base_unit_cost,pack_cost
    part_name    | 25 | ro
    uom          |  6 | ro
    order_qty    | 10 | num ro
    received_qty | 12 | num
    lot_no       | 12 |
    unit_cost    | 12 | num
    amount       | 14 | num formula:received_qty*unit_cost
    base_uom     |  6 | ro hidden
    pack_uom     |  6 | ro hidden
    pack_qty     |  8 | num ro hidden
    recv_uom     |  6 | ro hidden
    base_unit_cost | 10 | num ro hidden
    pack_cost    | 10 | num ro hidden
  END DETAIL
  COMPUTED
    subtotal    = SUM(lines, amount)
    tax_amt     = subtotal * tax_rate / 100
    grand_total = subtotal + tax_amt
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _grn_qty = BASEQTY(line.received_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_recalc(item._rowid, _grn_qty)
      IF po_number
        UPDATE item SET on_order = item.on_order - _grn_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty + line.received_qty WHERE _rowid = po_ln._rowid
      END
      INSERT item_lot SET entry_date = grn_date, doc_no = grn_no, lot_no = line.lot_no, warehouse = warehouse, on_hand = _grn_qty, uom = item.uom, unit_cost = line.unit_cost, _parent_rowid = item._rowid
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = grn_date, trans_type = "RECV", doc_no = grn_no, receive_qty = _grn_qty, issue_qty = 0, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _grn_qty = BASEQTY(line.received_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_recalc(item._rowid, 0 - _grn_qty)
      IF po_number
        UPDATE item SET on_order = item.on_order + _grn_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty - line.received_qty WHERE _rowid = po_ln._rowid
      END
      DELETE item_lot WHERE doc_no = grn_no AND _parent_rowid = item._rowid
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = grn_date, trans_type = "VOID-R", doc_no = grn_no, receive_qty = 0, issue_qty = _grn_qty, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT generate_pa
    SET _pa_count = COUNTROWS("put_away", "grn_no", grn_no)
    IF _pa_count > 0
      FAIL "Put-Away already generated for this GRN."
    END
    SET _pa_wh = warehouse
    IF ISBLANK(_pa_wh)
      SET _pa_wh = "MAIN"
    END
    SET pa_lines = PUTAWAY_ALLOCATE(_pa_wh, lines, for_customer, grn_no)
    IF COUNT(pa_lines) = 0
      FAIL "No available locations for put-away."
    END
    SET _pa_no = NEXTDOCNO("put_away", "pa_no", "PA", "YYMM")
    INSERT put_away SET pa_no = _pa_no, pa_date = grn_date, grn_no = grn_no, warehouse = _pa_wh, for_customer = for_customer, customer_name = customer_name, status = "Draft"
    SET _pa_rowid = _last_rowid
    FOREACH ln IN pa_lines
      INSERT pa_line SET part_no = ln.part_no, part_name = ln.part_name, qty = ln.qty, cbm_per_unit = ln.cbm_per_unit, total_cbm = ln.total_cbm, loc_id = ln.loc_id, level = ln.level, lot_no = ln.lot_no, _parent_rowid = _pa_rowid
    END
    FOREACH ln IN pa_lines
      INSERT warehouse_storage SET part_no = ln.part_no, part_name = ln.part_name, warehouse = _pa_wh, location = ln.loc_id, on_hand = ln.qty, picked = 0, balance = ln.qty, allocated = 0, available = ln.qty, lot_no = ln.lot_no, grn_no = grn_no, entry_date = grn_date, uom = ln.uom, customer = for_customer, customer_name = customer_name
    END
    OK "Put-Away created with " + STR(COUNT(pa_lines)) + " locations."
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
    ACTION "Generate Put-Away" : generate_pa
  END WORKFLOW
  LIST grn_no:18 grn_date:12 po_number:18 vendor_name:25 status:10
END

// ══════════════════════════════════════════
// ISSUE SLIP (Stock Out)
// ══════════════════════════════════════════

FORM issue TITLE "Issue Slip" COLS 2
  SECTION "Issue Slip"
  issue_no   | 18 | req prefix:IS:YYMM
  issue_date |    | date
  dept       | 20 |
  issued_to  | 30 |
  warehouse  |  8 | lookup:warehouse
  status     |    | ro default:Draft
  remarks    | 50 | span:2 rows:2
  DETAIL "Issue Lines" AS issue_line TOTALS
    part_no   | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,issue_uom,unit_cost=>base_unit_cost,pack_cost
    part_name | 25 | ro
    issue_qty | 10 | num
    uom       |  6 | ro
    unit_cost | 12 | num
    amount    | 14 | num formula:issue_qty*unit_cost
    base_uom  |  6 | ro hidden
    pack_uom  |  6 | ro hidden
    pack_qty  |  8 | num ro hidden
    issue_uom |  6 | ro hidden
    base_unit_cost | 10 | num ro hidden
    pack_cost | 10 | num ro hidden
  END DETAIL
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _issue_base = BASEQTY(line.issue_qty, line.uom, line.pack_uom, line.pack_qty)
      SET short = FIFOOUT("item_lot", "on_hand", "entry_date", _issue_base, "_parent_rowid", item._rowid)
      CALL stock_recalc(item._rowid, 0 - _issue_base)
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = issue_date, trans_type = "ISSUE", doc_no = issue_no, receive_qty = 0, issue_qty = _issue_base, balance = item.on_hand, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _issue_base = BASEQTY(line.issue_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_recalc(item._rowid, _issue_base)
      INSERT item_lot SET entry_date = issue_date, doc_no = issue_no, warehouse = warehouse, on_hand = _issue_base, uom = item.uom, unit_cost = line.unit_cost, _parent_rowid = item._rowid
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = issue_date, trans_type = "VOID-I", doc_no = issue_no, receive_qty = _issue_base, issue_qty = 0, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
  END WORKFLOW
  LIST issue_no:18 issue_date:12 dept:15 issued_to:25 status:10
END

// ══════════════════════════════════════════
// STOCK TRANSFER
// ══════════════════════════════════════════

FORM transfer TITLE "Stock Transfer" COLS 2
  SECTION "Stock Transfer"
  transfer_no   | 18 | req prefix:TRF:YYMM
  transfer_date |    | date
  from_wh       |  8 | lookup:warehouse
  to_wh         |  8 | lookup:warehouse
  status        |    | ro default:Draft
  remarks       | 50 | span:2 rows:2
  DETAIL "Transfer Lines" AS transfer_line TOTALS
    part_no      | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,issue_uom,unit_cost=>base_unit_cost,pack_cost
    part_name    | 25 | ro
    transfer_qty | 12 | num
    uom          |  6 | ro
    unit_cost    | 12 | num ro
    base_uom     |  6 | ro hidden
    pack_uom     |  6 | ro hidden
    pack_qty     |  8 | num ro hidden
    issue_uom    |  6 | ro hidden
    base_unit_cost | 10 | num ro hidden
    pack_cost    | 10 | num ro hidden
  END DETAIL
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _transfer_base = BASEQTY(line.transfer_qty, line.uom, line.pack_uom, line.pack_qty)
      IF item.on_hand < _transfer_base
        FAIL "Insufficient stock: " + line.part_no + " (have " + STR(item.on_hand) + ", need " + STR(_transfer_base) + ")"
      END
      SET short = FIFOOUT("item_lot", "on_hand", "entry_date", _transfer_base, "_parent_rowid", item._rowid)
      INSERT item_lot SET entry_date = transfer_date, doc_no = transfer_no, warehouse = to_wh, on_hand = _transfer_base, uom = item.uom, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
      INSERT item_card SET trans_date = transfer_date, trans_type = "TRF-OUT", doc_no = transfer_no, receive_qty = 0, issue_qty = _transfer_base, balance = item.on_hand, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
      INSERT item_card SET trans_date = transfer_date, trans_type = "TRF-IN", doc_no = transfer_no, receive_qty = _transfer_base, issue_qty = 0, balance = item.on_hand, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _transfer_base = BASEQTY(line.transfer_qty, line.uom, line.pack_uom, line.pack_qty)
      INSERT item_lot SET entry_date = transfer_date, doc_no = transfer_no, warehouse = from_wh, on_hand = _transfer_base, uom = item.uom, unit_cost = item.unit_cost, _parent_rowid = item._rowid
      INSERT item_card SET trans_date = transfer_date, trans_type = "VOID-T", doc_no = transfer_no, receive_qty = _transfer_base, issue_qty = 0, balance = item.on_hand, unit_cost = item.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
  END WORKFLOW
  LIST transfer_no:18 transfer_date:12 from_wh:10 to_wh:10 status:10
END

// ══════════════════════════════════════════
// QUICK ISSUE — ENTRY mode (memory table)
//   Prefills from item master, CoEdit on
//   issue_qty. Only non-zero rows saved.
// ══════════════════════════════════════════

FORM qi TITLE "Quick Issue" ENTRY COLS 2
  SECTION "Quick Issue"
  qi_number   | 18 | req prefix:QI:YYMM
  qi_date     |    | date
  dept        | 20 |
  issued_to   | 30 |
  warehouse   |  8 | lookup:warehouse
  status      |    | ro default:Draft
  SECTION "Summary"
  total_qty   |  8 | num ro
  total_cost  | 14 | num ro
  item_count  |  6 | num ro
  DETAIL "Issue Lines" AS qi_line TOTALS
    part_no   | 12 | ro
    part_name | 25 | ro
    category  | 14 | ro
    uom       |  5 | ro
    on_hand   |  8 | num ro
    unit_cost | 10 | num ro
    issue_qty | 10 | num
    amount    | 14 | num ro formula:issue_qty*unit_cost
    base_uom  |  6 | ro hidden
    pack_uom  |  6 | ro hidden
    pack_qty  |  8 | num ro hidden
    issue_uom |  6 | ro hidden
    base_unit_cost | 10 | num ro hidden
    pack_cost | 10 | num ro hidden
    PREFILL item filter:on_hand>0
    COEDIT issue_qty
  END DETAIL
  COMPUTED
    total_qty  = SUM(lines, issue_qty)
    total_cost = SUM(lines, amount)
    item_count = COUNT(lines)
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _qi_base = BASEQTY(line.issue_qty, line.uom, line.pack_uom, line.pack_qty)
      SET short = FIFOOUT("item_lot", "on_hand", "entry_date", _qi_base, "_parent_rowid", item._rowid)
      CALL stock_recalc(item._rowid, 0 - _qi_base)
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = qi_date, trans_type = "ISSUE", doc_no = qi_number, receive_qty = 0, issue_qty = _qi_base, balance = item.on_hand, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _qi_base = BASEQTY(line.issue_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_recalc(item._rowid, _qi_base)
      INSERT item_lot SET entry_date = qi_date, doc_no = qi_number, warehouse = warehouse, on_hand = _qi_base, uom = item.uom, unit_cost = line.unit_cost, _parent_rowid = item._rowid
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = qi_date, trans_type = "VOID-I", doc_no = qi_number, receive_qty = _qi_base, issue_qty = 0, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
  END WORKFLOW
  LIST qi_number:18 qi_date:12 dept:15 issued_to:25 total_cost:14 status:10
END

// ══════════════════════════════════════════
// SALES ORDER
// ══════════════════════════════════════════

FORM so TITLE "Sales Order" COLS 2
  SECTION "Sales Order"
  so_number   | 18 | req prefix:SO:YYMM
  so_date     |    | date
  cust_code   | 10 | lookup:customer fill:cust_name
  cust_name   | 40 | ro
  warehouse   |  8 | lookup:warehouse
  status      |    | ro default:Draft
  remarks     | 50 | span:2 rows:2
  SECTION "Totals"
  subtotal     | 14 | num ro
  discount_pct |  6 | num default:0
  discount_amt | 14 | num ro
  tax_rate     |  6 | num default:@setting.tax_rate_pct
  tax_amt      | 14 | num ro
  grand_total  | 14 | num ro
  line_count   |  6 | num ro
  DETAIL "Order Lines" AS so_line TOTALS
    part_no    | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,issue_uom,unit_price=>base_unit_price,pack_price
    part_name  | 25 | ro
    uom        |  6 | ro
    order_qty  | 10 | num
    shipped_qty| 10 | num ro
    unit_price | 12 | num
    discount   |  8 | num
    amount     | 14 | num formula:(order_qty*unit_price)-(order_qty*unit_price*discount/100)
    base_uom   |  6 | ro hidden
    pack_uom   |  6 | ro hidden
    pack_qty   |  8 | num ro hidden
    issue_uom  |  6 | ro hidden
    base_unit_price | 10 | num ro hidden
    pack_price | 10 | num ro hidden
  END DETAIL
  COMPUTED
    subtotal     = SUM(lines, amount)
    discount_amt = subtotal * discount_pct / 100
    tax_amt      = (subtotal - discount_amt) * tax_rate / 100
    grand_total  = subtotal - discount_amt + tax_amt
    line_count   = COUNT(lines)
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _so_base = BASEQTY(line.order_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_adjust_demand(item._rowid, _so_base)
      INSERT item_card SET trans_date = so_date, trans_type = "ALLOC", doc_no = so_number, receive_qty = 0, issue_qty = _so_base, balance = item.on_hand, unit_cost = item.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _so_base = BASEQTY(line.order_qty, line.uom, line.pack_uom, line.pack_qty)
      CALL stock_adjust_demand(item._rowid, 0 - _so_base)
      INSERT item_card SET trans_date = so_date, trans_type = "VOID-A", doc_no = so_number, receive_qty = _so_base, issue_qty = 0, balance = item.on_hand, unit_cost = item.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Confirmed : post
    Confirmed > Void  : void
  END WORKFLOW
  HIGHLIGHT
    status == "Void"      | error
    status == "Confirmed" | success
    status == "Closed"    | info
  END HIGHLIGHT
  LIST so_number:18 so_date:12 cust_code:10 cust_name:25 grand_total:14 status:10
END

// ══════════════════════════════════════════
// DELIVERY ORDER
// ══════════════════════════════════════════

FORM delivery TITLE "Delivery Order" COLS 2
  SECTION "Delivery Order"
  do_number   | 18 | req prefix:DO:YYMM
  do_date     |    | date
  so_number   | 18 | lookup:so filter:status=Confirmed fill:cust_code,cust_name,warehouse copydetail:so_line
  pk_no       | 18 | lookup:picking_list filter:status=Confirmed fill:cust_code,cust_name,warehouse copydetail:pk_line
  cust_code   | 10 | ro
  cust_name   | 40 | ro
  warehouse   |  8 | lookup:warehouse
  status      |    | ro default:Draft
  remarks     | 50 | span:2 rows:2
  SECTION "Totals"
  subtotal    | 14 | num ro
  tax_rate    |  6 | num default:@setting.tax_rate_pct
  tax_amt     | 14 | num ro
  grand_total | 14 | num ro
  DETAIL "Delivery Lines" AS do_line TOTALS
    part_no    | 12 | lookup:item fill:part_name,uom=>base_uom,pack_uom,pack_qty,issue_uom,unit_price=>base_unit_price,pack_price
    part_name  | 25 | ro
    uom        |  6 | ro
    order_qty  | 10 | num ro
    ship_qty   | 10 | num
    unit_price | 12 | num
    amount     | 14 | num formula:ship_qty*unit_price
    base_uom   |  6 | ro hidden
    pack_uom   |  6 | ro hidden
    pack_qty   |  8 | num ro hidden
    issue_uom  |  6 | ro hidden
    base_unit_price | 10 | num ro hidden
    pack_price | 10 | num ro hidden
  END DETAIL
  COMPUTED
    subtotal    = SUM(lines, amount)
    tax_amt     = subtotal * tax_rate / 100
    grand_total = subtotal + tax_amt
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _do_base = BASEQTY(line.ship_qty, line.uom, line.pack_uom, line.pack_qty)
      IF item.on_hand < _do_base
        FAIL "Insufficient stock: " + line.part_no + " (have " + STR(item.on_hand) + ", need " + STR(_do_base) + ")"
      END
      SET short = FIFOOUT("item_lot", "on_hand", "entry_date", _do_base, "_parent_rowid", item._rowid)
      IF so_number
        CALL stock_recalc_both(item._rowid, 0 - _do_base, 0 - _do_base)
      ELSE
        CALL stock_recalc(item._rowid, 0 - _do_base)
      END
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = do_date, trans_type = "SHIP", doc_no = do_number, receive_qty = 0, issue_qty = _do_base, balance = item.on_hand, unit_cost = _fifo_avg_cost, _parent_rowid = item._rowid
    END
    IF so_number
      FETCH so_rec FROM so WHERE so_number = so_number
      SET _all_shipped = 1
      FOREACH line IN lines
        FETCH so_ln FROM so_line WHERE part_no = line.part_no AND _parent_rowid = so_rec._rowid
        UPDATE so_line SET shipped_qty = so_ln.shipped_qty + line.ship_qty WHERE _rowid = so_ln._rowid
        FETCH so_ln FROM so_line WHERE _rowid = so_ln._rowid
        IF so_ln.shipped_qty < so_ln.order_qty
          SET _all_shipped = 0
        END
      END
      IF _all_shipped = 1
        UPDATE so SET status = "Closed" WHERE _rowid = so_rec._rowid
      END
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      SET _do_base = BASEQTY(line.ship_qty, line.uom, line.pack_uom, line.pack_qty)
      IF so_number
        CALL stock_recalc_both(item._rowid, _do_base, _do_base)
      ELSE
        CALL stock_recalc(item._rowid, _do_base)
      END
      INSERT item_lot SET entry_date = do_date, doc_no = do_number, warehouse = warehouse, on_hand = _do_base, uom = item.uom, unit_cost = item.unit_cost, _parent_rowid = item._rowid
      FETCH item FROM item WHERE _rowid = item._rowid
      INSERT item_card SET trans_date = do_date, trans_type = "VOID-S", doc_no = do_number, receive_qty = _do_base, issue_qty = 0, balance = item.on_hand, unit_cost = item.unit_cost, _parent_rowid = item._rowid
    END
    IF so_number
      FETCH so_rec FROM so WHERE so_number = so_number
      FOREACH line IN lines
        FETCH so_ln FROM so_line WHERE part_no = line.part_no AND _parent_rowid = so_rec._rowid
        UPDATE so_line SET shipped_qty = so_ln.shipped_qty - line.ship_qty WHERE _rowid = so_ln._rowid
      END
      UPDATE so SET status = "Confirmed" WHERE _rowid = so_rec._rowid
    END
  ENDSCRIPT
  SCRIPT generate_pk
    SET _pk_count = COUNTROWS("picking_list", "do_no", do_number)
    IF _pk_count > 0
      FAIL "Picking List already generated for this DO."
    END
    SET pick_lines = PICKING_ALLOCATE(warehouse, lines)
    IF COUNT(pick_lines) = 0
      FAIL "No items with balance > 0 in warehouse storage."
    END
    SET _pk_no = NEXTDOCNO("picking_list", "pk_no", "PK", "YYMM")
    INSERT picking_list SET pk_no = _pk_no, pk_date = do_date, do_no = do_number, cust_code = cust_code, cust_name = cust_name, warehouse = warehouse, status = "Draft"
    SET _pk_rowid = _last_rowid
    FOREACH ln IN pick_lines
      INSERT pk_line SET part_no = ln.part_no, part_name = ln.part_name, pick_qty = ln.pick_qty, uom = ln.uom, lot_no = ln.lot_no, grn_no = ln.grn_no, loc_id = ln.loc_id, pack_uom = ln.pack_uom, pack_qty = ln.pack_qty, pick_base_qty = ln.pick_base_qty, storage_rowid = ln._storage_rowid, _parent_rowid = _pk_rowid
    END
    OK "Picking List created with " + STR(COUNT(pick_lines)) + " lines."
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void  : void
    ACTION "Generate Picking" : generate_pk
  END WORKFLOW
  LIST do_number:18 do_date:12 so_number:18 cust_name:25 grand_total:14 status:10
END

// ══════════════════════════════════════════
// BILL OF MATERIALS
// ══════════════════════════════════════════

FORM bom TITLE "Bill of Materials" COLS 2
  SECTION "BOM Header"
  model_no     | 14 | req prefix:BOM:YYMM
  model_name   | 40 | req span:2
  revision     |  6 | default:A
  output_pn    | 12 | lookup:item fill:output_part_name
  output_part_name | 30 | ro
  base_qty     | 10 | num default:1
  status       |    | ro default:Draft
  notes        | 50 | span:2
  TREE_DETAIL "Components" AS bom_line children_key:children tree_label:component
    component       | 22 |
    part_no         | 14 | lookup:item fill:part_name,uom
    vendor_id       | 10 | lookup:vendor
    part_name       | 28 | ro
    qty             |  8 | num
    per             |  5 | num
    uom             |  6 | ro
    part_level      |  7 | opts:FG,PART,RAWMAT,TOOL,LIB,EXWORK
    safety_percent  |  5 | num
  END TREE_DETAIL
  SCRIPT activate
    OK "BOM activated"
  ENDSCRIPT
  SCRIPT obsolete
    OK "BOM marked obsolete"
  ENDSCRIPT
  SCRIPT hold
    OK "BOM on hold"
  ENDSCRIPT
  SCRIPT compute_bom
    SET result = BOMEXPLODE(model_no, _compute_qty)
    SET rollup = BOMROLLUP(model_no, _compute_qty)
  ENDSCRIPT
  SCRIPT rollup_bom
    SET rollup = BOMROLLUP(model_no, _compute_qty)
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Active : activate
    Draft > Hold : hold
    Active > Obsolete : obsolete
    Active > Hold : hold
    Hold > Active : activate
    Hold > Obsolete : obsolete
    Obsolete > Draft : activate
    ---
    ACTION "Compute BOM" : compute_bom
    ACTION "Rollup BOM" : rollup_bom
  END WORKFLOW
  HIGHLIGHT
    status == "Obsolete" | error
    status == "Active"   | success
    status == "Hold"     | warning
  END HIGHLIGHT
  LIST model_no:14 model_name:30 revision:6 output_pn:12 base_qty:8 status:10
END

// ══════════════════════════════════════════
// BOM EXPLOSION
// ══════════════════════════════════════════

FORM bom_explosion TITLE "BOM Explosion" COLS 2
  SECTION "Parameters"
  source_bom   | 14 | req lookup:bom fill:model_name,base_qty
  model_name   | 30 | ro
  base_qty     | 10 | num ro
  compute_qty  | 10 | num default:1
  explode_mode |    | opts:Explode,Rollup default:Explode
  status       |    | ro default:Draft
  SECTION "Results"
  total_parts  |  6 | num ro
  total_cost   | 14 | num ro
  DETAIL "Explosion Lines" AS explosion_line TOTALS
    level        |  3 | num ro
    part_no      | 12 | ro
    part_name    | 20 | ro
    part_level   |  8 | ro
    total_usage  | 10 | num ro
    uom          |  5 | ro
    unit_cost    | 10 | num ro
    ext_cost     | 12 | num ro formula:total_usage*unit_cost
    source_bom   | 14 | ro
    vendor_id    | 10 | ro
  END DETAIL
  COMPUTED
    total_parts = COUNT(explosion_line)
    total_cost  = SUM(explosion_line, ext_cost)
  END COMPUTED
  SCRIPT explode
    SET result = BOMEXPLODE(source_bom, compute_qty)
    FOREACH item IN result
      INSERT explosion_line SET part_no = item.part_no, part_name = item.part_name, part_level = item.part_level, total_usage = item.total_usage, uom = item.uom, unit_cost = item.unit_cost, level = item.level, source_bom = item.source_bom, vendor_id = item.vendor_id, _parent_rowid = id
    END
    SET total_parts = COUNT(result)
    OK "BOM exploded: " + STR(total_parts) + " lines"
  ENDSCRIPT
  SCRIPT rollup
    SET result = BOMROLLUP(source_bom, compute_qty)
    FOREACH item IN result
      INSERT explosion_line SET part_no = item.part_no, part_name = item.part_name, part_level = item.part_level, total_usage = item.total_usage, uom = item.uom, unit_cost = item.unit_cost, level = 0, source_bom = item.source_bom, vendor_id = item.vendor_id, _parent_rowid = id
    END
    SET total_parts = COUNT(result)
    OK "BOM rolled up: " + STR(total_parts) + " parts"
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Exploded : explode
    Draft > Rolled Up : rollup
  END WORKFLOW
  LIST source_bom:14 model_name:25 compute_qty:10 explode_mode:8 total_parts:8 total_cost:14 status:10
END

// ══════════════════════════════════════════
// REPORTS
// ══════════════════════════════════════════

REPORT rpt_stock_value TITLE "Stock Value Report"
  SOURCE item
  ORDER category, part_no
  COLUMNS
    part_no    | Part No    | 14
    part_name  | Part Name  | 24
    category   | Category   | 16
    uom        | UOM        |  6
    on_hand    | On Hand    | 10 | num sum
    unit_cost  | Unit Cost  | 12 | num
    stock_val  | Stock Value| 14 | num sum formula:on_hand*unit_cost
  END COLUMNS
  GROUP category | Category Total | on_hand:sum stock_val:sum
  TOTALS | Grand Total | on_hand:sum stock_val:sum
  CROSSTAB
    row: category
    col: uom
    value: on_hand:sum
    row_total: yes
    col_total: yes
  END CROSSTAB
END REPORT

REPORT rpt_stock_card TITLE "Stock Card History"
  SOURCE item_card
  ORDER trans_date, doc_no
  COLUMNS
    trans_date  | Date       | 12
    trans_type  | Type       |  8
    doc_no      | Doc No     | 14
    receive_qty | Received   | 10 | num sum
    issue_qty   | Issued     | 10 | num sum
    balance     | Balance    | 12 | num
    unit_cost   | Cost       | 10 | num
  END COLUMNS
  GROUP trans_type | Type Total | receive_qty:sum issue_qty:sum
  TOTALS | Grand Total | receive_qty:sum issue_qty:sum
END REPORT

REPORT rpt_sales_summary TITLE "Sales Summary"
  SOURCE so
  ORDER so_date, so_number
  COLUMNS
    so_number  | SO Number  | 14
    so_date    | Date       | 12
    cust_code  | Customer   | 10
    cust_name  | Name       | 24
    subtotal   | Total      | 14 | num sum
    status     | Status     | 10
  END COLUMNS
  GROUP status | Status Total | subtotal:sum
  TOTALS | Grand Total | subtotal:sum
END REPORT

REPORT rpt_bom_list TITLE "BOM Master List"
  SOURCE bom
  ORDER model_no
  COLUMNS
    model_no    | Model No    | 14
    model_name  | Model Name  | 30
    revision    | Rev         |  6
    output_pn   | Output P/N  | 12
    base_qty    | Base Qty    | 10 | num
    status      | Status      | 10
  END COLUMNS
  GROUP status | By Status
  TOTALS | Grand Total
END REPORT

REPORT rpt_bom_usage TITLE "BOM Component Usage"
  SOURCE explosion_line
  ORDER part_no
  COLUMNS
    part_no     | Part No     | 12
    part_name   | Part Name   | 20
    part_level  | Type        |  8
    total_usage | Usage       | 10 | num sum
    uom         | UOM         |  5
    unit_cost   | Unit Cost   | 10 | num
    ext_cost    | Ext Cost    | 12 | num sum
    source_bom  | Source BOM  | 14
  END COLUMNS
  GROUP part_level | By Type | total_usage:sum ext_cost:sum
  TOTALS | Grand Total | total_usage:sum ext_cost:sum
END REPORT

// ══════════════════════════════════════════
// MENU
// ══════════════════════════════════════════ //PULLDOWN

MENU "Inventory Management System"
  GROUP "Inventory"
    "Item Master" => item HOTKEY F2
    ---
    "Exit" => EXIT HOTKEY ESC
  END
  GROUP "Purchasing"
    "Purchase Orders" => po HOTKEY F3
    "Goods Receipts" => grn
  END
  GROUP "Sales"
    "Sales Orders" => so HOTKEY F5
    "Point of Sale" => POS.SCREEN HOTKEY F6
    "Web Orders" => wo
    "Deliveries" => delivery
  END
  GROUP "Warehouse"
    "Warehouse Map" => WHMAP
    "Warehouse Storage" => warehouse_storage
    "Put-Away List" => put_away
    "Picking List" => picking_list
    ---
    "Issue Slips" => issue
    "Quick Issue" => qi HOTKEY F7
    "Transfers" => transfer
  END
  GROUP "BOM"
    "Bill of Materials" => bom
    "BOM Explosion" => bom_explosion
  END
  GROUP "Reports"
    "Report Browser" => REPORTS
    ---
    "Stock Value" => report:rpt_stock_value
    "Stock Card" => report:rpt_stock_card
    ---
    "Sales Summary" => report:rpt_sales_summary
    ---
    "BOM Master List" => report:rpt_bom_list
    "BOM Usage" => report:rpt_bom_usage
  END
  GROUP "Setup"
    "Customers" => customer
    "Vendors" => vendor
    "Warehouses" => warehouse
    ---
    "Business Settings" => SETTINGS
    "Switch Company" => SWITCH
    ---
    "Reset All Data" => RESET
  END
END

RESET
  CLEAR po, po_line, grn, grn_line, issue, issue_line, transfer, transfer_line, so, so_line, delivery, do_line, qi, qi_line, item_lot, item_card, bom, bom_line, bom_explosion, explosion_line, wo, wo_line, warehouse_storage, wh_locations, put_away, pa_line, picking_list, pk_line
  UPDATE item SET on_hand = 0, on_order = 0, allocated = 0, back_order = 0, available = 0
END RESET
