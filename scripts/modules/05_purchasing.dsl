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
      STOCK RECEIVE
        ITEM line.part_no | QTY line.received_qty | UOM line.uom
        PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
        DATE grn_date | DOC grn_no | WAREHOUSE warehouse
        COST line.unit_cost | LOT line.lot_no | TYPE "RECV" | RESULT _grn_qty
      END STOCK
      IF po_number
        UPDATE item SET on_order = item.on_order - _grn_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty + line.received_qty WHERE _rowid = po_ln._rowid
      END
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      STOCK RECEIVE REVERSE
        ITEM line.part_no | QTY line.received_qty | UOM line.uom
        PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
        DATE grn_date | DOC grn_no | WAREHOUSE warehouse
        COST line.unit_cost | LOT line.lot_no | TYPE "VOID-R" | RESULT _grn_qty
      END STOCK
      IF po_number
        UPDATE item SET on_order = item.on_order + _grn_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty - line.received_qty WHERE _rowid = po_ln._rowid
      END
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
