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
