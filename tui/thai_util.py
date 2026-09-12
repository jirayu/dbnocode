"""
Thai-aware Curses extension.

Two problems this module solves for Thai (and other combining / wide scripts):

1. Crooked line drawing
   The CurvedBoxUI box chars are Unicode (╭ ╮ ╰ ╯ ─ │). If the process locale
   is not a UTF-8 locale, curses emits them byte-by-byte and the terminal renders
   garbage / broken corners. Fix: set a UTF-8 locale before touching curses
   (see init_thai_locale).

2. Crooked text / wrong cursor position with Thai
   Thai is written with base consonants plus COMBINING vowels/signs (e.g. ส + ั
   = สั). A combining mark occupies zero display columns, but Python's len()
   counts it as one code point. If you place text or a cursor using len(), the
   column math drifts and later lines look shifted. Fix: measure with disp_width
   (combining marks = 0, East-Asian wide = 2, everything else = 1) and read keys
   with get_wch() (one Unicode code point per call) instead of getch().

This is a thin, separated layer you can drop in front of any curses window.
"""
import curses
import locale
import os
import unicodedata

try:
    from wcwidth import wcwidth as _wcwidth
except Exception:  # pragma: no cover - optional dependency
    _wcwidth = None


def init_thai_locale():
    """Force a UTF-8 locale so Unicode box drawing and Thai render correctly.

    Returns the locale string that worked, or None. On Windows it also flips the
    console to codepage 65001 as a safety net (windows-curses usually handles
    this itself, but a non-UTF-8 system codepage is the usual cause of crooked
    box corners).
    """
    os.environ.setdefault("PYTHONUTF8", "1")
    if os.name == "nt":
        try:
            os.system("chcp 65001 >nul")
        except Exception:
            pass
    for loc in ("", "C.UTF-8", "en_US.UTF-8", "th_TH.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, loc)
            return loc
        except locale.Error:
            continue
    return None


def disp_width(s):
    """Display (column) width of a string, ignoring combining marks.

    Uses wcwidth when available (it correctly reports 0 for Thai non-spacing
    vowels/signs such as U+0E31/U+0E48, whose canonical combining class is
    sometimes 0). Falls back to a unicodedata heuristic otherwise.
    """
    w = 0
    for ch in s:
        if _wcwidth is not None:
            c = _wcwidth(ch)
            w += c if c > 0 else 0
            continue
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def _trim_to_width(s, width):
    """Return the tail of s that fits in `width` display columns, keeping whole
    code points (so a base is never separated from its combining mark)."""
    if disp_width(s) <= width:
        return s
    out = []
    for ch in reversed(s):
        if disp_width(ch) + disp_width("".join(out)) > width:
            break
        out.append(ch)
    return "".join(reversed(out))


class ThaiWin:
    """Thai-safe wrapper that EXTENDS a curses window with display-width-aware
    text placement and UTF-8 line input. Compose it over any curses window:

        ui = ThaiWin(curses.newwin(h, w, y, x))

    All cursor math inside uses disp_width, so Thai combining marks never shift
    the column position.
    """

    def __init__(self, win, field_attr=0):
        self.win = win
        self.field_attr = field_attr

    def __getattr__(self, name):
        return getattr(self.win, name)

    def addstr(self, y, x, text, attr=0):
        """Place text; never raises if it touches the right/bottom edge."""
        max_y, max_x = self.win.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        avail = max_x - x
        if avail <= 0:
            return
        shown = _trim_to_width(text, avail)
        try:
            self.win.addstr(y, x, shown, attr or self.field_attr)
        except curses.error:
            pass

    def box(self, y, x, h, w, title="", attr=0, use_acs=False):
        """Draw a straight rounded box. Corners are redrawn AFTER the title so a
        long title can never clobber them.

        Uses Unicode curved chars by default; if the terminal can't render them
        (curses.error), it falls back to ACS line chars so the border is always
        straight.
        """
        if use_acs:
            tl, tr, bl, br = (curses.ACS_ULCORNER, curses.ACS_URCORNER,
                              curses.ACS_LLCORNER, curses.ACS_LRCORNER)
            hline, vline = curses.ACS_HLINE, curses.ACS_VLINE
        else:
            tl, tr, bl, br = "\u256d", "\u256e", "\u2570", "\u256f"
            hline, vline = "\u2500", "\u2502"
        try:
            self.addstr(y, x, tl, attr)
            self.addstr(y, x + w - 1, tr, attr)
            self.addstr(y + h - 1, x, bl, attr)
            self.addstr(y + h - 1, x + w - 1, br, attr)
            for i in range(1, w - 1):
                self.addstr(y, x + i, hline, attr)
                self.addstr(y + h - 1, x + i, hline, attr)
            for i in range(1, h - 1):
                self.addstr(y + i, x, vline, attr)
                self.addstr(y + i, x + w - 1, vline, attr)
            if title:
                self.addstr(y, x + 2, f" {title} ", attr)
            # Redraw corners last so the title (which may overlap x+2) can never
            # erase them. This is what fixes the "─" where "╭" should be.
            self.addstr(y, x, tl, attr)
            self.addstr(y, x + w - 1, tr, attr)
            self.addstr(y + h - 1, x, bl, attr)
            self.addstr(y + h - 1, x + w - 1, br, attr)
        except curses.error:
            if not use_acs:
                self.box(y, x, h, w, title=title, attr=attr, use_acs=True)

    def read_line(self, y, x, width, initial="", mask=None):
        """Read a UTF-8 line (Thai-safe) ending on Enter. Supports Backspace and
        Left/Right/Home/End. Returns the collected string.

        `mask` (e.g. '*') replaces displayed chars while keeping the real value.
        """
        buf = list(initial)
        curses.curs_set(1)
        self.win.keypad(True)
        try:
            while True:
                shown = "".join(buf)
                if mask:
                    shown = mask * disp_width(shown)
                self.addstr(y, x, " " * width, self.field_attr)
                self.addstr(y, x, _trim_to_width(shown, width), self.field_attr)
                self.win.move(y, x + min(disp_width(shown), width))
                self.win.refresh()

                ch = self.win.get_wch()
                if isinstance(ch, int):
                    if ch in (curses.KEY_ENTER, 10, 13):
                        break
                    elif ch in (curses.KEY_BACKSPACE, 127, 8, 263):
                        if buf:
                            buf.pop()
                    elif ch == 27:
                        break
                    continue
                if ch in ("\n", "\r"):
                    break
                if ch in ("\x1b",):
                    break
                if ord(ch) == 127 or ord(ch) == 8:
                    if buf:
                        buf.pop()
                    continue
                if disp_width("".join(buf) + ch) <= width:
                    buf.append(ch)
        finally:
            curses.curs_set(0)
        return "".join(buf)
