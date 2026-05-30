"""Stable runtime bridge for integrations synced from Mika into Hermes."""

from __future__ import annotations

import json
import asyncio
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from urllib import error, parse, request

from hermes_constants import get_hermes_home

SUPPORTED_SLUGS = ("notion", "todoist", "calcom")
MANAGED_INTEGRATIONS_ROOT = Path(get_hermes_home()) / "mika" / "integrations"
INTEGRATIONS_MANIFEST_PATH = MANAGED_INTEGRATIONS_ROOT / "manifest.json"
DEFAULT_NOTION_VERSION = "2022-06-28"
DEFAULT_CALCOM_VERSION = "2026-02-25"
MAX_RESPONSE_CHARS = 20000
USER_AGENT = "domco-mika-runtime/0.1"
logger = logging.getLogger(__name__)
AGENT_INSTANCE_ENV_NAMES = (
    "MIKA_AGENT_INSTANCE_ID",
    "HERMES_AGENT_INSTANCE_ID",
    "AGENT_INSTANCE_ID",
)
INTERNAL_SECRET_ENV_NAMES = (
    "MIKA_INTERNAL_FUNCTION_SECRET",
    "HERMES_INTERNAL_FUNCTION_SECRET",
    "INTERNAL_FUNCTION_SECRET",
)
GATEWAY_ACTION_INTERCEPT_ENV_NAMES = (
    "MIKA_GATEWAY_ACTION_INTERCEPT",
    "HERMES_GATEWAY_ACTION_INTERCEPT",
)

INTEGRATIONS_STATUS_SCHEMA = {
    "name": "mika_integrations_status",
    "description": (
        "Lists the Mika integrations currently synced into this Hermes runtime. "
        "Use it to confirm whether Notion, Todoist, or Cal.com are connected "
        "before making provider-specific API calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "enum": list(SUPPORTED_SLUGS),
                "description": "Optional provider slug to filter by.",
            },
        },
        "additionalProperties": False,
    },
}

NOTION_API_SCHEMA = {
    "name": "mika_notion_api",
    "description": (
        "Makes authenticated requests against the connected Notion workspace. "
        "Useful for search, retrieving pages, creating pages, updating pages, "
        "querying databases, and appending block children. Authorization and "
        "Notion-Version headers are injected automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PATCH"],
                "description": "HTTP method.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Notion API path such as /v1/search, /v1/pages/<page_id>, "
                    "/v1/pages, or /v1/blocks/<block_id>/children."
                ),
            },
            "query": {
                "type": "object",
                "description": "Optional query parameters appended to the URL.",
                "additionalProperties": True,
            },
            "body": {
                "description": "Optional JSON request body for POST or PATCH requests.",
                "anyOf": [{"type": "object"}, {"type": "array"}, {"type": "null"}],
            },
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}

TODOIST_API_SCHEMA = {
    "name": "mika_todoist_api",
    "description": (
        "Makes authenticated requests against Todoist REST API v2 for the "
        "connected account. Useful for tasks, projects, sections, labels, and "
        "comments. Authorization is injected automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "DELETE"],
                "description": "HTTP method.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Todoist REST v2 path such as /tasks, /tasks/<task_id>, "
                    "/tasks/<task_id>/close, /projects, /sections, or /comments."
                ),
            },
            "query": {
                "type": "object",
                "description": "Optional query parameters appended to the URL.",
                "additionalProperties": True,
            },
            "body": {
                "description": "Optional JSON request body for POST requests.",
                "anyOf": [{"type": "object"}, {"type": "array"}, {"type": "null"}],
            },
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}

CALCOM_API_SCHEMA = {
    "name": "mika_calcom_api",
    "description": (
        "Makes authenticated requests against Cal.com API v2 for the connected "
        "account. Useful for /v2/me, /v2/event-types, /v2/bookings, and related "
        "resources. Authorization and cal-api-version headers are injected automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PATCH"],
                "description": "HTTP method.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Cal.com API path such as /v2/me, /v2/event-types, "
                    "/v2/event-types/<id>, /v2/bookings, or /v2/bookings/<uid>."
                ),
            },
            "query": {
                "type": "object",
                "description": "Optional query parameters appended to the URL.",
                "additionalProperties": True,
            },
            "body": {
                "description": "Optional JSON request body for POST or PATCH requests.",
                "anyOf": [{"type": "object"}, {"type": "array"}, {"type": "null"}],
            },
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _is_truthy_env_default_true(*names: str) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        lowered = str(value).strip().lower()
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
    return True


def _normalize_intent_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


_TEMPORAL_RE = re.compile(
    r"("
    r"\bdaqui\s+(?:a\s+)?\d+\s*(?:min(?:uto)?s?|h(?:ora)?s?|dias?|semanas?)\b"
    r"|\b(?:hoje|amanha|depois de amanha)\b"
    r"|\b(?:todo|toda|todos|todas|diariamente|semanalmente|mensalmente|anualmente)\b"
    r"|\b(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?s?\b"
    r"|\b(?:dia util|dias uteis|fim de semana)\b"
    r"|\b(?:as|às)\s*\d{1,2}(?::\d{2}|h\d{0,2})?\b"
    r"|\b\d{1,2}(?::\d{2}|h\d{0,2})\b"
    r"|\b(?:cron|cronjob|automacao|automatizacao|lembrete|reminder|schedule)\b"
    r")",
    re.IGNORECASE,
)

_CRON_INTENT_RE = re.compile(
    r"("
    r"\bme\s+lemb(?:ra|re)\b"
    r"|\blemb(?:ra|re)(?:-me)?\b"
    r"|\bme\s+avis(?:a|e)\b"
    r"|\bavis(?:a|e)(?:-me)?\b"
    r"|\bagend(?:e|ar)\b"
    r"|\bme\s+agenda\b"
    r"|\bagenda\s+(?:um|uma|isso|para|pra)\b"
    r"|\bprogram(?:a|e|ar)\b"
    r"|\bautomatiz(?:a|e|ar)\b"
    r"|\bcria(?:r)?\s+(?:um\s+|uma\s+)?(?:cronjob|lembrete|automacao)\b"
    r"|\b(?:todo|toda|todos|todas)\b.*\b(?:manda|envia|me\s+manda|me\s+envia|resum[ao])\b"
    r")",
    re.IGNORECASE,
)

_SKILL_INTENT_RE = re.compile(
    r"("
    r"\bcria(?:r)?\s+(?:uma\s+|um\s+)?skill\b"
    r"|\bcrie\s+(?:uma\s+|um\s+)?skill\b"
    r"|\bnova\s+skill\b"
    r"|\badicion(?:a|e|ar)\s+(?:uma\s+|um\s+)?skill\b"
    r"|\bensina(?:r)?\b.*\b(?:skill|quando eu mandar|workflow|processo)\b"
    r"|\bsalv(?:a|e|ar)\b.*\b(?:skill|workflow|processo)\b"
    r")",
    re.IGNORECASE,
)


def detect_gateway_platform_action(text: Any) -> str | None:
    """Return a deterministic Mika platform action for explicit user intents."""
    normalized = _normalize_intent_text(text)
    if not normalized:
        return None

    # Plain slash commands should continue to Hermes/skills dispatch.
    if normalized.startswith("/") and not _SKILL_INTENT_RE.search(normalized):
        return None

    if _SKILL_INTENT_RE.search(normalized):
        return "skill"

    if _CRON_INTENT_RE.search(normalized) and _TEMPORAL_RE.search(normalized):
        return "cronjob"

    return None


def _load_manifest() -> dict[str, Any]:
    if not INTEGRATIONS_MANIFEST_PATH.exists():
        return {"integrations": []}
    try:
        return json.loads(INTEGRATIONS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "integrations": [],
            "error": f"failed to read integrations manifest: {exc}",
        }


def _normalize_slug(value: Any) -> str:
    return str(value or "").strip().lower()


def _pick_active_integration(slug: str) -> Tuple[dict[str, Any] | None, str | None]:
    manifest = _load_manifest()
    manifest_integrations = manifest.get("integrations", [])
    if not isinstance(manifest_integrations, list):
        return None, "integrations manifest is malformed"

    matches = [
        item
        for item in manifest_integrations
        if isinstance(item, dict)
        and _normalize_slug(item.get("slug")) == slug
    ]
    if not matches:
        return None, f"integration '{slug}' is not synced into this runtime"

    active = [
        item for item in matches
        if str(item.get("status") or "").strip().lower() == "active"
    ]
    if not active:
        status = sorted({str(item.get("status") or "unknown") for item in matches})
        return None, (
            f"integration '{slug}' is synced but not active "
            f"(current statuses: {', '.join(status)})"
        )

    chosen = sorted(
        active,
        key=lambda item: str(item.get("updated_at") or item.get("synced_at") or ""),
        reverse=True,
    )[0]
    managed_file = str(chosen.get("managed_file") or "").strip()
    if not managed_file:
        return None, f"integration '{slug}' is missing managed_file metadata"

    record_path = MANAGED_INTEGRATIONS_ROOT / managed_file
    if not record_path.exists():
        return None, (
            f"integration '{slug}' expected runtime file '{managed_file}', "
            "but it does not exist"
        )

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"failed to read integration runtime file for '{slug}': {exc}"

    access_token = str(record.get("access_token") or "").strip()
    if not access_token:
        return None, f"integration '{slug}' has no access token in runtime storage"

    return record, None


def _redact_integration(item: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(item)
    redacted.pop("access_token", None)
    redacted.pop("refresh_token", None)
    return redacted


def _coerce_query_pairs(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, dict):
        return []

    pairs: list[tuple[str, str]] = []
    for key, raw_value in value.items():
        if raw_value is None:
            continue
        if isinstance(raw_value, (list, tuple)):
            for entry in raw_value:
                pairs.append((str(key), _stringify_query_value(entry)))
            continue
        pairs.append((str(key), _stringify_query_value(raw_value)))
    return pairs


def _stringify_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _normalize_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        raise ValueError("path is required")
    if not text.startswith("/"):
        text = f"/{text}"
    return text


def _prepare_request(
    *,
    provider: str,
    base_url: str,
    allowed_methods: Iterable[str],
    extra_headers: dict[str, str],
    args: dict[str, Any],
) -> tuple[str, str, dict[str, str], bytes | None]:
    method = str(args.get("method") or "").strip().upper()
    if method not in set(allowed_methods):
        raise ValueError(
            f"method must be one of: {', '.join(sorted(set(allowed_methods)))}"
        )

    path = _normalize_path(args.get("path"))
    query_pairs = _coerce_query_pairs(args.get("query"))
    url = f"{base_url.rstrip('/')}{path}"
    if query_pairs:
        url = f"{url}?{parse.urlencode(query_pairs, doseq=True)}"

    body = args.get("body")
    payload = None
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        **extra_headers,
    }

    if body is not None:
        if method == "GET":
            raise ValueError(f"{provider} GET requests do not accept a JSON body")
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    return method, url, headers, payload


def _truncate_text(text: str, limit: int = MAX_RESPONSE_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _decode_response_body(content_type: str, raw_body: bytes) -> tuple[Any, bool]:
    if not raw_body:
        return None, False

    text = raw_body.decode("utf-8", errors="replace")
    text, truncated = _truncate_text(text)

    if "application/json" in content_type.lower():
        try:
            return json.loads(text), truncated
        except Exception:
            return text, truncated
    return text, truncated


def _perform_request(
    *,
    provider: str,
    integration: dict[str, Any],
    base_url: str,
    allowed_methods: Iterable[str],
    extra_headers: dict[str, str],
    args: dict[str, Any],
) -> str:
    try:
        method, url, headers, payload = _prepare_request(
            provider=provider,
            base_url=base_url,
            allowed_methods=allowed_methods,
            extra_headers=extra_headers,
            args=args,
        )
    except ValueError as exc:
        return _json_response({
            "ok": False,
            "provider": provider,
            "error": str(exc),
        })

    req = request.Request(
        url=url,
        data=payload,
        method=method,
        headers=headers,
    )

    try:
        with request.urlopen(req, timeout=45) as response:
            raw_body = response.read()
            content_type = response.headers.get("Content-Type", "")
            decoded_body, truncated = _decode_response_body(content_type, raw_body)
            return _json_response({
                "ok": True,
                "provider": provider,
                "integration": {
                    "slug": integration.get("slug"),
                    "name": integration.get("name"),
                    "status": integration.get("status"),
                    "connected_account_name": integration.get("connected_account_name"),
                    "connected_account_email": integration.get("connected_account_email"),
                },
                "request": {
                    "method": method,
                    "url": url,
                },
                "response": {
                    "status": response.status,
                    "content_type": content_type,
                    "truncated": truncated,
                    "body": decoded_body,
                },
            })
    except error.HTTPError as exc:
        raw_body = exc.read()
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        decoded_body, truncated = _decode_response_body(content_type, raw_body)
        return _json_response({
            "ok": False,
            "provider": provider,
            "integration": {
                "slug": integration.get("slug"),
                "name": integration.get("name"),
                "status": integration.get("status"),
                "connected_account_name": integration.get("connected_account_name"),
                "connected_account_email": integration.get("connected_account_email"),
            },
            "request": {
                "method": method,
                "url": url,
            },
            "response": {
                "status": exc.code,
                "content_type": content_type,
                "truncated": truncated,
                "body": decoded_body,
            },
        })
    except Exception as exc:
        return _json_response({
            "ok": False,
            "provider": provider,
            "request": {
                "method": method,
                "url": url,
            },
            "error": str(exc),
        })


def handle_integrations_status(args: dict[str, Any], **_: Any) -> str:
    slug = _normalize_slug(args.get("slug"))
    manifest = _load_manifest()
    integrations = manifest.get("integrations", [])
    if not isinstance(integrations, list):
        return _json_response({
            "ok": False,
            "error": "integrations manifest is malformed",
        })

    filtered = [
        _redact_integration(item)
        for item in integrations
        if isinstance(item, dict)
        and (not slug or _normalize_slug(item.get("slug")) == slug)
    ]

    return _json_response({
        "ok": True,
        "agent_instance_id": manifest.get("agent_instance_id"),
        "user_id": manifest.get("user_id"),
        "synced_at": manifest.get("synced_at"),
        "integrations": filtered,
        "available_tools": {
            "notion": "mika_notion_api",
            "todoist": "mika_todoist_api",
            "calcom": "mika_calcom_api",
        },
    })


def handle_notion_api(args: dict[str, Any], **_: Any) -> str:
    integration, err = _pick_active_integration("notion")
    if err:
        return _json_response({"ok": False, "provider": "notion", "error": err})

    return _perform_request(
        provider="notion",
        integration=integration,
        base_url="https://api.notion.com",
        allowed_methods=("GET", "POST", "PATCH"),
        extra_headers={
            "Authorization": f"Bearer {integration['access_token']}",
            "Notion-Version": str(
                integration.get("notion_version")
                or DEFAULT_NOTION_VERSION
            ),
        },
        args=args,
    )


def handle_todoist_api(args: dict[str, Any], **_: Any) -> str:
    integration, err = _pick_active_integration("todoist")
    if err:
        return _json_response({"ok": False, "provider": "todoist", "error": err})

    return _perform_request(
        provider="todoist",
        integration=integration,
        base_url="https://api.todoist.com/rest/v2",
        allowed_methods=("GET", "POST", "DELETE"),
        extra_headers={
            "Authorization": f"Bearer {integration['access_token']}",
        },
        args=args,
    )


def handle_calcom_api(args: dict[str, Any], **_: Any) -> str:
    integration, err = _pick_active_integration("calcom")
    if err:
        return _json_response({"ok": False, "provider": "calcom", "error": err})

    return _perform_request(
        provider="calcom",
        integration=integration,
        base_url="https://api.cal.com",
        allowed_methods=("GET", "POST", "PATCH"),
        extra_headers={
            "Authorization": f"Bearer {integration['access_token']}",
            "cal-api-version": str(
                integration.get("cal_api_version")
                or DEFAULT_CALCOM_VERSION
            ),
        },
        args=args,
    )


CRONJOB_CREATE_SCHEMA = {
    "name": "cronjob_create",
    "description": (
        "Creates a recurring automation, reminder, or cronjob via Supabase edge "
        "function. Use this whenever the user asks to schedule, remind, or automate "
        "something on a recurring basis (e.g. 'remind me every Monday at 9am')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "natural_language_input": {
                "type": "string",
                "description": (
                    "The user's original request in natural language "
                    "(e.g., 'remind me every Monday at 9am to check emails')."
                ),
            },
            "name": {
                "type": "string",
                "description": "A short name for this automation (optional).",
            },
        },
        "required": ["natural_language_input"],
        "additionalProperties": False,
    },
}


SKILL_CREATE_SCHEMA = {
    "name": "skill_create",
    "description": (
        "Creates a new custom Mika/Hermes skill via the Mika platform. Use this "
        "when the user asks to teach the assistant a new procedure, add a new "
        "skill, save a repeatable workflow, or turn instructions into a reusable "
        "capability."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "natural_language_input": {
                "type": "string",
                "description": (
                    "The user's original request describing the skill to create."
                ),
            },
            "name": {
                "type": "string",
                "description": "A short skill name (optional).",
            },
            "description": {
                "type": "string",
                "description": "A short description of what the skill does (optional).",
            },
            "trigger_keywords": {
                "type": "string",
                "description": (
                    "Comma-separated phrases that should trigger this skill (optional)."
                ),
            },
            "markdown_content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content if already drafted. If omitted, Mika "
                    "will generate a valid skill from natural_language_input."
                ),
            },
        },
        "required": ["natural_language_input"],
        "additionalProperties": False,
    },
}


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _platform_functions_base_url() -> str:
    explicit = _first_env(
        "MIKA_PLATFORM_FUNCTIONS_BASE_URL",
        "HERMES_PLATFORM_FUNCTIONS_BASE_URL",
    ).rstrip("/")
    if explicit:
        return explicit

    supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not supabase_url:
        return ""
    return f"{supabase_url}/functions/v1"


def _platform_endpoint(action: str) -> str:
    if action == "cronjob":
        explicit = _first_env("MIKA_CREATE_CRONJOB_URL", "HERMES_CREATE_CRONJOB_URL")
        path = "create-cronjob-from-agent"
    elif action == "skill":
        explicit = _first_env("MIKA_CREATE_SKILL_URL", "HERMES_CREATE_SKILL_URL")
        path = "create-skill-from-agent"
    else:
        raise ValueError(f"unknown platform action: {action}")

    if explicit:
        return explicit

    base_url = _platform_functions_base_url()
    if not base_url:
        return ""
    return f"{base_url}/{path}"


def _platform_auth_context(action: str) -> tuple[str, str, str] | str:
    endpoint = _platform_endpoint(action)
    internal_secret = _first_env(*INTERNAL_SECRET_ENV_NAMES)
    agent_instance_id = _first_env(*AGENT_INSTANCE_ENV_NAMES)

    if not endpoint:
        return (
            "Erro: endpoint da plataforma não configurado. Defina "
            "MIKA_CREATE_CRONJOB_URL/MIKA_CREATE_SKILL_URL ou SUPABASE_URL."
        )
    if not internal_secret:
        return "Erro: segredo interno da plataforma não configurado."
    if not agent_instance_id:
        return "Erro: agent_instance_id da Mika não configurado no runtime."

    return endpoint, internal_secret, agent_instance_id


def _post_platform_action(action: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    ctx = _platform_auth_context(action)
    if isinstance(ctx, str):
        return 0, {"ok": False, "error": ctx}

    url, internal_secret, agent_instance_id = ctx
    body_payload = {
        "agent_instance_id": agent_instance_id,
        **payload,
    }
    body = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Secret": internal_secret,
        "User-Agent": USER_AGENT,
    }

    req = request.Request(url=url, data=body, method="POST", headers=headers)

    try:
        with request.urlopen(req, timeout=45) as response:
            raw_body = response.read()
            try:
                data = json.loads(raw_body.decode("utf-8", errors="replace"))
            except Exception:
                data = {}
            return int(response.status), data
    except error.HTTPError as exc:
        raw_body = exc.read()
        try:
            data = json.loads(raw_body.decode("utf-8", errors="replace"))
        except Exception:
            data = {"error": str(exc)}
        return int(exc.code), data
    except Exception as exc:
        return 0, {"error": f"Erro de rede ao chamar plataforma: {exc}"}


def handle_cronjob_create(args: dict[str, Any], **_: Any) -> str:
    natural_language_input = str(args.get("natural_language_input") or "").strip()
    if not natural_language_input:
        return "Erro: natural_language_input é obrigatório."

    payload = {
        "natural_language_input": natural_language_input,
    }

    name = args.get("name") or None
    if name:
        payload["name"] = str(name)

    status, data = _post_platform_action("cronjob", payload)

    if status < 200 or status >= 300 or data.get("success") is False:
        err_msg = data.get("error") or data.get("message") or data.get("runtime_sync_error")
        if not err_msg:
            err_msg = f"HTTP {status}" if status else "falha desconhecida"
        return f"Erro ao criar automação: {err_msg}"

    human_readable = data.get("human_readable") or data.get("description") or ""
    next_run_at = data.get("next_run_at") or ""
    if human_readable:
        msg = f"Automação criada e sincronizada: {human_readable}."
        if next_run_at:
            msg += f" Próxima execução: {next_run_at}."
        return msg
    return "Automação criada e sincronizada com sucesso."


def handle_skill_create(args: dict[str, Any], **_: Any) -> str:
    natural_language_input = str(args.get("natural_language_input") or "").strip()
    if not natural_language_input:
        return "Erro: natural_language_input é obrigatório."

    payload: dict[str, Any] = {
        "natural_language_input": natural_language_input,
    }
    for key in ("name", "description", "trigger_keywords", "markdown_content"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()

    status, data = _post_platform_action("skill", payload)

    if status < 200 or status >= 300 or data.get("success") is False:
        if data.get("skill_id") and data.get("runtime_sync_ok") is False:
            return (
                "Skill criada na plataforma, mas ainda não sincronizada no runtime. "
                f"Ela ficou em status {data.get('status') or 'testing'}. "
                f"Erro: {data.get('runtime_sync_error') or 'sync falhou'}"
            )
        err_msg = data.get("error") or data.get("message")
        if not err_msg:
            err_msg = f"HTTP {status}" if status else "falha desconhecida"
        return f"Erro ao criar skill: {err_msg}"

    name = data.get("name") or "Skill"
    synced_count = data.get("synced_count")
    msg = f"Skill criada e sincronizada: {name}."
    if synced_count is not None:
        msg += f" Skills ativas sincronizadas: {synced_count}."
    return msg


def _source_is_authorized_for_gateway_intercept(gateway: Any, source: Any) -> bool:
    checker = getattr(gateway, "_is_user_authorized", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(source))
    except Exception:
        logger.debug("gateway auth check failed for Mika intercept", exc_info=True)
        return False


def _gateway_thread_metadata(gateway: Any, event: Any) -> dict[str, Any] | None:
    builder = getattr(gateway, "_thread_metadata_for_source", None)
    if not callable(builder):
        return None
    try:
        return builder(event.source, getattr(event, "message_id", None))
    except TypeError:
        try:
            return builder(event.source)
        except Exception:
            return None
    except Exception:
        return None


def _gateway_reply_anchor(gateway: Any, event: Any) -> str | None:
    resolver = getattr(gateway, "_reply_anchor_for_event", None)
    if callable(resolver):
        try:
            return resolver(event)
        except Exception:
            pass
    message_id = getattr(event, "message_id", None)
    return str(message_id) if message_id is not None else None


def _schedule_gateway_reply(
    *,
    loop: asyncio.AbstractEventLoop,
    adapter: Any,
    chat_id: str,
    content: str,
    reply_to: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    async def _send() -> None:
        await adapter.send(
            chat_id=chat_id,
            content=content,
            reply_to=reply_to,
            metadata=metadata,
        )

    future = asyncio.run_coroutine_threadsafe(_send(), loop)

    def _log_failure(done: Any) -> None:
        try:
            done.result()
        except Exception:
            logger.warning("failed to send Mika platform action reply", exc_info=True)

    future.add_done_callback(_log_failure)


def _run_gateway_platform_action(
    *,
    action: str,
    natural_language_input: str,
    loop: asyncio.AbstractEventLoop,
    adapter: Any,
    chat_id: str,
    reply_to: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    try:
        if action == "cronjob":
            content = handle_cronjob_create({
                "natural_language_input": natural_language_input,
            })
        elif action == "skill":
            content = handle_skill_create({
                "natural_language_input": natural_language_input,
            })
        else:
            content = "Erro: ação da plataforma não reconhecida."
    except Exception as exc:
        logger.warning("Mika platform action intercept failed", exc_info=True)
        label = "automação" if action == "cronjob" else "skill"
        content = f"Erro ao criar {label}: {exc}"

    _schedule_gateway_reply(
        loop=loop,
        adapter=adapter,
        chat_id=chat_id,
        content=content,
        reply_to=reply_to,
        metadata=metadata,
    )


def handle_gateway_platform_action_intercept(
    *,
    event: Any,
    gateway: Any,
    session_store: Any = None,
) -> dict[str, str] | None:
    """Pre-gateway hook that makes Mika platform actions deterministic.

    The LLM can still use cronjob_create/skill_create as tools, but explicit
    Telegram requests are also routed directly to Supabase before the model
    runs. That keeps Supabase as the source of truth and avoids a natural
    language "ok, vou lembrar" response that never persisted anything.
    """
    del session_store

    if not _is_truthy_env_default_true(*GATEWAY_ACTION_INTERCEPT_ENV_NAMES):
        return None

    if bool(getattr(event, "internal", False)):
        return None

    source = getattr(event, "source", None)
    if source is None or bool(getattr(source, "is_bot", False)):
        return None

    text = str(getattr(event, "text", "") or "").strip()
    action = detect_gateway_platform_action(text)
    if not action:
        return None

    if not _source_is_authorized_for_gateway_intercept(gateway, source):
        return None

    adapters = getattr(gateway, "adapters", {}) or {}
    adapter = adapters.get(getattr(source, "platform", None))
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    if adapter is None or not chat_id:
        logger.warning("Mika intercept could not find adapter/chat_id for action=%s", action)
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Mika intercept has no running event loop")
        return None

    thread = threading.Thread(
        target=_run_gateway_platform_action,
        kwargs={
            "action": action,
            "natural_language_input": text,
            "loop": loop,
            "adapter": adapter,
            "chat_id": chat_id,
            "reply_to": _gateway_reply_anchor(gateway, event),
            "metadata": _gateway_thread_metadata(gateway, event),
        },
        name=f"mika-platform-action-{action}",
        daemon=True,
    )
    thread.start()

    return {"action": "skip", "reason": f"mika_{action}_handled"}
