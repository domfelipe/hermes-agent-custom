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
            "    - hermes-telegram\n"
            "    - mika_integrations",
            entrypoint,
        )

    def test_dockerfile_fallback_config_matches_runtime_plugin_settings(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("'plugins:' \\", dockerfile)
        self.assertIn("'    - mika_runtime' \\", dockerfile)
        self.assertIn("'platform_toolsets:' \\", dockerfile)
        self.assertIn("'    - hermes-telegram' \\", dockerfile)
        self.assertIn("'    - mika_integrations' \\", dockerfile)


if __name__ == "__main__":
    unittest.main()
