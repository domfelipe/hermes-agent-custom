from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from unittest import mock


class SkillsApiSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fake_web = types.SimpleNamespace(
            Request=object,
            Response=object,
            Application=object,
            json_response=lambda payload, status=200: {"payload": payload, "status": status},
            run_app=lambda *_args, **_kwargs: None,
        )
        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.web = fake_web
        fake_aiohttp.ClientSession = object
        fake_aiohttp.ClientTimeout = object
        sys.modules.setdefault("aiohttp", fake_aiohttp)
        cls.skills_api = importlib.import_module("patches.skills_api")

    def test_managed_skill_dirname_matches_user_facing_skill_name(self) -> None:
        dirname = self.skills_api._managed_skill_dirname({
            "skill_id": "ffdcdb9f-a52b-447b-ad47-57b390ce6c5d",
            "name": "teste fechamento codex",
        })

        self.assertEqual(dirname, "teste fechamento codex")

    def test_managed_skill_dirname_removes_path_separators(self) -> None:
        dirname = self.skills_api._managed_skill_dirname({
            "name": "../minha/skill\\nova",
        })

        self.assertEqual(dirname, "-minha-skill-nova")

    def test_managed_cronjobs_deliver_to_origin_with_telegram_home(self) -> None:
        def fake_next_run(_schedule, _last_run_at):
            return "2026-05-30T16:33:00+00:00"

        with mock.patch.dict(
            os.environ,
            {
                "TELEGRAM_HOME_CHANNEL": "12345",
                "TELEGRAM_CRON_THREAD_ID": "topic-1",
            },
            clear=False,
        ):
            job = self.skills_api._sanitize_cron_runtime_job(
                {
                    "job_id": "job-1",
                    "name": "validar fechamento final",
                    "action_prompt": "validar fechamento final",
                    "cron_expression": "*/3 * * * *",
                    "status": "active",
                    "human_readable": "A cada 3 minutos",
                },
                None,
                "2026-05-30T16:30:00Z",
                fake_next_run,
            )

        self.assertIsNotNone(job)
        self.assertEqual(job["deliver"], "origin")
        self.assertEqual(
            job["origin"],
            {"platform": "telegram", "chat_id": "12345", "thread_id": "topic-1"},
        )

    def test_existing_origin_is_preserved_on_resync(self) -> None:
        def fake_next_run(_schedule, _last_run_at):
            return "2026-05-30T16:33:00+00:00"

        job = self.skills_api._sanitize_cron_runtime_job(
            {
                "job_id": "job-1",
                "name": "validar fechamento final",
                "action_prompt": "validar fechamento final",
                "cron_expression": "*/3 * * * *",
                "status": "active",
                "human_readable": "A cada 3 minutos",
            },
            {
                "origin": {"platform": "telegram", "chat_id": "existing"},
                "schedule": {"expr": "*/3 * * * *"},
                "next_run_at": "2026-05-30T16:36:00+00:00",
            },
            "2026-05-30T16:30:00Z",
            fake_next_run,
        )

        self.assertIsNotNone(job)
        self.assertEqual(job["origin"], {"platform": "telegram", "chat_id": "existing"})


if __name__ == "__main__":
    unittest.main()
