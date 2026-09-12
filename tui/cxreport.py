"""ReportGrid — read-only report viewer with control-break subtotals.

Displays a listing with group headers, subtotal rows, and grand totals.
Supports view switching: F2 listing, F3 summary, F6 crosstab, F4 re-run.
F10 exports the current view to PDF.
"""
import curses
import os
import time
from datetime import datetime
from typing import List, Dict, Optional

from dsl_lib.report_engine import apply_runtime_filters, build_listing
from tui.cxgrid import GridColumn, _numeric_format_number
from tui.statusline import StatusLine

# Color pair IDs (after Grid's 20-30)
CLR_TITLE = 40
CLR_RPT_HEADER = 41
CLR_RPT_DATA = 42
CLR_RPT_ALT = 43
CLR_RPT_SELECTED = 44
CLR_RPT_GROUP_HDR = 45
CLR_RPT_SUBTOTAL = 46
CLR_RPT_GRAND = 47
CLR_RPT_STATUS = 48
CLR_RPT_BORDER = 49

_colors_ready = False


def _init_report_colors():
    # Colors initialized centrally by tui.themes.init_colors()
    global _colors_ready
    _colors_ready = True


def _safe_addstr(stdscr, y, x, text, attr=curses.A_NORMAL):
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or y >= max_y or x < 0 or x >= max_x:
        return
    text = text[:max_x - x]
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _fmt_num(val):
    """Format a numeric value for display."""
    if val is None or val == "":
        return ""
    try:
        n = float(str(val).replace(",", ""))
        return _numeric_format_number(n)
    except (ValueError, TypeError):
        return str(val)


class ReportGrid:
    """Read-only grid with control-break rendering."""

    def __init__(self, stdscr, columns: List[dict], rows: List[dict],
                 groups: Optional[List[dict]] = None,
                 totals: Optional[dict] = None,
                 sum_fields: Optional[set] = None,
                 title: str = "", subtitle: str = "",
                 has_crosstab: bool = False,
                 filter_state: Optional[Dict[str, str]] = None,
                 page_elements: Optional[Dict[str, bool]] = None):
        """
        Args:
            stdscr: curses window
            columns: list of {"id", "label", "width", "type", "sum"}
            rows: raw row list before runtime filters / control-break rendering
            title: report title
            subtitle: optional subtitle
            has_crosstab: whether crosstab view is available
        """
        self.stdscr = stdscr
        self.columns = columns
        self.all_rows = list(rows or [])
        self.groups = groups or []
        self.totals = totals
        self.sum_fields = set(sum_fields or set())
        self.title = title
        self.subtitle = subtitle
        self.has_crosstab = has_crosstab
        self.page_elements = self._normalize_page_elements(page_elements)

        self.row = 0
        self.scroll_offset = 0
        self.visible_rows = 0
        self.h_scroll = 0
        self.show_filter_row = False
        self.filter_mode = False
        self.filter_col = 0
        self.filter_edit_buffer = ""
        self.filter_state = {
            c["id"]: (filter_state or {}).get(c["id"], "")
            for c in self.columns if c.get("id")
        }
        self.filtered_rows = list(self.all_rows)
        self.display_list = []
        self._status_msg = None
        self._status_time = 0.0

        _init_report_colors()
        self.status_line = StatusLine(stdscr, color_pair=CLR_RPT_STATUS)
        self._rebuild_view()

    def run(self) -> str:
        """Main loop. Returns action: 'exit', 'summary', 'crosstab', 'rerun'."""
        self.stdscr.keypad(True)
        curses.curs_set(0)

        while True:
            self._update_dims()
            self.stdscr.erase()
            self._draw_title()
            self._draw_header()
            self._draw_data()
            self._draw_status()
            self.stdscr.refresh()

            key = self.stdscr.getch()

            if self.filter_mode:
                self._handle_filter_input(key)
                continue

            if key == curses.KEY_UP:
                if self.show_filter_row and self.row == 0:
                    self._enter_filter_mode(self.filter_col)
                elif self.row > 0:
                    self.row -= 1
                    self._ensure_visible()
            elif key == curses.KEY_DOWN:
                if self.row < len(self.display_list) - 1:
                    self.row += 1
                    self._ensure_visible()
            elif key == curses.KEY_PPAGE:
                self.row = max(0, self.row - self.visible_rows)
                self._ensure_visible()
            elif key == curses.KEY_NPAGE:
                self.row = min(len(self.display_list) - 1,
                               self.row + self.visible_rows)
                self._ensure_visible()
            elif key == curses.KEY_HOME:
                self.row = 0
                self._ensure_visible()
            elif key == curses.KEY_END:
                self.row = max(0, len(self.display_list) - 1)
                self._ensure_visible()
            elif key == curses.KEY_LEFT:
                if self.h_scroll > 0:
                    self.h_scroll -= 1
            elif key == curses.KEY_RIGHT:
                self.h_scroll += 1
            elif key == curses.KEY_F7:
                self._toggle_filter_row()
            elif key == curses.KEY_F3:
                return "summary"
            elif key == curses.KEY_F6:
                return "crosstab"
            elif key == curses.KEY_F4:
                return "rerun"
            elif key == curses.KEY_F10:
                self._export_pdf()
            elif key == 12:  # Ctrl+L
                self.clear_filters()
            elif key == 27:  # ESC
                return "exit"
            elif key == curses.KEY_RESIZE:
                continue

    def _update_dims(self):
        self.max_y, self.max_x = self.stdscr.getmaxyx()
        # Title(1) + subtitle(0-1) + header(1) + filter(0-1) + sep(1)
        top = 3 + (1 if self.subtitle else 0) + (1 if self.show_filter_row else 0)
        self.data_y_start = top
        self.visible_rows = max(1, self.max_y - top - 1)

    def _ensure_visible(self):
        if self.row < self.scroll_offset:
            self.scroll_offset = self.row
        elif self.row >= self.scroll_offset + self.visible_rows:
            self.scroll_offset = self.row - self.visible_rows + 1

    def _get_visible_cols(self):
        """Return list of (col_def, x_pos) for visible columns."""
        result = []
        x = 4  # left margin + indicator
        skip = self.h_scroll
        for col in self.columns:
            if skip > 0:
                skip -= 1
                continue
            if x + col["width"] > self.max_x:
                # Add partial column if at least 4 chars
                remaining = self.max_x - x
                if remaining >= 4:
                    partial = dict(col)
                    partial["width"] = remaining
                    result.append((partial, x))
                break
            result.append((col, x))
            x += col["width"] + 1
        return result

    def _rebuild_view(self):
        self.filtered_rows = apply_runtime_filters(
            self.all_rows, self.filter_state, self.columns)
        self.display_list = build_listing(
            self.filtered_rows, self.groups, self.totals, self.sum_fields)
        if self.row >= len(self.display_list):
            self.row = max(0, len(self.display_list) - 1)
        self._ensure_visible()

    def _normalize_page_elements(self, page_elements: Optional[Dict[str, bool]]):
        defaults = {
            "title": True,
            "subtitle": True,
            "page_no": True,
            "printed_at": True,
            "row_count": True,
        }
        if isinstance(page_elements, dict):
            defaults.update({k: bool(v) for k, v in page_elements.items()})
        return defaults

    def get_filter_state(self) -> Dict[str, str]:
        return {k: v for k, v in self.filter_state.items() if (v or "").strip()}

    def get_filtered_rows(self) -> List[dict]:
        return list(self.filtered_rows)

    def clear_filters(self):
        for col_id in list(self.filter_state.keys()):
            self.filter_state[col_id] = ""
        self.filter_mode = False
        self.filter_edit_buffer = ""
        self._rebuild_view()
        self.set_status("Report filters cleared")

    def _enter_filter_mode(self, col_idx: Optional[int] = None):
        if col_idx is not None:
            self.filter_col = max(0, min(col_idx, len(self.columns) - 1))
        self.filter_mode = True
        col_id = self.columns[self.filter_col]["id"]
        self.filter_edit_buffer = self.filter_state.get(col_id, "")
        self._scroll_to_column(self.filter_col)
        self.set_status("Filter: use = != <> > < >= <= or BETWEEN a AND b")

    def _exit_filter_mode(self, save: bool = True):
        if save and 0 <= self.filter_col < len(self.columns):
            col_id = self.columns[self.filter_col]["id"]
            self.filter_state[col_id] = self.filter_edit_buffer
            self._rebuild_view()
        self.filter_mode = False
        self.filter_edit_buffer = ""

    def _next_visible_col(self, cur: int, step: int) -> int:
        total = len(self.columns)
        if total <= 0:
            return 0
        return (cur + step) % total

    def _scroll_to_column(self, col_idx: int):
        if col_idx < self.h_scroll:
            self.h_scroll = col_idx
            return
        while True:
            visible_ids = [col["id"] for col, _ in self._get_visible_cols()]
            target_id = self.columns[col_idx]["id"]
            if target_id in visible_ids:
                return
            self.h_scroll += 1
            if self.h_scroll >= len(self.columns) - 1:
                return

    def _toggle_filter_row(self):
        if self.filter_mode:
            self._exit_filter_mode(save=True)
        self.show_filter_row = not self.show_filter_row
        if self.show_filter_row:
            self._enter_filter_mode(self.filter_col)
        else:
            self.set_status("Filter bar hidden — active filters kept")

    def _handle_filter_input(self, key):
        if key == 27:
            self._exit_filter_mode(save=False)
            return
        if key in (curses.KEY_ENTER, 10, 13, curses.PADENTER):
            self._exit_filter_mode(save=True)
            return
        if key == 9:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            self._enter_filter_mode(self._next_visible_col(cur, 1))
            return
        if key == 353:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            self._enter_filter_mode(self._next_visible_col(cur, -1))
            return
        if key == 12:
            cur = self.filter_col
            self.clear_filters()
            self._enter_filter_mode(cur)
            return
        if key == curses.KEY_DC:
            self.filter_edit_buffer = ""
            col_id = self.columns[self.filter_col]["id"]
            self.filter_state[col_id] = ""
            self._rebuild_view()
            return
        if key in (curses.KEY_BACKSPACE, 8, 127):
            if self.filter_edit_buffer:
                self.filter_edit_buffer = self.filter_edit_buffer[:-1]
                col_id = self.columns[self.filter_col]["id"]
                self.filter_state[col_id] = self.filter_edit_buffer
                self._rebuild_view()
            return
        if key == curses.KEY_LEFT:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            self._enter_filter_mode(self._next_visible_col(cur, -1))
            return
        if key == curses.KEY_RIGHT:
            cur = self.filter_col
            self._exit_filter_mode(save=True)
            self._enter_filter_mode(self._next_visible_col(cur, 1))
            return
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            self._exit_filter_mode(save=True)
            return
        if 32 <= key <= 126:
            self.filter_edit_buffer += chr(key)
            col_id = self.columns[self.filter_col]["id"]
            self.filter_state[col_id] = self.filter_edit_buffer
            self._rebuild_view()

    # ── Drawing ─────────────────────────────────────────────────

    def _draw_title(self):
        attr = curses.color_pair(CLR_TITLE) | curses.A_BOLD
        title_line = f"  {self.title}  "
        _safe_addstr(self.stdscr, 0, 0, " " * self.max_x, attr)
        _safe_addstr(self.stdscr, 0, 0, title_line, attr)
        if self.subtitle:
            _safe_addstr(self.stdscr, 1, 0, " " * self.max_x,
                         curses.color_pair(CLR_TITLE))
            _safe_addstr(self.stdscr, 1, 2, self.subtitle,
                         curses.color_pair(CLR_TITLE))

    def _draw_header(self):
        """Draw column headers."""
        y = 1 + (1 if self.subtitle else 0)
        vis_cols = self._get_visible_cols()
        attr = curses.color_pair(CLR_RPT_HEADER) | curses.A_BOLD
        # Header background
        _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)
        _safe_addstr(self.stdscr, y, 1, "  ", attr)  # indicator space
        for col, x in vis_cols:
            label = col["label"][:col["width"]]
            if col.get("type") == "FLOAT":
                label = label.rjust(col["width"])
            else:
                label = label.ljust(col["width"])
            _safe_addstr(self.stdscr, y, x, label, attr)
        if self.show_filter_row:
            self._draw_filter_row(y + 1, vis_cols)
        # Separator line
        sep_y = y + 1 + (1 if self.show_filter_row else 0)
        border_attr = curses.color_pair(CLR_RPT_BORDER)
        _safe_addstr(self.stdscr, sep_y, 0, "─" * self.max_x, border_attr)

    def _draw_filter_row(self, y, vis_cols):
        attr = curses.color_pair(CLR_RPT_DATA) | curses.A_DIM
        _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)
        _safe_addstr(self.stdscr, y, 1, "F ", attr | curses.A_BOLD)
        for idx, (col, x) in enumerate(vis_cols):
            cid = col["id"]
            active = self.filter_mode and self.columns[self.filter_col]["id"] == cid
            text = (self.filter_edit_buffer if active
                    else self.filter_state.get(cid, ""))
            disp = text[:col["width"]].ljust(col["width"])
            cell_attr = (curses.color_pair(CLR_RPT_SELECTED) | curses.A_BOLD
                         if active else attr)
            _safe_addstr(self.stdscr, y, x, disp, cell_attr)

    def _draw_data(self):
        """Draw the display list rows."""
        vis_cols = self._get_visible_cols()
        total = len(self.display_list)
        if total == 0:
            _safe_addstr(self.stdscr, self.data_y_start, 4,
                         "No rows match the active report filters", curses.A_DIM)
            return

        end = min(self.scroll_offset + self.visible_rows, total)
        for vi in range(self.scroll_offset, end):
            y = self.data_y_start + (vi - self.scroll_offset)
            if y >= self.max_y - 1:
                break
            entry = self.display_list[vi]
            tag = entry[0]
            is_current = (vi == self.row)

            if tag == "data":
                self._draw_data_row(y, entry[1], vis_cols, is_current, vi)
            elif tag == "group_header":
                self._draw_group_header(y, entry[1], is_current)
            elif tag == "group_summary":
                self._draw_summary_row(y, entry[1], entry[2],
                                       vis_cols, is_current, is_grand=False)
            elif tag == "grand_total":
                self._draw_summary_row(y, entry[1], "Grand Total",
                                       vis_cols, is_current, is_grand=True)

    def _draw_data_row(self, y, row_data: dict, vis_cols, is_current, vi):
        """Draw a normal data row."""
        is_alt = (vi % 2 == 1)
        if is_current:
            attr = curses.color_pair(CLR_RPT_SELECTED)
        elif is_alt:
            attr = curses.color_pair(CLR_RPT_ALT) | curses.A_DIM
        else:
            attr = curses.color_pair(CLR_RPT_DATA)

        # Clear line
        _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)

        # Indicator
        if is_current:
            _safe_addstr(self.stdscr, y, 1, "\u25ba ",
                         curses.color_pair(CLR_RPT_SELECTED) | curses.A_BOLD)
        else:
            _safe_addstr(self.stdscr, y, 1, "  ", attr)

        for col, x in vis_cols:
            val = row_data.get(col["id"], "")
            w = col["width"]
            if col.get("type") == "FLOAT":
                disp = _fmt_num(val).rjust(w)
            else:
                disp = str(val or "")[:w].ljust(w)
            _safe_addstr(self.stdscr, y, x, disp, attr)

    def _draw_group_header(self, y, label: str, is_current):
        """Draw a group header bar."""
        attr = curses.color_pair(CLR_RPT_GROUP_HDR) | curses.A_BOLD
        _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)
        text = f"  {label}  "
        _safe_addstr(self.stdscr, y, 1, text, attr)

    def _draw_summary_row(self, y, agg: dict, label: str,
                          vis_cols, is_current, is_grand: bool):
        """Draw a subtotal or grand total row."""
        if is_grand:
            attr = curses.color_pair(CLR_RPT_GRAND) | curses.A_BOLD
        else:
            attr = curses.color_pair(CLR_RPT_SUBTOTAL) | curses.A_BOLD

        _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)

        # Put label in first text column
        if vis_cols:
            first_col, first_x = vis_cols[0]
            _safe_addstr(self.stdscr, y, first_x,
                         label[:first_col["width"]].ljust(first_col["width"]),
                         attr)

        # Put aggregated values in their columns
        for col, x in vis_cols:
            cid = col["id"]
            if cid in agg:
                val = agg[cid]
                disp = _fmt_num(val).rjust(col["width"])
                _safe_addstr(self.stdscr, y, x, disp, attr)

    def set_status(self, msg: str):
        """Show a temporary status message."""
        self._status_msg = msg
        self._status_time = time.time()

    def _export_pdf(self):
        """Export the current report to PDF using reportlab."""
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.units import mm
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            self.set_status("reportlab not installed — run: pip install reportlab")
            return

        # Register Tahoma for Unicode support
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

        # Output path
        title = self.title or 'Report'
        safe_name = ''.join(
            c if c.isalnum() or c in (' ', '-', '_') else '_'
            for c in title).strip()
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        if not os.path.isdir(desktop):
            desktop = os.path.expanduser('~')
        pdf_path = os.path.join(desktop, f'{safe_name}.pdf')
        counter = 1
        base_path = pdf_path
        while os.path.exists(pdf_path):
            name_part = base_path.rsplit('.', 1)[0]
            pdf_path = f'{name_part}_{counter}.pdf'
            counter += 1

        try:
            page_w, page_h = landscape(A4)
            c = canvas.Canvas(pdf_path, pagesize=landscape(A4))

            margin = 15 * mm
            usable_w = page_w - 2 * margin
            y_top = page_h - margin
            printed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            page_no = 1

            # Column widths proportional to character widths
            total_char_w = sum(col["width"] for col in self.columns) or 1
            col_widths = [(col["width"] / total_char_w) * usable_w
                          for col in self.columns]
            min_w = 25
            for i in range(len(col_widths)):
                if col_widths[i] < min_w:
                    col_widths[i] = min_w

            font_size = 8
            header_size = 9
            title_size = 12
            row_height = font_size + 5
            header_height = header_size + 6
            cols = self.columns

            def _new_page():
                nonlocal y, page_no
                c.showPage()
                page_no += 1
                y = y_top
                _draw_page_header()

            def _draw_page_header():
                nonlocal y
                right_parts = []
                if self.page_elements.get("page_no", True):
                    right_parts.append(f"Page {page_no}")
                if self.page_elements.get("printed_at", True):
                    right_parts.append(printed_at)
                if self.page_elements.get("row_count", True):
                    right_parts.append(f"Rows {len(self.filtered_rows)}")
                if self.page_elements.get("title", True):
                    c.setFont(font_bold, title_size)
                    c.drawString(margin, y - title_size, title)
                if right_parts:
                    c.setFont(font_name, 8)
                    c.drawRightString(page_w - margin, y - 8,
                                      "   ".join(right_parts))
                if self.page_elements.get("title", True):
                    y -= title_size + 4
                else:
                    y -= 12
                if self.subtitle and self.page_elements.get("subtitle", True):
                    c.setFont(font_name, 8)
                    c.drawString(margin, y - 8, self.subtitle)
                    y -= 12
                y -= 4
                _draw_header_row()

            def _draw_header_row():
                nonlocal y
                c.setFont(font_bold, header_size)
                c.setFillColorRGB(0.15, 0.15, 0.4)
                c.rect(margin, y - header_height, usable_w,
                       header_height, fill=1)
                c.setFillColorRGB(1, 1, 1)
                x = margin + 2
                for i, col in enumerate(cols):
                    text = str(col["label"])[:30]
                    c.drawString(x, y - header_height + 3, text)
                    x += col_widths[i]
                c.setFillColorRGB(0, 0, 0)
                y -= header_height + 1

            def _draw_data_row(row_data, bold=False):
                nonlocal y
                if y - row_height < margin:
                    _new_page()
                fn = font_bold if bold else font_name
                c.setFont(fn, font_size)
                x = margin + 2
                for i, col in enumerate(cols):
                    val = row_data.get(col["id"], '')
                    if val is None:
                        val = ''
                    text = str(val)
                    if col.get("type") == "FLOAT":
                        try:
                            text = f'{float(val):,.2f}' if val not in (None, '') else ''
                        except (ValueError, TypeError):
                            pass
                        tw = c.stringWidth(text, fn, font_size)
                        c.drawString(x + col_widths[i] - tw - 4,
                                     y - font_size, text)
                    else:
                        max_chars = max(3, int(col_widths[i] / (font_size * 0.5)))
                        if len(text) > max_chars:
                            text = text[:max_chars - 1] + '\u2026'
                        c.drawString(x, y - font_size, text)
                    x += col_widths[i]
                y -= row_height

            def _draw_summary_row(agg, label, is_grand=False):
                nonlocal y
                if y - row_height - 2 < margin:
                    _new_page()
                if is_grand:
                    c.setFillColorRGB(0.15, 0.15, 0.5)
                else:
                    c.setFillColorRGB(0.85, 0.85, 0.7)
                c.rect(margin, y - row_height, usable_w, row_height, fill=1)
                fn = font_bold
                c.setFont(fn, font_size)
                if is_grand:
                    c.setFillColorRGB(1, 1, 1)
                else:
                    c.setFillColorRGB(0, 0, 0)
                disp = 'Grand Total' if is_grand else f'Subtotal: {label}'
                c.drawString(margin + 2, y - font_size, disp[:40])
                x = margin + 2
                for i, col in enumerate(cols):
                    val = agg.get(col["id"], '')
                    if val not in (None, ''):
                        text = str(val)
                        try:
                            text = f'{float(val):,.2f}'
                        except (ValueError, TypeError):
                            pass
                        tw = c.stringWidth(text, fn, font_size)
                        c.drawString(x + col_widths[i] - tw - 4,
                                     y - font_size, text)
                    x += col_widths[i]
                c.setFillColorRGB(0, 0, 0)
                y -= row_height + 1

            def _draw_group_header(label):
                nonlocal y
                if y - row_height < margin:
                    _new_page()
                c.setFillColorRGB(0.6, 0.3, 0.6)
                c.rect(margin, y - row_height, usable_w, row_height, fill=1)
                c.setFillColorRGB(1, 1, 1)
                c.setFont(font_bold, font_size)
                c.drawString(margin + 4, y - font_size,
                             f'\u25ba {label}'[:60])
                c.setFillColorRGB(0, 0, 0)
                y -= row_height

            # Render
            y = y_top
            _draw_page_header()

            for entry in self.display_list:
                tag = entry[0]
                if tag == 'data':
                    _draw_data_row(entry[1])
                elif tag == 'group_header':
                    _draw_group_header(entry[1])
                elif tag == 'group_summary':
                    _draw_summary_row(entry[1], entry[2])
                elif tag == 'grand_total':
                    _draw_summary_row(entry[1], '', is_grand=True)

            c.save()
            self.set_status(f"PDF saved: {pdf_path}")

            try:
                os.startfile(pdf_path)
            except Exception:
                pass

        except Exception as exc:
            self.set_status(f"PDF export failed: {exc}")

    def _draw_status(self):
        """Draw status bar at bottom using StatusLine."""
        self.status_line.set_page("report")

        # Show temporary status message if recent
        if hasattr(self, '_status_msg') and self._status_msg:
            elapsed = time.time() - self._status_time
            if elapsed < 5.0:
                self.status_line.set_info(self._status_msg)
                self.status_line.draw()
                return
            self._status_msg = None

        total = len(self.display_list)
        if total > 0:
            pos = f"Row {self.row + 1}/{total}"
        else:
            pos = "No data"
        if self.get_filter_state():
            pos += f"  |  Filtered {len(self.filtered_rows)}/{len(self.all_rows)} rows"
        else:
            pos += f"  |  {len(self.filtered_rows)} rows"
        self.status_line.set_info(pos)
        self.status_line.draw()


class SummaryGrid:
    """Simple read-only grid for summary/crosstab views."""

    def __init__(self, stdscr, col_names: List[str], rows: List[dict],
                 title: str = "", subtitle: str = "",
                 numeric_cols: set = None):
        self.stdscr = stdscr
        self.col_names = col_names
        self.rows = rows
        self.title = title
        self.subtitle = subtitle
        self.numeric_cols = numeric_cols or set()

        self.row = 0
        self.scroll_offset = 0
        self.visible_rows = 0
        self.h_scroll = 0

        # Auto-detect column widths
        self.col_widths = {}
        for cn in col_names:
            w = len(cn)
            for r in rows:
                v = str(r.get(cn, ""))
                if cn in self.numeric_cols:
                    v = _fmt_num(r.get(cn, ""))
                w = max(w, len(v))
            self.col_widths[cn] = min(w + 2, 20)

        _init_report_colors()
        self.status_line = StatusLine(stdscr, color_pair=CLR_RPT_STATUS)

    def run(self) -> str:
        """Main loop. Returns 'listing', 'exit', 'rerun'."""
        self.stdscr.keypad(True)
        curses.curs_set(0)

        while True:
            self.max_y, self.max_x = self.stdscr.getmaxyx()
            top = 3 + (1 if self.subtitle else 0)
            self.data_y_start = top
            self.visible_rows = max(1, self.max_y - top - 1)

            self.stdscr.erase()
            self._draw()
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key == curses.KEY_UP and self.row > 0:
                self.row -= 1
                self._ensure_visible()
            elif key == curses.KEY_DOWN and self.row < len(self.rows) - 1:
                self.row += 1
                self._ensure_visible()
            elif key == curses.KEY_PPAGE:
                self.row = max(0, self.row - self.visible_rows)
                self._ensure_visible()
            elif key == curses.KEY_NPAGE:
                self.row = min(len(self.rows) - 1, self.row + self.visible_rows)
                self._ensure_visible()
            elif key == curses.KEY_LEFT and self.h_scroll > 0:
                self.h_scroll -= 1
            elif key == curses.KEY_RIGHT:
                self.h_scroll += 1
            elif key == curses.KEY_F2:
                return "listing"
            elif key == curses.KEY_F4:
                return "rerun"
            elif key == 27:
                return "exit"
            elif key == curses.KEY_RESIZE:
                continue

    def _ensure_visible(self):
        if self.row < self.scroll_offset:
            self.scroll_offset = self.row
        elif self.row >= self.scroll_offset + self.visible_rows:
            self.scroll_offset = self.row - self.visible_rows + 1

    def _draw(self):
        # Title
        attr = curses.color_pair(CLR_TITLE) | curses.A_BOLD
        _safe_addstr(self.stdscr, 0, 0, " " * self.max_x, attr)
        _safe_addstr(self.stdscr, 0, 0, f"  {self.title}  ", attr)
        if self.subtitle:
            _safe_addstr(self.stdscr, 1, 0, " " * self.max_x,
                         curses.color_pair(CLR_TITLE))
            _safe_addstr(self.stdscr, 1, 2, self.subtitle,
                         curses.color_pair(CLR_TITLE))

        # Get visible columns
        vis_cols = self._vis_cols()

        # Header
        hy = 1 + (1 if self.subtitle else 0)
        hattr = curses.color_pair(CLR_RPT_HEADER) | curses.A_BOLD
        _safe_addstr(self.stdscr, hy, 0, " " * self.max_x, hattr)
        for cn, x, w in vis_cols:
            label = cn[:w]
            if cn in self.numeric_cols:
                label = label.rjust(w)
            else:
                label = label.ljust(w)
            _safe_addstr(self.stdscr, hy, x, label, hattr)

        # Separator
        sep_y = hy + 1
        _safe_addstr(self.stdscr, sep_y, 0, "─" * self.max_x,
                     curses.color_pair(CLR_RPT_BORDER))

        # Data rows
        total = len(self.rows)
        end = min(self.scroll_offset + self.visible_rows, total)
        for vi in range(self.scroll_offset, end):
            y = self.data_y_start + (vi - self.scroll_offset)
            if y >= self.max_y - 1:
                break
            row = self.rows[vi]
            is_current = (vi == self.row)
            is_grand = (str(row.get("_group", row.get("_row", ""))).upper() == "TOTAL")

            if is_grand:
                attr = curses.color_pair(CLR_RPT_GRAND) | curses.A_BOLD
            elif is_current:
                attr = curses.color_pair(CLR_RPT_SELECTED)
            elif vi % 2 == 1:
                attr = curses.color_pair(CLR_RPT_ALT) | curses.A_DIM
            else:
                attr = curses.color_pair(CLR_RPT_DATA)

            _safe_addstr(self.stdscr, y, 0, " " * self.max_x, attr)
            if is_current and not is_grand:
                _safe_addstr(self.stdscr, y, 1, "\u25ba ",
                             curses.color_pair(CLR_RPT_SELECTED) | curses.A_BOLD)

            for cn, x, w in vis_cols:
                val = row.get(cn, "")
                if cn in self.numeric_cols:
                    disp = _fmt_num(val).rjust(w)
                else:
                    disp = str(val or "")[:w].ljust(w)
                _safe_addstr(self.stdscr, y, x, disp, attr)

        # Status
        pos = f"Row {self.row + 1}/{total}" if total > 0 else "No data"
        self.status_line.set_page("summary")
        self.status_line.set_info(pos)
        self.status_line.draw()

    def _vis_cols(self):
        """Return list of (col_name, x, width)."""
        result = []
        x = 4
        skip = self.h_scroll
        for cn in self.col_names:
            w = self.col_widths.get(cn, 12)
            if skip > 0:
                skip -= 1
                continue
            if x + w > self.max_x:
                remaining = self.max_x - x
                if remaining >= 4:
                    result.append((cn, x, remaining))
                break
            result.append((cn, x, w))
            x += w + 1
        return result
