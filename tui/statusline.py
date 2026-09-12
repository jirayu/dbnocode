"""Reusable status line for bottom-of-screen hotkey hints.

Each page type has a predefined key profile so status bars are consistent
and comparable across pages. Callers pick a page type; conditional keys
(e.g. F5 only when workflow exists) are toggled via set_visible().

Usage:
    bar = StatusLine(stdscr)
    bar.set_page("grid")
    bar.draw()

    # Form with workflow:
    bar.set_page("form")
    bar.set_visible("F5", True)
    bar.set_info("Editing #3")
    bar.draw()
"""
import curses
from .themes import CLR_STATUS_BAR

# ── Page key profiles ────────────────────────────────────────────────
# Each profile is a list of (key, label, visible_by_default).
# Order matters — rendered left to right.

PAGE_KEYS = {
    "grid": [
        ("Enter", "Edit", True),
        ("F3", "New", True),
        ("Ctrl+D", "Delete", True),
        ("F7", "Filter", True),
        ("F8", "Columns", True),
        ("F9", "Theme", True),
        ("ESC", "Back", True),
    ],
    "form": [
        ("F4", "Lookup", True),
        ("F5", "Post", False),       # shown only if workflow
        ("F6", "Focus", False),      # header -> detail -> UDF
        ("F7", "UDF Edit", False),   # shown only if udf_schema
        ("F9", "Theme", True),
        ("F10", "Save", True),
        ("Ctrl+P", "Print", True),
        ("Ctrl+B", "B2B Send", True),
        ("ESC", "Cancel", True),
    ],
    "form_edit": [
        ("F4", "Lookup", True),
        ("F5", "Post", False),       # shown only if workflow
        ("F6", "Focus", False),      # header -> detail -> UDF
        ("F7", "UDF Edit", False),   # shown only if udf_schema
        ("F9", "Theme", True),
        ("F10", "Save", True),
        ("Ctrl+P", "Print", True),
        ("Ctrl+B", "B2B Send", True),
        ("ESC", "Cancel", True),
    ],
    "tab_grid": [
        ("F4", "Lookup", True),
        ("Ins", "New Row", True),
        ("Ctrl+D", "Del Row", True),
        ("F5", "Post", False),       # shown only if workflow
        ("Ctrl+U", "UOM", True),
        ("F7", "Filter", True),
        ("F8", "Columns", True),
        ("F10", "Save", True),
    ],
    "tree_grid": [
        ("F3", "Sibling", True),
        ("F4", "Child", True),
        ("Enter", "Edit", True),
        ("Del", "Delete", True),
        ("Space", "Expand", True),
        ("F5", "Post", False),       # shown only if workflow
        ("F9", "Theme", True),
        ("F10", "Save", True),
        ("ESC", "Back", True),
    ],
    "menu": [
        ("\u2191\u2193", "Navigate", True),
        ("Enter", "Select", True),
        ("F9", "Theme", True),
        ("ESC", "Exit", True),
    ],
    "menu_2col": [
        ("\u2191\u2193", "Navigate", True),
        ("Tab", "Column", True),
        ("Enter", "Select", True),
        ("F9", "Theme", True),
        ("ESC", "Exit", True),
    ],
    "report": [
        ("F7", "Filter", True),
        ("Ctrl+L", "Clear", True),
        ("F3", "Summary", True),
        ("F6", "Crosstab", True),
        ("F4", "Re-run", True),
        ("F10", "PDF", True),
        ("ESC", "Back", True),
    ],
    "summary": [
        ("F2", "Listing", True),
        ("F4", "Re-run", True),
        ("ESC", "Back", True),
    ],
    "pos": [
        ("Enter", "Scan", True),
        ("Del", "Remove", True),
        ("F5", "Post", True),
        ("F8", "Void", False),
        ("F10", "Save", True),
        ("F9", "Theme", True),
        ("ESC", "Back", True),
    ],
}

# Runtime capability keys shared by all form variants.
CAPABILITY_KEYS = (
    ("lookup", "F4", "Lookup"),
    ("workflow", "F5", "Post"),
    ("focus", "F6", "Focus"),
    ("udf_edit", "F7", "UDF Edit"),
    ("uom", "Ctrl+U", "UOM"),
    ("filter", "F7", "Filter"),
    ("columns", "F8", "Columns"),
    ("theme", "F9", "Theme"),
    ("save", "F10", "Save"),
    ("print", "Ctrl+P", "Print"),
    ("b2b", "Ctrl+B", "B2B Send"),
    ("back", "ESC", "Cancel"),
)


class StatusLine:
    """Draws a formatted hotkey bar from a page profile."""

    __slots__ = ("stdscr", "_page", "_overrides", "_info", "_color_pair",
                 "_f5_label", "_capabilities")

    def __init__(self, stdscr, color_pair=None):
        self.stdscr = stdscr
        self._page = "grid"
        self._overrides = {}     # key_str -> visible bool override
        self._info = ""
        self._color_pair = color_pair
        self._f5_label = "Post"  # can be "Actions" for workflow with actions
        self._capabilities = None

    # ── Public API ──────────────────────────────────────────────────

    def set_page(self, page_type):
        """Set page profile. Resets visibility overrides."""
        self._page = page_type
        self._overrides = {}
        self._capabilities = None

    def set_capabilities(self, capabilities=None, **kwargs):
        """Configure status keys from runtime feature capabilities."""
        self._page = None
        self._overrides = {}
        self._capabilities = dict(capabilities or {})
        self._capabilities.update(kwargs)

    def set_visible(self, key_str, visible):
        """Override visibility of a specific key in current profile."""
        self._overrides[key_str] = visible

    def set_f5_label(self, label):
        """Set F5 label ('Post' or 'Actions')."""
        self._f5_label = label

    def set_info(self, text):
        """Set left-side info text (e.g. 'Editing #3'). Shown before keys."""
        self._info = text or ""

    def draw(self, y=None):
        """Render status line at row y (default: last row of screen)."""
        try:
            max_y, max_x = self.stdscr.getmaxyx()
        except curses.error:
            return
        if y is None:
            y = max_y - 1

        pair_id = self._color_pair or CLR_STATUS_BAR
        bar_attr = curses.color_pair(pair_id)

        # Clear line
        try:
            self.stdscr.addstr(y, 0, " " * (max_x - 1), bar_attr)
        except curses.error:
            pass

        cx = 1

        # Draw info text first if set
        if self._info:
            try:
                self.stdscr.addstr(y, cx, self._info, bar_attr | curses.A_DIM)
                cx += len(self._info) + 2
            except curses.error:
                pass

        if self._capabilities is not None:
            profile = [
                (key_str, label, True)
                for capability, key_str, label in CAPABILITY_KEYS
                if self._capabilities.get(capability, False)
            ]
        else:
            profile = PAGE_KEYS.get(self._page, [])

        for key_str, label, default_vis in profile:
            visible = self._overrides.get(key_str, default_vis)
            if not visible:
                continue
            if cx >= max_x - 2:
                break
            # F5 uses dynamic label
            disp_label = self._f5_label if key_str == "F5" else label
            try:
                self.stdscr.addstr(y, cx, f" {key_str} ",
                                   bar_attr | curses.A_BOLD)
                cx += len(key_str) + 2
                self.stdscr.addstr(y, cx, f" {disp_label}", curses.A_NORMAL)
                cx += len(disp_label) + 3
            except curses.error:
                pass
