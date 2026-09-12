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
    STOCK ISSUE
      LINES lines | ITEM line.part_no | QTY line.quantity | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE issue_date | DOC slip_no | COST line.unit_price | TYPE "POS"
      LOTS FALSE
    END STOCK
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
  END WORKFLOW
END
