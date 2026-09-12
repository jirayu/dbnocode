APP "Inventory Management" VERSION "1.0"

DATASOURCE "inventory_db" ADAPTER "sqlite"

// ══════════════════════════════════════════
// REFERENCE TABLES
// ══════════════════════════════════════════

// ── Item Master ──
FORM "item_form"
  SECTION "Item Information"
  FIELD "part_no" TYPE STRING REQUIRED LEN 12 PREFIX "ITM"
  FIELD "part_name" TYPE STRING REQUIRED LEN 40 SPAN 2
  FIELD "category" TYPE ENUM ENUM ["Raw Material", "Finished Good", "Component", "Consumable", "Service"]
  FIELD "uom" TYPE ENUM ENUM ["PC", "KG", "M", "L", "BOX", "SET", "HR"]
  FIELD "unit_cost" TYPE FLOAT
  FIELD "unit_price" TYPE FLOAT
  SECTION "Stock Levels"
  FIELD "min_qty" TYPE FLOAT DEFAULT 0
  FIELD "max_qty" TYPE FLOAT DEFAULT 0
  FIELD "reorder_qty" TYPE FLOAT DEFAULT 0
  FIELD "on_hand" TYPE FLOAT DEFAULT 0
END FORM

GRID "item_grid" BELONGS_TO "item_form"
  COLUMN "part_no" TYPE STRING WIDTH 12
  COLUMN "part_name" TYPE STRING WIDTH 30
  COLUMN "category" TYPE STRING WIDTH 14
  COLUMN "uom" TYPE STRING WIDTH 5
  COLUMN "unit_cost" TYPE FLOAT WIDTH 12
  COLUMN "unit_price" TYPE FLOAT WIDTH 12
  COLUMN "on_hand" TYPE FLOAT WIDTH 10
END GRID

// ── Vendor/Supplier ──
FORM "vendor_form"
  FIELD "vendor_code" TYPE STRING REQUIRED LEN 10 PREFIX "VEN"
  FIELD "vendor_name" TYPE STRING REQUIRED LEN 40 SPAN 2
  FIELD "contact" TYPE STRING LEN 30
  FIELD "phone" TYPE STRING LEN 15
  FIELD "email" TYPE STRING LEN 30
  FIELD "address" TYPE STRING LEN 50 SPAN 2
END FORM

GRID "vendor_grid" BELONGS_TO "vendor_form"
  COLUMN "vendor_code" TYPE STRING WIDTH 10
  COLUMN "vendor_name" TYPE STRING WIDTH 30
  COLUMN "contact" TYPE STRING WIDTH 20
  COLUMN "phone" TYPE STRING WIDTH 15
END GRID

// ── Warehouse ──
FORM "warehouse_form"
  FIELD "wh_code" TYPE STRING REQUIRED LEN 8 PREFIX "WH"
  FIELD "wh_name" TYPE STRING REQUIRED LEN 30
  FIELD "wh_type" TYPE ENUM ENUM ["Main", "Transit", "Damage", "WIP"]
  FIELD "location" TYPE STRING LEN 40
END FORM

GRID "warehouse_grid" BELONGS_TO "warehouse_form"
  COLUMN "wh_code" TYPE STRING WIDTH 8
  COLUMN "wh_name" TYPE STRING WIDTH 25
  COLUMN "wh_type" TYPE STRING WIDTH 10
  COLUMN "location" TYPE STRING WIDTH 30
END GRID

// ══════════════════════════════════════════
// PURCHASE ORDER
// ══════════════════════════════════════════

FORM "po_form"
  SECTION "Purchase Order"
  FIELD "po_number" TYPE STRING REQUIRED LEN 12 PREFIX "PO"
  FIELD "po_date" TYPE DATE
  FIELD "vendor_code" TYPE STRING LEN 10 LOOKUP "vendor"
  FIELD "vendor_name" TYPE STRING LEN 40
  FIELD "warehouse" TYPE STRING LEN 8 LOOKUP "warehouse"
  FIELD "status" TYPE ENUM ENUM ["Draft", "Posted", "Partial", "Closed", "Void"]
  FIELD "notes" TYPE STRING LEN 50 SPAN 2
END FORM

// ── PO Detail Lines ──
FORM "po_line_form"
  FIELD "part_no" TYPE STRING LEN 12
  FIELD "part_name" TYPE STRING LEN 30
  FIELD "order_qty" TYPE FLOAT
  FIELD "unit_cost" TYPE FLOAT
  FIELD "amount" TYPE FLOAT
  FIELD "received_qty" TYPE FLOAT
END FORM

GRID "po_line_grid" BELONGS_TO "po_line_form" TOTALS 1
  COLUMN "part_no" TYPE STRING WIDTH 12 LOOKUP "item" LOOKUPFILL "part_name"
  COLUMN "part_name" TYPE STRING WIDTH 25 READONLY
  COLUMN "order_qty" TYPE FLOAT WIDTH 10
  COLUMN "unit_cost" TYPE FLOAT WIDTH 12
  COLUMN "amount" TYPE FLOAT WIDTH 14 FORMULA "order_qty*unit_cost"
  COLUMN "received_qty" TYPE FLOAT WIDTH 10 READONLY
END GRID

// ── PO Listing Grid ──
GRID "po_grid" BELONGS_TO "po_form"
  COLUMN "po_number" TYPE STRING WIDTH 12
  COLUMN "po_date" TYPE DATE WIDTH 12
  COLUMN "vendor_code" TYPE STRING WIDTH 10
  COLUMN "vendor_name" TYPE STRING WIDTH 25
  COLUMN "status" TYPE STRING WIDTH 10
END GRID

// ══════════════════════════════════════════
// GOODS RECEIPT NOTE
// ══════════════════════════════════════════

FORM "grn_form"
  SECTION "Goods Receipt"
  FIELD "grn_no" TYPE STRING REQUIRED LEN 12 PREFIX "GRN"
  FIELD "grn_date" TYPE DATE
  FIELD "po_number" TYPE STRING LEN 12 LOOKUP "po"
  FIELD "vendor_name" TYPE STRING LEN 40
  FIELD "warehouse" TYPE STRING LEN 8 LOOKUP "warehouse"
  FIELD "status" TYPE ENUM ENUM ["Draft", "Posted", "Void"]
  FIELD "remarks" TYPE STRING LEN 50 SPAN 2
END FORM

// ── GRN Detail Lines ──
FORM "grn_line_form"
  FIELD "part_no" TYPE STRING LEN 12
  FIELD "part_name" TYPE STRING LEN 30
  FIELD "order_qty" TYPE FLOAT
  FIELD "received_qty" TYPE FLOAT
  FIELD "unit_cost" TYPE FLOAT
  FIELD "amount" TYPE FLOAT
END FORM

GRID "grn_line_grid" BELONGS_TO "grn_line_form" TOTALS 1
  COLUMN "part_no" TYPE STRING WIDTH 12 LOOKUP "item" LOOKUPFILL "part_name"
  COLUMN "part_name" TYPE STRING WIDTH 25 READONLY
  COLUMN "order_qty" TYPE FLOAT WIDTH 10 READONLY
  COLUMN "received_qty" TYPE FLOAT WIDTH 12
  COLUMN "unit_cost" TYPE FLOAT WIDTH 12
  COLUMN "amount" TYPE FLOAT WIDTH 14 FORMULA "received_qty*unit_cost"
END GRID

// ── GRN Listing Grid ──
GRID "grn_grid" BELONGS_TO "grn_form"
  COLUMN "grn_no" TYPE STRING WIDTH 12
  COLUMN "grn_date" TYPE DATE WIDTH 12
  COLUMN "po_number" TYPE STRING WIDTH 12
  COLUMN "vendor_name" TYPE STRING WIDTH 25
  COLUMN "status" TYPE STRING WIDTH 10
END GRID

// ══════════════════════════════════════════
// ISSUE SLIP (Stock Out)
// ══════════════════════════════════════════

FORM "issue_form"
  SECTION "Issue Slip"
  FIELD "issue_no" TYPE STRING REQUIRED LEN 12 PREFIX "IS"
  FIELD "issue_date" TYPE DATE
  FIELD "dept" TYPE STRING LEN 20
  FIELD "issued_to" TYPE STRING LEN 30
  FIELD "warehouse" TYPE STRING LEN 8 LOOKUP "warehouse"
  FIELD "status" TYPE ENUM ENUM ["Draft", "Posted", "Void"]
  FIELD "remarks" TYPE STRING LEN 50 SPAN 2
END FORM

// ── Issue Detail Lines ──
FORM "issue_line_form"
  FIELD "part_no" TYPE STRING LEN 12
  FIELD "part_name" TYPE STRING LEN 30
  FIELD "issue_qty" TYPE FLOAT
  FIELD "uom" TYPE STRING LEN 5
  FIELD "unit_cost" TYPE FLOAT
  FIELD "amount" TYPE FLOAT
END FORM

GRID "issue_line_grid" BELONGS_TO "issue_line_form" TOTALS 1
  COLUMN "part_no" TYPE STRING WIDTH 12 LOOKUP "item" LOOKUPFILL "part_name"
  COLUMN "part_name" TYPE STRING WIDTH 25 READONLY
  COLUMN "issue_qty" TYPE FLOAT WIDTH 10
  COLUMN "uom" TYPE STRING WIDTH 6 ENUM ["PC", "KG", "M", "L", "BOX", "SET"]
  COLUMN "unit_cost" TYPE FLOAT WIDTH 12
  COLUMN "amount" TYPE FLOAT WIDTH 14 FORMULA "issue_qty*unit_cost"
END GRID

// ── Issue Listing Grid ──
GRID "issue_grid" BELONGS_TO "issue_form"
  COLUMN "issue_no" TYPE STRING WIDTH 12
  COLUMN "issue_date" TYPE DATE WIDTH 12
  COLUMN "dept" TYPE STRING WIDTH 15
  COLUMN "issued_to" TYPE STRING WIDTH 25
  COLUMN "status" TYPE STRING WIDTH 10
END GRID

// ══════════════════════════════════════════
// STOCK TRANSFER
// ══════════════════════════════════════════

FORM "transfer_form"
  SECTION "Stock Transfer"
  FIELD "transfer_no" TYPE STRING REQUIRED LEN 12 PREFIX "TRF"
  FIELD "transfer_date" TYPE DATE
  FIELD "from_wh" TYPE STRING LEN 8 LOOKUP "warehouse"
  FIELD "to_wh" TYPE STRING LEN 8 LOOKUP "warehouse"
  FIELD "status" TYPE ENUM ENUM ["Draft", "Posted", "Void"]
  FIELD "remarks" TYPE STRING LEN 50 SPAN 2
END FORM

// ── Transfer Detail Lines ──
FORM "transfer_line_form"
  FIELD "part_no" TYPE STRING LEN 12
  FIELD "part_name" TYPE STRING LEN 30
  FIELD "transfer_qty" TYPE FLOAT
  FIELD "uom" TYPE STRING LEN 5
END FORM

GRID "transfer_line_grid" BELONGS_TO "transfer_line_form" TOTALS 1
  COLUMN "part_no" TYPE STRING WIDTH 12 LOOKUP "item" LOOKUPFILL "part_name"
  COLUMN "part_name" TYPE STRING WIDTH 25 READONLY
  COLUMN "transfer_qty" TYPE FLOAT WIDTH 12
  COLUMN "uom" TYPE STRING WIDTH 6 ENUM ["PC", "KG", "M", "L", "BOX", "SET"]
END GRID

// ── Transfer Listing Grid ──
GRID "transfer_grid" BELONGS_TO "transfer_form"
  COLUMN "transfer_no" TYPE STRING WIDTH 12
  COLUMN "transfer_date" TYPE DATE WIDTH 12
  COLUMN "from_wh" TYPE STRING WIDTH 10
  COLUMN "to_wh" TYPE STRING WIDTH 10
  COLUMN "status" TYPE STRING WIDTH 10
END GRID

// ══════════════════════════════════════════
// LAYOUTS
// ══════════════════════════════════════════

LAYOUT "main_menu" TITLE "Inventory Management System" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  TOP_MENU
    GROUP "Inventory"
      ITEM "Item Master" RUN "goto_items"
      ITEM "Item List" RUN "goto_item_list" HOTKEY "F2"
      SEPARATOR
      ITEM "Exit" RUN "exit_app" HOTKEY "ESC"
    END GROUP
    GROUP "Purchasing"
      ITEM "New Purchase Order" RUN "goto_po_add" HOTKEY "F3"
      ITEM "PO List" RUN "goto_po_list" HOTKEY "F4"
      SEPARATOR
      ITEM "New Goods Receipt" RUN "goto_grn_add" HOTKEY "F5"
      ITEM "GRN List" RUN "goto_grn_list" HOTKEY "F6"
    END GROUP
    GROUP "Warehouse"
      ITEM "New Issue Slip" RUN "goto_issue_add"
      ITEM "Issue List" RUN "goto_issue_list"
      SEPARATOR
      ITEM "New Transfer" RUN "goto_transfer_add"
      ITEM "Transfer List" RUN "goto_transfer_list"
    END GROUP
    GROUP "Setup"
      ITEM "Vendors" RUN "goto_vendors"
      ITEM "Vendor List" RUN "goto_vendor_list"
      SEPARATOR
      ITEM "Warehouses" RUN "goto_warehouses"
      ITEM "Warehouse List" RUN "goto_wh_list"
    END GROUP
  END TOP_MENU
  CENTER_MENU
    ITEM "1. Item Master" RUN "goto_items"
    ITEM "2. Item List" RUN "goto_item_list"
    ITEM "3. Purchase Orders" RUN "goto_po_list"
    ITEM "4. Goods Receipt" RUN "goto_grn_list"
    ITEM "5. Issue Slips" RUN "goto_issue_list"
    ITEM "6. Stock Transfer" RUN "goto_transfer_list"
    ITEM "7. Vendors" RUN "goto_vendor_list"
    ITEM "8. Warehouses" RUN "goto_wh_list"
    ITEM "9. Exit" RUN "exit_app"
  END CENTER_MENU
END LAYOUT

// ── Item Master Form ──
LAYOUT "add_item" TITLE "Item Master" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "item_form" Y=3 X=2 COLS=2
END LAYOUT

LAYOUT "list_items" TITLE "Item List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "item_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Vendor Form ──
LAYOUT "add_vendor" TITLE "Vendor Master" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "vendor_form" Y=3 X=2 COLS=2
END LAYOUT

LAYOUT "list_vendors" TITLE "Vendor List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "vendor_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Warehouse Form ──
LAYOUT "add_warehouse" TITLE "Warehouse" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "warehouse_form" Y=3 X=2 COLS=2
END LAYOUT

LAYOUT "list_warehouses" TITLE "Warehouse List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "warehouse_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Purchase Order Form ──
LAYOUT "add_po" TITLE "Purchase Order" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "po_form" Y=3 X=2 COLS=2
  TABS Y=9 X=2 HEIGHT=14
    TAB "Order Lines" GRID "po_line_grid"
  END TABS
END LAYOUT

LAYOUT "list_po" TITLE "Purchase Orders" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "po_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Goods Receipt Form ──
LAYOUT "add_grn" TITLE "Goods Receipt" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "grn_form" Y=3 X=2 COLS=2
  TABS Y=9 X=2 HEIGHT=14
    TAB "Receipt Lines" GRID "grn_line_grid"
  END TABS
END LAYOUT

LAYOUT "list_grn" TITLE "Goods Receipt List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "grn_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Issue Slip Form ──
LAYOUT "add_issue" TITLE "Issue Slip" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "issue_form" Y=3 X=2 COLS=2
  TABS Y=9 X=2 HEIGHT=14
    TAB "Issue Lines" GRID "issue_line_grid"
  END TABS
END LAYOUT

LAYOUT "list_issue" TITLE "Issue Slip List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "issue_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ── Stock Transfer Form ──
LAYOUT "add_transfer" TITLE "Stock Transfer" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "transfer_form" Y=3 X=2 COLS=2
  TABS Y=9 X=2 HEIGHT=14
    TAB "Transfer Lines" GRID "transfer_line_grid"
  END TABS
END LAYOUT

LAYOUT "list_transfer" TITLE "Transfer List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "transfer_grid" Y=3 X=2 HEIGHT=18
END LAYOUT

// ══════════════════════════════════════════
// ACTIONS
// ══════════════════════════════════════════

// Item
ACTION "goto_items" GOTO "add_item"
ACTION "goto_item_list" GOTO "list_items"

// Vendor
ACTION "goto_vendors" GOTO "add_vendor"
ACTION "goto_vendor_list" GOTO "list_vendors"

// Warehouse
ACTION "goto_warehouses" GOTO "add_warehouse"
ACTION "goto_wh_list" GOTO "list_warehouses"

// Purchase Order
ACTION "goto_po_add" GOTO "add_po"
ACTION "goto_po_list" GOTO "list_po"

// Goods Receipt
ACTION "goto_grn_add" GOTO "add_grn"
ACTION "goto_grn_list" GOTO "list_grn"

// Issue Slip
ACTION "goto_issue_add" GOTO "add_issue"
ACTION "goto_issue_list" GOTO "list_issue"

// Stock Transfer
ACTION "goto_transfer_add" GOTO "add_transfer"
ACTION "goto_transfer_list" GOTO "list_transfer"

// Navigation
ACTION "goto_main" GOTO "main_menu"
ACTION "exit_app" EXIT
