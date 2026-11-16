"""Big pixel-text title banner for listing pages.

Replaces the old pyfiglet banner with pytitle's fixed 5x5 pixel font:
compact half-block mode (3 terminal lines, one char per pixel) and no
shadow by default. Falls back silently to height 0 when pytitle is not
installed, the title is empty, or it does not fit the terminal width --
the layout then draws the plain in-frame title as before.
"""

import curses

try:
    from pytitle import BigText
    HAS_PYTITLE = True
except ImportError:
    HAS_PYTITLE = False

from .themes import CLR_FORM_BORDER

SHADOW_CHAR = "\u2591"  # ░ -- shadow pixels in plain compact lines


class BigTitleBanner:
    """Reusable header banner that measures itself against the terminal.

    Usage:
        banner = BigTitleBanner(stdscr, "INVENTORY")
        banner.height          # 0 when no banner should be drawn
        banner.draw(y0=0)      # centered block-art lines
    """

    def __init__(self, stdscr, title, shadow=False, compact=True,
                 half_blocks=True, font="pixel", attr=None, shadow_attr=None):
        """Defaults recreate the opencode header look: 5x5 font, half-block
        rendering (square pixels), 3 terminal lines tall.
        attr/shadow_attr: curses attribute(s); None -> CLR_FORM_BORDER|BOLD.

        Note: the 'twisted' rows seen earlier were a centering bug (each
        line centered by its own length), not a console font issue -- fixed
        by centering all lines on one shared left edge."""
        self.stdscr = stdscr
        self.title = (title or "").strip()
        self.shadow = bool(shadow)
        self.compact = bool(compact)
        self.half_blocks = bool(half_blocks)
        self.font = font
        self.attr = attr
        self.shadow_attr = shadow_attr
        self._lines = []
        self._measure()

    def _measure(self):
        self._lines = []
        if not HAS_PYTITLE or not self.title:
            return
        try:
            h, w = self.stdscr.getmaxyx()
        except curses.error:
            return
        big = BigText(shadow=self.shadow, compact=self.compact,
                      half_blocks=self.half_blocks, font=self.font)
        # Thai / unsupported characters would render as '?' -- skip banner
        # so the layout falls back to the plain in-frame title.
        if not big.supported(self.title):
            return
        lines = big.lines(self.title, max_width=w - 4)
        # Keep at least 12 terminal rows for the frame + grid below.
        if lines and len(lines) > max(3, h - 12):
            return
        self._lines = lines

    @property
    def height(self) -> int:
        """Lines the banner occupies (0 = no banner)."""
        return len(self._lines)

    def draw(self, y0: int = 0):
        """Draw the banner centered at row y0. No-op when empty."""
        if not self._lines:
            return
        attr = self.attr
        if attr is None:
            attr = curses.color_pair(CLR_FORM_BORDER) | curses.A_BOLD
        try:
            h, w = self.stdscr.getmaxyx()
        except curses.error:
            return
        # All lines share one left edge -- center by the WIDEST line.
        # (Per-line centering would skew rows whose last glyph doesn't
        # reach its final column, e.g. 'T' in the mini font.)
        cx = max(0, (w - max(len(l) for l in self._lines)) // 2)
        avail = max(0, w - 1 - cx)
        for i, line in enumerate(self._lines):
            if self.shadow_attr:
                # Per-char attrs: shadow pixels get the dimmer attribute.
                for j, ch in enumerate(line):
                    if ch == " ":
                        continue
                    a = self.shadow_attr if ch == SHADOW_CHAR else attr
                    try:
                        self.stdscr.addstr(y0 + i, cx + j, ch, a)
                    except curses.error:
                        pass
            else:
                try:
                    self.stdscr.addstr(y0 + i, cx, line[:avail], attr)
                except curses.error:
                    pass
