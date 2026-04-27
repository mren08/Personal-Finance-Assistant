import os
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import app


class RuntimeConfigTests(unittest.TestCase):
    def test_get_runtime_config_uses_render_port_and_public_host(self):
        with patch.dict(
            os.environ,
            {"PORT": "10000", "FLASK_DEBUG": "0"},
            clear=False,
        ):
            config = app.get_runtime_config()

        self.assertEqual(
            config,
            {"host": "0.0.0.0", "port": 10000, "debug": False},
        )

    def test_get_runtime_config_defaults_for_local_run(self):
        with patch.dict(os.environ, {}, clear=True):
            config = app.get_runtime_config()

        self.assertEqual(
            config,
            {"host": "0.0.0.0", "port": 5055, "debug": True},
        )

    def test_create_app_exports_routes_for_wsgi_entrypoint(self):
        flask_app = app.create_app()

        self.assertIsInstance(flask_app, Flask)
        self.assertIsInstance(app.app, Flask)

        routes = {rule.rule for rule in flask_app.url_map.iter_rules()}
        self.assertTrue({"/", "/healthz", "/api/analyze"}.issubset(routes))


class DeploymentFileTests(unittest.TestCase):
    def test_procfile_and_render_config_match_gunicorn_entrypoint(self):
        procfile = Path("Procfile").read_text(encoding="utf-8").strip()
        render_yaml = Path("render.yaml").read_text(encoding="utf-8")

        self.assertEqual(procfile, "web: gunicorn --bind 0.0.0.0:${PORT:-5055} app:app")
        self.assertIn("startCommand: gunicorn --bind 0.0.0.0:$PORT app:app", render_yaml)
        self.assertIn("healthCheckPath: /healthz", render_yaml)

    def test_requirements_include_openai_sdk_for_render_agent_mode(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")

        self.assertIn("openai", requirements.lower())

    def test_python_version_is_pinned_below_render_default_breaking_version(self):
        python_version = Path(".python-version").read_text(encoding="utf-8").strip()

        self.assertEqual(python_version, "3.13.5")


if __name__ == "__main__":
    unittest.main()
