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
