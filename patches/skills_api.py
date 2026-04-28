#!/usr/bin/env python3
"""
skills_api.py — Reverse proxy + Skills API para Hermes Agent.

Roda na porta pública ($PORT) e:
  - Intercepta /api/skills/*  → handlers locais
  - Proxy todo o resto       → Hermes interno (127.0.0.1:$HERMES_INTERNAL_PORT)
"""
import asyncio
import os
import sys
from aiohttp import web, ClientSession, ClientTimeout

# ============================================================
# Configuração
# ============================================================
PUBLIC_PORT = int(os.environ.get("PORT", "8642"))
HERMES_INTERNAL_PORT = int(os.environ.get("HERMES_INTERNAL_PORT", "8000"))
HERMES_BASE = f"http://127.0.0.1:{HERMES_INTERNAL_PORT}"
API_SERVER_KEY = os.environ.get("API_SERVER_KEY", "")
SKILLS_SYNC_TOKEN = os.environ.get("HERMES_SKILLS_SYNC_TOKEN", "")

# Hop-by-hop headers que NÃO devem ser repassados no proxy
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

# ============================================================
# Skills API handlers
# ============================================================
async def skills_health(request: web.Request) -> web.Response:
    """Health check — não requer auth."""
    return web.json_response({
        "status": "ok",
        "service": "skills_api",
        "hermes_backend": HERMES_BASE,
    })


def _check_auth(request: web.Request) -> bool:
    """Valida Authorization: Bearer <API_SERVER_KEY>."""
    if not API_SERVER_KEY:
        return True  # sem chave configurada = aberto
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth[7:].strip() == API_SERVER_KEY


async def skills_list(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    # TODO: integrar com sistema real de skills do Hermes
    return web.json_response({"skills": []})


async def skills_sync(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    # TODO: persistir skills recebidas
    return web.json_response({"ok": True, "received": len(payload.get("skills", []))})


# ============================================================
# Reverse proxy para o Hermes
# ============================================================
async def proxy(request: web.Request) -> web.StreamResponse:
    """Encaminha qualquer request para o Hermes interno."""
    target_url = f"{HERMES_BASE}{request.rel_url}"

    # Filtra headers hop-by-hop
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP
    }

    body = await request.read() if request.body_exists else None

    try:
        async with ClientSession(timeout=ClientTimeout(total=300)) as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
            ) as upstream:
                # Stream da resposta de volta
                resp_headers = {
                    k: v for k, v in upstream.headers.items()
                    if k.lower() not in HOP_BY_HOP
                }
                response = web.StreamResponse(
                    status=upstream.status,
                    headers=resp_headers,
                )
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await response.write(chunk)
                await response.write_eof()
                return response
    except asyncio.TimeoutError:
        return web.json_response({"error": "upstream timeout"}, status=504)
    except Exception as e:
        print(f"[proxy] error: {e}", file=sys.stderr)
        return web.json_response({"error": "bad gateway", "detail": str(e)}, status=502)


# ============================================================
# App setup
# ============================================================
def make_app() -> web.Application:
    app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB

    # Skills API (precedência sobre o proxy)
    app.router.add_get("/api/skills/health", skills_health)
    app.router.add_get("/api/skills", skills_list)
    app.router.add_post("/api/skills/sync", skills_sync)

    # Proxy catch-all (qualquer outro path)
    app.router.add_route("*", "/{tail:.*}", proxy)

    return app


if __name__ == "__main__":
    print(f"[skills_api] listening on 0.0.0.0:{PUBLIC_PORT}")
    print(f"[skills_api] proxying to {HERMES_BASE}")
    web.run_app(make_app(), host="0.0.0.0", port=PUBLIC_PORT, access_log=None)
