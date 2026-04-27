"""
Injeta o registro das rotas aiohttp do skills_api dentro do método start()
do APIServerAdapter no api_server.py do Hermes.

Idempotente: se o marcador já existir, não faz nada.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "# >>> hermes-custom: skills_api <<<"

# Regex: captura uma linha que cria a aiohttp Application, ex:
#   self._app = web.Application(...)
#   app = web.Application(...)
APP_ASSIGN_RE = re.compile(
    r"^(?P<indent>[ \t]+)(?P<lhs>(?:self\._?app|app))\s*=\s*web\.Application\([^)]*\)\s*$",
    re.MULTILINE,
)


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

    match = APP_ASSIGN_RE.search(src)
    if not match:
        print(
            "[apply_patch] ERROR: não encontrei `... = web.Application(...)` no api_server.py",
            file=sys.stderr,
        )
        return 2

    indent = match.group("indent")
    app_var = match.group("lhs")
    insert_at = match.end()
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
