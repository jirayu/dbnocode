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
    STOCK ALLOCATE
      LINES lines | ITEM line.part_no | QTY line.order_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE so_date | DOC so_number | TYPE "ALLOC"
    END STOCK
  ENDSCRIPT
  SCRIPT void
    STOCK ALLOCATE REVERSE
      LINES lines | ITEM line.part_no | QTY line.order_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE so_date | DOC so_number | TYPE "VOID-A"
    END STOCK
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
    STOCK ISSUE
      LINES lines | ITEM line.part_no | QTY line.ship_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE do_date | DOC do_number | WAREHOUSE warehouse | TYPE "SHIP"
      DEMAND NUM(NOT ISBLANK(so_number)) * line.ship_qty
    END STOCK
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
    STOCK ISSUE REVERSE
      LINES lines | ITEM line.part_no | QTY line.ship_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE do_date | DOC do_number | WAREHOUSE warehouse | TYPE "VOID-S"
      DEMAND NUM(NOT ISBLANK(so_number)) * line.ship_qty
    END STOCK
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
