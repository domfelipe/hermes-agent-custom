#!/usr/bin/env python3
"""
skills_api.py — Reverse proxy + Runtime Sync API para Hermes Agent.

Roda na porta pública ($PORT) e:
  - Intercepta /api/skills/*, /api/cronjobs/*, /api/integrations/*
  - Proxy todo o resto para o Hermes interno (127.0.0.1:$HERMES_INTERNAL_PORT)
"""
import asyncio
import json
import os
import re
import shutil
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
HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data/.hermes")
SKILLS_ROOT = os.path.join(HERMES_HOME, "skills")
MANAGED_SKILLS_ROOT = os.path.join(SKILLS_ROOT, "mika-managed")
SKILLS_MANIFEST_PATH = os.path.join(MANAGED_SKILLS_ROOT, "manifest.json")
RUNTIME_ROOT = os.path.join(HERMES_HOME, "mika")
MANAGED_CRON_ROOT = os.path.join(RUNTIME_ROOT, "cronjobs")
CRON_MANIFEST_PATH = os.path.join(MANAGED_CRON_ROOT, "manifest.json")
MANAGED_INTEGRATIONS_ROOT = os.path.join(RUNTIME_ROOT, "integrations")
INTEGRATIONS_MANIFEST_PATH = os.path.join(MANAGED_INTEGRATIONS_ROOT, "manifest.json")

# Hop-by-hop headers que NÃO devem ser repassados no proxy
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


# ============================================================
# Helpers
# ============================================================
def _check_auth(request: web.Request, expected_token: str = "") -> bool:
    """Valida Authorization: Bearer <TOKEN>."""
    token = (expected_token or API_SERVER_KEY).strip()
    if not token:
        return True  # sem chave configurada = aberto
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth[7:].strip() == token


def _load_json(path: str, default: dict | list | None = None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _secure_file(path: str, mode: int = 0o600) -> None:
    try:
        if os.path.exists(path):
            os.chmod(path, mode)
    except OSError:
        pass


def _write_json(path: str, payload: dict, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    _secure_file(path, mode)


def _write_text(path: str, content: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    _secure_file(path, mode)


def _managed_skill_dirname(skill: dict) -> str:
    raw_name = str(skill.get("name") or "skill").strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw_name).strip("-") or "skill"
    skill_id = str(skill.get("skill_id") or "manual").strip()[:8] or "manual"
    return f"{slug}--{skill_id}"


def _managed_entry_name(slug_source: str, entry_id: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(slug_source or "entry").strip().lower()).strip("-") or "entry"
    suffix = re.sub(r"[^a-z0-9]+", "", str(entry_id or "manual").lower())[:8] or "manual"
    return f"{slug}--{suffix}"


def _load_skills_manifest() -> dict:
    data = _load_json(SKILLS_MANIFEST_PATH, {"skills": []})
    return data if isinstance(data, dict) else {"skills": []}


def _load_runtime_manifest(path: str, key: str) -> dict:
    data = _load_json(path, {key: []})
    return data if isinstance(data, dict) else {key: []}


def _load_cron_helpers():
    try:
        from cron.jobs import ensure_dirs, load_jobs, save_jobs, compute_next_run
    except Exception as exc:
        raise RuntimeError(f"cron helpers unavailable: {exc}") from exc
    return ensure_dirs, load_jobs, save_jobs, compute_next_run


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                items.append(text)
    return items


def _sanitize_cron_runtime_job(raw_job: dict, existing_job: dict | None, synced_at: str, compute_next_run):
    job_id = str(raw_job.get("job_id") or "").strip()
    name = str(raw_job.get("name") or "").strip()
    prompt = str(raw_job.get("action_prompt") or "").strip()
    cron_expression = str(raw_job.get("cron_expression") or "").strip()

    if not job_id or not name or not prompt or not cron_expression:
        return None

    status = str(raw_job.get("status") or "paused").strip().lower()
    enabled = status == "active"
    human_readable = str(raw_job.get("human_readable") or cron_expression).strip() or cron_expression
    schedule = {
        "kind": "cron",
        "expr": cron_expression,
        "display": human_readable,
    }

    last_run_at = (
        existing_job.get("last_run_at")
        if existing_job and existing_job.get("last_run_at")
        else raw_job.get("last_run_at")
    )

    schedule_changed = (
        not existing_job or
        existing_job.get("schedule", {}).get("expr") != cron_expression
    )

    if existing_job and not schedule_changed and existing_job.get("next_run_at"):
        next_run_at = existing_job.get("next_run_at")
    else:
        next_run_at = raw_job.get("next_run_at") or compute_next_run(schedule, last_run_at)

    repeat = existing_job.get("repeat") if isinstance(existing_job, dict) else None
    if not isinstance(repeat, dict):
        repeat = {"times": None, "completed": 0}
    repeat_completed = repeat.get("completed")
    if not isinstance(repeat_completed, int):
        repeat_completed = 0
    repeat_times = repeat.get("times")
    if not isinstance(repeat_times, int):
        repeat_times = None

    paused_reason = None if enabled else (raw_job.get("auto_paused_reason") or "Sincronizado como pausado pelo Mika")
    paused_at = None if enabled else (
        existing_job.get("paused_at")
        if existing_job and existing_job.get("paused_at")
        else synced_at
    )

    return {
        "id": job_id,
        "name": name,
        "prompt": prompt,
        "skills": [],
        "skill": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": None,
        "context_from": None,
        "schedule": schedule,
        "schedule_display": human_readable,
        "repeat": {
            "times": repeat_times,
            "completed": repeat_completed,
        },
        "enabled": enabled,
        "state": "scheduled" if enabled else "paused",
        "paused_at": paused_at,
        "paused_reason": paused_reason,
        "created_at": (
            existing_job.get("created_at")
            if existing_job and existing_job.get("created_at")
            else str(raw_job.get("created_at") or synced_at)
        ),
        "next_run_at": next_run_at,
        "last_run_at": last_run_at,
        "last_status": existing_job.get("last_status") if existing_job else None,
        "last_error": existing_job.get("last_error") if existing_job else None,
        "last_delivery_error": existing_job.get("last_delivery_error") if existing_job else None,
        "deliver": "local",
        "origin": None,
        "enabled_toolsets": None,
        "workdir": None,
        "managed_by": "mika",
        "mika": {
            "job_id": job_id,
            "description": raw_job.get("description"),
            "natural_language_input": raw_job.get("natural_language_input"),
            "required_mcp_slugs": _normalize_string_list(raw_job.get("required_mcp_slugs")),
            "status": status,
            "auto_paused_reason": raw_job.get("auto_paused_reason"),
            "timezone": raw_job.get("timezone"),
            "source_updated_at": raw_job.get("updated_at"),
            "synced_at": synced_at,
        },
    }


# ============================================================
# Skills API handlers
# ============================================================
async def skills_health(request: web.Request) -> web.Response:
    """Health check — não requer auth."""
    manifest = _load_skills_manifest()
    return web.json_response({
        "status": "ok",
        "service": "skills_api",
        "hermes_backend": HERMES_BASE,
        "managed_skills_root": MANAGED_SKILLS_ROOT,
        "managed_skills_count": len(manifest.get("skills", [])),
        "last_sync_at": manifest.get("synced_at"),
    })


async def skills_list(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manifest = _load_skills_manifest()
    return web.json_response({
        "skills": manifest.get("skills", []),
        "managed_skills_root": MANAGED_SKILLS_ROOT,
        "synced_at": manifest.get("synced_at"),
        "agent_instance_id": manifest.get("agent_instance_id"),
    })


async def skills_sync(request: web.Request) -> web.Response:
    if not _check_auth(request, SKILLS_SYNC_TOKEN or API_SERVER_KEY):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a json object"}, status=400)

    incoming_skills = payload.get("skills", [])
    if not isinstance(incoming_skills, list):
        return web.json_response({"error": "skills must be a list"}, status=400)

    os.makedirs(MANAGED_SKILLS_ROOT, exist_ok=True)

    expected_dirs = set()
    written_skills = []
    skipped = 0

    for raw_skill in incoming_skills:
        if not isinstance(raw_skill, dict):
            skipped += 1
            continue

        skill_id = str(raw_skill.get("skill_id") or "").strip()
        name = str(raw_skill.get("name") or "").strip()
        markdown_content = str(raw_skill.get("markdown_content") or "").strip()

        if not skill_id or not name or not markdown_content:
            skipped += 1
            continue

        dirname = _managed_skill_dirname(raw_skill)
        skill_dir = os.path.join(MANAGED_SKILLS_ROOT, dirname)
        os.makedirs(skill_dir, exist_ok=True)

        skill_markdown = markdown_content if markdown_content.endswith("\n") else f"{markdown_content}\n"
        _write_text(os.path.join(skill_dir, "SKILL.md"), skill_markdown, mode=0o644)

        metadata = dict(raw_skill)
        metadata.pop("markdown_content", None)
        metadata["managed_dir"] = dirname
        metadata["synced_at"] = payload.get("synced_at")
        _write_json(os.path.join(skill_dir, "mika.json"), metadata, mode=0o644)

        expected_dirs.add(dirname)
        written_skills.append(metadata)

    removed = []
    if os.path.isdir(MANAGED_SKILLS_ROOT):
        for entry in os.listdir(MANAGED_SKILLS_ROOT):
            if entry == "manifest.json":
                continue
            full_path = os.path.join(MANAGED_SKILLS_ROOT, entry)
            if os.path.isdir(full_path) and entry not in expected_dirs:
                shutil.rmtree(full_path, ignore_errors=True)
                removed.append(entry)

    manifest = {
        "agent_instance_id": payload.get("agent_instance_id"),
        "synced_at": payload.get("synced_at"),
        "skills": written_skills,
    }
    _write_json(SKILLS_MANIFEST_PATH, manifest, mode=0o644)

    return web.json_response({
        "ok": True,
        "received": len(incoming_skills),
        "written": len(written_skills),
        "skipped": skipped,
        "removed": removed,
        "managed_skills_root": MANAGED_SKILLS_ROOT,
    })


# ============================================================
# Cronjobs API handlers
# ============================================================
async def cronjobs_health(request: web.Request) -> web.Response:
    manifest = _load_runtime_manifest(CRON_MANIFEST_PATH, "cronjobs")
    return web.json_response({
        "status": "ok",
        "service": "cronjobs_api",
        "managed_cron_root": MANAGED_CRON_ROOT,
        "managed_cronjobs_count": len(manifest.get("cronjobs", [])),
        "last_sync_at": manifest.get("synced_at"),
    })


async def cronjobs_list(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    ensure_dirs, load_jobs, _, _ = _load_cron_helpers()
    ensure_dirs()
    jobs = load_jobs()
    managed_jobs = [job for job in jobs if job.get("managed_by") == "mika"]
    manifest = _load_runtime_manifest(CRON_MANIFEST_PATH, "cronjobs")

    return web.json_response({
        "cronjobs": managed_jobs,
        "manifest": manifest,
        "managed_cron_root": MANAGED_CRON_ROOT,
        "synced_at": manifest.get("synced_at"),
        "agent_instance_id": manifest.get("agent_instance_id"),
    })


async def cronjobs_sync(request: web.Request) -> web.Response:
    if not _check_auth(request, SKILLS_SYNC_TOKEN or API_SERVER_KEY):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a json object"}, status=400)

    incoming_jobs = payload.get("cronjobs", [])
    if not isinstance(incoming_jobs, list):
        return web.json_response({"error": "cronjobs must be a list"}, status=400)

    ensure_dirs, load_jobs, save_jobs, compute_next_run = _load_cron_helpers()
    ensure_dirs()
    os.makedirs(MANAGED_CRON_ROOT, exist_ok=True)

    existing_jobs = load_jobs()
    existing_managed = {
        str(job.get("id")): job
        for job in existing_jobs
        if job.get("managed_by") == "mika"
    }
    unmanaged_jobs = [job for job in existing_jobs if job.get("managed_by") != "mika"]

    synced_at = str(payload.get("synced_at") or "")

    runtime_jobs = []
    manifest_jobs = []
    skipped = 0

    for raw_job in incoming_jobs:
        if not isinstance(raw_job, dict):
            skipped += 1
            continue

        job_id = str(raw_job.get("job_id") or "").strip()
        existing_job = existing_managed.get(job_id)
        runtime_job = _sanitize_cron_runtime_job(raw_job, existing_job, synced_at, compute_next_run)
        if not runtime_job:
            skipped += 1
            continue

        runtime_jobs.append(runtime_job)
        manifest_jobs.append({
            "job_id": runtime_job["id"],
            "name": runtime_job["name"],
            "status": runtime_job["mika"]["status"],
            "cron_expression": runtime_job["schedule"]["expr"],
            "human_readable": runtime_job["schedule_display"],
            "required_mcp_slugs": runtime_job["mika"]["required_mcp_slugs"],
            "timezone": runtime_job["mika"]["timezone"],
            "last_run_at": runtime_job.get("last_run_at"),
            "next_run_at": runtime_job.get("next_run_at"),
            "last_status": runtime_job.get("last_status"),
            "last_error": runtime_job.get("last_error"),
            "last_delivery_error": runtime_job.get("last_delivery_error"),
            "source_updated_at": runtime_job["mika"].get("source_updated_at"),
            "synced_at": runtime_job["mika"].get("synced_at"),
        })

    save_jobs(unmanaged_jobs + runtime_jobs)

    incoming_ids = {str(job.get("id")) for job in runtime_jobs}
    removed = [
        job_id
        for job_id in existing_managed.keys()
        if job_id not in incoming_ids
    ]

    manifest = {
        "agent_instance_id": payload.get("agent_instance_id"),
        "synced_at": payload.get("synced_at"),
        "cronjobs": manifest_jobs,
    }
    _write_json(CRON_MANIFEST_PATH, manifest, mode=0o644)

    return web.json_response({
        "ok": True,
        "received": len(incoming_jobs),
        "written": len(runtime_jobs),
        "skipped": skipped,
        "removed": removed,
        "managed_cron_root": MANAGED_CRON_ROOT,
    })


# ============================================================
# Integrations API handlers
# ============================================================
async def integrations_health(request: web.Request) -> web.Response:
    manifest = _load_runtime_manifest(INTEGRATIONS_MANIFEST_PATH, "integrations")
    return web.json_response({
        "status": "ok",
        "service": "integrations_api",
        "managed_integrations_root": MANAGED_INTEGRATIONS_ROOT,
        "managed_integrations_count": len(manifest.get("integrations", [])),
        "last_sync_at": manifest.get("synced_at"),
    })


async def integrations_list(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    manifest = _load_runtime_manifest(INTEGRATIONS_MANIFEST_PATH, "integrations")
    return web.json_response({
        "integrations": manifest.get("integrations", []),
        "managed_integrations_root": MANAGED_INTEGRATIONS_ROOT,
        "synced_at": manifest.get("synced_at"),
        "agent_instance_id": manifest.get("agent_instance_id"),
        "user_id": manifest.get("user_id"),
    })


async def integrations_sync(request: web.Request) -> web.Response:
    if not _check_auth(request, SKILLS_SYNC_TOKEN or API_SERVER_KEY):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "payload must be a json object"}, status=400)

    incoming_integrations = payload.get("integrations", [])
    if not isinstance(incoming_integrations, list):
        return web.json_response({"error": "integrations must be a list"}, status=400)

    os.makedirs(MANAGED_INTEGRATIONS_ROOT, exist_ok=True)

    expected_files = set()
    written_integrations = []
    skipped = 0

    for raw_integration in incoming_integrations:
        if not isinstance(raw_integration, dict):
            skipped += 1
            continue

        integration_id = str(raw_integration.get("integration_id") or "").strip()
        slug = str(raw_integration.get("slug") or "").strip()
        name = str(raw_integration.get("name") or "").strip()

        if not integration_id or not slug or not name:
            skipped += 1
            continue

        entry_name = _managed_entry_name(slug, integration_id)
        full_path = os.path.join(MANAGED_INTEGRATIONS_ROOT, f"{entry_name}.json")

        full_record = dict(raw_integration)
        full_record["managed_file"] = f"{entry_name}.json"
        full_record["synced_at"] = payload.get("synced_at")
        _write_json(full_path, full_record, mode=0o600)

        manifest_record = dict(full_record)
        manifest_record.pop("access_token", None)
        manifest_record.pop("refresh_token", None)
        written_integrations.append(manifest_record)
        expected_files.add(f"{entry_name}.json")

    removed = []
    if os.path.isdir(MANAGED_INTEGRATIONS_ROOT):
        for entry in os.listdir(MANAGED_INTEGRATIONS_ROOT):
            if entry == "manifest.json":
                continue
            full_path = os.path.join(MANAGED_INTEGRATIONS_ROOT, entry)
            if os.path.isfile(full_path) and entry not in expected_files:
                os.remove(full_path)
                removed.append(entry)

    manifest = {
        "agent_instance_id": payload.get("agent_instance_id"),
        "user_id": payload.get("user_id"),
        "synced_at": payload.get("synced_at"),
        "integrations": written_integrations,
    }
    _write_json(INTEGRATIONS_MANIFEST_PATH, manifest, mode=0o600)

    return web.json_response({
        "ok": True,
        "received": len(incoming_integrations),
        "written": len(written_integrations),
        "skipped": skipped,
        "removed": removed,
        "managed_integrations_root": MANAGED_INTEGRATIONS_ROOT,
    })


# ============================================================
# Reverse proxy para o Hermes
# ============================================================
async def proxy(request: web.Request) -> web.StreamResponse:
    """Encaminha qualquer request para o Hermes interno."""
    target_url = f"{HERMES_BASE}{request.rel_url}"

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
    app = web.Application(client_max_size=50 * 1024 * 1024)

    app.router.add_get("/api/skills/health", skills_health)
    app.router.add_get("/api/skills", skills_list)
    app.router.add_post("/api/skills/sync", skills_sync)

    app.router.add_get("/api/cronjobs/health", cronjobs_health)
    app.router.add_get("/api/cronjobs", cronjobs_list)
    app.router.add_post("/api/cronjobs/sync", cronjobs_sync)

    app.router.add_get("/api/integrations/health", integrations_health)
    app.router.add_get("/api/integrations", integrations_list)
    app.router.add_post("/api/integrations/sync", integrations_sync)

    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


if __name__ == "__main__":
    print(f"[skills_api] listening on 0.0.0.0:{PUBLIC_PORT}")
    print(f"[skills_api] proxying to {HERMES_BASE}")
    web.run_app(make_app(), host="0.0.0.0", port=PUBLIC_PORT, access_log=None)
