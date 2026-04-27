"""
Injeta o registro das rotas aiohttp do skills_api dentro do método start()
do APIServerAdapter no api_server.py do Hermes.

Idempotente: se o marcador já existir, não faz nada.

Strategy
--------
The `web.Application(...)` call may span multiple lines, e.g.:

    self._app = web.Application(
        middlewares=[...],
        client_max_size=...,
    )

We therefore use a two-phase approach:
  1. Locate the LHS assignment (``<indent><var> = web.Application(``) with a
     simple MULTILINE regex — this is always a single line.
  2. From that position, scan forward to find the matching closing parenthesis,
     correctly handling nested parentheses.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "# >>> hermes-custom: skills_api <<<"

# Phase-1 regex: matches the *start* of the assignment up to and including the
# opening parenthesis of web.Application.  Does NOT require the call to end on
# the same line, so it works for both single-line and multi-line forms.
APP_ASSIGN_START_RE = re.compile(
    r"^(?P<indent>[ \t]+)(?P<lhs>(?:self\._?app|app))\s*=\s*web\.Application\(",
    re.MULTILINE,
)


def _find_closing_paren(src: str, open_pos: int) -> int:
    """Return the index of the ')' that closes the '(' at *open_pos*.

    Handles nested parentheses.  Raises ValueError if not found.
    """
    depth = 0
    for i in range(open_pos, len(src)):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"No matching closing parenthesis found starting at offset {open_pos}")


def build_injection(indent: str, app_var: str) -> str:
    lines = [
        "",
        f"{indent}{MARKER}",
        f"{indent}try:",
        f"{indent}    from skills_api import skills_routes as _skills_routes",
        f"{indent}    {app_var}.add_routes(_skills_routes)",
        f'{indent}    print("[hermes-custom] skills_api routes registered", flush=True)',
        f"{indent}except Exception as _e:",
        f'{indent}    print(f"[hermes-custom] failed to register skills_api: {{_e}}", flush=True)',
        f"{indent}# <<< hermes-custom: skills_api >>>",
    ]
    return "\n".join(lines)


def main(target: str) -> int:
    p = Path(target)
    if not p.exists():
        print(f"[apply_patch] target not found: {target}", file=sys.stderr)
        return 1

    src = p.read_text(encoding="utf-8")
    if MARKER in src:
        print("[apply_patch] already applied, skipping")
        return 0

    # Phase 1 — find the assignment start
    match = APP_ASSIGN_START_RE.search(src)
    if not match:
        # Emit a diagnostic snippet to help debug future mismatches
        print(
            "[apply_patch] ERROR: não encontrei `... = web.Application(` no api_server.py",
            file=sys.stderr,
        )
        # Print lines that contain "Application" to aid debugging
        for lineno, line in enumerate(src.splitlines(), 1):
            if "Application" in line:
                print(f"[apply_patch] hint line {lineno}: {line!r}", file=sys.stderr)
        return 2

    indent = match.group("indent")
    app_var = match.group("lhs")

    # Phase 2 — walk forward to find the matching closing parenthesis
    open_paren_pos = match.end() - 1  # position of the '(' in web.Application(
    try:
        close_paren_pos = _find_closing_paren(src, open_paren_pos)
    except ValueError as exc:
        print(f"[apply_patch] ERROR: {exc}", file=sys.stderr)
        return 2

    # Insert after the closing ')' (and any trailing whitespace on that line)
    # so we land right after the complete statement.
    insert_at = close_paren_pos + 1
    # Advance past an optional trailing comment / whitespace up to the newline
    newline_pos = src.find("\n", insert_at)
    if newline_pos != -1:
        insert_at = newline_pos  # inject before the newline so indentation stays clean

    injection = build_injection(indent, app_var)

    new_src = src[:insert_at] + injection + src[insert_at:]
    p.write_text(new_src, encoding="utf-8")
    print(
        f"[apply_patch] injected after `{app_var} = web.Application(...)` "
        f"at offset {insert_at} (indent={len(indent)} spaces)"
    )
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/gateway/platforms/api_server.py"
    sys.exit(main(target))
