import curses
from curses.textpad import rectangle
from typing import List, Dict, Any

class EditableGrid:
    def __init__(self, y: int, x: int, width: int, height: int, columns: List[Dict], data: List[Dict] = None):
        self.y = y
        self.x = x
        self.width = width
        self.height = height
        self.columns = columns
        self.data = data or []
        self.sel_row = 0
        self.sel_col = 0

    def draw(self, stdscr):
        col_x = self.x
        for col in self.columns:
            w = col.get("width", 10)
            stdscr.addstr(self.y, col_x, col["label"][:w].ljust(w), curses.A_REVERSE)
            col_x += w
        for ri, row in enumerate(self.data[:self.height-1]):
            col_x = self.x
            for ci, col in enumerate(self.columns):
                w = col.get("width", 10)
                val = str(row.get(col["id"], ""))[:w].ljust(w)
                attr = curses.A_REVERSE if ri == self.sel_row and ci == self.sel_col else 0
                stdscr.addstr(self.y + 1 + ri, col_x, val, attr)
                col_x += w