APP "Membership System" VERSION "1.0"

DATASOURCE "membership_db" ADAPTER "sqlite"

// ── Member Master ──
FORM member TITLE "Add New Member" COLS 2
  SECTION "Personal Information"
  member_id   | 10 | req prefix:MEM
  full_name   | 40 | req span:2
  phone       | 15 |
  email       | 25 |
  SECTION "Membership Details"
  member_type |    | opts:Standard,Premium,VIP
  join_date   |    | date
  is_active   |    |
  annual_fee  |    | num default:0
  DETAIL "History" AS history
    action_date  | 12 | date
    description  | 35 |
  END DETAIL
  DETAIL "Payments" AS payment
    pay_date | 12 | date
    amount   | 10 | num
    method   | 15 | opts:Cash,Card,Transfer,Check
  END DETAIL
  LIST member_id:10 full_name:25 member_type:12 join_date:12
END

// ── Service reference ──
FORM service TITLE "Services"
  code  | 10 | req
  name  | 40 | req
  price |    | num req
  LIST code:10 name:30 price:12
END

// ── Invoice ──
FORM invoice TITLE "Invoice Entry" COLS 2
  SECTION "Invoice Header"
  invoice_no   | 10 | req prefix:INV
  member_id    | 10 | lookup:member fill:full_name
  member_name  | 40 |
  invoice_date |    | date
  notes        | 50 |
  SECTION "Totals"
  subtotal     | 14 | num ro
  tax_rate     |  6 | num default:@setting.tax_rate_pct
  tax_amt      | 14 | num ro
  grand_total  | 14 | num ro
  DETAIL "Detail Lines" AS inv_detail TOTALS
    service_code | 10 | lookup:service fill:description
    description  | 25 | ro
    qty          |  8 | num
    unit_price   | 12 | num
    amount       | 12 | num formula:qty*unit_price
  END DETAIL
  COMPUTED
    subtotal    = SUM(lines, amount)
    tax_amt     = subtotal * tax_rate / 100
    grand_total = subtotal + tax_amt
  END COMPUTED
  LIST invoice_no:10 member_id:10 member_name:25 invoice_date:12 grand_total:14
END

// ── Membership Types ──
FORM type TITLE "Membership Types"
  type_code  | 10 | req
  type_name  | 30 | req
  fee_amount |    | num req
  max_books  |    | num default:5
END

// ── Menu ──
MENU "Membership Management"
  GROUP "Members"
    "Members" => member HOTKEY F2
    ---
    "Exit" => EXIT HOTKEY ESC
  END
  GROUP "Invoices"
    "Invoices" => invoice HOTKEY F4
  END
  GROUP "Settings"
    "Services" => service
    "Member Types" => type
    ---
    "Business Settings" => SETTINGS
  END
  CENTER
    "1. Members" => member
    "2. Invoices" => invoice
    "3. Services" => service
    "4. Member Types" => type
    "5. Exit" => EXIT
  END
END
