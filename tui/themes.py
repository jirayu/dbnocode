"""Theme system for dbnocode TUI.

Manages color pair definitions across all TUI modules.
Themes are dictionaries mapping color pair IDs to (fg, bg) tuples.
"""
import curses
import json
import os

# ── Color pair ID constants (all modules share this namespace) ──────────
# Grid (20-35)
CLR_HEADER = 20
CLR_SELECTED = 21
CLR_ALT_ROW = 22
CLR_FOOTER = 23
CLR_FILTER = 24
CLR_INDICATOR = 25
CLR_EDIT = 26
CLR_BOOKMARK = 27
CLR_BORDER = 28
CLR_SORT_IND = 29
CLR_COEDIT = 30
CLR_HL_ERROR = 31
CLR_HL_WARN = 32
CLR_HL_HILITE = 33
CLR_HL_SUCCESS = 34
CLR_HL_DIM = 35

# Report (40-49)
CLR_RPT_TITLE = 40
CLR_RPT_HEADER = 41
CLR_RPT_DATA = 42
CLR_RPT_ALT = 43
CLR_RPT_SELECTED = 44
CLR_RPT_GROUP_HDR = 45
CLR_RPT_SUBTOTAL = 46
CLR_RPT_GRAND = 47
CLR_RPT_STATUS = 48
CLR_RPT_BORDER = 49

# Column config dialog (50-54)
CLR_CFG_BORDER = 50
CLR_CFG_NORMAL = 51
CLR_CFG_SELECTED = 52
CLR_CFG_HEADER = 53
CLR_CFG_DIM = 54

# Search dialog (60-65)
CLR_SEARCH_BORDER = 60
CLR_SEARCH_INPUT = 61
CLR_SEARCH_HEADER = 62
CLR_SEARCH_NORMAL = 63
CLR_SEARCH_SELECTED = 64
CLR_SEARCH_DIM = 65

# Runner / menu (1-5)
CLR_OFFLINE = 1
CLR_FORM_BORDER = 2
CLR_FORM_LABEL = 3
CLR_FORM_SECTION = 4
CLR_STATUS_BAR = 5
CLR_FORM_INPUT = 6
CLR_FORM_INPUT_FOCUS = 7

# ── Shorthand ───────────────────────────────────────────────────────────
W = curses.COLOR_WHITE
B = curses.COLOR_BLACK
C = curses.COLOR_CYAN
Y = curses.COLOR_YELLOW
G = curses.COLOR_GREEN
R = curses.COLOR_RED
M = curses.COLOR_MAGENTA
BL = curses.COLOR_BLUE

# ── Theme definitions ───────────────────────────────────────────────────
# Each theme maps pair_id -> (fg, bg)
# _BG = -1 means "use terminal default background"

_BG = -1  # sentinel for use_default_colors()

THEMES = {
    "DEFAULT": {
        # Grid
        CLR_HEADER:     (W, BL),
        CLR_SELECTED:   (B, W),
        CLR_ALT_ROW:    (W, B),
        CLR_FOOTER:     (W, BL),
        CLR_FILTER:     (B, Y),
        CLR_INDICATOR:  (C, B),
        CLR_EDIT:       (B, G),
        CLR_BOOKMARK:   (Y, B),
        CLR_BORDER:     (BL, B),
        CLR_SORT_IND:   (Y, BL),
        CLR_COEDIT:     (B, Y),
        CLR_HL_ERROR:   (W, R),
        CLR_HL_WARN:    (Y, B),
        CLR_HL_HILITE:  (C, B),
        CLR_HL_SUCCESS: (G, B),
        CLR_HL_DIM:     (BL, B),
        # Report
        CLR_RPT_TITLE:     (W, BL),
        CLR_RPT_HEADER:    (W, BL),
        CLR_RPT_DATA:      (W, B),
        CLR_RPT_ALT:       (W, B),
        CLR_RPT_SELECTED:  (B, W),
        CLR_RPT_GROUP_HDR: (W, M),
        CLR_RPT_SUBTOTAL:  (Y, B),
        CLR_RPT_GRAND:     (W, BL),
        CLR_RPT_STATUS:    (W, BL),
        CLR_RPT_BORDER:    (BL, B),
        # Column config
        CLR_CFG_BORDER:   (C, B),
        CLR_CFG_NORMAL:   (W, B),
        CLR_CFG_SELECTED: (B, C),
        CLR_CFG_HEADER:   (C, B),
        CLR_CFG_DIM:      (BL, B),
        # Search dialog
        CLR_SEARCH_BORDER:   (C, B),
        CLR_SEARCH_INPUT:    (B, Y),
        CLR_SEARCH_HEADER:   (C, B),
        CLR_SEARCH_NORMAL:   (W, B),
        CLR_SEARCH_SELECTED: (B, C),
        CLR_SEARCH_DIM:      (BL, B),
        # Runner / menu
        CLR_OFFLINE:      (R, B),
        CLR_FORM_BORDER:  (BL, B),
        CLR_FORM_LABEL:   (C, B),
        CLR_FORM_SECTION: (C, B),
        CLR_STATUS_BAR:   (W, BL),
        CLR_FORM_INPUT:       (W, B),
        CLR_FORM_INPUT_FOCUS: (B, W),
    },

    "MONOCHROME": {
        CLR_HEADER:     (B, G),
        CLR_SELECTED:   (B, G),
        CLR_ALT_ROW:    (G, B),
        CLR_FOOTER:     (B, G),
        CLR_FILTER:     (B, G),
        CLR_INDICATOR:  (G, B),
        CLR_EDIT:       (B, G),
        CLR_BOOKMARK:   (G, B),
        CLR_BORDER:     (G, B),
        CLR_SORT_IND:   (B, G),
        CLR_COEDIT:     (B, G),
        CLR_HL_ERROR:   (B, G),
        CLR_HL_WARN:    (G, B),
        CLR_HL_HILITE:  (G, B),
        CLR_HL_SUCCESS: (G, B),
        CLR_HL_DIM:     (G, B),
        CLR_RPT_TITLE:     (B, G),
        CLR_RPT_HEADER:    (B, G),
        CLR_RPT_DATA:      (G, B),
        CLR_RPT_ALT:       (G, B),
        CLR_RPT_SELECTED:  (B, G),
        CLR_RPT_GROUP_HDR: (B, G),
        CLR_RPT_SUBTOTAL:  (G, B),
        CLR_RPT_GRAND:     (B, G),
        CLR_RPT_STATUS:    (B, G),
        CLR_RPT_BORDER:    (G, B),
        CLR_CFG_BORDER:   (G, B),
        CLR_CFG_NORMAL:   (G, B),
        CLR_CFG_SELECTED: (B, G),
        CLR_CFG_HEADER:   (G, B),
        CLR_CFG_DIM:      (G, B),
        CLR_SEARCH_BORDER:   (G, B),
        CLR_SEARCH_INPUT:    (B, G),
        CLR_SEARCH_HEADER:   (G, B),
        CLR_SEARCH_NORMAL:   (G, B),
        CLR_SEARCH_SELECTED: (B, G),
        CLR_SEARCH_DIM:      (G, B),
        CLR_OFFLINE:      (G, B),
        CLR_FORM_BORDER:  (G, B),
        CLR_FORM_LABEL:   (G, B),
        CLR_FORM_SECTION: (G, B),
        CLR_STATUS_BAR:   (B, G),
        CLR_FORM_INPUT:       (G, B),
        CLR_FORM_INPUT_FOCUS: (B, G),
    },

    "CLASSIC": {
        # Norton Commander / DOS style: dark body, blue accents
        CLR_HEADER:     (Y, BL),
        CLR_SELECTED:   (B, C),
        CLR_ALT_ROW:    (W, B),
        CLR_FOOTER:     (B, C),
        CLR_FILTER:     (B, Y),
        CLR_INDICATOR:  (Y, B),
        CLR_EDIT:       (W, BL),
        CLR_BOOKMARK:   (Y, B),
        CLR_BORDER:     (BL, B),
        CLR_SORT_IND:   (Y, BL),
        CLR_COEDIT:     (B, Y),
        CLR_HL_ERROR:   (W, R),
        CLR_HL_WARN:    (Y, B),
        CLR_HL_HILITE:  (C, B),
        CLR_HL_SUCCESS: (G, B),
        CLR_HL_DIM:     (BL, B),
        CLR_RPT_TITLE:     (Y, BL),
        CLR_RPT_HEADER:    (Y, BL),
        CLR_RPT_DATA:      (W, B),
        CLR_RPT_ALT:       (W, B),
        CLR_RPT_SELECTED:  (B, C),
        CLR_RPT_GROUP_HDR: (W, BL),
        CLR_RPT_SUBTOTAL:  (Y, B),
        CLR_RPT_GRAND:     (W, BL),
        CLR_RPT_STATUS:    (Y, BL),
        CLR_RPT_BORDER:    (BL, B),
        CLR_CFG_BORDER:   (C, B),
        CLR_CFG_NORMAL:   (W, B),
        CLR_CFG_SELECTED: (B, C),
        CLR_CFG_HEADER:   (C, B),
        CLR_CFG_DIM:      (BL, B),
        CLR_SEARCH_BORDER:   (C, B),
        CLR_SEARCH_INPUT:    (B, Y),
        CLR_SEARCH_HEADER:   (C, B),
        CLR_SEARCH_NORMAL:   (W, B),
        CLR_SEARCH_SELECTED: (B, C),
        CLR_SEARCH_DIM:      (BL, B),
        CLR_OFFLINE:      (R, B),
        CLR_FORM_BORDER:  (BL, B),
        CLR_FORM_LABEL:   (C, B),
        CLR_FORM_SECTION: (Y, B),
        CLR_STATUS_BAR:   (B, C),
        CLR_FORM_INPUT:       (W, B),
        CLR_FORM_INPUT_FOCUS: (B, C),
    },

    "HIGH_CONTRAST": {
        CLR_HEADER:     (B, W),
        CLR_SELECTED:   (B, Y),
        CLR_ALT_ROW:    (W, B),
        CLR_FOOTER:     (B, W),
        CLR_FILTER:     (B, Y),
        CLR_INDICATOR:  (W, B),
        CLR_EDIT:       (B, W),
        CLR_BOOKMARK:   (Y, B),
        CLR_BORDER:     (W, B),
        CLR_SORT_IND:   (B, W),
        CLR_COEDIT:     (B, Y),
        CLR_HL_ERROR:   (W, R),
        CLR_HL_WARN:    (Y, B),
        CLR_HL_HILITE:  (C, B),
        CLR_HL_SUCCESS: (G, B),
        CLR_HL_DIM:     (W, B),
        CLR_RPT_TITLE:     (B, W),
        CLR_RPT_HEADER:    (B, W),
        CLR_RPT_DATA:      (W, B),
        CLR_RPT_ALT:       (W, B),
        CLR_RPT_SELECTED:  (B, Y),
        CLR_RPT_GROUP_HDR: (B, Y),
        CLR_RPT_SUBTOTAL:  (Y, B),
        CLR_RPT_GRAND:     (B, W),
        CLR_RPT_STATUS:    (B, W),
        CLR_RPT_BORDER:    (W, B),
        CLR_CFG_BORDER:   (W, B),
        CLR_CFG_NORMAL:   (W, B),
        CLR_CFG_SELECTED: (B, Y),
        CLR_CFG_HEADER:   (W, B),
        CLR_CFG_DIM:      (W, B),
        CLR_SEARCH_BORDER:   (W, B),
        CLR_SEARCH_INPUT:    (B, Y),
        CLR_SEARCH_HEADER:   (W, B),
        CLR_SEARCH_NORMAL:   (W, B),
        CLR_SEARCH_SELECTED: (B, Y),
        CLR_SEARCH_DIM:      (W, B),
        CLR_OFFLINE:      (R, B),
        CLR_FORM_BORDER:  (W, B),
        CLR_FORM_LABEL:   (Y, B),
        CLR_FORM_SECTION: (Y, B),
        CLR_STATUS_BAR:   (B, W),
        CLR_FORM_INPUT:       (W, B),
        CLR_FORM_INPUT_FOCUS: (B, Y),
    },

    "YELLOW": {
        CLR_HEADER:     (B, Y),
        CLR_SELECTED:   (B, Y),
        CLR_ALT_ROW:    (W, B),
        CLR_FOOTER:     (B, Y),
        CLR_FILTER:     (B, Y),
        CLR_INDICATOR:  (Y, B),
        CLR_EDIT:       (B, G),
        CLR_BOOKMARK:   (Y, B),
        CLR_BORDER:     (Y, B),
        CLR_SORT_IND:   (B, Y),
        CLR_COEDIT:     (B, Y),
        CLR_HL_ERROR:   (W, R),
        CLR_HL_WARN:    (Y, B),
        CLR_HL_HILITE:  (C, B),
        CLR_HL_SUCCESS: (G, B),
        CLR_HL_DIM:     (BL, B),
        CLR_RPT_TITLE:     (B, Y),
        CLR_RPT_HEADER:    (B, Y),
        CLR_RPT_DATA:      (W, B),
        CLR_RPT_ALT:       (W, B),
        CLR_RPT_SELECTED:  (B, Y),
        CLR_RPT_GROUP_HDR: (B, C),
        CLR_RPT_SUBTOTAL:  (Y, B),
        CLR_RPT_GRAND:     (B, Y),
        CLR_RPT_STATUS:    (B, Y),
        CLR_RPT_BORDER:    (Y, B),
        CLR_CFG_BORDER:   (Y, B),
        CLR_CFG_NORMAL:   (W, B),
        CLR_CFG_SELECTED: (B, Y),
        CLR_CFG_HEADER:   (Y, B),
        CLR_CFG_DIM:      (Y, B),
        CLR_SEARCH_BORDER:   (Y, B),
        CLR_SEARCH_INPUT:    (B, Y),
        CLR_SEARCH_HEADER:   (Y, B),
        CLR_SEARCH_NORMAL:   (W, B),
        CLR_SEARCH_SELECTED: (B, Y),
        CLR_SEARCH_DIM:      (Y, B),
        CLR_OFFLINE:      (R, B),
        CLR_FORM_BORDER:  (Y, B),
        CLR_FORM_LABEL:   (Y, B),
        CLR_FORM_SECTION: (Y, B),
        CLR_STATUS_BAR:   (B, Y),
        CLR_FORM_INPUT:       (Y, B),
        CLR_FORM_INPUT_FOCUS: (B, Y),
    },
}

THEME_ORDER = ["DEFAULT", "MONOCHROME", "CLASSIC", "HIGH_CONTRAST", "YELLOW"]

# ── Settings persistence ────────────────────────────────────────────────
_settings_path = ""
_current_theme = "DEFAULT"


def _resolve_settings_path():
    """Find settings file next to the DSL script or in current directory."""
    global _settings_path
    if _settings_path:
        return _settings_path
    _settings_path = "dbnocode_settings.json"
    return _settings_path


def load_settings() -> dict:
    path = _resolve_settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(data: dict):
    path = _resolve_settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def get_current_theme() -> str:
    return _current_theme


# ── Color initialization ───────────────────────────────────────────────

def init_colors(theme_name: str = None):
    """Initialize all curses color pairs for the given theme.

    Call once at startup, and again when user switches theme.
    """
    global _current_theme
    if theme_name is None:
        settings = load_settings()
        theme_name = settings.get("theme", "DEFAULT")
    theme_name = theme_name.upper()
    if theme_name not in THEMES:
        theme_name = "DEFAULT"
    _current_theme = theme_name

    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        pass

    # Windows Terminal renders COLOR_BLACK (0) as dark gray, not true black.
    # Use 256-color palette slot 232 (#080808) as a reliable near-black
    # substitute for foreground text on light backgrounds.
    true_black = curses.COLOR_BLACK
    if curses.COLORS >= 256:
        true_black = 232  # xterm-256 near-black

    theme = THEMES[theme_name]
    _light_bg = {curses.COLOR_WHITE, curses.COLOR_YELLOW, curses.COLOR_GREEN,
                 curses.COLOR_CYAN}
    for pair_id, (fg, bg) in theme.items():
        if fg == curses.COLOR_BLACK and bg in _light_bg:
            fg = true_black
        try:
            curses.init_pair(pair_id, fg, bg)
        except curses.error:
            pass


# ── Theme picker UI ─────────────────────────────────────────────────────

def theme_picker(stdscr) -> str:
    """Show theme selection popup. Returns selected theme name or empty string."""
    themes = THEME_ORDER
    sel = 0
    current = _current_theme
    for i, name in enumerate(themes):
        if name == current:
            sel = i
            break

    while True:
        max_y, max_x = stdscr.getmaxyx()
        # Popup dimensions
        w = 36
        h = len(themes) + 6
        y = max(0, (max_y - h) // 2)
        x = max(0, (max_x - w) // 2)

        # Draw popup border
        try:
            for row in range(h):
                stdscr.addstr(y + row, x, " " * w, curses.A_REVERSE)
            stdscr.addstr(y, x + 2, " Theme Selection ", curses.A_REVERSE | curses.A_BOLD)
            stdscr.addstr(y + 1, x + 2, "-" * (w - 4), curses.A_REVERSE)
        except curses.error:
            pass

        # Draw theme options
        for i, name in enumerate(themes):
            label = f"  {name:<28}"
            row = y + 2 + i
            if i == sel:
                attr = curses.A_BOLD
                marker = "> "
            else:
                attr = curses.A_REVERSE
                marker = "  "
            if name == current:
                label = f"  {name:<24} [*] "
            try:
                stdscr.addstr(row, x, marker + label[2:], attr)
            except curses.error:
                pass

        # Hint
        try:
            hint = " Enter=Apply  ESC=Cancel "
            stdscr.addstr(y + h - 2, x + 2, hint[:w - 4], curses.A_REVERSE | curses.A_DIM)
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_UP and sel > 0:
            sel -= 1
        elif key == curses.KEY_DOWN and sel < len(themes) - 1:
            sel += 1
        elif key in (curses.KEY_ENTER, 10, 13):
            chosen = themes[sel]
            init_colors(chosen)
            settings = load_settings()
            settings["theme"] = chosen
            save_settings(settings)
            return chosen
        elif key == 27:  # ESC
            return ""
