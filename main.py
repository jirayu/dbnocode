"""DSL Application Runner — Validates then executes scripts"""
import glob
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsl_lib import (DSLValidator, DSLParser, CompactDSLParser, ScriptRunner,
                     validate_app_definition)

_INCLUDE_RE = re.compile(r'^\s*INCLUDE\s+(?:"([^"]+)"|([^\s]+))\s*$', re.I)
_DEFAULT_SCRIPT = os.path.join("scripts", "main.dsl")


def _is_compact(text: str) -> bool:
    """Detect compact DSL format.

    Compact uses unquoted FORM names:  FORM item TITLE "..."
    Verbose uses quoted FORM names:    FORM "item_form"
    """
    for line in text.splitlines():
        stripped = re.sub(r'//.*', '', line).strip()
        if re.match(r'^FORM\s+\w', stripped, re.I):
            # Check if the name after FORM is quoted or not
            if re.match(r'^FORM\s+"', stripped, re.I):
                return False  # verbose: FORM "name"
            return True       # compact: FORM name
    return False


def _resolve_script_path(script_path: str) -> str:
    """Resolve a script path relative to the application directory."""
    if not os.path.isabs(script_path):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        resolved = os.path.join(app_dir, script_path)
        if os.path.isfile(resolved):
            return resolved
    elif os.name == 'nt' and not os.path.isfile(script_path):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        resolved = os.path.join(app_dir, script_path.lstrip('\\').lstrip('/'))
        if os.path.isfile(resolved):
            return resolved
    return script_path


def _select_script_path(args) -> str:
    """Return an explicit DSL argument or the standard application entry point."""
    return next((arg for arg in args if not arg.startswith("--")), _DEFAULT_SCRIPT)


def _load_dsl_with_includes(script_path: str, stack=None) -> str:
    """Load a DSL file and inline any top-level INCLUDE directives."""
    resolved_path = os.path.abspath(_resolve_script_path(script_path))
    include_stack = list(stack or [])
    if resolved_path in include_stack:
        chain = " -> ".join(include_stack + [resolved_path])
        raise ValueError(f"Circular INCLUDE detected: {chain}")
    include_stack.append(resolved_path)

    with open(resolved_path, encoding="utf-8") as fh:
        raw_lines = fh.readlines()

    merged = []
    base_dir = os.path.dirname(resolved_path)

    for raw_line in raw_lines:
        stripped = re.sub(r'//.*', '', raw_line).strip()
        match = _INCLUDE_RE.fullmatch(stripped)
        if not match:
            merged.append(raw_line)
            continue

        include_ref = (match.group(1) or match.group(2) or '').strip()
        if not include_ref:
            continue

        include_pattern = include_ref
        if not os.path.isabs(include_pattern):
            include_pattern = os.path.join(base_dir, include_pattern)
        include_pattern = os.path.normpath(include_pattern)

        include_paths = sorted(glob.glob(include_pattern))
        if not include_paths:
            raise FileNotFoundError(
                f"INCLUDE not found from '{resolved_path}': {include_ref}"
            )

        for include_path in include_paths:
            merged.append(f'// INCLUDE "{include_path}"\n')
            merged.append(_load_dsl_with_includes(include_path, include_stack))
            if not merged[-1].endswith("\n"):
                merged.append("\n")

    include_stack.pop()
    return ''.join(merged)


def main():
    args = sys.argv[1:]
    if "--import-excel" in args:
        from dsl_lib.excel_importer import import_workbook

        def option_value(name):
            if name not in args:
                return None
            index = args.index(name)
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"{name} requires a value")
            return args[index + 1]

        try:
            workbook = option_value("--import-excel")
            result = import_workbook(
                workbook,
                script_path=option_value("--output"),
                database_path=option_value("--database"),
                force="--force" in args,
            )
        except Exception as exc:
            print(f"Excel import failed: {exc}")
            sys.exit(1)
        print(
            f"Imported {result.forms} form(s), {result.rows} row(s)\n"
            f"DSL: {result.script_path}\nDatabase: {result.database_path}\n"
            f"Run: python main.py \"{result.script_path}\""
        )
        return
    script_path = _resolve_script_path(_select_script_path(args))
    validate_only = "--validate-only" in args

    text = _load_dsl_with_includes(script_path)

    compact = _is_compact(text)

    # Validate first for BOTH dialects. Block-balance / hotkey / undeclared-
    # action checks catch real mistakes before we hand off to the parser.
    validator = DSLValidator()
    result = validator.validate(text, compact=compact)

    if not result.valid:
        print("Validation failed:")
        for err in result.errors:
            print(f"  Line {err.line}: {err.message}")
            if err.fix:
                print(f"    Fix: {err.fix}")
        sys.exit(1)
    print(f"Valid: {result.summary['forms_declared']} forms, {result.summary['grids_declared']} grids")

    if compact:
        print("Compact DSL detected")
        parser = CompactDSLParser(text)
        try:
            app_def = parser.parse()
        except SyntaxError as exc:
            print(f"Parse failed: {exc}")
            sys.exit(1)
        form_count = len(app_def.get("forms", {}))
        grid_count = len(app_def.get("grids", {}))
        print(f"Parsed: {form_count} forms, {grid_count} grids")
    else:
        parser = DSLParser(text)
        try:
            app_def = parser.parse()
        except SyntaxError as exc:
            print(f"Parse failed: {exc}")
            sys.exit(1)

    # Excel imports created from the main menu are registered beside the host
    # script. Merge their generated forms before semantic validation so they
    # behave exactly like forms declared in the main DSL on every launch.
    app_def["_script_path"] = os.path.abspath(script_path)
    try:
        from dsl_lib.excel_importer import load_registered_imports
        for warning in load_registered_imports(app_def, script_path):
            print(f"Excel import warning: {warning}")
    except ImportError:
        pass

    semantic = validate_app_definition(app_def)
    if not semantic.valid:
        print("Semantic validation failed:")
        for err in semantic.errors:
            print(f"  Line {err.line}: [{err.code}] {err.message}")
            if err.fix:
                print(f"    Fix: {err.fix}")
        sys.exit(1)
    print("Semantic validation passed")

    if validate_only:
        sys.exit(0)

    # --db path overrides company selection (used by showcase / standalone scripts)
    db_override = None
    if "--db" in args:
        idx = args.index("--db")
        if idx + 1 < len(args):
            db_override = args[idx + 1]

    runner = ScriptRunner(app_def, db_override=db_override)
    runner.run()


if __name__ == "__main__":
    main()
