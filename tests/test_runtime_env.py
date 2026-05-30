from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimeEnvTests(unittest.TestCase):
    def test_image_sets_default_hermes_home(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ENV HERMES_HOME=/opt/data/.hermes", dockerfile)

    def test_entrypoint_exports_hermes_home_to_child_processes(self) -> None:
        entrypoint = (REPO_ROOT / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("export HERMES_HOME CONFIG_PATH SOUL_PATH", entrypoint)


if __name__ == "__main__":
    unittest.main()
