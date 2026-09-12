APP "Sales Order System" VERSION "1.0"

DATASOURCE "sales_db" ADAPTER "sqlite"

// ══════════════════════════════════════════
// REFERENCE TABLES
// ══════════════════════════════════════════

// ── Customer ──
FORM customer TITLE "Customer Master" COLS 2
  SECTION "Customer Information"
  cust_code    | 10 | req prefix:CUS
  cust_name    | 40 | req span:2
  online_id    | 24 | ro
  b2b_host     | 24 |
  b2b_port     |  8 | num default:8085
  contact      | 30 |
  phone        | 15 |
  email        | 30 |
  credit_limit |    | num
  SECTION "Address"
  address      | 50 | span:2
  city         | 20 |
  region       |    | opts:North,South,East,West,Central
  LIST cust_code:10 cust_name:30 contact:20 phone:15 credit_limit:12 region:10
END

// ── Product ──
FORM product TITLE "Product Catalog" COLS 2
  SECTION "Product Details"
  prod_code    | 12 | req prefix:PRD
  prod_name    | 40 | req span:2
  category     |    | opts:Electronics,Clothing,Food,Hardware,Software,Service
  uom          |    | opts:PC,KG,M,L,BOX,SET
  unit_price   |    | num
  unit_cost    |    | num
  SECTION "Inventory"
  on_hand      |    | num
  reserved     |    | num ro
  available    |    | num ro
  min_stock    |    | num
  HIGHLIGHT
    on_hand == 0                   | error
    on_hand < min_stock            | warn
    on_hand > min_stock * 3        | success
  END HIGHLIGHT
  LIST prod_code:12 prod_name:30 category:14 uom:5 unit_price:10 on_hand:8 reserved:8 available:8
END

// ── Sales Rep ──
FORM salesrep TITLE "Sales Rep" COLS 2
  rep_code   |  8 | req prefix:REP
  rep_name   | 30 | req
  phone      | 15 |
  commission |    | num
  LIST rep_code:8 rep_name:25 phone:15 commission:8
END

// ══════════════════════════════════════════
// SALES ORDER — demonstrates COMPUTED, COEDIT,
//   fill mapping, and HIGHLIGHT
// ══════════════════════════════════════════

FORM so TITLE "Sales Order" COLS 2
  SECTION "Sales Order"
  so_number    | 18 | req prefix:SO:YYMM
  so_date      |    | date
  cust_code    | 10 | lookup:customer fill:cust_name
  cust_name    | 40 | ro
  rep_code     |  8 | lookup:salesrep fill:rep_name
  rep_name     | 30 | ro
  status       |    | ro default:Draft
  remarks      | 50 | span:2
  SECTION "Totals"
  subtotal     | 14 | num ro
  discount_pct |  6 | num default:0
  discount_amt | 14 | num ro
  tax_rate     |  6 | num default:@setting.tax_rate_pct
  tax_amt      | 14 | num ro
  grand_total  | 14 | num ro
  line_count   |  6 | num ro
  DETAIL "Order Lines" AS so_line TOTALS
    prod_code  | 12 | lookup:product fill:prod_name,unit_price
    prod_name  | 25 | ro
    order_qty  | 10 | num
    unit_price | 12 | num
    discount   |  8 | num
    amount     | 14 | num formula:(order_qty*unit_price)-(order_qty*unit_price*discount/100)
    COEDIT order_qty
  END DETAIL
  COMPUTED
    subtotal     = SUM(lines, amount)
    discount_amt = SUM(lines, amount) * discount_pct / 100
    tax_amt      = (SUM(lines, amount) - SUM(lines, amount) * discount_pct / 100) * tax_rate / 100
    grand_total  = SUM(lines, amount) - SUM(lines, amount) * discount_pct / 100 + (SUM(lines, amount) - SUM(lines, amount) * discount_pct / 100) * tax_rate / 100
    line_count   = COUNT(lines)
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET reserved = prod.reserved + line.order_qty WHERE _rowid = prod._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET reserved = prod.reserved - line.order_qty WHERE _rowid = prod._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Confirmed : post
    Confirmed > Void  : void
  END WORKFLOW
  HIGHLIGHT
    status == "Void"      | error
    status == "Confirmed" | success
  END HIGHLIGHT
  LIST so_number:18 so_date:12 cust_code:10 cust_name:25 grand_total:14 status:10
END

// ══════════════════════════════════════════
// WEB ORDER — CoEdit-focused quick entry
//   like pystock web_order_entry
//
//   PREFILL loads all products into the grid.
//   User types quantities via CoEdit(order_qty).
//   F7 filter persists CoEdit mode.
//   Only lines with order_qty > 0 are saved.
// ══════════════════════════════════════════

FORM wo TITLE "Web Order" ENTRY COLS 2
  SECTION "Web Order"
  wo_number    | 18 | req prefix:WO:YYMM
  wo_date      |    | date
  cust_code    | 10 | lookup:customer fill:cust_name
  cust_name    | 40 | ro
  warehouse    |  8 | lookup:warehouse default:@setting.default_warehouse
  status       |    | ro default:Draft
  SECTION "Summary"
  subtotal     | 14 | num ro
  tax_rate     |  6 | num default:@setting.tax_rate_pct
  tax_amt      | 14 | num ro
  grand_total  | 14 | num ro
  total_qty    |  8 | num ro
  item_count   |  6 | num ro
  DETAIL "Order Lines" AS wo_line TOTALS
    prod_code  | 12 | ro
    prod_name  | 25 | ro
    category   | 14 | ro
    uom        |  5 | ro
    on_hand    |  8 | num ro
    unit_price | 10 | num ro
    order_qty  | 10 | num
    amount     | 14 | num ro formula:order_qty*unit_price
    PREFILL product filter:on_hand>0
    COEDIT order_qty
  END DETAIL
  COMPUTED
    subtotal     = SUM(lines, amount)
    tax_amt      = subtotal * tax_rate / 100
    grand_total  = subtotal + tax_amt
    total_qty    = SUM(lines, order_qty)
    item_count   = COUNT(lines)
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET reserved = prod.reserved + line.order_qty WHERE _rowid = prod._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET reserved = prod.reserved - line.order_qty WHERE _rowid = prod._rowid
    END
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
      INSERT pk_line SET part_no = ln.part_no, part_name = ln.part_name, pick_qty = ln.pick_qty, lot_no = ln.lot_no, grn_no = ln.grn_no, loc_id = ln.loc_id, storage_rowid = ln._storage_rowid, _parent_rowid = _pk_rowid
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
  LIST wo_number:18 wo_date:12 cust_code:10 cust_name:25 grand_total:14 status:10
END

// ══════════════════════════════════════════
// DELIVERY ORDER — fill mapping demo
//   fill:prod_name=>description maps source
//   field to different dest field name
// ══════════════════════════════════════════

FORM delivery TITLE "Delivery Order" COLS 2
  SECTION "Delivery Order"
  do_number    | 18 | req prefix:DO:YYMM
  do_date      |    | date
  so_number    | 12 | lookup:so filter:status=Confirmed fill:cust_code,cust_name
  cust_code    | 10 | ro
  cust_name    | 40 | ro
  ship_to      | 50 | span:2
  status       |    | ro default:Draft
  SECTION "Totals"
  subtotal     | 14 | num ro
  tax_rate     |  6 | num default:@setting.tax_rate_pct
  tax_amt      | 14 | num ro
  grand_total  | 14 | num ro
  total_shipped|  8 | num ro
  DETAIL "Delivery Lines" AS do_line TOTALS
    prod_code   | 12 | lookup:product fill:prod_name=>description
    description | 25 | ro
    order_qty   | 10 | num ro
    ship_qty    | 10 | num
    uom         |  6 | opts:PC,KG,M,L,BOX,SET
    amount      | 14 | num ro formula:ship_qty*unit_price
    unit_price  | 12 | num ro
    COEDIT ship_qty
  END DETAIL
  COMPUTED
    subtotal      = SUM(lines, amount)
    tax_amt       = subtotal * tax_rate / 100
    grand_total   = subtotal + tax_amt
    total_shipped = SUM(lines, ship_qty)
  END COMPUTED
  SCRIPT post
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET on_hand = prod.on_hand - line.ship_qty WHERE _rowid = prod._rowid
      UPDATE product SET reserved = prod.reserved - line.ship_qty WHERE _rowid = prod._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH prod FROM product WHERE prod_code = line.prod_code
      UPDATE product SET on_hand = prod.on_hand + line.ship_qty WHERE _rowid = prod._rowid
      UPDATE product SET reserved = prod.reserved + line.ship_qty WHERE _rowid = prod._rowid
    END
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void  : void
  END WORKFLOW
  LIST do_number:18 do_date:12 so_number:18 cust_name:25 status:10
END

// ══════════════════════════════════════════
// REPORTS
// ══════════════════════════════════════════

REPORT rpt_sales_summary TITLE "Sales Summary"
  SOURCE so
  ORDER so_date, so_number
  COLUMNS
    so_number   | SO Number   | 14
    so_date     | Date        | 12
    cust_code   | Customer    | 10
    cust_name   | Name        | 24
    grand_total | Total       | 14 | num sum
    status      | Status      | 10
  END COLUMNS
  GROUP status | Status Total | grand_total:sum
  TOTALS | Grand Total | grand_total:sum
END REPORT

REPORT rpt_product_sales TITLE "Product Sales Report"
  SOURCE so_line
  ORDER prod_code
  COLUMNS
    prod_code  | Product    | 14
    prod_name  | Name       | 24
    order_qty  | Qty        | 10 | num sum
    unit_price | Price      | 12 | num
    amount     | Amount     | 14 | num sum
  END COLUMNS
  GROUP prod_code | Product Total | order_qty:sum amount:sum
  TOTALS | Grand Total | order_qty:sum amount:sum
END REPORT

// ══════════════════════════════════════════
// MENU
// ══════════════════════════════════════════

MENU "Sales Order System"
  GROUP "Sales"
    "Sales Orders" => so HOTKEY F3
    "Web Orders" => wo
    ---
    "Exit" => EXIT HOTKEY ESC
  END
  GROUP "Delivery"
    "Deliveries" => delivery
  END
  GROUP "Reports"
    "Report Browser" => REPORTS
    ---
    "Sales Summary" => report:rpt_sales_summary
    "Product Sales" => report:rpt_product_sales
  END
  GROUP "Setup"
    "Customers" => customer HOTKEY F2
    "Products" => product
    "Sales Reps" => salesrep
    ---
    "Business Settings" => SETTINGS
    "Switch Company" => SWITCH
    ---
    "Reset All Data" => RESET
  END
END

RESET
  CLEAR so, so_line, wo, wo_line, delivery, do_line
  UPDATE product SET on_hand = 0, reserved = 0, available = 0
END RESET
