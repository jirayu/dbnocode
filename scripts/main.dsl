APP "Inventory Management" VERSION "1.0"

DATASOURCE "inventory_db" ADAPTER "sqlite"

// Modules are explicit and ordered because later forms reference earlier tables.
INCLUDE "modules/02_master_data.dsl"
INCLUDE "modules/03_warehouse.dsl"
INCLUDE "modules/04_web_pos.dsl"
INCLUDE "modules/05_purchasing.dsl"
INCLUDE "modules/06_stock_operations.dsl"
INCLUDE "modules/07_sales.dsl"
INCLUDE "modules/08_bom.dsl"
INCLUDE "modules/09_reports.dsl"
INCLUDE "modules/10_menu.dsl"
