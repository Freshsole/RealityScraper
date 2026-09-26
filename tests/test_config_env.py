from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config


class ApiKeyEnvTests(unittest.TestCase):
    def test_placeholder_stripe_key_is_ignored(self):
        os.environ["RF_TEST_STRIPE"] = "sk_test_..."
        self.assertEqual(config._api_key_env("RF_TEST_STRIPE"), "")

    def test_empty_key_is_ignored(self):
        os.environ["RF_TEST_STRIPE"] = ""
        self.assertEqual(config._api_key_env("RF_TEST_STRIPE"), "")

    def test_real_looking_key_is_kept(self):
        os.environ["RF_TEST_STRIPE"] = "sk_test_51abcdefghijklmnopqrstuvwxyz"
        self.assertTrue(config._api_key_env("RF_TEST_STRIPE").startswith("sk_test_51"))

    def test_dotenv_replaces_empty_inherited_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("RF_DOTENV_FILL=sk_test_51realkeyfromfile\n", encoding="utf-8")
            with patch.dict(os.environ, {"RF_DOTENV_FILL": ""}, clear=False):
                config._apply_dotenv(path)
                self.assertEqual(os.environ["RF_DOTENV_FILL"], "sk_test_51realkeyfromfile")

    def test_dotenv_keeps_real_process_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("RF_DOTENV_KEEP=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"RF_DOTENV_KEEP": "from-process"}, clear=False):
                config._apply_dotenv(path)
                self.assertEqual(os.environ["RF_DOTENV_KEEP"], "from-process")


if __name__ == "__main__":
    unittest.main()
