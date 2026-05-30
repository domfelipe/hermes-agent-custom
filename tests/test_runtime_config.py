from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfigTests(unittest.TestCase):
    def test_entrypoint_enables_mika_runtime_plugin_for_telegram(self) -> None:
        entrypoint = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("plugins:\n  enabled:\n    - mika_runtime", entrypoint)
        self.assertIn(
            "platform_toolsets:\n"
            "  telegram:\n"
            "    - web\n"
            "    - browser\n"
            "    - terminal\n"
            "    - file\n"
            "    - code_execution\n"
            "    - vision\n"
            "    - image_gen\n"
            "    - tts\n"
            "    - todo\n"
            "    - memory\n"
            "    - session_search\n"
            "    - clarify\n"
            "    - delegation\n"
            "    - messaging\n"
            "    - computer_use\n"
            "    - mika_integrations",
            entrypoint,
        )
        self.assertNotIn("    - hermes-telegram", entrypoint)
        self.assertNotIn("    - cronjob", entrypoint)
        self.assertNotIn("    - skills", entrypoint)

    def test_dockerfile_fallback_config_matches_runtime_plugin_settings(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("'plugins:' \\", dockerfile)
        self.assertIn("'    - mika_runtime' \\", dockerfile)
        self.assertIn("'platform_toolsets:' \\", dockerfile)
        self.assertIn("'    - web' \\", dockerfile)
        self.assertIn("'    - browser' \\", dockerfile)
        self.assertIn("'    - terminal' \\", dockerfile)
        self.assertIn("'    - file' \\", dockerfile)
        self.assertIn("'    - code_execution' \\", dockerfile)
        self.assertIn("'    - vision' \\", dockerfile)
        self.assertIn("'    - image_gen' \\", dockerfile)
        self.assertIn("'    - tts' \\", dockerfile)
        self.assertIn("'    - todo' \\", dockerfile)
        self.assertIn("'    - memory' \\", dockerfile)
        self.assertIn("'    - session_search' \\", dockerfile)
        self.assertIn("'    - clarify' \\", dockerfile)
        self.assertIn("'    - delegation' \\", dockerfile)
        self.assertIn("'    - messaging' \\", dockerfile)
        self.assertIn("'    - computer_use' \\", dockerfile)
        self.assertIn("'    - mika_integrations' \\", dockerfile)
        self.assertNotIn("'    - hermes-telegram' \\", dockerfile)
        self.assertNotIn("'    - cronjob' \\", dockerfile)
        self.assertNotIn("'    - skills' \\", dockerfile)


if __name__ == "__main__":
    unittest.main()
