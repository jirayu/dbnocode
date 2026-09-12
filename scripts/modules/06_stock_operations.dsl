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
    STOCK ISSUE
      LINES lines | ITEM line.part_no | QTY line.issue_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE issue_date | DOC issue_no | WAREHOUSE warehouse | TYPE "ISSUE"
    END STOCK
  ENDSCRIPT
  SCRIPT void
    STOCK ISSUE REVERSE
      LINES lines | ITEM line.part_no | QTY line.issue_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty | COST line.unit_cost
      DATE issue_date | DOC issue_no | WAREHOUSE warehouse | TYPE "VOID-I"
    END STOCK
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
    STOCK TRANSFER
      LINES lines | ITEM line.part_no | QTY line.transfer_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE transfer_date | DOC transfer_no
      FROM_WAREHOUSE from_wh | TO_WAREHOUSE to_wh
      TYPE_OUT "TRF-OUT" | TYPE_IN "TRF-IN"
    END STOCK
  ENDSCRIPT
  SCRIPT void
    STOCK TRANSFER REVERSE
      LINES lines | ITEM line.part_no | QTY line.transfer_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE transfer_date | DOC transfer_no | TYPE "VOID-T"
      FROM_WAREHOUSE from_wh | TO_WAREHOUSE to_wh
    END STOCK
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
    STOCK ISSUE
      LINES lines | ITEM line.part_no | QTY line.issue_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty
      DATE qi_date | DOC qi_number | WAREHOUSE warehouse | TYPE "ISSUE"
    END STOCK
  ENDSCRIPT
  SCRIPT void
    STOCK ISSUE REVERSE
      LINES lines | ITEM line.part_no | QTY line.issue_qty | UOM line.uom
      PACK_UOM line.pack_uom | PACK_QTY line.pack_qty | COST line.unit_cost
      DATE qi_date | DOC qi_number | WAREHOUSE warehouse | TYPE "VOID-I"
    END STOCK
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Posted : post
    Posted > Void : void
  END WORKFLOW
  LIST qi_number:18 qi_date:12 dept:15 issued_to:25 total_cost:14 status:10
END
