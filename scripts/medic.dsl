APP "medic" VERSION "1.0"
DATASOURCE "D:\dbnocode\medic" ADAPTER "sqlite"

FORM inventory TITLE "Inventory" COLS 2
  id | 8 | num
  item_name | 20 |
  category | 10 |
  supplier | 10 |
  qty | 8 | num
  expiry_date | 19 | date
  expiry_units | 12 | num
  unit_price | 10 | num
  total_value | 18 | num
  location | 9 |
  reorder | 8 | num
  flag | 8 |
  ordered | 8 |
  delivery | 19 | date
  LIST id:8 item_name:20 category:10 supplier:10 qty:8 expiry_date:19 expiry_units:12 unit_price:10 total_value:18 location:9 reorder:8 flag:8 ordered:8 delivery:19
END

FORM helper TITLE "Helper" COLS 2
  expiring_units | 20 |
  total_units | 19 | date
  number_critical | 16 |
  delivery | 31 | date
  top_3_by_value | 20 |
  value_by_category | 17 |
  total_value | 28 |
  LIST expiring_units:20 total_units:19 number_critical:16 delivery:24 top_3_by_value:20 value_by_category:17 total_value:24
END

FORM categories TITLE "Categories" COLS 2
  category_name | 50 |
  description_notes | 45 |
  LIST category_name:24 description_notes:24
END

FORM suppliers TITLE "Suppliers" COLS 2
  supplier_name | 13 |
  contact_person | 14 |
  email | 22 |
  notes | 22 |
  LIST supplier_name:13 contact_person:14 email:22 notes:22
END

FORM locations TITLE "Locations" COLS 2
  location | 9 |
  description | 36 |
  LIST location:9 description:24
END

MENU "medic"
  GROUP "Imported Sheets"
    "Inventory" => inventory
    "Helper" => helper
    "Categories" => categories
    "Suppliers" => suppliers
    "Locations" => locations
    ---
    "Exit" => EXIT HOTKEY ESC
  END
  CENTER
    "1. Inventory" => inventory
    "2. Helper" => helper
    "3. Categories" => categories
    "4. Suppliers" => suppliers
    "5. Locations" => locations
    "6. Exit" => EXIT
  END
END
