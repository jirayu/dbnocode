APP "Main Menu Demo" VERSION "1.0"

LAYOUT "main" TITLE "Inventory System" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
  TOP_MENU
    GROUP "File"
      ITEM "Delivery Note" RUN "dn_entry" HOTKEY "F2"
      ITEM "Item Master" RUN "item_entry" HOTKEY "F3"
      SEPARATOR
      ITEM "Exit" RUN "close" HOTKEY "ESC"
    END GROUP
  END TOP_MENU
  CENTER_MENU
    ITEM "1. Delivery Note Entry" RUN "dn_entry"
    ITEM "2. Item Master File" RUN "item_entry"
    ITEM "3. Exit System" RUN "close"
  END CENTER_MENU
END LAYOUT

LAYOUT "dn_form" TITLE "Delivery Note Entry" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
END LAYOUT

LAYOUT "item_form" TITLE "Item Master File" POSITION 0 0 WIDTH 80 HEIGHT 24 BORDER 1
END LAYOUT

ACTION "dn_entry" GOTO "dn_form"
ACTION "item_entry" GOTO "item_form"
ACTION "close" EXIT
