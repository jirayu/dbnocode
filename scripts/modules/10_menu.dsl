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
