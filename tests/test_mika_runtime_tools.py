from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


_HERMES_HOME = tempfile.mkdtemp(prefix="mika-runtime-test-")
_hermes_constants = types.ModuleType("hermes_constants")
_hermes_constants.get_hermes_home = lambda: _HERMES_HOME
sys.modules.setdefault("hermes_constants", _hermes_constants)

from plugins.mika_runtime import tools  # noqa: E402


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]):
        self.status = status
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class MikaRuntimePlatformActionTests(unittest.TestCase):
    def test_cronjob_create_posts_platform_contract(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["secret"] = req.get_header("X-internal-secret")
            captured["user_agent"] = req.get_header("User-agent")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(
                200,
                {
                    "success": True,
                    "human_readable": "toda segunda as 09:00",
                    "next_run_at": "2026-06-01T09:00:00Z",
                },
            )

        with mock.patch.dict(
            os.environ,
            {
                "MIKA_AGENT_INSTANCE_ID": "agent-123",
                "MIKA_INTERNAL_FUNCTION_SECRET": "secret-abc",
                "MIKA_CREATE_CRONJOB_URL": "https://example.test/functions/v1/create-cronjob-from-agent",
            },
            clear=True,
        ), mock.patch.object(tools.request, "urlopen", side_effect=fake_urlopen):
            result = tools.handle_cronjob_create({
                "natural_language_input": "toda segunda as 9h me envie um resumo",
                "name": "Resumo semanal",
            })

        self.assertIn("Automação criada e sincronizada", result)
        self.assertEqual(
            captured["url"],
            "https://example.test/functions/v1/create-cronjob-from-agent",
        )
        self.assertEqual(captured["secret"], "secret-abc")
        self.assertEqual(captured["user_agent"], tools.USER_AGENT)
        self.assertEqual(captured["timeout"], 45)
        self.assertEqual(captured["body"]["agent_instance_id"], "agent-123")
        self.assertEqual(
            captured["body"]["natural_language_input"],
            "toda segunda as 9h me envie um resumo",
        )
        self.assertEqual(captured["body"]["name"], "Resumo semanal")

    def test_skill_create_uses_supabase_url_fallback_and_reports_sync_failure(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(
                502,
                {
                    "success": False,
                    "skill_id": "skill-123",
                    "status": "testing",
                    "runtime_sync_ok": False,
                    "runtime_sync_error": "runtime offline",
                },
            )

        with mock.patch.dict(
            os.environ,
            {
                "AGENT_INSTANCE_ID": "agent-456",
                "INTERNAL_FUNCTION_SECRET": "secret-def",
                "SUPABASE_URL": "https://project.supabase.co",
            },
            clear=True,
        ), mock.patch.object(tools.request, "urlopen", side_effect=fake_urlopen):
            result = tools.handle_skill_create({
                "natural_language_input": "aprenda meu processo de pré-vendas",
                "name": "Pré-vendas",
            })

        self.assertEqual(
            captured["url"],
            "https://project.supabase.co/functions/v1/create-skill-from-agent",
        )
        self.assertEqual(captured["body"]["agent_instance_id"], "agent-456")
        self.assertEqual(captured["body"]["name"], "Pré-vendas")
        self.assertIn("Skill criada na plataforma", result)
        self.assertIn("testing", result)
        self.assertIn("runtime offline", result)

    def test_missing_platform_config_does_not_call_network(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            tools.request,
            "urlopen",
        ) as urlopen:
            result = tools.handle_cronjob_create({
                "natural_language_input": "me lembre todo dia",
            })

        urlopen.assert_not_called()
        self.assertIn("endpoint da plataforma não configurado", result)


if __name__ == "__main__":
    unittest.main()
