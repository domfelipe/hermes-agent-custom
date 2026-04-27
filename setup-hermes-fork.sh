#!/usr/bin/env bash
# setup-hermes-fork.sh
# Rode este script DENTRO do clone do repo hermes-agent-custom.
# Ele cria/atualiza os 4 arquivos do patch e não altera mais nada.
#
# Uso:
#   cd ~/code/hermes-agent-custom
#   bash setup-hermes-fork.sh
#   git add .
#   git commit -m "fix: skills_api as aiohttp routes injected into start()"
#   git push

set -euo pipefail

if [ ! -f "Dockerfile" ]; then
  echo "❌ Dockerfile não encontrado. Rode este script na raiz do repo hermes-agent-custom."
  exit 1
fi

echo "📁 Criando diretório patches/..."
mkdir -p patches

# ---------------------------------------------------------------------------
# 1) patches/skills_api.py  — aiohttp RouteTableDef (NÃO é FastAPI)
# ---------------------------------------------------------------------------
# IMPORTANTE: o api_server.py do Hermes usa aiohttp.web, não FastAPI.
# Por isso expomos um aiohttp RouteTableDef e registramos via
# `app.add_routes(skills_routes)` dentro do método start() do adapter.
echo "✍️  patches/skills_api.py"
cat > patches/skills_api.py << 'PY_EOF'
"""
Skills API (aiohttp) — injetado no api_server.py do Hermes via apply_patch.py.

Expõe:
  GET  /api/skills          → lista skills do tenant
  POST /api/skills/sync     → upsert em lote (chamado pelo Lovable)
  GET  /api/skills/health   → ping
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aiohttp import web

skills_routes = web.RouteTableDef()

SKILLS_DIR = Path(os.environ.get("HERMES_SKILLS_DIR", "/opt/data/.hermes/skills"))
SYNC_TOKEN = os.environ.get("HERMES_SKILLS_SYNC_TOKEN", "")


def _ensure_dir() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _check_auth(token: str | None) -> None:
    if not SYNC_TOKEN:
        raise web.HTTPInternalServerError(reason="HERMES_SKILLS_SYNC_TOKEN não configurado")
    if token != SYNC_TOKEN:
        raise web.HTTPUnauthorized(reason="Token inválido")


@skills_routes.get("/api/skills/health")
async def health(_request: web.Request) -> web.Response:
    _ensure_dir()
    return web.json_response(
        {"ok": True, "dir": str(SKILLS_DIR), "count": len(list(SKILLS_DIR.glob("*.md")))}
    )


@skills_routes.get("/api/skills")
async def list_skills(_request: web.Request) -> web.Response:
    _ensure_dir()
    items: list[dict[str, Any]] = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        items.append({"name": f.stem, "size": f.stat().st_size})
    return web.json_response({"skills": items})


@skills_routes.post("/api/skills/sync")
async def sync_skills(request: web.Request) -> web.Response:
    _check_auth(request.headers.get("X-Sync-Token"))
    _ensure_dir()

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(reason="JSON inválido")

    skills = payload.get("skills") or []
    if not isinstance(skills, list):
        raise web.HTTPBadRequest(reason="skills deve ser uma lista")

    written: list[str] = []
    for s in skills:
        name = (s.get("name") or "").strip()
        body = s.get("markdown") or ""
        if not name or "/" in name or ".." in name:
            continue
        target = SKILLS_DIR / f"{name}.md"
        target.write_text(body, encoding="utf-8")
        written.append(name)

    if payload.get("prune"):
        keep = set(written)
        for f in SKILLS_DIR.glob("*.md"):
            if f.stem not in keep:
                f.unlink(missing_ok=True)

    return web.json_response({"written": written, "count": len(written)})
PY_EOF

# ---------------------------------------------------------------------------
# 2) patches/apply_patch.py  — injeta add_routes dentro de start() (idempotente)
# ---------------------------------------------------------------------------
# Estratégia:
#   - Procura o trecho dentro de APIServerAdapter.start() onde o
#     `web.Application(...)` é instanciado e atribuído (ex.: `self._app = web.Application(...)`
#     ou `app = web.Application(...)`).
#   - Logo após essa atribuição, injeta:
#         try:
#             from skills_api import skills_routes as _skills_routes
#             <app_var>.add_routes(_skills_routes)
#             print("[hermes-custom] skills_api routes registered", flush=True)
#         except Exception as _e:
#             print(f"[hermes-custom] failed to register skills_api: {_e}", flush=True)
#   - Idempotente: se o marcador já existir, não faz nada.
echo "✍️  patches/apply_patch.py"
cat > patches/apply_patch.py << 'PY_EOF'
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
PY_EOF

# ---------------------------------------------------------------------------
# 3) entrypoint.sh — adiciona criação do diretório de skills
# ---------------------------------------------------------------------------
echo "✍️  entrypoint.sh"
cat > entrypoint.sh << 'SH_EOF'
#!/bin/bash
set -e

# SOUL.md override via env
if [ -n "$HERMES_SOUL_OVERRIDE" ]; then
  echo "$HERMES_SOUL_OVERRIDE" > /opt/data/.hermes/SOUL.md
fi

# Garante diretório de skills
mkdir -p /opt/data/.hermes/skills

exec /opt/hermes/docker/entrypoint.sh "$@"
SH_EOF
chmod +x entrypoint.sh

# ---------------------------------------------------------------------------
# 4) Dockerfile — copia patches, expõe skills_api no PYTHONPATH e aplica o patch
# ---------------------------------------------------------------------------
echo "✍️  Dockerfile"
cat > Dockerfile << 'DOCKER_EOF'
FROM nousresearch/hermes-agent:latest

USER root

RUN mkdir -p /opt/data/.hermes /opt/data/.hermes/skills /opt/hermes-custom

# Config + SOUL default
RUN printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml
RUN printf 'Você é Mika, uma assistente pessoal de IA criada pela DomCo.' > /opt/data/.hermes/SOUL.md

# Patches
COPY patches/skills_api.py   /opt/hermes-custom/skills_api.py
COPY patches/apply_patch.py  /opt/hermes-custom/apply_patch.py

# Disponibiliza skills_api no PYTHONPATH e injeta add_routes dentro de start()
ENV PYTHONPATH="/opt/hermes-custom:${PYTHONPATH}"
RUN python3 /opt/hermes-custom/apply_patch.py /opt/hermes/gateway/platforms/api_server.py

# Entrypoint custom
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gateway"]
DOCKER_EOF

echo ""
echo "✅ Tudo pronto. Arquivos criados/atualizados:"
echo "   - Dockerfile (sobrescrito)"
echo "   - entrypoint.sh (sobrescrito, +x)"
echo "   - patches/skills_api.py (sobrescrito — agora aiohttp)"
echo "   - patches/apply_patch.py (sobrescrito — injeta dentro de start())"
echo ""
echo "⚠️  IMPORTANTE: o patch anterior foi anexado ao FIM do api_server.py e"
echo "    quebrou o build (name 'app' is not defined). O novo apply_patch.py"
echo "    é idempotente pelo marcador, MAS o marcador antigo ainda está no"
echo "    arquivo da imagem base? NÃO — o build sempre parte da imagem"
echo "    nousresearch/hermes-agent:latest limpa, então não há resíduo."
echo ""
echo "Próximos passos:"
echo "   git add Dockerfile entrypoint.sh patches/"
echo "   git commit -m 'fix: skills_api as aiohttp routes injected into start()'"
echo "   git push"
echo ""
echo "Depois do deploy, teste:"
echo "   curl https://<seu-app>.up.railway.app/api/skills/health"
