"""
Skills API router — injetado no api_server.py do Hermes pelo apply_patch.py.
Expõe:
  GET  /api/skills          → lista skills do tenant
  POST /api/skills/sync     → upsert em lote (chamado pelo Lovable)
  GET  /api/skills/health   → ping
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/skills", tags=["skills"])

SKILLS_DIR = Path(os.environ.get("HERMES_SKILLS_DIR", "/opt/data/.hermes/skills"))
SYNC_TOKEN = os.environ.get("HERMES_SKILLS_SYNC_TOKEN", "")


def _ensure_dir() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _check_auth(token: str | None) -> None:
    if not SYNC_TOKEN:
        raise HTTPException(500, "HERMES_SKILLS_SYNC_TOKEN não configurado")
    if token != SYNC_TOKEN:
        raise HTTPException(401, "Token inválido")


@router.get("/health")
def health() -> dict[str, Any]:
    _ensure_dir()
    return {"ok": True, "dir": str(SKILLS_DIR), "count": len(list(SKILLS_DIR.glob("*.md")))}


@router.get("")
def list_skills() -> dict[str, Any]:
    _ensure_dir()
    items = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        items.append({"name": f.stem, "size": f.stat().st_size})
    return {"skills": items}


@router.post("/sync")
def sync_skills(
    payload: dict[str, Any],
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
) -> dict[str, Any]:
    _check_auth(x_sync_token)
    _ensure_dir()
    skills = payload.get("skills") or []
    if not isinstance(skills, list):
        raise HTTPException(400, "skills deve ser uma lista")

    written: list[str] = []
    for s in skills:
        name = (s.get("name") or "").strip()
        body = s.get("markdown") or ""
        if not name or "/" in name or ".." in name:
            continue
        target = SKILLS_DIR / f"{name}.md"
        target.write_text(body, encoding="utf-8")
        written.append(name)

    # Remove skills que não vieram no payload (sync completo, opcional)
    if payload.get("prune"):
        keep = set(written)
        for f in SKILLS_DIR.glob("*.md"):
            if f.stem not in keep:
                f.unlink(missing_ok=True)

    return {"written": written, "count": len(written)}
