"""
DSL Script Engine — tokenizer, parser, and executor.

Supports: SET, IF/ELIF/ELSE/END, FOREACH/END, FETCH, UPDATE, INSERT,
          DELETE, FAIL, OK, CALL, STOCK, BREAK, CONTINUE.

Expression functions: ADD, SUB, COUNT, SUM, SUMIF, TODAY, NOW, ISNULL,
    ISBLANK, STR, NUM, MIN, MAX, ABS, ROUND, CEIL, FLOOR, DIV,
    UPPER, LOWER, LEN, REPLACE, LPAD, TRIM, LEFT, RIGHT, CONCAT,
    DATEDIFF, DATEADD, YEAR, MONTH, DAY, QUARTER, YEARMONTH, DAYOFWEEK,
    LOOKUPVALUE, LOOKUPRANGE, LOOKUPSTEP.
"""
import math
import re, json
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional

# ── Tokenizer ────────────────────────────────────────────────────────

_TOK_RE = re.compile(
    r'"[^"]*"'                # double-quoted string
    r"|'[^']*'"               # single-quoted string
    r'|>=|<=|!=|<>'           # two-char operators
    r'|[=><()+\-*/,.]'       # single-char operators
    r'|[A-Za-z_]\w*'         # identifiers / keywords
    r'|\d+(?:\.\d+)?'        # numbers
)

_KEYWORDS = {
    'AND', 'OR', 'NOT', 'IS', 'NULL', 'IN', 'TRUE', 'FALSE',
    'SET', 'IF', 'ELIF', 'ELSE', 'END', 'FOREACH', 'FETCH', 'FROM',
    'WHERE', 'UPDATE', 'INSERT', 'DELETE', 'FAIL', 'OK', 'CALL',
    'BREAK', 'CONTINUE', 'TODAY', 'NOW',
    'ADD', 'SUB', 'COUNT', 'SUM', 'SUMIF', 'MIN', 'MAX', 'ABS',
    'ROUND', 'CEIL', 'FLOOR', 'DIV',
    'UPPER', 'LOWER', 'LEN', 'ISNULL', 'ISBLANK', 'STR', 'NUM',
    'REPLACE', 'LPAD', 'TRIM', 'LEFT', 'RIGHT', 'CONCAT',
    'DATEDIFF', 'DATEADD', 'YEAR', 'MONTH', 'DAY', 'QUARTER',
    'YEARMONTH', 'DAYOFWEEK',
    'LOOKUPVALUE', 'LOOKUPRANGE', 'LOOKUPSTEP',
    'BOMEXPLODE', 'BOMROLLUP', 'FIFOOUT', 'COUNTROWS', 'NEXTDOCNO',
    'BASEQTY', 'DISPLAYQTY',
    'PUTAWAY_ALLOCATE', 'PICKING_ALLOCATE', 'PICKING_CONFIRM',
    'SETTING',
}

def tokenize(line: str) -> List[str]:
    return _TOK_RE.findall(line)


# ── Expression Parser ────────────────────────────────────────────────

class ExprParser:
    """Recursive-descent expression parser producing AST dicts."""

    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> str:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, val: str):
        t = self.advance()
        if t.upper() != val.upper():
            raise SyntaxError(f"Expected '{val}', got '{t}'")

    def parse(self):
        node = self._or_expr()
        if self.peek() is not None:
            raise SyntaxError(f"Unexpected token '{self.peek()}' in expression")
        return node

    def parse_remaining(self):
        """Parse and return any unconsumed tokens as well."""
        node = self._or_expr()
        return node

    # ── Precedence levels ──

    def _or_expr(self):
        left = self._and_expr()
        while self.peek() and self.peek().upper() == 'OR':
            self.advance()
            right = self._and_expr()
            left = {"type": "logic", "op": "OR", "left": left, "right": right}
        return left

    def _and_expr(self):
        left = self._not_expr()
        while self.peek() and self.peek().upper() == 'AND':
            self.advance()
            right = self._not_expr()
            left = {"type": "logic", "op": "AND", "left": left, "right": right}
        return left

    def _not_expr(self):
        if self.peek() and self.peek().upper() == 'NOT':
            self.advance()
            operand = self._not_expr()
            return {"type": "not", "expr": operand}
        return self._compare_expr()

    def _compare_expr(self):
        left = self._add_expr()
        p = self.peek()
        if p and p.upper() == 'IS':
            self.advance()
            nxt = self.peek()
            if nxt and nxt.upper() == 'NOT':
                self.advance()
                self.expect('NULL')
                return {"type": "is_not_null", "expr": left}
            elif nxt and nxt.upper() == 'NULL':
                self.advance()
                return {"type": "is_null", "expr": left}
        if p in ('=', '!=', '<>', '>', '>=', '<', '<='):
            op = self.advance()
            if op == '<>':
                op = '!='
            right = self._add_expr()
            return {"type": "compare", "op": op, "left": left, "right": right}
        return left

    def _add_expr(self):
        left = self._mul_expr()
        while self.peek() in ('+', '-'):
            op = self.advance()
            right = self._mul_expr()
            left = {"type": "binop", "op": op, "left": left, "right": right}
        return left

    def _mul_expr(self):
        left = self._unary()
        while self.peek() in ('*', '/'):
            op = self.advance()
            right = self._unary()
            left = {"type": "binop", "op": op, "left": left, "right": right}
        return left

    def _unary(self):
        if self.peek() == '-':
            self.advance()
            operand = self._primary()
            return {"type": "binop", "op": "*",
                    "left": {"type": "literal", "value": -1},
                    "right": operand}
        return self._primary()

    def _primary(self):
        t = self.peek()
        if t is None:
            return {"type": "literal", "value": ""}

        # Parenthesized expression
        if t == '(':
            self.advance()
            node = self._or_expr()
            if self.peek() != ')':
                raise SyntaxError("Expected ')' in expression")
            self.advance()
            return node

        # String literal
        if (t.startswith('"') and t.endswith('"')) or \
           (t.startswith("'") and t.endswith("'")):
            self.advance()
            return {"type": "literal", "value": t[1:-1]}

        # Numeric literal
        if re.match(r'^\d+(\.\d+)?$', t):
            self.advance()
            v = float(t) if '.' in t else int(t)
            return {"type": "literal", "value": v}

        # Boolean / NULL
        up = t.upper()
        if up == 'TRUE':
            self.advance()
            return {"type": "literal", "value": True}
        if up == 'FALSE':
            self.advance()
            return {"type": "literal", "value": False}
        if up == 'NULL':
            self.advance()
            return {"type": "literal", "value": None}

        # Function call: NAME(args)
        if re.match(r'^[A-Za-z_]\w*$', t):
            # Peek ahead for '('
            if self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1] == '(':
                fname = self.advance().upper()
                self.advance()  # consume '('
                args = []
                if self.peek() == ')':
                    self.advance()
                    return {"type": "func", "name": fname, "args": args}
                while True:
                    args.append(self._or_expr())
                    if self.peek() == ',':
                        self.advance()
                        if self.peek() in (None, ')', ','):
                            raise SyntaxError(f"Malformed argument separator in {fname}()")
                        continue
                    if self.peek() != ')':
                        raise SyntaxError(f"Expected ',' or ')' in {fname}()")
                    self.advance()
                    break
                return {"type": "func", "name": fname, "args": args}

            # Identifier — possibly dotted (var.field)
            name = self.advance()
            if self.peek() == '.':
                self.advance()  # consume '.'
                field = self.advance()
                return {"type": "dot", "obj": name, "field": field}
            return {"type": "ref", "name": name}

        # Fallback
        self.advance()
        return {"type": "literal", "value": t}


def parse_expr(tokens: List[str]):
    """Parse a token list into an expression AST."""
    if not tokens:
        return {"type": "literal", "value": ""}
    p = ExprParser(tokens)
    return p.parse()


def parse_expr_string(s: str):
    """Tokenize and parse a string into an expression AST."""
    return parse_expr(tokenize(s))


# ── Instruction Parser ───────────────────────────────────────────────

def parse_script(lines: List[str]) -> List[dict]:
    """Parse script body lines into a list of instruction ASTs."""
    parser = ScriptParser(lines)
    return parser.parse()


class ScriptParser:
    def __init__(self, lines: List[str]):
        self.lines = [l.strip() for l in lines]
        self.pos = 0

    def peek_line(self) -> Optional[str]:
        while self.pos < len(self.lines):
            if self.lines[self.pos] == '' or self.lines[self.pos].startswith('//'):
                self.pos += 1
                continue
            return self.lines[self.pos]
        return None

    def next_line(self) -> Optional[str]:
        line = self.peek_line()
        if line is not None:
            self.pos += 1
        return line

    def parse(self) -> List[dict]:
        return self._parse_block(['ENDSCRIPT', 'END SCRIPT', None])

    def _parse_block(self, end_tokens: List) -> List[dict]:
        """Parse instructions until an end token is found."""
        instructions = []
        while True:
            line = self.peek_line()
            if line is None:
                break
            up = line.upper()
            if any(up == et for et in end_tokens if et):
                break
            # Check for block-ending keywords (don't consume)
            if up in ('END', 'ELIF', 'ELSE'):
                break
            self.next_line()
            inst = self._parse_instruction(line)
            if inst:
                instructions.append(inst)
        return instructions

    def _parse_instruction(self, line: str) -> Optional[dict]:
        up = line.upper()
        tokens = tokenize(line)
        if not tokens:
            return None
        kw = tokens[0].upper()

        if kw == 'SET':
            return self._parse_set(tokens)
        elif kw == 'IF':
            return self._parse_if(tokens)
        elif kw == 'FOREACH':
            return self._parse_foreach(tokens)
        elif kw == 'FETCH':
            return self._parse_fetch(tokens)
        elif kw == 'UPDATE':
            return self._parse_update(tokens)
        elif kw == 'INSERT':
            return self._parse_insert(tokens)
        elif kw == 'DELETE':
            return self._parse_delete(tokens)
        elif kw == 'FAIL':
            return {"type": "fail", "expr": parse_expr(tokens[1:])}
        elif kw == 'OK':
            return {"type": "ok", "expr": parse_expr(tokens[1:])}
        elif kw == 'CALL':
            return self._parse_call(tokens)
        elif kw == 'STOCK':
            return self._parse_stock(tokens)
        elif kw == 'BREAK':
            return {"type": "break"}
        elif kw == 'CONTINUE':
            return {"type": "continue"}
        raise SyntaxError(f"Unknown script instruction: {kw}")

    def _parse_stock(self, tokens):
        """Parse a mapped STOCK operation ending with END STOCK."""
        if len(tokens) < 2:
            raise SyntaxError("STOCK requires RECEIVE, ISSUE, ALLOCATE, or TRANSFER")
        operation = tokens[1].upper()
        allowed_operations = {"RECEIVE", "ISSUE", "ALLOCATE", "TRANSFER"}
        if operation not in allowed_operations:
            raise SyntaxError(f"Unknown STOCK operation: {operation}")
        reverse = any(token.upper() == "REVERSE" for token in tokens[2:])
        if any(token.upper() != "REVERSE" for token in tokens[2:]):
            raise SyntaxError(f"Invalid STOCK {operation} option")

        allowed_maps = {
            "LINES", "ITEM", "QTY", "UOM", "PACK_UOM", "PACK_QTY", "DATE",
            "DOC", "WAREHOUSE", "FROM_WAREHOUSE", "TO_WAREHOUSE",
            "COST", "LOT", "TYPE", "TYPE_OUT", "TYPE_IN", "DEMAND",
            "LOTS", "RESULT",
        }
        mappings = {}
        while True:
            line = self.next_line()
            if line is None:
                raise SyntaxError(f"STOCK {operation} requires END STOCK")
            if line.upper() == "END STOCK":
                break
            for segment in line.split("|"):
                segment = segment.strip()
                map_tokens = tokenize(segment)
                if len(map_tokens) < 2:
                    raise SyntaxError(f"Invalid STOCK mapping: {segment}")
                role = map_tokens[0].upper()
                if role not in allowed_maps:
                    raise SyntaxError(f"Unknown STOCK mapping: {role}")
                if role == "RESULT":
                    if (len(map_tokens) != 2 or
                            not re.match(r'^[A-Za-z_]\w*$', map_tokens[1])):
                        raise SyntaxError("STOCK RESULT requires a variable name")
                    mappings[role] = map_tokens[1]
                else:
                    mappings[role] = parse_expr(map_tokens[1:])
        for required in ("ITEM", "QTY"):
            if required not in mappings:
                raise SyntaxError(f"STOCK {operation} requires {required}")
        return {
            "type": "stock", "operation": operation.lower(),
            "reverse": reverse, "mappings": mappings,
        }

    def _parse_set(self, tokens):
        # SET var = expr
        if len(tokens) < 4 or tokens[2] != '=':
            return None
        var_name = tokens[1]
        expr = parse_expr(tokens[3:])
        return {"type": "set", "var": var_name, "expr": expr}

    def _parse_if(self, tokens):
        # IF expr ... END (with optional ELIF/ELSE)
        cond = parse_expr(tokens[1:])
        body = self._parse_block(['END', 'ELIF', 'ELSE'])
        branches = [{"cond": cond, "body": body}]
        # Parse ELIF / ELSE
        while True:
            line = self.peek_line()
            if line is None:
                break
            up = line.upper()
            if up == 'END':
                self.next_line()
                break
            elif up.startswith('ELIF'):
                self.next_line()
                elif_tokens = tokenize(line)
                elif_cond = parse_expr(elif_tokens[1:])
                elif_body = self._parse_block(['END', 'ELIF', 'ELSE'])
                branches.append({"cond": elif_cond, "body": elif_body})
            elif up == 'ELSE':
                self.next_line()
                else_body = self._parse_block(['END'])
                branches.append({"cond": None, "body": else_body})
                # Consume END
                if self.peek_line() and self.peek_line().upper() == 'END':
                    self.next_line()
                break
            else:
                break
        return {"type": "if", "branches": branches}

    def _parse_foreach(self, tokens):
        # FOREACH var IN expr
        if len(tokens) < 4:
            return None
        var_name = tokens[1]
        # Find IN keyword
        in_idx = next((i for i, t in enumerate(tokens)
                       if t.upper() == 'IN'), None)
        if in_idx is None:
            return None
        iter_expr = parse_expr(tokens[in_idx + 1:])
        body = self._parse_block(['END'])
        if self.peek_line() and self.peek_line().upper() == 'END':
            self.next_line()
        return {"type": "foreach", "var": var_name,
                "iter_expr": iter_expr, "body": body}

    def _parse_fetch(self, tokens):
        # FETCH var FROM table WHERE field = expr [AND field = expr]
        if len(tokens) < 4:
            raise SyntaxError("FETCH requires a variable, FROM, and table")
        var_name = tokens[1]
        # Find FROM
        from_idx = next((i for i, t in enumerate(tokens)
                         if t.upper() == 'FROM'), None)
        if from_idx is None:
            raise SyntaxError("FETCH requires FROM")
        if from_idx + 1 >= len(tokens):
            raise SyntaxError("FETCH requires a table name")
        table = tokens[from_idx + 1]
        # Find WHERE
        where_idx = next((i for i, t in enumerate(tokens)
                          if t.upper() == 'WHERE'), None)
        where_clauses = []
        if where_idx is not None:
            where_clauses = self._parse_where(tokens[where_idx + 1:])
        return {"type": "fetch", "var": var_name, "table": table,
                "where": where_clauses}

    def _parse_update(self, tokens):
        # UPDATE table SET f=v, f=v WHERE field = expr
        if len(tokens) < 4:
            raise SyntaxError("UPDATE requires a table, SET, and assignment")
        table = tokens[1]
        # Find SET and WHERE positions
        set_idx = next((i for i, t in enumerate(tokens)
                        if t.upper() == 'SET'), None)
        where_idx = next((i for i, t in enumerate(tokens)
                          if t.upper() == 'WHERE'), None)
        if set_idx is None:
            raise SyntaxError("UPDATE requires SET")
        set_end = where_idx if where_idx else len(tokens)
        sets = self._parse_assignments(tokens[set_idx + 1:set_end])
        where_clauses = []
        if where_idx is not None:
            where_clauses = self._parse_where(tokens[where_idx + 1:])
        return {"type": "update", "table": table, "sets": sets,
                "where": where_clauses}

    def _parse_insert(self, tokens):
        # INSERT table SET f=v, f=v
        if len(tokens) < 4:
            raise SyntaxError("INSERT requires a table, SET, and assignment")
        table = tokens[1]
        set_idx = next((i for i, t in enumerate(tokens)
                        if t.upper() == 'SET'), None)
        if set_idx is None:
            raise SyntaxError("INSERT requires SET")
        sets = self._parse_assignments(tokens[set_idx + 1:])
        return {"type": "insert", "table": table, "sets": sets}

    def _parse_call(self, tokens):
        """Parse CALL name or CALL name(arg1, arg2, ...).

        Returns {"type": "call", "script": name, "args": [expr_ast, ...]}.
        """
        if len(tokens) < 2:
            raise SyntaxError("CALL requires a script name")
        name = tokens[1]
        args = []
        # Check for parenthesized arguments: CALL name ( ... )
        if len(tokens) > 2 and tokens[2] == '(':
            # Find closing paren
            depth = 1
            arg_start = 3
            i = 3
            while i < len(tokens) and depth > 0:
                if tokens[i] == '(':
                    depth += 1
                elif tokens[i] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth != 0:
                raise SyntaxError(f"Expected ')' in CALL {name}()")
            # tokens[3..i) are the argument tokens
            arg_tokens = tokens[3:i]
            if arg_tokens:
                # Split on commas
                parts = self._split_on_comma(arg_tokens)
                for part in parts:
                    if part:
                        args.append(parse_expr(part))
            if i + 1 != len(tokens):
                raise SyntaxError(f"Unexpected token '{tokens[i + 1]}' after CALL {name}()")
        elif len(tokens) > 2:
            raise SyntaxError(f"Expected '(' after CALL {name}")
        return {"type": "call", "script": name, "args": args}

    def _parse_delete(self, tokens):
        # DELETE table WHERE field = expr
        if len(tokens) < 2:
            raise SyntaxError("DELETE requires a table name")
        table = tokens[1]
        where_idx = next((i for i, t in enumerate(tokens)
                          if t.upper() == 'WHERE'), None)
        where_clauses = []
        if where_idx is not None:
            where_clauses = self._parse_where(tokens[where_idx + 1:])
        return {"type": "delete", "table": table, "where": where_clauses}

    def _parse_assignments(self, tokens) -> Dict[str, Any]:
        """Parse f=v, f=v assignments into {field: expr_ast}."""
        sets = {}
        parts = self._split_on_comma(tokens)
        for part in parts:
            if len(part) < 3 or part[1] != '=':
                raise SyntaxError("Invalid assignment; expected field = expression")
            field = part[0]
            expr = parse_expr(part[2:])
            sets[field] = expr
        return sets

    def _parse_where(self, tokens) -> List[dict]:
        """Parse WHERE field = expr [AND field = expr]."""
        clauses = []
        # Split on AND
        parts = []
        current = []
        for t in tokens:
            if t.upper() == 'AND':
                if current:
                    parts.append(current)
                current = []
            else:
                current.append(t)
        if current:
            parts.append(current)
        for part in parts:
            if len(part) < 3 or part[1] not in ('=', '!=', '<>', '>', '>=', '<', '<='):
                raise SyntaxError("Invalid WHERE clause; expected field operator expression")
            field = part[0]
            op = part[1]
            expr = parse_expr(part[2:])
            clauses.append({"field": field, "op": op, "expr": expr})
        return clauses

    def _split_on_comma(self, tokens) -> List[List[str]]:
        """Split token list on commas, respecting parentheses."""
        parts = []
        current = []
        depth = 0
        for t in tokens:
            if t == '(':
                depth += 1
            elif t == ')':
                depth -= 1
            if t == ',' and depth == 0:
                if current:
                    parts.append(current)
                current = []
            else:
                current.append(t)
        if current:
            parts.append(current)
        return parts


# ── Script Executor ──────────────────────────────────────────────────

def _log_exception(exc, context: str = ""):
    """Append a timestamped traceback for a swallowed exception.

    Mirrors ScriptRunner._log in runner.py. Many DB ops here catch
    Exception and continue (to keep the script running), but doing so
    silently hid failures. Best-effort: never raises.
    """
    import datetime
    import traceback as _tb
    try:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        header = f"[{stamp}] script_engine {context}: {exc!r}\n"
        body = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        with open("errors.log", "a", encoding="utf-8") as fh:
            fh.write(header + body + "\n")
    except Exception:
        pass


class ScriptError(Exception):
    """Raised by FAIL instruction — carries the user message."""
    pass

class BreakSignal(Exception):
    pass

class ContinueSignal(Exception):
    pass


class ScriptExecutor:
    """Execute parsed script AST against a database."""

    def __init__(self, db, scripts: Dict[str, list] = None,
                 settings_fn=None):
        self.db = db
        self.scripts = scripts or {}
        self._settings_fn = settings_fn  # callable(key, default) -> value

    def run(self, instructions: List[dict], ctx: dict) -> str:
        """Execute instructions. Returns OK message or empty string.
        Raises ScriptError on FAIL.

        Wraps the whole script in a single DB transaction when the adapter
        supports begin/commit/rollback, so a multi-statement posting (e.g.
        GRN updating item + po_line + inserting item_card) is atomic: if any
        statement fails, none of the partial changes persist.
        """
        has_txn = hasattr(self.db, "begin")
        started_txn = has_txn and not getattr(self.db, "_in_txn", False)
        if started_txn:
            self.db.begin()
        try:
            self._exec_block(instructions, ctx)
        except ScriptError:
            if started_txn: self.db.rollback()
            raise
        except BreakSignal:
            pass
        except ContinueSignal:
            pass
        except Exception:
            # Any other failure (DB error etc.) — roll back the partial work.
            if started_txn: self.db.rollback()
            raise
        else:
            if started_txn: self.db.commit()
        return ctx.get("_ok_msg", "")

    def _exec_block(self, instructions: List[dict], ctx: dict):
        for inst in instructions:
            self._exec(inst, ctx)

    def _exec(self, inst: dict, ctx: dict):
        itype = inst["type"]
        if itype == "set":
            self._exec_set(inst, ctx)
        elif itype == "if":
            self._exec_if(inst, ctx)
        elif itype == "foreach":
            self._exec_foreach(inst, ctx)
        elif itype == "fetch":
            self._exec_fetch(inst, ctx)
        elif itype == "update":
            self._exec_update(inst, ctx)
        elif itype == "insert":
            self._exec_insert(inst, ctx)
        elif itype == "delete":
            self._exec_delete(inst, ctx)
        elif itype == "fail":
            msg = str(self._eval(inst["expr"], ctx))
            raise ScriptError(msg)
        elif itype == "ok":
            ctx["_ok_msg"] = str(self._eval(inst["expr"], ctx))
        elif itype == "call":
            self._exec_call(inst, ctx)
        elif itype == "stock":
            self._exec_stock(inst, ctx)
        elif itype == "break":
            raise BreakSignal()
        elif itype == "continue":
            raise ContinueSignal()

    # ── Instruction handlers ──

    def _exec_set(self, inst, ctx):
        ctx["vars"][inst["var"]] = self._eval(inst["expr"], ctx)

    def _exec_if(self, inst, ctx):
        for branch in inst["branches"]:
            if branch["cond"] is None:
                # ELSE branch
                self._exec_block(branch["body"], ctx)
                return
            val = self._eval(branch["cond"], ctx)
            if self._truthy(val):
                self._exec_block(branch["body"], ctx)
                return

    def _exec_foreach(self, inst, ctx):
        var_name = inst["var"]
        iterable = self._eval(inst["iter_expr"], ctx)
        if not isinstance(iterable, list):
            return
        for item in iterable:
            ctx["vars"][var_name] = item
            try:
                self._exec_block(inst["body"], ctx)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        # Clean up loop var
        ctx["vars"].pop(var_name, None)

    def _exec_fetch(self, inst, ctx):
        """FETCH var FROM table WHERE field = expr"""
        var_name = inst["var"]
        table = inst["table"]
        where = inst["where"]
        rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            doc["_rowid"] = row.get("rowid")
            if self._match_where(doc, where, ctx):
                ctx["vars"][var_name] = doc
                return
        # Not found — set to None-like dict
        ctx["vars"][var_name] = {"_rowid": None}

    def _exec_update(self, inst, ctx):
        """UPDATE table SET f=v WHERE field = expr"""
        table = inst["table"]
        sets = inst["sets"]
        where = inst["where"]
        rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            doc["_rowid"] = row.get("rowid")
            if self._match_where(doc, where, ctx):
                for field, expr in sets.items():
                    doc[field] = self._eval(expr, ctx)
                rowid = row["rowid"]
                clean = {k: v for k, v in doc.items() if k != "_rowid"}
                self.db.execute(
                    f'UPDATE "{table}" SET data=? WHERE rowid=?',
                    (json.dumps(clean), rowid))
                # Update any fetched var that points to this record
                for vname, vval in ctx["vars"].items():
                    if isinstance(vval, dict) and vval.get("_rowid") == rowid:
                        vval.update(doc)

    def _exec_insert(self, inst, ctx):
        """INSERT table SET f=v, f=v"""
        table = inst["table"]
        doc = {}
        for field, expr in inst["sets"].items():
            val = self._eval(expr, ctx)
            if isinstance(val, (date, datetime)):
                val = val.isoformat()
            doc[field] = val
        self.db.execute(f'INSERT INTO "{table}" (data) VALUES (?)',
                        (json.dumps(doc),))
        # Store last insert rowid
        try:
            last = self.db.query(
                f'SELECT MAX(rowid) as rid FROM "{table}"')
            if last:
                ctx["vars"]["_last_rowid"] = last[0].get("rid")
        except Exception as e:
            _log_exception(e, context=f"INSERT {table} (last_rowid lookup)")

    def _exec_delete(self, inst, ctx):
        """DELETE table WHERE field = expr"""
        table = inst["table"]
        where = inst["where"]
        rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            doc["_rowid"] = row.get("rowid")
            if self._match_where(doc, where, ctx):
                self.db.execute(
                    f'DELETE FROM "{table}" WHERE rowid=?',
                    (row["rowid"],))

    def _exec_stock(self, inst, ctx):
        """Execute a field-mapped inventory operation."""
        line_expr = inst.get("mappings", {}).get("LINES")
        if line_expr is None:
            self._exec_stock_one(inst, ctx)
            return
        rows = self._eval(line_expr, ctx)
        if not isinstance(rows, list):
            return
        marker = object()
        previous = ctx["vars"].get("line", marker)
        try:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ctx["vars"]["line"] = row
                self._exec_stock_one(inst, ctx)
        finally:
            if previous is marker:
                ctx["vars"].pop("line", None)
            else:
                ctx["vars"]["line"] = previous

    def _exec_stock_one(self, inst, ctx):
        """Execute one mapped stock row in the current script context."""
        mappings = inst.get("mappings", {})
        values = {
            role: self._eval(expr, ctx)
            for role, expr in mappings.items()
            if role not in ("LINES", "RESULT")
        }
        operation = inst.get("operation", "")
        reverse = bool(inst.get("reverse"))
        part_no = str(values.get("ITEM") or "").strip()
        item = self._stock_find_item(part_no)
        if not item:
            raise ScriptError(f"Stock item not found: {part_no}")

        uom = values.get("UOM") or item.get("uom") or ""
        pack_uom = values.get("PACK_UOM") or item.get("pack_uom") or ""
        pack_qty = values.get("PACK_QTY") or item.get("pack_qty") or 0
        base_qty = self._base_qty(values.get("QTY"), uom, pack_uom, pack_qty)
        if base_qty <= 0:
            raise ScriptError(f"Stock quantity must be greater than zero: {part_no}")
        result_var = mappings.get("RESULT")
        if result_var:
            ctx["vars"][result_var] = round(base_qty, 6)

        trans_date = values.get("DATE") or date.today().isoformat()
        doc_no = values.get("DOC") or ""
        warehouse = values.get("WAREHOUSE") or ""
        lot_no = values.get("LOT") or ""
        lots_enabled = self._truthy(values.get("LOTS", True))
        mapped_cost = (self._to_num(values.get("COST"))
                       if "COST" in values else None)
        item_cost = self._to_num(item.get("unit_cost", 0))
        demand_qty = 0.0
        if "DEMAND" in values:
            demand_qty = self._base_qty(
                values.get("DEMAND"), uom, pack_uom, pack_qty)

        if operation == "receive":
            cost = mapped_cost if mapped_cost is not None else item_cost
            if reverse:
                self._stock_require_on_hand(item, base_qty)
                if lots_enabled:
                    shortage, lot_cost = self._stock_remove_received_lot(
                        item["_rowid"], base_qty, doc_no, warehouse, lot_no, ctx)
                    if shortage > 1e-9:
                        raise ScriptError(f"Insufficient received lot stock: {part_no}")
                    if mapped_cost is None and lot_cost:
                        cost = lot_cost
                self._stock_recalc_item(item, oh_delta=-base_qty)
            else:
                self._stock_recalc_item(item, oh_delta=base_qty)
                if lots_enabled:
                    self._stock_add_lot(item, base_qty, trans_date, doc_no,
                                        warehouse, lot_no, cost)
            self._stock_save_item(item, ctx)
            self._stock_add_card(
                item, trans_date, doc_no,
                values.get("TYPE") or ("VOID-R" if reverse else "RECV"),
                0 if reverse else base_qty, base_qty if reverse else 0, cost)

        elif operation == "issue":
            cost = mapped_cost if mapped_cost is not None else item_cost
            if reverse:
                self._stock_recalc_item(item, oh_delta=base_qty,
                                        demand_delta=demand_qty)
                if lots_enabled:
                    self._stock_add_lot(item, base_qty, trans_date, doc_no,
                                        warehouse, lot_no, cost)
            else:
                self._stock_require_on_hand(item, base_qty)
                if lots_enabled:
                    if warehouse:
                        shortage, _ = self._stock_remove_received_lot(
                            item["_rowid"], base_qty, "", warehouse, lot_no, ctx)
                    else:
                        shortage = self._fifo_out(
                            "item_lot", "on_hand", "entry_date", base_qty,
                            "_parent_rowid", item["_rowid"], ctx)
                    if shortage > 1e-9:
                        raise ScriptError(f"Insufficient lot stock: {part_no}")
                    if mapped_cost is None:
                        cost = self._to_num(ctx["vars"].get("_fifo_avg_cost"))
                self._stock_recalc_item(item, oh_delta=-base_qty,
                                        demand_delta=-demand_qty)
            self._stock_save_item(item, ctx)
            self._stock_add_card(
                item, trans_date, doc_no,
                values.get("TYPE") or ("VOID-I" if reverse else "ISSUE"),
                base_qty if reverse else 0, 0 if reverse else base_qty, cost)

        elif operation == "allocate":
            delta = -base_qty if reverse else base_qty
            self._stock_recalc_item(item, demand_delta=delta)
            self._stock_save_item(item, ctx)
            cost = mapped_cost if mapped_cost is not None else item_cost
            self._stock_add_card(
                item, trans_date, doc_no,
                values.get("TYPE") or ("VOID-A" if reverse else "ALLOC"),
                base_qty if reverse else 0, 0 if reverse else base_qty, cost)

        elif operation == "transfer":
            from_wh = values.get("FROM_WAREHOUSE") or warehouse
            to_wh = values.get("TO_WAREHOUSE") or ""
            if not from_wh or not to_wh:
                raise ScriptError("STOCK TRANSFER requires FROM_WAREHOUSE and TO_WAREHOUSE")
            cost = mapped_cost if mapped_cost is not None else item_cost
            self._stock_require_on_hand(item, base_qty)
            if reverse:
                shortage, lot_cost = self._stock_remove_received_lot(
                    item["_rowid"], base_qty, doc_no, to_wh, lot_no, ctx)
                if shortage > 1e-9:
                    raise ScriptError(f"Insufficient destination lot stock: {part_no}")
                if mapped_cost is None and lot_cost:
                    cost = lot_cost
                self._stock_add_lot(item, base_qty, trans_date, doc_no,
                                    from_wh, lot_no, cost)
                self._stock_add_card(
                    item, trans_date, doc_no,
                    values.get("TYPE") or "VOID-T",
                    base_qty, 0, cost)
            else:
                shortage, _ = self._stock_remove_received_lot(
                    item["_rowid"], base_qty, "", from_wh, lot_no, ctx)
                if shortage > 1e-9:
                    raise ScriptError(f"Insufficient lot stock: {part_no}")
                if mapped_cost is None:
                    cost = self._to_num(ctx["vars"].get("_fifo_avg_cost"))
                self._stock_add_lot(item, base_qty, trans_date, doc_no,
                                    to_wh, lot_no, cost)
                self._stock_add_card(
                    item, trans_date, doc_no,
                    values.get("TYPE_OUT") or "TRF-OUT",
                    0, base_qty, cost)
                self._stock_add_card(
                    item, trans_date, doc_no,
                    values.get("TYPE_IN") or "TRF-IN",
                    base_qty, 0, cost)

    def _stock_find_item(self, part_no):
        rows = self.db.query('SELECT rowid, data FROM "item"')
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            if str(doc.get("part_no", "")) == str(part_no):
                doc["_rowid"] = row.get("rowid")
                return doc
        return None

    def _stock_require_on_hand(self, item, qty):
        on_hand = self._to_num(item.get("on_hand", 0))
        if on_hand + 1e-9 < qty:
            raise ScriptError(
                f"Insufficient stock: {item.get('part_no', '')} "
                f"(have {on_hand:g}, need {qty:g})")

    def _stock_recalc_item(self, item, oh_delta=0.0, demand_delta=0.0):
        on_hand = self._to_num(item.get("on_hand", 0)) + oh_delta
        demand = max(0.0, self._to_num(item.get("allocated", 0)) +
                     self._to_num(item.get("back_order", 0)) + demand_delta)
        allocated = min(on_hand, demand)
        item["on_hand"] = round(on_hand, 6)
        item["allocated"] = round(allocated, 6)
        item["back_order"] = round(demand - allocated, 6)
        item["available"] = round(on_hand - allocated, 6)

    def _stock_save_item(self, item, ctx):
        rowid = item["_rowid"]
        clean = {key: value for key, value in item.items() if key != "_rowid"}
        self.db.execute('UPDATE "item" SET data=? WHERE rowid=?',
                        (json.dumps(clean), rowid))
        for value in ctx.get("vars", {}).values():
            if (isinstance(value, dict) and value.get("_rowid") == rowid
                    and value.get("part_no") == item.get("part_no")):
                value.update(item)

    def _stock_insert(self, table, doc):
        clean = {}
        for key, value in doc.items():
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            clean[key] = value
        self.db.execute(f'INSERT INTO "{table}" (data) VALUES (?)',
                        (json.dumps(clean),))

    def _stock_add_lot(self, item, qty, trans_date, doc_no,
                       warehouse, lot_no, cost):
        self._stock_insert("item_lot", {
            "entry_date": trans_date, "doc_no": doc_no, "lot_no": lot_no,
            "warehouse": warehouse, "on_hand": round(qty, 6),
            "uom": item.get("uom", ""), "unit_cost": cost,
            "_parent_rowid": item["_rowid"],
        })

    def _stock_add_card(self, item, trans_date, doc_no, trans_type,
                        receive_qty, issue_qty, cost):
        self._stock_insert("item_card", {
            "trans_date": trans_date, "trans_type": trans_type,
            "doc_no": doc_no, "receive_qty": round(receive_qty, 6),
            "issue_qty": round(issue_qty, 6),
            "balance": item.get("on_hand", 0), "unit_cost": cost,
            "_parent_rowid": item["_rowid"],
        })

    def _stock_remove_received_lot(self, item_rowid, qty, doc_no,
                                   warehouse, lot_no, ctx):
        rows = self.db.query('SELECT rowid, data FROM "item_lot"')
        matches = []
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            if str(doc.get("_parent_rowid")) != str(item_rowid):
                continue
            if doc_no and str(doc.get("doc_no", "")) != str(doc_no):
                continue
            if warehouse and str(doc.get("warehouse", "")) != str(warehouse):
                continue
            if lot_no and str(doc.get("lot_no", "")) != str(lot_no):
                continue
            doc["_rowid"] = row.get("rowid")
            matches.append(doc)
        matches.sort(key=lambda lot: str(lot.get("entry_date", "")))

        remaining = qty
        total_cost = 0.0
        consumed = 0.0
        for lot in matches:
            if remaining <= 1e-9:
                break
            lot_qty = self._to_num(lot.get("on_hand", 0))
            take = min(lot_qty, remaining)
            if take <= 0:
                continue
            total_cost += take * self._to_num(lot.get("unit_cost", 0))
            consumed += take
            new_qty = lot_qty - take
            if new_qty <= 1e-9:
                self.db.execute('DELETE FROM "item_lot" WHERE rowid=?',
                                (lot["_rowid"],))
            else:
                clean = {key: value for key, value in lot.items()
                         if key != "_rowid"}
                clean["on_hand"] = round(new_qty, 6)
                self.db.execute(
                    'UPDATE "item_lot" SET data=? WHERE rowid=?',
                    (json.dumps(clean), lot["_rowid"]))
            remaining -= take
        avg_cost = total_cost / consumed if consumed > 1e-9 else 0.0
        ctx["vars"]["_fifo_total_cost"] = round(total_cost, 4)
        ctx["vars"]["_fifo_avg_cost"] = round(avg_cost, 4)
        return max(0.0, remaining), round(avg_cost, 4)

    def _exec_call(self, inst, ctx):
        """CALL script_name or CALL script_name(arg1, arg2).

        For subroutines with params, binds positional args to param names
        in ctx vars, executes body, then cleans up param vars.
        """
        name = inst["script"]
        call_args = inst.get("args", [])
        target = self.scripts.get(name)
        if target is None:
            return

        # Check if target is a subroutine (dict with params + body)
        if isinstance(target, dict) and "params" in target:
            params = target["params"]
            body = target["body"]
            # Save any existing vars that would be shadowed
            saved = {}
            for i, pname in enumerate(params):
                if pname in ctx["vars"]:
                    saved[pname] = ctx["vars"][pname]
                if i < len(call_args):
                    ctx["vars"][pname] = self._eval(call_args[i], ctx)
                else:
                    ctx["vars"][pname] = None
            self._exec_block(body, ctx)
            # Restore shadowed vars, remove params that didn't exist before
            for pname in params:
                if pname in saved:
                    ctx["vars"][pname] = saved[pname]
                else:
                    ctx["vars"].pop(pname, None)
        else:
            # Plain script (list of instructions) — backward compatible
            self._exec_block(target, ctx)

    # ── WHERE matching ──

    def _match_where(self, doc: dict, where: List[dict], ctx: dict) -> bool:
        if not where:
            return True
        for clause in where:
            field = clause["field"]
            op = clause["op"]
            expected = self._eval(clause["expr"], ctx)
            actual = doc.get(field, "")
            if not self._compare(actual, op, expected):
                return False
        return True

    def _compare(self, left, op, right) -> bool:
        # Normalize for comparison
        left = self._coerce_for_compare(left)
        right = self._coerce_for_compare(right)
        try:
            if op == '=':
                return left == right
            elif op in ('!=', '<>'):
                return left != right
            elif op == '>':
                return float(left) > float(right)
            elif op == '>=':
                return float(left) >= float(right)
            elif op == '<':
                return float(left) < float(right)
            elif op == '<=':
                return float(left) <= float(right)
        except (ValueError, TypeError):
            return str(left) == str(right) if op == '=' else str(left) != str(right)
        return False

    def _coerce_for_compare(self, val):
        if val is None:
            return ""
        if isinstance(val, (int, float)):
            return val
        s = str(val)
        try:
            return float(s) if '.' in s else int(s)
        except (ValueError, TypeError):
            return s

    # ── Expression evaluator ──

    def _eval(self, expr: dict, ctx: dict):
        if expr is None:
            return ""
        etype = expr.get("type", "")

        if etype == "literal":
            return expr["value"]

        elif etype == "ref":
            return self._resolve_ref(expr["name"], ctx)

        elif etype == "dot":
            obj_name = expr["obj"]
            field = expr["field"]
            obj = ctx["vars"].get(obj_name)
            if isinstance(obj, dict):
                return obj.get(field, "")
            return ""

        elif etype == "binop":
            left = self._eval(expr["left"], ctx)
            right = self._eval(expr["right"], ctx)
            return self._binop(expr["op"], left, right)

        elif etype == "compare":
            left = self._eval(expr["left"], ctx)
            right = self._eval(expr["right"], ctx)
            return self._compare(left, expr["op"], right)

        elif etype == "logic":
            left = self._eval(expr["left"], ctx)
            if expr["op"] == "AND":
                return self._truthy(left) and self._truthy(
                    self._eval(expr["right"], ctx))
            elif expr["op"] == "OR":
                return self._truthy(left) or self._truthy(
                    self._eval(expr["right"], ctx))

        elif etype == "not":
            return not self._truthy(self._eval(expr["expr"], ctx))

        elif etype == "is_null":
            val = self._eval(expr["expr"], ctx)
            # NULL means absent/empty only — a legitimate 0 is NOT null.
            # (Previously `or val == 0` misclassified on_hand=0 etc. as null.)
            return val is None or val == ""

        elif etype == "is_not_null":
            val = self._eval(expr["expr"], ctx)
            return val is not None and val != ""

        elif etype == "func":
            return self._eval_func(expr["name"], expr["args"], ctx)

        return ""

    def _resolve_ref(self, name: str, ctx: dict):
        """Resolve a bare name: vars > fields > empty."""
        # Script-local variables
        if name in ctx["vars"]:
            return ctx["vars"][name]
        # Header fields (current record)
        if name in ctx["fields"]:
            val = ctx["fields"][name]
            if isinstance(val, (date, datetime)):
                return val.isoformat()
            return val
        # Special refs
        if name == "id":
            return ctx.get("id", "")
        if name == "lines":
            return ctx.get("lines", [])
        if name == "_last_rowid":
            return ctx["vars"].get("_last_rowid", "")
        return ""

    def _binop(self, op, left, right):
        if op == '+':
            # String concatenation if either side is a non-numeric string
            if isinstance(left, str) and not self._is_numeric(left):
                return str(left) + str(right)
            if isinstance(right, str) and not self._is_numeric(right):
                return str(left) + str(right)
            try:
                return self._to_num(left) + self._to_num(right)
            except (ValueError, TypeError):
                return str(left) + str(right)
        try:
            l, r = self._to_num(left), self._to_num(right)
            if op == '-':
                return l - r
            elif op == '*':
                return l * r
            elif op == '/':
                return l / r if r != 0 else 0
        except (ValueError, TypeError):
            return 0

    def _eval_func(self, name, args, ctx):
        vals = [self._eval(a, ctx) for a in args]

        if name == 'ADD':
            return self._to_num(vals[0]) + self._to_num(vals[1]) if len(vals) >= 2 else 0
        elif name == 'SUB':
            return self._to_num(vals[0]) - self._to_num(vals[1]) if len(vals) >= 2 else 0
        elif name == 'TODAY':
            return date.today().isoformat()
        elif name == 'NOW':
            return datetime.now().isoformat()
        elif name == 'COUNT':
            return len(vals[0]) if vals and isinstance(vals[0], list) else 0
        elif name == 'SUM':
            if len(vals) >= 2 and isinstance(vals[0], list):
                field = str(vals[1])
                return sum(self._to_num(r.get(field, 0))
                           for r in vals[0] if isinstance(r, dict))
            return 0
        elif name == 'MIN':
            return min(self._to_num(vals[0]), self._to_num(vals[1])) if len(vals) >= 2 else 0
        elif name == 'MAX':
            return max(self._to_num(vals[0]), self._to_num(vals[1])) if len(vals) >= 2 else 0
        elif name == 'ABS':
            return abs(self._to_num(vals[0])) if vals else 0
        elif name == 'ROUND':
            n = self._to_num(vals[0]) if vals else 0
            d = int(self._to_num(vals[1])) if len(vals) >= 2 else 0
            return round(n, d)
        elif name == 'ISNULL':
            v = vals[0] if vals else None
            # NULL means absent/empty only — a legitimate 0 is NOT null.
            return v is None or v == ""
        elif name == 'ISBLANK':
            v = vals[0] if vals else None
            return v is None or str(v).strip() == ""
        elif name == 'STR':
            v = vals[0] if vals else ""
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return str(v)
        elif name == 'NUM':
            return self._to_num(vals[0]) if vals else 0
        elif name == 'UPPER':
            return str(vals[0]).upper() if vals else ""
        elif name == 'LEN':
            v = vals[0] if vals else ""
            return len(v) if isinstance(v, (str, list)) else 0
        elif name == 'BOMEXPLODE':
            model_no = str(vals[0]) if vals else ""
            compute_qty = self._to_num(vals[1]) if len(vals) >= 2 else 1
            return self._bom_explode(model_no, compute_qty)
        elif name == 'BOMROLLUP':
            model_no = str(vals[0]) if vals else ""
            compute_qty = self._to_num(vals[1]) if len(vals) >= 2 else 1
            exploded = self._bom_explode(model_no, compute_qty)
            return self._bom_rollup(exploded)
        elif name == 'FIFOOUT':
            # FIFOOUT(table, qty_field, sort_field, deduct_qty, key_field, key_value)
            # Deducts qty FIFO from lot table. Deletes fully consumed rows.
            # Sets ctx vars: _fifo_total_cost, _fifo_avg_cost
            # Returns shortage (0 = fully satisfied).
            if len(vals) < 6:
                return self._to_num(vals[3]) if len(vals) >= 4 else 0.0
            return self._fifo_out(
                table=str(vals[0]), qty_field=str(vals[1]),
                sort_field=str(vals[2]), deduct_qty=self._to_num(vals[3]),
                key_field=str(vals[4]), key_value=vals[5], ctx=ctx)
        elif name == 'COUNTROWS':
            # COUNTROWS(table, key_field, key_value) — count matching rows
            if len(vals) < 3:
                return 0
            return self._count_rows(str(vals[0]), str(vals[1]), vals[2])
        elif name == 'BASEQTY':
            if len(vals) < 4:
                return self._to_num(vals[0]) if vals else 0
            return self._base_qty(vals[0], vals[1], vals[2], vals[3])
        elif name == 'DISPLAYQTY':
            if len(vals) < 4:
                return self._to_num(vals[0]) if vals else 0
            return self._display_qty(vals[0], vals[1], vals[2], vals[3])

        elif name == 'NEXTDOCNO':
            # NEXTDOCNO(table, field, prefix) or NEXTDOCNO(table, field, prefix, format)
            # format: "YYMM" for monthly sequence, omit for simple sequential
            if len(vals) < 3:
                return ""
            tbl = str(vals[0])
            fld = str(vals[1])
            pfx = str(vals[2])
            fmt = str(vals[3]).upper() if len(vals) > 3 else None
            return self._next_doc_no(tbl, fld, pfx, fmt)

        # ── Warehouse: Put-Away / Picking ──
        elif name == 'PUTAWAY_ALLOCATE':
            # PUTAWAY_ALLOCATE(warehouse, grn_lines, for_customer, grn_no)
            if len(vals) < 2:
                return []
            wh_id = str(vals[0] or '').strip()
            grn_lines = vals[1] if isinstance(vals[1], list) else []
            for_cust = str(vals[2] or '').strip() if len(vals) > 2 else ''
            grn_no = str(vals[3] or '').strip() if len(vals) > 3 else ''
            return self._putaway_allocate(wh_id, grn_lines, for_cust,
                                          grn_no)
        elif name == 'PICKING_ALLOCATE':
            # PICKING_ALLOCATE(warehouse, do_lines)
            if len(vals) < 2:
                return []
            wh_id = str(vals[0] or '').strip()
            do_lines = vals[1] if isinstance(vals[1], list) else []
            return self._picking_allocate(wh_id, do_lines)
        elif name == 'PICKING_CONFIRM':
            # PICKING_CONFIRM(warehouse, pick_lines)
            if len(vals) < 2:
                return 0
            wh_id = str(vals[0] or '').strip()
            pick_lines = vals[1] if isinstance(vals[1], list) else []
            return self._picking_confirm(wh_id, pick_lines)

        # ── Math (new) ──
        elif name == 'CEIL':
            return math.ceil(self._to_num(vals[0])) if vals else 0
        elif name == 'FLOOR':
            return math.floor(self._to_num(vals[0])) if vals else 0
        elif name == 'DIV':
            if len(vals) >= 2:
                d = self._to_num(vals[1])
                return self._to_num(vals[0]) / d if d else 0
            return 0

        # ── String (new) ──
        elif name == 'LOWER':
            return str(vals[0]).lower() if vals else ""
        elif name == 'TRIM':
            return str(vals[0]).strip() if vals else ""
        elif name == 'REPLACE':
            # REPLACE(string, old, new)
            if len(vals) >= 3:
                return str(vals[0]).replace(str(vals[1]), str(vals[2]))
            return str(vals[0]) if vals else ""
        elif name == 'LPAD':
            # LPAD(value, width [, pad_char])
            if len(vals) >= 2:
                s = str(vals[0])
                w = int(self._to_num(vals[1]))
                ch = str(vals[2]) if len(vals) >= 3 else "0"
                return s.rjust(w, ch[0] if ch else "0")
            return str(vals[0]) if vals else ""
        elif name == 'LEFT':
            # LEFT(string, n)
            if len(vals) >= 2:
                return str(vals[0])[:int(self._to_num(vals[1]))]
            return str(vals[0]) if vals else ""
        elif name == 'RIGHT':
            # RIGHT(string, n)
            if len(vals) >= 2:
                n = int(self._to_num(vals[1]))
                return str(vals[0])[-n:] if n > 0 else ""
            return str(vals[0]) if vals else ""
        elif name == 'CONCAT':
            return "".join(str(v) for v in vals)

        # ── Date (new) ──
        elif name == 'DATEDIFF':
            # DATEDIFF(date1, date2) — days between (date1 - date2)
            if len(vals) >= 2:
                try:
                    d1 = date.fromisoformat(str(vals[0])[:10])
                    d2 = date.fromisoformat(str(vals[1])[:10])
                    return (d1 - d2).days
                except (ValueError, TypeError):
                    return 0
            return 0
        elif name == 'DATEADD':
            # DATEADD(date, days) — add N days to date
            if len(vals) >= 2:
                try:
                    d = date.fromisoformat(str(vals[0])[:10])
                    return (d + timedelta(days=int(self._to_num(vals[1])))).isoformat()
                except (ValueError, TypeError):
                    return str(vals[0]) if vals else ""
            return str(vals[0]) if vals else ""
        elif name == 'YEAR':
            try:
                return date.fromisoformat(str(vals[0])[:10]).year if vals else 0
            except (ValueError, TypeError):
                return 0
        elif name == 'MONTH':
            try:
                return date.fromisoformat(str(vals[0])[:10]).month if vals else 0
            except (ValueError, TypeError):
                return 0
        elif name == 'DAY':
            try:
                return date.fromisoformat(str(vals[0])[:10]).day if vals else 0
            except (ValueError, TypeError):
                return 0
        elif name == 'QUARTER':
            try:
                m = date.fromisoformat(str(vals[0])[:10]).month if vals else 0
                return (m - 1) // 3 + 1 if m else 0
            except (ValueError, TypeError):
                return 0
        elif name == 'YEARMONTH':
            # YEARMONTH(date) — returns "YYYY-MM"
            try:
                d = date.fromisoformat(str(vals[0])[:10]) if vals else None
                return d.strftime("%Y-%m") if d else ""
            except (ValueError, TypeError):
                return ""
        elif name == 'DAYOFWEEK':
            # DAYOFWEEK(date) — 1=Mon, 7=Sun
            try:
                d = date.fromisoformat(str(vals[0])[:10]) if vals else None
                return d.isoweekday() if d else 0
            except (ValueError, TypeError):
                return 0

        # ── Aggregation (new) ──
        elif name == 'SUMIF':
            # SUMIF(list, sum_field, cond_field, cond_value)
            if len(vals) >= 4 and isinstance(vals[0], list):
                sum_f = str(vals[1])
                cond_f = str(vals[2])
                cond_v = vals[3]
                total = 0
                for r in vals[0]:
                    if isinstance(r, dict) and r.get(cond_f) == cond_v:
                        total += self._to_num(r.get(sum_f, 0))
                return total
            return 0

        # ── Lookup (new) ──
        elif name == 'LOOKUPVALUE':
            # LOOKUPVALUE(table, return_field, match_field, match_value)
            if len(vals) >= 4:
                return self._lookup_value(
                    str(vals[0]), str(vals[1]), str(vals[2]), vals[3])
            return ""
        elif name == 'LOOKUPRANGE':
            # LOOKUPRANGE(table, return_field, range_field, value [, key_field, key_value])
            if len(vals) >= 4:
                key_f = str(vals[4]) if len(vals) >= 5 else None
                key_v = vals[5] if len(vals) >= 6 else None
                return self._lookup_range(
                    str(vals[0]), str(vals[1]), str(vals[2]),
                    self._to_num(vals[3]), key_f, key_v)
            return ""
        elif name == 'LOOKUPSTEP':
            # LOOKUPSTEP(table, return_field, step_field, value [, key_field, key_value])
            # Same as LOOKUPRANGE but finds highest step_field <= value
            if len(vals) >= 4:
                key_f = str(vals[4]) if len(vals) >= 5 else None
                key_v = vals[5] if len(vals) >= 6 else None
                return self._lookup_step(
                    str(vals[0]), str(vals[1]), str(vals[2]),
                    self._to_num(vals[3]), key_f, key_v)
            return ""

        # ── Business Settings ──
        elif name == 'SETTING':
            # SETTING("key") — read from business_settings
            if vals and self._settings_fn:
                return self._settings_fn(str(vals[0]), "")
            return ""

        return ""

    # ── BOM Explosion Engine ──

    def _bom_explode(self, model_no: str, compute_qty: float,
                     _depth: int = 0, _visited: set = None) -> list:
        """Recursively explode a BOM into a flat material list.

        Formula per component:
          usage = (compute_qty / base_qty) * (qty / per) * (1 + safety_percent / 100)

        LIB/EXWORK components use part_no to find another BOM model and recurse.
        Tree data stored as nested JSON in bom_line table with _tree_data key.
        """
        if _visited is None:
            _visited = set()
        if model_no in _visited or _depth > 20:
            return []
        _visited.add(model_no)

        # Fetch BOM header by model_no
        bom_header = None
        try:
            rows = self.db.query('SELECT rowid, data FROM "bom"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if doc.get("model_no") == model_no:
                    doc["_rowid"] = row.get("rowid")
                    bom_header = doc
                    break
        except Exception as e:
            _log_exception(e, context="bom_header lookup")
            return []
        if not bom_header:
            return []

        base_qty = max(float(bom_header.get("base_qty", 1) or 1), 0.0001)
        parent_rowid = bom_header["_rowid"]

        # Load tree data (nested JSON stored with _tree_data key)
        tree_nodes = []
        try:
            rows = self.db.query('SELECT data FROM "bom_line"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if doc.get("_parent_rowid") == parent_rowid and "_tree_data" in doc:
                    tree_nodes = doc["_tree_data"]
                    break
        except Exception as e:
            _log_exception(e, context=f"tree_data lookup parent={parent_rowid}")
            return []

        # Build item cost cache
        item_cache = {}
        try:
            item_rows = self.db.query('SELECT data FROM "item"')
            for ir in item_rows:
                idoc = json.loads(ir.get("data", "{}"))
                pn = idoc.get("part_no", "")
                if pn:
                    item_cache[pn] = float(idoc.get("unit_cost", 0) or 0)
        except Exception as e:
            _log_exception(e, context="item_cache build")

        # Walk tree nodes recursively
        result = []
        self._walk_bom_tree(tree_nodes, compute_qty, base_qty, model_no,
                            _depth, _visited, item_cache, result, "")
        return result

    def _walk_bom_tree(self, nodes, compute_qty, base_qty, source_bom,
                       _depth, _visited, item_cache, result, parent_path):
        """Walk nested BOM tree nodes, computing usage and recursing into sub-assemblies."""
        for comp in (nodes or []):
            if not isinstance(comp, dict):
                continue
            qty = float(comp.get("qty", 0) or 0)
            per = float(comp.get("per", 1) or 1) or 1
            safety_pct = float(comp.get("safety_percent", 0) or 0)
            usage = (compute_qty / base_qty) * (qty / per) * (1 + safety_pct / 100)

            part_no = str(comp.get("part_no", ""))
            part_level = str(comp.get("part_level", "PART"))
            comp_name = str(comp.get("component", "")) or part_no
            path = f"{parent_path} / {comp_name}" if parent_path else comp_name

            entry = {
                "part_no": part_no,
                "vendor_id": str(comp.get("vendor_id", "")),
                "part_name": str(comp.get("part_name", "")),
                "uom": str(comp.get("uom", "")),
                "part_level": part_level,
                "total_usage": round(usage, 4),
                "unit_cost": item_cache.get(part_no, 0),
                "path": path,
                "level": _depth,
                "source_bom": source_bom,
            }
            result.append(entry)

            # Walk children at same depth+1
            children = comp.get("children") or []
            if children:
                self._walk_bom_tree(children, usage, 1, source_bom,
                                    _depth + 1, _visited, item_cache, result,
                                    path)

            # LIB/EXWORK: part_no references another BOM model
            if part_level in ("LIB", "EXWORK") and part_no:
                sub_result = self._bom_explode(
                    part_no, usage, _depth + 1, _visited.copy())
                result.extend(sub_result)

    def _bom_rollup(self, exploded: list) -> list:
        """Aggregate exploded BOM by part_no, summing total_usage.
        Skips LIB/EXWORK (sub-assembly headers)."""
        agg = {}
        for item in exploded:
            if item.get("part_level") in ("LIB", "EXWORK"):
                continue
            pn = item.get("part_no", "")
            if pn in agg:
                agg[pn]["total_usage"] = round(
                    agg[pn]["total_usage"] + item.get("total_usage", 0), 4)
            else:
                agg[pn] = dict(item)
                agg[pn]["level"] = 0
        return sorted(agg.values(), key=lambda x: x.get("part_no", ""))

    # ── FIFO / Lot helpers ──

    def _fifo_out(self, table: str, qty_field: str, sort_field: str,
                  deduct_qty: float, key_field: str, key_value, ctx: dict) -> float:
        """FIFO deduction from a lot table.

        Sorts rows by sort_field ascending (oldest first), deducts qty
        row by row. Fully consumed rows are deleted. Returns shortage
        (0 = fully satisfied). Sets ctx _fifo_total_cost, _fifo_avg_cost.
        """
        remaining = deduct_qty
        if remaining <= 1e-9:
            ctx["vars"]["_fifo_total_cost"] = 0.0
            ctx["vars"]["_fifo_avg_cost"] = 0.0
            return 0.0

        # Fetch all lots matching key
        rows = self.db.query(f'SELECT rowid, data FROM "{table}"')
        lots = []
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            doc["_rowid"] = row.get("rowid")
            kv = doc.get(key_field)
            # Compare as string to handle int/str mismatch
            if str(kv) == str(key_value):
                lots.append(doc)

        # Sort ascending by sort_field (FIFO — oldest first)
        def _sort_key(lot):
            v = lot.get(sort_field)
            try:
                return (float(v), '')
            except (TypeError, ValueError):
                return (0.0, str(v) if v is not None else '')
        lots.sort(key=_sort_key)

        total_cost = 0.0
        qty_consumed = 0.0

        for lot in lots:
            if remaining <= 1e-9:
                break
            lot_qty = self._to_num(lot.get(qty_field, 0))
            if lot_qty <= 1e-9:
                continue
            take = min(lot_qty, remaining)
            lot_cost = self._to_num(lot.get('unit_cost', 0))
            total_cost += take * lot_cost
            qty_consumed += take
            new_qty = lot_qty - take
            rowid = lot["_rowid"]

            if new_qty <= 1e-9:
                # Fully consumed — delete lot row
                self.db.execute(
                    f'DELETE FROM "{table}" WHERE rowid=?', (rowid,))
            else:
                # Partially consumed — update remaining qty
                clean = {k: v for k, v in lot.items() if k != "_rowid"}
                clean[qty_field] = round(new_qty, 6)
                self.db.execute(
                    f'UPDATE "{table}" SET data=? WHERE rowid=?',
                    (json.dumps(clean), rowid))
            remaining -= take

        avg_cost = (total_cost / qty_consumed) if qty_consumed > 1e-9 else 0.0
        ctx["vars"]["_fifo_total_cost"] = round(total_cost, 4)
        ctx["vars"]["_fifo_avg_cost"] = round(avg_cost, 4)
        return max(0.0, remaining)

    def _next_doc_no(self, table: str, field: str, prefix: str,
                     fmt: str = None) -> str:
        """Generate next running number for a table/field."""
        if fmt == "YYMM":
            yymm = date.today().strftime("%y%m")
            doc_prefix = f"{prefix}-{yymm}"
        else:
            doc_prefix = f"{prefix}-"
        pad = 5
        max_seq = 0
        try:
            rows = self.db.query(f'SELECT data FROM "{table}"')
            for row in rows:
                try:
                    doc = json.loads(row.get("data", "{}"))
                    val = str(doc.get(field, ""))
                    if val.startswith(doc_prefix):
                        num_part = val[len(doc_prefix):]
                        seq = int(num_part)
                        if seq > max_seq:
                            max_seq = seq
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except Exception as e:
            self._log_err(e, f"NEXTDOCNO {table}/{field}")
        return f"{doc_prefix}{max_seq + 1:0{pad}d}"

    def _count_rows(self, table: str, key_field: str, key_value) -> int:
        """Count rows in table where key_field matches key_value."""
        rows = self.db.query(f'SELECT data FROM "{table}"')
        count = 0
        for row in rows:
            doc = json.loads(row.get("data", "{}"))
            if str(doc.get(key_field, '')) == str(key_value):
                count += 1
        return count

    @classmethod
    def _uom_needs_conversion(cls, uom, pack_uom, pack_qty) -> bool:
        pack_num = cls._to_num(pack_qty)
        if pack_num <= 1:
            return False
        u = str(uom or '').strip().upper()
        p = str(pack_uom or '').strip().upper()
        return bool(u and p and u == p)

    @classmethod
    def _base_qty(cls, qty, uom, pack_uom, pack_qty) -> float:
        qty_num = cls._to_num(qty)
        pack_num = cls._to_num(pack_qty)
        if cls._uom_needs_conversion(uom, pack_uom, pack_num):
            return qty_num * pack_num
        return qty_num

    @classmethod
    def _display_qty(cls, base_qty, uom, pack_uom, pack_qty) -> float:
        base_num = cls._to_num(base_qty)
        pack_num = cls._to_num(pack_qty)
        if cls._uom_needs_conversion(uom, pack_uom, pack_num):
            return base_num / pack_num if pack_num else 0.0
        return base_num

    def _line_base_qty(self, row: dict, *qty_fields: str) -> float:
        if not isinstance(row, dict):
            return 0.0
        for field in qty_fields:
            if field in row and str(row.get(field, '') or '').strip() != '':
                return self._base_qty(row.get(field, 0), row.get('uom'),
                                      row.get('pack_uom'),
                                      row.get('pack_qty'))
        return 0.0

    def _line_pack_count(self, row: dict, *qty_fields: str) -> float:
        if not isinstance(row, dict):
            return 0.0
        for field in qty_fields:
            if field in row and str(row.get(field, '') or '').strip() != '':
                qty_num = self._to_num(row.get(field, 0))
                if self._uom_needs_conversion(row.get('uom'),
                                              row.get('pack_uom'),
                                              row.get('pack_qty')):
                    return qty_num
                base_qty = self._base_qty(qty_num, row.get('uom'),
                                          row.get('pack_uom'),
                                          row.get('pack_qty'))
                return self._display_qty(base_qty, row.get('pack_uom'),
                                         row.get('pack_uom'),
                                         row.get('pack_qty'))
        return 0.0

    # ── Warehouse: Put-Away / Picking ──

    def _putaway_allocate(self, wh_id: str, grn_lines: list,
                          for_cust: str, grn_no: str) -> list:
        """Allocate GRN items to warehouse locations based on CBM capacity.

        Algorithm: load warehouse grid config, generate location grid,
        prioritize customer-assigned locations, then free locations,
        sort by level asc (bottom-up), allocate items by CBM fit.
        Returns list of pa_line dicts.
        """
        if not wh_id or not grn_lines:
            return []

        # Load warehouse config
        wh_rows, wh_cols, wh_levels = 8, 10, 1
        default_cbm = 150.0
        try:
            rows = self.db.query('SELECT data FROM "warehouse"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if doc.get("whid") == wh_id:
                    wh_rows = int(self._to_num(doc.get("wh_rows", 8)) or 8)
                    wh_cols = int(self._to_num(doc.get("wh_cols", 10)) or 10)
                    wh_levels = int(self._to_num(doc.get("wh_levels", 1)) or 1)
                    default_cbm = self._to_num(doc.get("default_cbm", 150.0)) or 150.0
                    break
        except Exception as e:
            self._log_err(e, "PUTAWAY_ALLOCATE warehouse lookup")

        # Load existing wh_locations
        db_loc_map = {}
        try:
            rows = self.db.query('SELECT rowid, data FROM "wh_locations"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                doc["_rowid"] = row.get("rowid")
                loc_key = doc.get("id", "")
                if loc_key and doc.get("warehouse") == wh_id:
                    db_loc_map[loc_key] = doc
        except Exception as e:
            self._log_err(e, "PUTAWAY_ALLOCATE wh_locations load")

        # Generate full location grid
        all_locs = []
        for lv in range(1, wh_levels + 1):
            for ro in range(1, wh_rows + 1):
                for co in range(1, wh_cols + 1):
                    loc_id = f'R{ro}C{co}L{lv}'
                    db_key = f'{wh_id}:{loc_id}'
                    if db_key in db_loc_map:
                        all_locs.append(db_loc_map[db_key])
                    else:
                        all_locs.append({
                            'id': db_key, 'warehouse': wh_id,
                            'loc_id': loc_id,
                            'row': ro, 'col': co, 'level': lv,
                            'max_cbm': default_cbm, 'used_cbm': 0,
                            'customer': '', 'customer_name': '',
                        })

        # Sort: level asc (bottom up), then loc_id
        def _loc_sort(loc):
            return (int(loc.get('level', 1) or 1),
                    str(loc.get('loc_id', '')))

        # Separate into customer-assigned, free, and other
        cust_locs = []
        free_locs = []
        other_locs = []
        for loc in all_locs:
            loc_cust = str(loc.get('customer', '') or '').strip()
            if for_cust and loc_cust.upper() == for_cust.upper():
                cust_locs.append(loc)
            elif not loc_cust:
                free_locs.append(loc)
            else:
                other_locs.append(loc)

        cust_locs.sort(key=_loc_sort)
        free_locs.sort(key=_loc_sort)
        other_locs.sort(key=_loc_sort)

        if for_cust:
            # Customer GRN: customer locations first, then free
            candidates = cust_locs + free_locs
        else:
            # No customer: free locations first, then others
            candidates = free_locs + other_locs + cust_locs

        # Filter to locations with free space
        avail = [l for l in candidates
                 if self._to_num(l.get('max_cbm', default_cbm)) -
                    self._to_num(l.get('used_cbm', 0)) > 0.001]

        # Overflow to FLOOR if all full
        if not avail:
            floor_id = f'{wh_id}:FLOOR'
            avail = [{'id': floor_id, 'warehouse': wh_id,
                      'loc_id': 'FLOOR', 'level': 1,
                      'max_cbm': 999999, 'used_cbm': 0,
                      'customer': for_cust, 'customer_name': ''}]

        # Build item CBM cache
        item_cbm = {}
        try:
            rows = self.db.query('SELECT data FROM "item"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                pn = doc.get("part_no", "")
                if pn:
                    item_cbm[pn] = self._to_num(doc.get("cbm", 0.10)) or 0.10
        except Exception as e:
            self._log_err(e, "PUTAWAY_ALLOCATE item cbm cache")

        # Allocate
        pa_lines = []
        loc_idx = 0
        for ln in grn_lines:
            part_no = str(ln.get('part_no', '') or '').strip()
            part_name = str(ln.get('part_name', '') or '').strip()
            recv_qty = self._to_num(ln.get('received_qty', 0))
            recv_base = self._line_base_qty(ln, 'received_qty', 'qty')
            if recv_qty <= 0 or recv_base <= 0:
                continue
            uom = str(ln.get('uom', '') or '').strip()
            pack_uom = str(ln.get('pack_uom', '') or '').strip()
            pack_qty = self._to_num(ln.get('pack_qty', 0)) or 1
            cbm_unit = item_cbm.get(part_no, 0.10)
            lot_no = str(ln.get('lot_no', '') or '').strip()
            remaining = recv_base
            remaining_packs = self._line_pack_count(ln, 'received_qty', 'qty')
            if remaining_packs <= 0:
                remaining_packs = recv_base

            while remaining > 0.001 and remaining_packs > 0.001 and loc_idx < len(avail):
                loc = avail[loc_idx]
                max_c = self._to_num(loc.get('max_cbm', default_cbm))
                used_c = self._to_num(loc.get('used_cbm', 0))
                free_c = max_c - used_c

                if free_c <= 0.001:
                    loc_idx += 1
                    continue

                fit_packs = int(free_c / cbm_unit) if cbm_unit > 0 else int(remaining_packs)
                alloc_packs = min(remaining_packs, fit_packs)
                if alloc_packs <= 0:
                    loc_idx += 1
                    continue

                alloc_cbm = alloc_packs * cbm_unit
                alloc_qty = self._base_qty(alloc_packs, pack_uom, pack_uom, pack_qty)
                if pack_qty <= 1 or not pack_uom:
                    alloc_qty = alloc_packs
                pa_lines.append({
                    'part_no': part_no, 'part_name': part_name,
                    'qty': alloc_qty,
                    'cbm_per_unit': cbm_unit,
                    'total_cbm': round(alloc_cbm, 4),
                    'loc_id': loc.get('loc_id', ''),
                    'level': int(loc.get('level', 1)),
                    'warehouse': wh_id,
                    'lot_no': lot_no, 'grn_no': grn_no,
                    'uom': str(ln.get('base_uom') or ln.get('uom') or '').strip(),
                    'pack_uom': pack_uom,
                    'pack_qty': pack_qty,
                })

                loc['used_cbm'] = used_c + alloc_cbm
                remaining -= alloc_qty
                remaining_packs -= alloc_packs
                if loc['used_cbm'] >= max_c - 0.001:
                    loc_idx += 1

        # Persist updated wh_locations
        for loc in avail:
            loc_id = loc.get('id')
            if not loc_id or self._to_num(loc.get('used_cbm', 0)) <= 0:
                continue
            clean = {k: v for k, v in loc.items() if k != '_rowid'}
            rowid = loc.get('_rowid')
            try:
                if rowid:
                    self.db.execute(
                        'UPDATE "wh_locations" SET data=? WHERE rowid=?',
                        (json.dumps(clean), rowid))
                else:
                    self.db.execute(
                        'INSERT INTO "wh_locations" (data) VALUES (?)',
                        (json.dumps(clean),))
            except Exception as e:
                self._log_err(e, f"PUTAWAY_ALLOCATE upsert {loc_id}")

        return pa_lines

    def _picking_allocate(self, wh_id: str, do_lines: list) -> list:
        """Allocate DO lines from warehouse_storage using FIFO.

        Queries warehouse_storage for matching part_no + warehouse,
        sorted by entry_date asc. Takes from oldest stock first.
        Returns list of pick_line dicts (does not modify storage).
        """
        if not wh_id or not do_lines:
            return []

        # Load warehouse_storage for this warehouse
        storage_by_part = {}
        try:
            rows = self.db.query('SELECT rowid, data FROM "warehouse_storage"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                doc["_rowid"] = row.get("rowid")
                if doc.get("warehouse") == wh_id:
                    pn = doc.get("part_no", "")
                    balance = self._to_num(doc.get("balance", 0))
                    if pn and balance > 0.001:
                        storage_by_part.setdefault(pn, []).append(doc)
        except Exception as e:
            self._log_err(e, "PICKING_ALLOCATE storage load")
            return []

        # Sort each part's storage by entry_date (FIFO)
        for pn in storage_by_part:
            storage_by_part[pn].sort(
                key=lambda r: str(r.get('entry_date', '') or ''))

        pick_lines = []
        for ln in do_lines:
            part_no = str(ln.get('part_no', '') or '').strip()
            part_name = str(ln.get('part_name', '') or '').strip()
            need_qty = self._to_num(ln.get('ship_qty', 0))
            if need_qty <= 0:
                need_qty = self._to_num(ln.get('order_qty', 0))
            need_base = self._line_base_qty(ln, 'ship_qty', 'order_qty')
            if not part_no or need_qty <= 0 or need_base <= 0:
                continue

            storage = storage_by_part.get(part_no, [])
            remaining = need_base
            uom = str(ln.get('uom', '') or '').strip()
            pack_uom = str(ln.get('pack_uom', '') or '').strip()
            pack_qty = self._to_num(ln.get('pack_qty', 0)) or 1

            for st in storage:
                if remaining <= 1e-9:
                    break
                avail = self._to_num(st.get('balance', 0))
                if avail <= 1e-9:
                    continue
                take = min(remaining, avail)
                display_take = self._display_qty(take, uom, pack_uom, pack_qty)

                pick_lines.append({
                    'part_no': part_no, 'part_name': part_name,
                    'pick_qty': display_take,
                    'pick_base_qty': take,
                    'uom': uom,
                    'pack_uom': pack_uom,
                    'pack_qty': pack_qty,
                    'lot_no': str(st.get('lot_no', '') or ''),
                    'grn_no': str(st.get('grn_no', '') or ''),
                    'loc_id': str(st.get('location', '') or ''),
                    'warehouse': wh_id,
                    '_storage_rowid': st.get('_rowid'),
                })

                # Reduce balance for subsequent allocations within same call
                st['balance'] = avail - take
                st['picked'] = self._to_num(st.get('picked', 0)) + take
                remaining -= take

        # Note: storage NOT updated here — PICKING_CONFIRM handles
        # picked/balance updates when picking list is confirmed.

        return pick_lines

    def _picking_confirm(self, wh_id: str, pick_lines: list) -> int:
        """Confirm picking: update warehouse_storage and wh_locations."""
        updated = 0

        # Item CBM cache for location CBM reduction
        item_cbm = {}
        try:
            rows = self.db.query('SELECT data FROM "item"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                pn = doc.get("part_no", "")
                if pn:
                    item_cbm[pn] = self._to_num(doc.get("cbm", 0.10)) or 0.10
        except Exception as e:
            self._log_err(e, "PICKING_CONFIRM item cache")

        # Pre-load warehouse_storage for fallback lookup
        storage_cache = []
        try:
            all_ws = self.db.query('SELECT rowid, data FROM "warehouse_storage"')
            for wr in all_ws:
                wd = json.loads(wr.get("data", "{}"))
                wd['_rowid'] = wr.get('rowid')
                storage_cache.append(wd)
        except Exception as e:
            self._log_err(e, "PICKING_CONFIRM storage cache")

        for pl in pick_lines:
            storage_rowid = pl.get('_storage_rowid') or pl.get('storage_rowid')
            pick_qty = self._to_num(pl.get('pick_base_qty', 0))
            if pick_qty <= 0:
                pick_qty = self._base_qty(pl.get('pick_qty', 0), pl.get('uom'),
                                          pl.get('pack_uom'),
                                          pl.get('pack_qty'))
            part_no = str(pl.get('part_no', '') or '')
            loc_id = str(pl.get('loc_id', '') or '')

            if pick_qty <= 0:
                continue

            # Update warehouse_storage
            try:
                rows = []
                if storage_rowid:
                    rows = self.db.query(
                        'SELECT rowid, data FROM "warehouse_storage" WHERE rowid=?',
                        (storage_rowid,))
                if not rows:
                    # Fallback: match by part_no + lot_no + location
                    lot = str(pl.get('lot_no', '') or '')
                    for sc in storage_cache:
                        if (str(sc.get('part_no', '')) == part_no
                                and str(sc.get('lot_no', '') or '') == lot
                                and str(sc.get('location', '') or '') == loc_id
                                and self._to_num(sc.get('balance', 0)) > 0):
                            storage_rowid = sc['_rowid']
                            rows = self.db.query(
                                'SELECT rowid, data FROM "warehouse_storage" WHERE rowid=?',
                                (storage_rowid,))
                            break
                if not rows:
                    continue
                doc = json.loads(rows[0].get("data", "{}"))
                # Confirm = increase picked, recalc balance
                # on_hand stays unchanged — only DO post reduces item stock
                cur_on_hand = self._to_num(doc.get('on_hand', 0))
                cur_picked = self._to_num(doc.get('picked', 0))
                doc['picked'] = cur_picked + pick_qty
                doc['balance'] = cur_on_hand - (cur_picked + pick_qty)
                actual = pick_qty
                self.db.execute(
                    'UPDATE "warehouse_storage" SET data=? WHERE rowid=?',
                    (json.dumps(doc), storage_rowid))
                updated += 1
            except Exception as e:
                self._log_err(e, f"PICKING_CONFIRM storage {part_no}")
                continue

            # Reduce used_cbm on wh_locations
            if loc_id:
                cbm_per = item_cbm.get(part_no, 0.10)
                pick_packs = self._display_qty(actual, pl.get('uom'),
                                               pl.get('pack_uom'),
                                               pl.get('pack_qty'))
                pick_cbm = round(pick_packs * cbm_per, 4)
                db_key = f"{wh_id}:{loc_id}"
                try:
                    loc_rows = self.db.query(
                        'SELECT rowid, data FROM "wh_locations"')
                    for lr in loc_rows:
                        ld = json.loads(lr.get("data", "{}"))
                        if ld.get("id") == db_key:
                            cur_cbm = self._to_num(ld.get('used_cbm', 0))
                            ld['used_cbm'] = max(0.0, round(cur_cbm - pick_cbm, 4))
                            self.db.execute(
                                'UPDATE "wh_locations" SET data=? WHERE rowid=?',
                                (json.dumps(ld), lr["rowid"]))
                            break
                except Exception as e:
                    self._log_err(e, f"PICKING_CONFIRM loc {db_key}")

        return updated

    def _log_err(self, exc, context=""):
        """Log exception to errors.log (best-effort)."""
        import datetime
        import traceback as _tb
        try:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            header = f"[{stamp}] {context}: {exc!r}\n"
            body = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            with open("errors.log", "a", encoding="utf-8") as fh:
                fh.write(header + body + "\n")
        except Exception:
            pass

    # ── Lookup helpers ──

    def _lookup_value(self, table, return_field, match_field, match_value):
        """LOOKUPVALUE — return first matching row's field value."""
        try:
            rows = self.db.query(f'SELECT data FROM "{table}"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if str(doc.get(match_field, '')) == str(match_value):
                    return doc.get(return_field, "")
        except Exception:
            pass
        return ""

    def _lookup_range(self, table, return_field, range_field, value,
                      key_field=None, key_value=None):
        """LOOKUPRANGE — find row where range_field >= value (lowest qualifying).

        Used for price tiers: find the tier where min_qty <= order_qty.
        Returns the return_field from the best matching row.
        """
        best = None
        best_range = None
        try:
            rows = self.db.query(f'SELECT data FROM "{table}"')
            for row in rows:
                doc = json.loads(row.get("data", "{}"))
                if key_field and str(doc.get(key_field, '')) != str(key_value):
                    continue
                rv = self._to_num(doc.get(range_field, 0))
                if rv <= value:
                    if best_range is None or rv > best_range:
                        best_range = rv
                        best = doc.get(return_field, "")
        except Exception:
            pass
        return best if best is not None else ""

    def _lookup_step(self, table, return_field, step_field, value,
                     key_field=None, key_value=None):
        """LOOKUPSTEP — find highest step_field <= value.

        Same as LOOKUPRANGE but semantically for stepped pricing/tax brackets.
        """
        return self._lookup_range(table, return_field, step_field, value,
                                  key_field, key_value)

    # ── Helpers ──

    @staticmethod
    def _truthy(val) -> bool:
        if val is None:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val != 0
        if isinstance(val, str):
            return val != "" and val.lower() not in ('false', '0')
        if isinstance(val, list):
            return len(val) > 0
        return bool(val)

    @staticmethod
    def _to_num(val) -> float:
        if val is None or val == "":
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(str(val).replace(',', ''))
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _is_numeric(val) -> bool:
        if isinstance(val, (int, float)):
            return True
        try:
            float(str(val))
            return True
        except (ValueError, TypeError):
            return False
