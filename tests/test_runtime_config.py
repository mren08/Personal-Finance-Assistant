import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
