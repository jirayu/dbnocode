APP "Membership System" VERSION "1.0"

DATASOURCE "membership_db" ADAPTER "sqlite"

FORM "member_form"
  SECTION "Personal Information"
  FIELD "member_id" TYPE STRING REQUIRED LEN 10 PREFIX "MEM"
  FIELD "full_name" TYPE STRING REQUIRED LEN 40 SPAN 2
  FIELD "phone" TYPE STRING LEN 15
  FIELD "email" TYPE STRING LEN 25
  SECTION "Membership Details"
  FIELD "member_type" TYPE ENUM ENUM ["Standard", "Premium", "VIP"]
  FIELD "join_date" TYPE DATE
  FIELD "is_active" TYPE BOOL
  FIELD "annual_fee" TYPE FLOAT DEFAULT 0.00
END FORM

GRID "member_grid" BELONGS_TO "member_form"
  COLUMN "member_id" TYPE STRING WIDTH 10
  COLUMN "full_name" TYPE STRING WIDTH 25
  COLUMN "member_type" TYPE STRING WIDTH 12
  COLUMN "join_date" TYPE DATE WIDTH 12
END GRID

FORM "history_form"
  FIELD "member_id" TYPE INT LEN 6
  FIELD "action_date" TYPE DATE
  FIELD "description" TYPE STRING LEN 40
END FORM

GRID "history_grid" BELONGS_TO "history_form"
  COLUMN "action_date" TYPE DATE WIDTH 12
  COLUMN "description" TYPE STRING WIDTH 35
END GRID

FORM "payment_form"
  FIELD "member_id" TYPE INT LEN 6
  FIELD "pay_date" TYPE DATE
  FIELD "amount" TYPE FLOAT
  FIELD "method" TYPE STRING LEN 15
END FORM

GRID "payment_grid" BELONGS_TO "payment_form"
  COLUMN "pay_date" TYPE DATE WIDTH 12
  COLUMN "amount" TYPE FLOAT WIDTH 10
  COLUMN "method" TYPE STRING WIDTH 15 ENUM ["Cash", "Card", "Transfer", "Check"]
END GRID

// ── Service reference table ──
FORM "service_form"
  FIELD "code" TYPE STRING REQUIRED LEN 10
  FIELD "name" TYPE STRING REQUIRED LEN 40
  FIELD "price" TYPE FLOAT REQUIRED
END FORM

GRID "service_grid" BELONGS_TO "service_form"
  COLUMN "code" TYPE STRING WIDTH 10
  COLUMN "name" TYPE STRING WIDTH 30
  COLUMN "price" TYPE FLOAT WIDTH 12
END GRID

// ── Invoice header ──
FORM "invoice_form"
  SECTION "Invoice Header"
  FIELD "invoice_no" TYPE STRING REQUIRED LEN 10 PREFIX "INV"
  FIELD "member_id" TYPE STRING LEN 10 LOOKUP "member"
  FIELD "member_name" TYPE STRING LEN 40
  FIELD "invoice_date" TYPE DATE
  FIELD "notes" TYPE STRING LEN 50
END FORM

// ── Invoice detail lines ──
FORM "inv_detail_form"
  FIELD "service_code" TYPE STRING LEN 10
  FIELD "description" TYPE STRING LEN 40
  FIELD "qty" TYPE FLOAT
  FIELD "unit_price" TYPE FLOAT
  FIELD "amount" TYPE FLOAT
END FORM

GRID "inv_detail_grid" BELONGS_TO "inv_detail_form" TOTALS 1
  COLUMN "service_code" TYPE STRING WIDTH 10 LOOKUP "service" LOOKUPFILL "description"
  COLUMN "description" TYPE STRING WIDTH 25 READONLY
  COLUMN "qty" TYPE FLOAT WIDTH 8
  COLUMN "unit_price" TYPE FLOAT WIDTH 12
  COLUMN "amount" TYPE FLOAT WIDTH 12 FORMULA "qty*unit_price"
END GRID

// ── Invoice listing grid ──
GRID "invoice_grid" BELONGS_TO "invoice_form"
  COLUMN "invoice_no" TYPE STRING WIDTH 10
  COLUMN "member_id" TYPE STRING WIDTH 10
  COLUMN "member_name" TYPE STRING WIDTH 25
  COLUMN "invoice_date" TYPE DATE WIDTH 12
END GRID

FORM "type_form"
  FIELD "type_code" TYPE STRING REQUIRED LEN 10
  FIELD "type_name" TYPE STRING REQUIRED LEN 30
  FIELD "fee_amount" TYPE FLOAT REQUIRED
  FIELD "max_books" TYPE INT DEFAULT 5
END FORM

LAYOUT "main_menu" TITLE "Membership Management" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  TOP_MENU
    GROUP "Members"
      ITEM "Add Member" RUN "goto_add" HOTKEY "F2"
      ITEM "Member List" RUN "goto_list" HOTKEY "F3"
      SEPARATOR
      ITEM "Exit" RUN "exit_app" HOTKEY "ESC"
    END GROUP
    GROUP "Invoices"
      ITEM "New Invoice" RUN "goto_inv_add" HOTKEY "F4"
      ITEM "Invoice List" RUN "goto_inv_list" HOTKEY "F5"
    END GROUP
    GROUP "Settings"
      ITEM "Services" RUN "goto_services"
      ITEM "Member Types" RUN "goto_types"
    END GROUP
    GROUP "Help"
      ITEM "About" RUN "goto_about"
    END GROUP
  END TOP_MENU
  CENTER_MENU
    ITEM "1. Add New Member" RUN "goto_add"
    ITEM "2. View Member List" RUN "goto_list"
    ITEM "3. New Invoice" RUN "goto_inv_add"
    ITEM "4. Invoice List" RUN "goto_inv_list"
    ITEM "5. Manage Services" RUN "goto_services"
    ITEM "6. About System" RUN "goto_about"
    ITEM "7. Exit" RUN "exit_app"
  END CENTER_MENU
END LAYOUT

LAYOUT "add_member" TITLE "Add New Member" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "member_form" Y=3 X=2 COLS=2
  TABS Y=10 X=2 HEIGHT=13
    TAB "History" GRID "history_grid"
    TAB "Payments" GRID "payment_grid"
  END TABS
END LAYOUT

LAYOUT "list_members" TITLE "Member List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "member_grid" Y=3 X=2 HEIGHT=15
  BUTTON "Back" RUN "goto_main" Y=20 X=36
END LAYOUT

LAYOUT "manage_types" TITLE "Membership Types" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "type_form" Y=3 X=2
END LAYOUT

LAYOUT "add_invoice" TITLE "Invoice Entry" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "invoice_form" Y=3 X=2 COLS=2
  TABS Y=7 X=2 HEIGHT=15
    TAB "Detail Lines" GRID "inv_detail_grid"
  END TABS
END LAYOUT

LAYOUT "list_invoices" TITLE "Invoice List" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  GRID "invoice_grid" Y=3 X=2 HEIGHT=15
  BUTTON "Back" RUN "goto_main" Y=20 X=36
END LAYOUT

LAYOUT "manage_services" TITLE "Services" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  FORM "service_form" Y=3 X=2
  GRID "service_grid" Y=9 X=2 HEIGHT=12
END LAYOUT

LAYOUT "about_screen" TITLE "About" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
END LAYOUT

ACTION "goto_add" GOTO "add_member"
ACTION "goto_list" GOTO "list_members"
ACTION "goto_inv_add" GOTO "add_invoice"
ACTION "goto_inv_list" GOTO "list_invoices"
ACTION "goto_services" GOTO "manage_services"
ACTION "goto_types" GOTO "manage_types"
ACTION "goto_about" GOTO "about_screen"
ACTION "goto_main" GOTO "main_menu"
ACTION "save_member" GOTO "main_menu"
ACTION "save_invoice" GOTO "main_menu"
ACTION "save_service" GOTO "main_menu"
ACTION "save_type" GOTO "main_menu"
ACTION "cancel_back" GOTO "main_menu"
ACTION "exit_app" EXIT
