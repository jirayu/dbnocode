"""Thai text editor — Alt-L overlay window for the curses app (text-mode TUI).

The curses TUI cannot place Thai inside grids/forms (combining vowels/tone
marks break column math). This overlay is a simple bordered input area drawn
over the current screen (nothing else is cleared or shown).

The input area is NON-CURSOR: the terminal caret is never moved or shown.
Typing always APPENDS to the end of the active line, Backspace deletes the
last character, and the whole line is redrawn after every keypress — so no
cursor-position math can drift with Thai combining marks.

Keys:
    single-line:  type=append · Backspace=delete · Enter=OK · Esc=cancel
    multiline:    Up/Down select line · type=append to line ·
                  Backspace=delete char (or merge empty line) ·
                  Enter=new line · Ctrl+D=delete line · F10=OK · Esc=cancel

Side data: the last values typed for the same field label are remembered in
tui/thai_popup_side.json (gitignored).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curses

from thai_util import ThaiWin, _trim_to_width

SIDE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "thai_popup_side.json")
SIDE_CAP = 8

# High pair numbers to avoid clashing with the host app's palette
PAIR_BOX = 90
PAIR_TEXT = 92
PAIR_ACTIVE = 93


def load_side() -> dict:
    try:
        with open(SIDE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def remember(label: str, value: str) -> None:
    """Prepend value to the per-field history (dedupe, cap SIDE_CAP)."""
    if not label or not value:
        return
    data = load_side()
    hist = [v for v in (data.get(label) or []) if v != value]
    hist.insert(0, value)
    data[label] = hist[:SIDE_CAP]
    try:
        with open(SIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
    except Exception:
        pass  # side data is best-effort


def _init_colors():
    try:
        curses.init_pair(PAIR_BOX, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(PAIR_TEXT, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(PAIR_ACTIVE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    except curses.error:
        pass


def popup_edit(stdscr, label: str, value: str, multiline: bool = False,
               form_name: str = ""):
    """Open the Thai editor overlay and return the new value, or None on
    cancel. Non-cursor input: typing appends, Backspace deletes."""
    _init_colors()
    max_y, max_x = stdscr.getmaxyx()

    if multiline:
        lines = value.split("\n") if value else [""]
        h = min(max_y - 1, len(lines) + 5)
    else:
        h = 6
    w = min(max_x - 2, max(50, len(label) + 24))
    h = max(4, min(h, max_y - 1))
    w = max(20, min(w, max_x - 2))
    y = max(0, (max_y - h) // 2)
    x = max(0, (max_x - w) // 2)

    win = curses.newwin(h, w, y, x)
    win.keypad(True)
    ui = ThaiWin(win)
    inner_w = max(8, w - 4)
    # Hide the terminal cursor for the whole popup: without this it keeps
    # blinking at the background field the form positioned it at (cosmetic,
    # but looks like the background field is stealing focus). The parent
    # loop re-shows it on its next draw pass.
    try:
        curses.curs_set(0)
    except curses.error:
        pass

    def draw_line(row, text, active):
        # Clear the row with spaces, then draw the real Thai text.
        # A dedicated editor row has no column alignment to preserve, so
        # Thai renders acceptably here — unlike grids/forms where combining
        # marks drift every column. _trim_to_width caps BOTH display width
        # and code-point count so curses can never wrap the line outside
        # the box (that was the source of the 'weird characters').
        #
        # No caret block: a reverse-video cell drawn after the text collides
        # with the last Thai glyph's overhanging marks. The cyan highlight
        # bar is the focus indicator; typing always appends at the end, so
        # there is no insertion point to indicate.
        attr = curses.color_pair(PAIR_ACTIVE if active else PAIR_TEXT)
        ui.addstr(row, 2, " " * inner_w, attr)
        if text:
            ui.addstr(row, 2, _trim_to_width(text, inner_w - 2), attr)

    if not multiline:
        buf = list(value)
        while True:
            ui.box(0, 0, h, w, title=f" {label} " +
                   (f"[{form_name}]" if form_name else ""),
                   attr=curses.color_pair(PAIR_BOX))
            draw_line(2, "".join(buf), True)
            win.refresh()

            k = stdscr.get_wch()  # one code point per key (Thai-safe)
            if isinstance(k, str):
                if k in ("\r", "\n"):
                    value = "".join(buf).strip()
                    remember(label, value)
                    return value
                if k == "\x1b":
                    return None
                if k in ("\x7f", "\b"):
                    if buf:
                        buf.pop()
                    continue
                buf.append(k)  # ASCII or Thai — append at end
            else:
                if k in (curses.KEY_ENTER, 10, 13, curses.KEY_F10, 19):
                    value = "".join(buf).strip()
                    remember(label, value)
                    return value
                if k in (curses.KEY_BACKSPACE, 127, 8, 263):
                    if buf:
                        buf.pop()
                # arrows/home/end are ignored — non-cursor input area

    # ---- multiline (append-at-end per line, Up/Down selects) ----
    cur = len(lines) - 1
    first = 0
    inner_h = max(1, h - 4)
    result = None

    while True:
        ui.box(0, 0, h, w, title=f" {label} " +
               (f"[{form_name}]" if form_name else ""),
               attr=curses.color_pair(PAIR_BOX))
        # keep active line visible
        if cur < first:
            first = cur
        if cur >= first + inner_h:
            first = cur - inner_h + 1
        for i in range(first, min(first + inner_h, len(lines))):
            draw_line(2 + i - first, lines[i], i == cur)
        if first + inner_h < len(lines):
            ui.addstr(2 + inner_h - 1, 2, "…",
                      curses.color_pair(PAIR_ACTIVE))
        win.refresh()

        k = stdscr.get_wch()
        if isinstance(k, str):
            if k in ("\r", "\n"):
                # Enter: new empty line below the active one
                lines.insert(cur + 1, "")
                cur += 1
                continue
            if k == "\x1b":
                return None
            if k in ("\x7f", "\b"):
                if lines[cur]:
                    lines[cur] = lines[cur][:-1]
                elif len(lines) > 1:
                    del lines[cur]
                    cur = min(cur, len(lines) - 1)
                continue
            lines[cur] += k  # append at end (ASCII or Thai)
        else:
            if k in (curses.KEY_F10, 19):
                result = "\n".join(lines)
                break
            if k == 27:
                return None
            if k == curses.KEY_UP:
                cur = max(0, cur - 1)
            elif k == curses.KEY_DOWN:
                cur = min(len(lines) - 1, cur + 1)
            elif k in (curses.KEY_BACKSPACE, 127, 8, 263):
                if lines[cur]:
                    lines[cur] = lines[cur][:-1]
                elif len(lines) > 1:
                    del lines[cur]
                    cur = min(cur, len(lines) - 1)
            elif k == 4:  # Ctrl+D — delete active line
                if len(lines) > 1:
                    del lines[cur]
                    cur = min(cur, len(lines) - 1)

    try:
        win.erase()
        win.refresh()
        del win
    except Exception:
        pass
    if result is None:
        return None
    value = "\n".join(l for l in result.split("\n")).strip()
    remember(label, value)
    return value


def main() -> int:  # pragma: no cover - manual testing only
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--value", default="")
    ap.add_argument("--multiline", action="store_true")
    args = ap.parse_args()

    def curses_main(stdscr):
        return popup_edit(stdscr, args.label, args.value, args.multiline)

    result = curses.wrapper(curses_main)
    print("result:", repr(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
