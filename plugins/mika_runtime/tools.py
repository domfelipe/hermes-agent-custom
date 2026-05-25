"""Stable runtime bridge for integrations synced from Mika into Hermes."""

from __future__ import annotations

import json
import os
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


def handle_cronjob_create(args: dict[str, Any], **_: Any) -> str:
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    internal_secret = os.environ.get("INTERNAL_FUNCTION_SECRET", "")
    agent_instance_id = os.environ.get("AGENT_INSTANCE_ID", "")

    if not supabase_url:
        return "Erro: variável de ambiente SUPABASE_URL não configurada."
    if not internal_secret:
        return "Erro: variável de ambiente INTERNAL_FUNCTION_SECRET não configurada."
    if not agent_instance_id:
        return "Erro: variável de ambiente AGENT_INSTANCE_ID não configurada."

    natural_language_input = str(args.get("natural_language_input") or "").strip()
    if not natural_language_input:
        return "Erro: natural_language_input é obrigatório."

    name = args.get("name") or None

    url = f"{supabase_url}/functions/v1/create-cronjob-from-agent"
    body_payload = {
        "agent_instance_id": agent_instance_id,
        "natural_language_input": natural_language_input,
        "name": name,
    }
    payload = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-internal-secret": internal_secret,
        "User-Agent": USER_AGENT,
    }

    req = request.Request(url=url, data=payload, method="POST", headers=headers)

    try:
        with request.urlopen(req, timeout=30) as response:
            raw_body = response.read()
            try:
                data = json.loads(raw_body.decode("utf-8", errors="replace"))
            except Exception:
                data = {}
            human_readable = data.get("human_readable") or data.get("description") or ""
            next_run_at = data.get("next_run_at") or ""
            if human_readable:
                msg = f"Automação criada: {human_readable}."
                if next_run_at:
                    msg += f" Próxima execução: {next_run_at}."
                return msg
            return "Automação criada com sucesso."
    except error.HTTPError as exc:
        raw_body = exc.read()
        try:
            data = json.loads(raw_body.decode("utf-8", errors="replace"))
            err_msg = data.get("error") or data.get("message") or str(exc)
        except Exception:
            err_msg = str(exc)
        return f"Erro ao criar automação: {err_msg}"
    except Exception as exc:
        return f"Erro de rede ao criar automação: {exc}"
