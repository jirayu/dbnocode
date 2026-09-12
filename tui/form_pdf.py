"""Generic form-to-PDF renderer.

Prints any transaction form (PO, GRN, Invoice, SO, DO, Put-Away, Picking)
as a professional PDF with business settings header.  Triggered by Ctrl+P
in the form layout renderer.

Uses ReportLab (already in requirements.txt).
"""
import os
import json
import curses


# ---------------------------------------------------------------------------
# Print config persistence
# ---------------------------------------------------------------------------

_PRINT_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'print_config.json')


def _load_print_config(form_name: str) -> dict:
    try:
        with open(_PRINT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            all_cfg = json.load(f)
        return all_cfg.get(form_name, {})
    except Exception:
        return {}


def _save_print_config(form_name: str, cfg: dict):
    try:
        with open(_PRINT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            all_cfg = json.load(f)
    except Exception:
        all_cfg = {}
    all_cfg[form_name] = cfg
    try:
        with open(_PRINT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_cfg, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Print options dialog
# ---------------------------------------------------------------------------

def show_print_dialog(win, detail_grids, form_name: str):
    """Show checkbox dialog for column selection + orientation.

    detail_grids: list of {"label", "columns": [{"id","label",...}], "data"}
    Returns (filtered_detail_grids, landscape) or None if cancelled.
    """
    cfg = _load_print_config(form_name)
    saved_cols = cfg.get('columns')
    landscape = cfg.get('landscape', False)

    # Flatten all columns from all grids
    all_cols = []
    for dg in detail_grids:
        for col in dg.get("columns", []):
            cid = col.get("id", "")
            if cid.startswith("_"):
                continue
            label = col.get("label") or cid.replace("_", " ").title()
            if saved_cols is not None:
                checked = cid in saved_cols
            else:
                checked = True
            all_cols.append((cid, label, checked))

    if not all_cols:
        return detail_grids, False

    max_h, max_w = win.getmaxyx()
    dlg_h = min(len(all_cols) + 7, max_h - 4)
    dlg_w = min(50, max_w - 4)
    dlg_y = max(0, (max_h - dlg_h) // 2)
    dlg_x = max(0, (max_w - dlg_w) // 2)

    try:
        popup = curses.newwin(dlg_h, dlg_w, dlg_y, dlg_x)
    except Exception:
        return None
    popup.keypad(True)

    cur = 0
    checked = [c[2] for c in all_cols]
    scroll = 0
    visible = dlg_h - 6
    total_items = len(all_cols) + 2

    while True:
        popup.erase()
        popup.border()
        title = " Print Options "
        popup.addstr(0, max(1, (dlg_w - len(title)) // 2), title, curses.A_BOLD)

        row = 1
        popup.addstr(row, 2, "Columns:", curses.A_BOLD)
        row += 1

        if cur < len(all_cols):
            if cur < scroll:
                scroll = cur
            if cur >= scroll + visible:
                scroll = cur - visible + 1

        for vi in range(visible):
            idx = scroll + vi
            if idx >= len(all_cols):
                break
            _, label, _ = all_cols[idx]
            mark = "[x]" if checked[idx] else "[ ]"
            attr = curses.A_REVERSE if cur == idx else 0
            line = f" {mark} {label} "
            popup.addstr(row + vi, 2, line[:dlg_w - 4], attr)

        orient_row = row + visible
        orient_label = f" [{'x' if landscape else ' '}] Landscape "
        attr = curses.A_REVERSE if cur == len(all_cols) else 0
        popup.addstr(orient_row, 2, orient_label[:dlg_w - 4], attr)

        btn_row = orient_row + 1
        popup.addstr(btn_row, 2, "-" * (dlg_w - 4))
        btn_row += 1
        btn = " [ F10 Print ]  [ ESC Cancel ] "
        attr = curses.A_REVERSE if cur == len(all_cols) + 1 else 0
        popup.addstr(btn_row, 2, btn[:dlg_w - 4], attr)

        popup.refresh()
        k = popup.getch()

        if k == 27:
            del popup
            return None
        elif k == curses.KEY_UP:
            if cur > 0:
                cur -= 1
        elif k == curses.KEY_DOWN:
            if cur < total_items - 1:
                cur += 1
        elif k == ord(' ') or k in (10, 13):
            if cur < len(all_cols):
                checked[cur] = not checked[cur]
            elif cur == len(all_cols):
                landscape = not landscape
            elif cur == len(all_cols) + 1:
                break
        elif k == curses.KEY_F10:
            break
        elif k in (ord('a'), ord('A')):
            if all(checked):
                checked = [False] * len(all_cols)
            else:
                checked = [True] * len(all_cols)

    del popup

    if not any(checked):
        checked = [True] * len(all_cols)

    selected_ids = {all_cols[i][0] for i in range(len(all_cols)) if checked[i]}
    _save_print_config(form_name, {
        'columns': list(selected_ids),
        'landscape': landscape,
    })

    # Filter columns in each detail grid
    filtered = []
    for dg in detail_grids:
        cols = [c for c in dg.get("columns", [])
                if c.get("id", "") in selected_ids or c.get("id", "").startswith("_")]
        filtered.append({**dg, "columns": cols})

    return filtered, landscape


def export_form_pdf(form_fields, record, detail_grids, settings, title,
                    computed_defs=None, landscape=False):
    """Render a form record as PDF and open it.

    Args:
        form_fields: list of field defs from app["forms"][form_id]
        record: dict of current header values
        detail_grids: list of {"label", "columns", "data"} per detail tab
        settings: business settings dict (company_name, address_line1, ...)
        title: form title string
        computed_defs: list of {"name", "expr"} for computed fields

    Returns:
        pdf_path on success, error string on failure.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return "reportlab not installed — run: pip install reportlab"

    # ── Fonts ──
    font_name = 'Tahoma'
    font_bold = 'Tahoma-Bold'
    try:
        windir = os.environ.get('WINDIR', r'C:\Windows')
        tahoma_path = os.path.join(windir, 'Fonts', 'tahoma.ttf')
        tahoma_bold = os.path.join(windir, 'Fonts', 'tahomabd.ttf')
        if os.path.exists(tahoma_path):
            pdfmetrics.registerFont(TTFont(font_name, tahoma_path))
        if os.path.exists(tahoma_bold):
            pdfmetrics.registerFont(TTFont(font_bold, tahoma_bold))
        else:
            font_bold = font_name
    except Exception:
        font_name = 'Helvetica'
        font_bold = 'Helvetica-Bold'

    # ── Output path ──
    safe_title = ''.join(
        c if c.isalnum() or c in (' ', '-', '_') else '_'
        for c in title).strip()
    # Try to include document number in filename
    doc_no = ""
    for f in form_fields:
        fid = f.get("id", "")
        if f.get("type") == "SECTION":
            continue
        if f.get("prefix") or fid.endswith(("_no", "_number", "_code")):
            doc_no = str(record.get(fid, "") or "")
            if doc_no:
                break
    if doc_no:
        safe_doc = ''.join(
            c if c.isalnum() or c in ('-', '_') else '_' for c in doc_no)
        fname = f"{safe_title}_{safe_doc}"
    else:
        fname = safe_title

    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    if not os.path.isdir(desktop):
        desktop = os.path.expanduser('~')
    pdf_path = os.path.join(desktop, f'{fname}.pdf')
    counter = 1
    base_path = pdf_path
    while os.path.exists(pdf_path):
        name_part = base_path.rsplit('.', 1)[0]
        pdf_path = f'{name_part}_{counter}.pdf'
        counter += 1

    # ── Page setup ──
    if landscape:
        page_w, page_h = A4[1], A4[0]
        pagesize = (page_w, page_h)
    else:
        page_w, page_h = A4
        pagesize = A4
    margin = 15 * mm
    usable_w = page_w - 2 * margin
    y_top = page_h - margin

    c = canvas.Canvas(pdf_path, pagesize=pagesize)
    c.setTitle(f'{title} — {doc_no}' if doc_no else title)

    # ── Sizes ──
    company_size = 13
    company_sub_size = 8
    title_size = 12
    label_size = 8
    value_size = 9
    detail_header_size = 7.5
    detail_font_size = 7.5
    row_height = 13
    section_gap = 6
    detail_row_height = 12

    # ── Gather settings ──
    company_name = str(settings.get("company_name", "") or "")
    addr1 = str(settings.get("address_line1", "") or "")
    addr2 = str(settings.get("address_line2", "") or "")
    phone = str(settings.get("telephone", "") or "")
    email = str(settings.get("email", "") or "")
    tax_id = str(settings.get("tax_id", "") or "")

    # ── Classify fields ──
    # Separate header fields, section labels, computed/totals, remarks
    header_fields = []
    section_labels = []
    computed_names = set()
    if computed_defs:
        for cd in computed_defs:
            computed_names.add(cd.get("name", ""))

    remarks_field = None
    for f in form_fields:
        ftype = f.get("type", "STRING")
        fid = f.get("id", "")
        if ftype == "SECTION":
            section_labels.append(f.get("label", ""))
            header_fields.append(f)
            continue
        if ftype == "DETAIL":
            continue
        if fid in ("status",):
            header_fields.append(f)
            continue
        if fid in ("remarks", "notes", "note", "remark"):
            remarks_field = f
            continue
        header_fields.append(f)

    page_num = [0]

    def _draw_page_footer():
        c.setFont(font_name, 7)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawRightString(page_w - margin, margin - 5,
                          f"Page {page_num[0]}")
        if doc_no:
            c.drawString(margin, margin - 5, doc_no)
        c.setFillColorRGB(0, 0, 0)

    def _new_page():
        nonlocal y
        _draw_page_footer()
        c.showPage()
        y = y_top
        _draw_header()

    def _check_space(needed):
        nonlocal y
        if y - needed < margin + 20:
            _new_page()

    def _draw_header():
        nonlocal y
        page_num[0] += 1

        # Company header
        if company_name:
            c.setFont(font_bold, company_size)
            c.drawString(margin, y - company_size, company_name)
            y -= company_size + 3

        # Address / contact line(s)
        c.setFont(font_name, company_sub_size)
        if addr1:
            c.drawString(margin, y - company_sub_size, addr1)
            y -= company_sub_size + 1
        if addr2:
            c.drawString(margin, y - company_sub_size, addr2)
            y -= company_sub_size + 1

        contact_parts = []
        if phone:
            contact_parts.append(f"Tel: {phone}")
        if email:
            contact_parts.append(f"Email: {email}")
        if tax_id:
            contact_parts.append(f"Tax ID: {tax_id}")
        if contact_parts:
            c.drawString(margin, y - company_sub_size,
                         "  |  ".join(contact_parts))
            y -= company_sub_size + 2

        # Separator line
        y -= 4
        c.setStrokeColorRGB(0.4, 0.4, 0.4)
        c.setLineWidth(0.5)
        c.line(margin, y, margin + usable_w, y)
        y -= 8

        # Document title — centered
        c.setFont(font_bold, title_size)
        c.drawCentredString(page_w / 2, y - title_size, title.upper())
        y -= title_size + 10

    def _fmt_value(val, field):
        """Format a value for display."""
        if val is None or val == "":
            return ""
        ftype = field.get("type", "STRING")
        if ftype == "FLOAT":
            try:
                num = float(val)
                if num == int(num) and abs(num) < 1e12:
                    return f"{int(num):,}"
                return f"{num:,.2f}"
            except (ValueError, TypeError):
                return str(val)
        if ftype == "DATE":
            s = str(val)
            if len(s) >= 10:
                return s[:10]
            return s
        return str(val)

    def _fmt_num(val):
        """Format a numeric value."""
        if val is None or val == "":
            return ""
        try:
            num = float(str(val).replace(",", ""))
            if num == int(num) and abs(num) < 1e12:
                return f"{int(num):,}"
            return f"{num:,.2f}"
        except (ValueError, TypeError):
            return str(val)

    # ── Start rendering ──
    y = y_top
    _draw_header()

    # ── Header fields — 2-column layout ──
    col_w = usable_w / 2 - 5
    label_w = 80  # points for label
    val_w = col_w - label_w

    cur_col = 0
    for f in header_fields:
        ftype = f.get("type", "STRING")
        fid = f.get("id", "")

        if ftype == "SECTION":
            # Section separator
            if cur_col > 0:
                y -= row_height
                cur_col = 0
            y -= section_gap
            _check_space(row_height + 4)
            c.setFont(font_bold, label_size)
            c.setFillColorRGB(0.2, 0.2, 0.5)
            c.drawString(margin, y - label_size, f.get("label", ""))
            c.setFillColorRGB(0, 0, 0)
            y -= label_size + 2
            c.setStrokeColorRGB(0.7, 0.7, 0.7)
            c.setLineWidth(0.3)
            c.line(margin, y, margin + usable_w, y)
            y -= 4
            continue

        # Skip fields with no value that are computed
        val = record.get(fid, "")
        label = f.get("label") or fid.replace("_", " ").title()
        span = f.get("span", 1)

        _check_space(row_height)

        if span >= 2 or fid in ("remarks", "notes"):
            # Full-width field
            if cur_col > 0:
                y -= row_height
                cur_col = 0
            x = margin
            c.setFont(font_bold, label_size)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            c.drawString(x, y - label_size, f"{label}:")
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_name, value_size)
            c.drawString(x + label_w, y - value_size, _fmt_value(val, f))
            y -= row_height
        else:
            # 2-column field
            if cur_col == 0:
                x = margin
            else:
                x = margin + col_w + 10

            c.setFont(font_bold, label_size)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            c.drawString(x, y - label_size, f"{label}:")
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_name, value_size)
            c.drawString(x + label_w, y - value_size, _fmt_value(val, f))

            cur_col += 1
            if cur_col >= 2:
                y -= row_height
                cur_col = 0

    if cur_col > 0:
        y -= row_height

    # ── Detail grids ──
    for dg in detail_grids:
        dg_label = dg.get("label", "")
        dg_columns = dg.get("columns", [])
        dg_data = dg.get("data", [])
        show_totals = dg.get("show_totals", False)

        if not dg_columns:
            continue

        y -= 10
        _check_space(detail_row_height * 3)

        # Detail section label
        if dg_label:
            c.setFont(font_bold, label_size + 1)
            c.setFillColorRGB(0.15, 0.15, 0.4)
            c.drawString(margin, y - label_size, dg_label)
            c.setFillColorRGB(0, 0, 0)
            y -= label_size + 6

        # Calculate column widths proportionally
        visible_cols = [col for col in dg_columns
                        if not col.get("hidden") and col.get("id") != "_parent_rowid"]
        if not visible_cols:
            continue

        # Add sequence number column
        seq_w = 20
        total_char = sum(col.get("width", 10) for col in visible_cols)
        avail_w = usable_w - seq_w
        col_ws = [max(25, (col.get("width", 10) / total_char) * avail_w)
                  for col in visible_cols]

        # Draw table header
        hdr_y = y
        hdr_h = detail_row_height + 2
        # Header background
        c.setFillColorRGB(0.15, 0.15, 0.4)
        c.rect(margin, hdr_y - hdr_h, usable_w, hdr_h, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(font_bold, detail_header_size)

        hx = margin + 2
        # Seq header
        c.drawString(hx, hdr_y - detail_row_height + 1, "#")
        hx += seq_w
        for ci, col in enumerate(visible_cols):
            label = col.get("label") or col["id"].replace("_", " ").title()
            cw = col_ws[ci]
            is_num = col.get("type", "STRING") == "FLOAT"
            if is_num:
                c.drawRightString(hx + cw - 2, hdr_y - detail_row_height + 1,
                                  label[:int(cw // 4)])
            else:
                c.drawString(hx, hdr_y - detail_row_height + 1,
                             label[:int(cw // 4)])
            hx += cw

        y -= hdr_h
        c.setFillColorRGB(0, 0, 0)

        # Data rows
        totals = {}
        for ri, row_data in enumerate(dg_data):
            _check_space(detail_row_height + 2)

            # Alternating row background
            if ri % 2 == 1:
                c.setFillColorRGB(0.95, 0.95, 0.95)
                c.rect(margin, y - detail_row_height, usable_w,
                       detail_row_height, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)

            c.setFont(font_name, detail_font_size)
            dx = margin + 2
            # Sequence number
            c.drawString(dx, y - detail_row_height + 2, str(ri + 1))
            dx += seq_w

            for ci, col in enumerate(visible_cols):
                cid = col["id"]
                cw = col_ws[ci]
                is_num = col.get("type", "STRING") == "FLOAT"
                raw = row_data.get(cid, "")

                if is_num:
                    disp = _fmt_num(raw)
                    c.drawRightString(dx + cw - 2, y - detail_row_height + 2,
                                      disp[:int(cw // 4)])
                    # Accumulate totals
                    try:
                        totals[cid] = totals.get(cid, 0) + float(
                            str(raw).replace(",", "") or 0)
                    except (ValueError, TypeError):
                        pass
                else:
                    disp = str(raw or "")
                    c.drawString(dx, y - detail_row_height + 2,
                                 disp[:int(cw // 4)])
                dx += cw

            y -= detail_row_height

        # Totals row
        if show_totals and totals:
            _check_space(detail_row_height + 4)
            # Total background
            c.setFillColorRGB(0.85, 0.85, 0.7)
            c.rect(margin, y - detail_row_height, usable_w,
                   detail_row_height, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_bold, detail_font_size)

            dx = margin + 2
            c.drawString(dx, y - detail_row_height + 2, "TOTAL")
            dx += seq_w

            for ci, col in enumerate(visible_cols):
                cid = col["id"]
                cw = col_ws[ci]
                is_num = col.get("type", "STRING") == "FLOAT"
                if is_num and cid in totals:
                    disp = _fmt_num(totals[cid])
                    c.drawRightString(dx + cw - 2, y - detail_row_height + 2,
                                      disp)
                dx += cw

            y -= detail_row_height

        # Bottom border of table
        c.setStrokeColorRGB(0.3, 0.3, 0.3)
        c.setLineWidth(0.5)
        c.line(margin, y, margin + usable_w, y)

    # ── Computed totals (subtotal, tax, grand_total) ──
    if computed_defs:
        y -= 12
        _check_space(row_height * len(computed_defs) + 10)
        totals_x = margin + usable_w - 200
        for cd in computed_defs:
            cname = cd.get("name", "")
            val = record.get(cname, "")
            if val is None or val == "":
                continue
            label = cname.replace("_", " ").title()
            c.setFont(font_bold, value_size)
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.drawRightString(totals_x + 80, y - value_size, f"{label}:")
            c.setFont(font_name, value_size + 1)
            c.setFillColorRGB(0, 0, 0)
            c.drawRightString(totals_x + 180, y - value_size, _fmt_num(val))
            y -= row_height

    # ── Remarks ──
    if remarks_field:
        rval = str(record.get(remarks_field["id"], "") or "")
        if rval:
            y -= 10
            _check_space(row_height * 2)
            c.setFont(font_bold, label_size)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            c.drawString(margin, y - label_size, "Remarks:")
            c.setFillColorRGB(0, 0, 0)
            y -= label_size + 2
            c.setFont(font_name, value_size)
            # Word-wrap remarks
            words = rval.split()
            line = ""
            for word in words:
                test = f"{line} {word}".strip()
                if c.stringWidth(test, font_name, value_size) > usable_w - 10:
                    c.drawString(margin, y - value_size, line)
                    y -= value_size + 2
                    _check_space(value_size + 4)
                    line = word
                else:
                    line = test
            if line:
                c.drawString(margin, y - value_size, line)
                y -= value_size + 2

    # ── Signature lines ──
    y -= 30
    _check_space(40)
    c.setStrokeColorRGB(0.4, 0.4, 0.4)
    c.setLineWidth(0.5)
    sig_w = usable_w / 3 - 20
    sig_labels = ["Prepared By", "Checked By", "Approved By"]
    for si, sl in enumerate(sig_labels):
        sx = margin + si * (sig_w + 20)
        c.line(sx, y, sx + sig_w, y)
        c.setFont(font_name, 7)
        c.drawCentredString(sx + sig_w / 2, y - 10, sl)

    # Final page footer
    _draw_page_footer()

    try:
        c.save()
    except Exception as e:
        return f"PDF save failed: {e}"

    # Auto-open in background (avoid stealing terminal focus)
    try:
        import subprocess
        subprocess.Popen(['cmd', '/c', 'start', '', pdf_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception:
        try:
            os.startfile(pdf_path)
        except Exception:
            pass

    return pdf_path
