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
