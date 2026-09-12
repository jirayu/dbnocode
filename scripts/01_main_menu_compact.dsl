APP "Main Menu Demo" VERSION "1.0"
DATASOURCE "main_menu_db" ADAPTER "sqlite"

// ── Placeholder forms (empty screens) ──
FORM dn TITLE "Delivery Note Entry"
END

FORM item TITLE "Item Master File"
END

// ── Menu ──
MENU "Inventory System"
  GROUP "File"
    "Delivery Note" => dn HOTKEY F2
    "Item Master" => item HOTKEY F3
    ---
    "Exit" => EXIT HOTKEY ESC
  END
  CENTER
    "1. Delivery Note Entry" => dn
    "2. Item Master File" => item
    "3. Exit System" => EXIT
  END
END
