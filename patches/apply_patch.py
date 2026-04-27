"""
Injeta `from skills_api import router as skills_router` + `app.include_router(...)`
no api_server.py do Hermes durante o build da imagem.

Idempotente: se o marcador já existir, não faz nada.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# >>> hermes-custom: skills_api <<<"
INJECT = f"""
{MARKER}
try:
    from skills_api import router as _skills_router
    app.include_router(_skills_router)
    print("[hermes-custom] skills_api router registered", flush=True)
except Exception as _e:
    print(f"[hermes-custom] failed to register skills_api: {{_e}}", flush=True)
# <<< hermes-custom: skills_api >>>
"""


def main(target: str) -> int:
    p = Path(target)
    if not p.exists():
        print(f"[apply_patch] target not found: {target}", file=sys.stderr)
        return 1

    src = p.read_text(encoding="utf-8")
    if MARKER in src:
        print("[apply_patch] already applied, skipping")
        return 0

    # Procura a linha onde o `app = FastAPI(...)` é instanciado e injeta logo
    # após o bloco de criação. Estratégia: anexa no final do arquivo — mais
    # robusto contra mudanças upstream do que tentar achar a linha exata.
    new_src = src.rstrip() + "\n\n" + INJECT + "\n"
    p.write_text(new_src, encoding="utf-8")
    print(f"[apply_patch] injected at offset {len(src)}")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/gateway/platforms/api_server.py"
    sys.exit(main(target))
