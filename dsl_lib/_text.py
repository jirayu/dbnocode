"""Shared DSL text utilities used by main, parsers, and the validator."""


def strip_comments(line: str) -> str:
    """Remove DSL line comments but preserve ``//`` inside string literals.

    DSL strings are double-quoted (``"text"``); a doubled quote escapes
    itself. Stripping without quote tracking truncated lines such as::

        APP "My//App" VERSION "1.0"   ->  APP "My

    which then failed with a misleading "Invalid APP declaration".
    """
    out = []
    in_quote = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_quote:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    out.append('""')
                    i += 2
                    continue
                in_quote = False
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_quote = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        out.append(ch)
        i += 1
    return "".join(out)
