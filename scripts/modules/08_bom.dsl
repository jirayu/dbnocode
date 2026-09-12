// ══════════════════════════════════════════
// BILL OF MATERIALS
// ══════════════════════════════════════════

FORM bom TITLE "Bill of Materials" COLS 2
  SECTION "BOM Header"
  model_no     | 14 | req prefix:BOM:YYMM
  model_name   | 40 | req span:2
  revision     |  6 | default:A
  output_pn    | 12 | lookup:item fill:output_part_name
  output_part_name | 30 | ro
  base_qty     | 10 | num default:1
  status       |    | ro default:Draft
  notes        | 50 | span:2
  TREE_DETAIL "Components" AS bom_line children_key:children tree_label:component
    component       | 22 |
    part_no         | 14 | lookup:item fill:part_name,uom
    vendor_id       | 10 | lookup:vendor
    part_name       | 28 | ro
    qty             |  8 | num
    per             |  5 | num
    uom             |  6 | ro
    part_level      |  7 | opts:FG,PART,RAWMAT,TOOL,LIB,EXWORK
    safety_percent  |  5 | num
  END TREE_DETAIL
  SCRIPT activate
    OK "BOM activated"
  ENDSCRIPT
  SCRIPT obsolete
    OK "BOM marked obsolete"
  ENDSCRIPT
  SCRIPT hold
    OK "BOM on hold"
  ENDSCRIPT
  SCRIPT compute_bom
    SET result = BOMEXPLODE(model_no, _compute_qty)
    SET rollup = BOMROLLUP(model_no, _compute_qty)
  ENDSCRIPT
  SCRIPT rollup_bom
    SET rollup = BOMROLLUP(model_no, _compute_qty)
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Active : activate
    Draft > Hold : hold
    Active > Obsolete : obsolete
    Active > Hold : hold
    Hold > Active : activate
    Hold > Obsolete : obsolete
    Obsolete > Draft : activate
    ---
    ACTION "Compute BOM" : compute_bom
    ACTION "Rollup BOM" : rollup_bom
  END WORKFLOW
  HIGHLIGHT
    status == "Obsolete" | error
    status == "Active"   | success
    status == "Hold"     | warning
  END HIGHLIGHT
  LIST model_no:14 model_name:30 revision:6 output_pn:12 base_qty:8 status:10
END

// ══════════════════════════════════════════
// BOM EXPLOSION
// ══════════════════════════════════════════

FORM bom_explosion TITLE "BOM Explosion" COLS 2
  SECTION "Parameters"
  source_bom   | 14 | req lookup:bom fill:model_name,base_qty
  model_name   | 30 | ro
  base_qty     | 10 | num ro
  compute_qty  | 10 | num default:1
  explode_mode |    | opts:Explode,Rollup default:Explode
  status       |    | ro default:Draft
  SECTION "Results"
  total_parts  |  6 | num ro
  total_cost   | 14 | num ro
  DETAIL "Explosion Lines" AS explosion_line TOTALS
    level        |  3 | num ro
    part_no      | 12 | ro
    part_name    | 20 | ro
    part_level   |  8 | ro
    total_usage  | 10 | num ro
    uom          |  5 | ro
    unit_cost    | 10 | num ro
    ext_cost     | 12 | num ro formula:total_usage*unit_cost
    source_bom   | 14 | ro
    vendor_id    | 10 | ro
  END DETAIL
  COMPUTED
    total_parts = COUNT(explosion_line)
    total_cost  = SUM(explosion_line, ext_cost)
  END COMPUTED
  SCRIPT explode
    SET result = BOMEXPLODE(source_bom, compute_qty)
    FOREACH item IN result
      INSERT explosion_line SET part_no = item.part_no, part_name = item.part_name, part_level = item.part_level, total_usage = item.total_usage, uom = item.uom, unit_cost = item.unit_cost, level = item.level, source_bom = item.source_bom, vendor_id = item.vendor_id, _parent_rowid = id
    END
    SET total_parts = COUNT(result)
    OK "BOM exploded: " + STR(total_parts) + " lines"
  ENDSCRIPT
  SCRIPT rollup
    SET result = BOMROLLUP(source_bom, compute_qty)
    FOREACH item IN result
      INSERT explosion_line SET part_no = item.part_no, part_name = item.part_name, part_level = item.part_level, total_usage = item.total_usage, uom = item.uom, unit_cost = item.unit_cost, level = 0, source_bom = item.source_bom, vendor_id = item.vendor_id, _parent_rowid = id
    END
    SET total_parts = COUNT(result)
    OK "BOM rolled up: " + STR(total_parts) + " parts"
  ENDSCRIPT
  WORKFLOW ON status
    Draft > Exploded : explode
    Draft > Rolled Up : rollup
  END WORKFLOW
  LIST source_bom:14 model_name:25 compute_qty:10 explode_mode:8 total_parts:8 total_cost:14 status:10
END
