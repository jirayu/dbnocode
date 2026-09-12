APP "Inventory Management" V 1.0
DS inventory_db AD sqlite

// ── Item Master ──
FORM item COLS 2
  // Item Information
  part_no    | 12 | r p:ITM
  part_name  | 40 | r w:2
  category   |    | e:Raw Material,Finished Good,Component,Consumable,Service
  uom        |    | e:PC,KG,M,L,BOX,SET,HR
  unit_cost  |    | n
  unit_price |    | n
  // Stock Levels
  min_qty    |    | n
  max_qty    |    | n
  reorder_qty|    | n
  on_hand    |    | n
  on_order   |    | n
  allocated  |    | n o
  DETAIL "Stock Card" AS item_card
    trans_date  | 12 | d o
    trans_type  |  8 | o
    doc_no      | 14 | o
    receive_qty | 10 | n o
    issue_qty   | 10 | n o
    balance     | 12 | n o
    unit_cost   | 10 | n o
  \
  SAVE: IF on_hand| WRITE item_card: trans_date=TODAY, trans_type="INIT", doc_no=part_no, receive_qty=on_hand, issue_qty=0, balance=on_hand, unit_cost=unit_cost
  LIST part_no:12 part_name:30 category:14 uom:5 unit_cost:12 unit_price:12 on_hand:10 on_order:10 allocated:10
\

// ── Vendor ──
FORM vendor COLS 2
  vendor_code  | 10 | r p:VEN
  vendor_name  | 40 | r w:2
  contact      | 30 |
  phone        | 15 |
  email        | 30 |
  address      | 50 | w:2
  LIST vendor_code:10 vendor_name:30
\

// ── Warehouse ──
FORM warehouse COLS 2
  wh_code  |  8 | r p:WH
  wh_name  | 30 | r
  location | 40 |
  LIST wh_code:8 wh_name:25
\

// ── Purchase Order ──
FORM po COLS 2
  po_number   | 12 | r p:PO
  po_date     |    | d
  vendor_code | 10 | lk:vendor fl:vendor_name
  vendor_name | 40 |
  warehouse   |  8 | lk:warehouse
  status      |    | o v:Draft
  notes       | 50 | w:2
  DETAIL "Lines" AS po_line T
    part_no      | 12 | lk:item fl:part_name
    part_name    | 25 | o
    order_qty    | 10 | n
    unit_cost    | 12 | n
    amount       | 14 | n f:order_qty*unit_cost
    received_qty | 10 | n o
  \
  SCRIPT post: FOREACH line| FETCH item WHERE part_no = line.part_no| UPDATE item.on_order += line.order_qty
  SCRIPT void: FOREACH line| FETCH item WHERE part_no = line.part_no| UPDATE item.on_order -= line.order_qty
  WORKFLOW Draft>Posted:post, Posted>Void:void
  LIST po_number:12 po_date:12 vendor_code:10 vendor_name:25 order_no:20 status:10
\

// ── Goods Receipt ──
FORM grn COLS 2
  grn_no      | 12 | r p:GRN
  grn_date    |    | d
  po_number   | 12 | lk:po ft:status=Posted fl:vendor_name cd:po_line
  vendor_name | 40 |
  warehouse   |  8 | lk:warehouse
  status      |    | o v:Draft
  remarks     | 50 | w:2
  DETAIL "Lines" AS grn_line T
    part_no      | 12 | lk:item fl:part_name
    part_name    | 25 | o
    order_qty    | 10 | n o
    received_qty | 12 | n
    unit_cost    | 12 | n
    amount       | 14 | n f:received_qty*unit_cost
  \
  SCRIPT post
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      UPDATE item SET on_hand = item.on_hand + line.received_qty WHERE _rowid = item._rowid
      IF po_number
        UPDATE item SET on_order = item.on_order - line.received_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty + line.received_qty WHERE _rowid = po_ln._rowid
      END
      INSERT item_card SET trans_date = grn_date, trans_type = "RECV", doc_no = grn_no, receive_qty = line.received_qty, issue_qty = 0, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  SCRIPT void
    FOREACH line IN lines
      FETCH item FROM item WHERE part_no = line.part_no
      UPDATE item SET on_hand = item.on_hand - line.received_qty WHERE _rowid = item._rowid
      IF po_number
        UPDATE item SET on_order = item.on_order + line.received_qty WHERE _rowid = item._rowid
        FETCH po_rec FROM po WHERE po_number = po_number
        FETCH po_ln FROM po_line WHERE part_no = line.part_no AND _parent_rowid = po_rec._rowid
        UPDATE po_line SET received_qty = po_ln.received_qty - line.received_qty WHERE _rowid = po_ln._rowid
      END
      INSERT item_card SET trans_date = grn_date, trans_type = "VOID-R", doc_no = grn_no, receive_qty = 0, issue_qty = line.received_qty, balance = item.on_hand, unit_cost = line.unit_cost, _parent_rowid = item._rowid
    END
  ENDSCRIPT
  WORKFLOW Draft>Posted:post, Posted>Void:void
  LIST grn_no:12 grn_date:12 po_number:12 vendor_name:25 status:10
\

// ── Issue Slip ──
FORM issue COLS 2
  issue_no   | 12 | r p:IS
  issue_date |    | d
  dept       | 20 |
  issued_to  | 30 |
  warehouse  |  8 | lk:warehouse
  status     |    | o v:Draft
  remarks    | 50 | w:2
  DETAIL "Lines" AS issue_line T
    part_no   | 12 | lk:item fl:part_name
    part_name | 25 | o
    issue_qty | 10 | n
    unit_cost | 12 | n
    amount    | 14 | n f:issue_qty*unit_cost
  \
  SCRIPT post: FOREACH line| FETCH item WHERE part_no = line.part_no| UPDATE item.on_hand -= line.issue_qty| INSERT item_card: trans_date=issue_date, trans_type="ISSUE", doc_no=issue_no, receive_qty=0, issue_qty=line.issue_qty, balance=item.on_hand, unit_cost=line.unit_cost
  SCRIPT void: FOREACH line| FETCH item WHERE part_no = line.part_no| UPDATE item.on_hand += line.issue_qty| INSERT item_card: trans_date=issue_date, trans_type="VOID-I", doc_no=issue_no, receive_qty=line.issue_qty, issue_qty=0, balance=item.on_hand, unit_cost=line.unit_cost
  WORKFLOW Draft>Posted:post, Posted>Void:void
  LIST issue_no:12 issue_date:12 dept:15 issued_to:25 status:10
\

// ── Stock Transfer ──
FORM transfer COLS 2
  transfer_no   | 12 | r p:TRF
  transfer_date |    | d
  from_wh       |  8 | lk:warehouse
  to_wh         |  8 | lk:warehouse
  status        |    | o v:Draft
  remarks       | 50 | w:2
  DETAIL "Lines" AS transfer_line T
    part_no      | 12 | lk:item fl:part_name
    part_name    | 25 | o
    transfer_qty | 12 | n
    uom          |  6 | e:PC,KG,M,L,BOX,SET
  \
  WORKFLOW Draft>Posted:post, Posted>Void:void
  LIST transfer_no:12 transfer_date:12 from_wh:10 to_wh:10 status:10
\

// ── Reports ──
REPORT rpt_stock_value TITLE "Stock Value Report"
  SO item ORDER category, part_no
  COLS
    part_no    | Part No    | 14
    part_name  | Part Name  | 24
    category   | Category   | 16
    uom        | UOM        |  6
    on_hand    | On Hand    | 10 | n s
    unit_cost  | Unit Cost  | 12 | n
    stock_val  | Stock Value| 14 | n s f:on_hand*unit_cost
  END
  GRP category | Category Total | on_hand:s stock_val:s
  TOT | Grand Total | on_hand:s stock_val:s
  CT: row:category, col:uom, val:on_hand:sum, rt:yes, ct:yes
\

REPORT rpt_stock_card TITLE "Stock Card History"
  SO item_card ORDER trans_date, doc_no
  COLS
    trans_date  | Date       | 12
    trans_type  | Type       |  8
    doc_no      | Doc No     | 14
    receive_qty | Received   | 10 | n s
    issue_qty   | Issued     | 10 | n s
    balance     | Balance    | 12 | n
    unit_cost   | Cost       | 10 | n
  END
  GRP trans_type | Type Total | receive_qty:s issue_qty:s
  TOT | Grand Total | receive_qty:s issue_qty:s
\

// ── Menu ──
MENU "Inventory Management System"
  GROUP "Inventory": item, item.list HK:F2, -, EXIT HK:ESC
  GROUP "Purchasing": po HK:F3, po.list HK:F4, -, grn, grn.list
  GROUP "Warehouse": issue, issue.list, -, transfer, transfer.list
  GROUP "Reports": report:rpt_stock_value, report:rpt_stock_card
  GROUP "Setup": vendor, vendor.list, -, warehouse, warehouse.list, -, SWITCH, -, RESET
\

RESET
  CLEAR po, po_line, grn, grn_line, issue, issue_line, transfer, transfer_line, item_card
  UPDATE item SET on_hand = 0, on_order = 0
\
