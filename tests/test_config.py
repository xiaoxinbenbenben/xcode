import os
import unittest
from unittest.mock import patch

from src.runtime.config import RuntimeConfig, load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_load_runtime_config_reads_env_and_defaults_model(self) -> None:
        with patch("src.runtime.config.load_dotenv"):
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.com/v1",
                },
                clear=True,
            ):
                config = load_runtime_config()

        self.assertEqual(
            config,
            RuntimeConfig(
                api_key="test-key",
                model="gpt-5.2",
                base_url="https://example.com/v1",
            ),
        )

    def test_load_runtime_config_requires_api_key(self) -> None:
        with patch("src.runtime.config.load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(SystemExit) as context:
                    load_runtime_config()

        self.assertIn("OPENAI_API_KEY", str(context.exception))

    def test_load_runtime_config_strips_bearer_prefix_from_api_key(self) -> None:
        with patch("src.runtime.config.load_dotenv"):
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "Bearer test-key",
                    "OPENAI_BASE_URL": "https://example.com/v1",
                    "OPENAI_MODEL": "gpt-5.2-codex",
                },
                clear=True,
            ):
                config = load_runtime_config()

        self.assertEqual(
            config,
            RuntimeConfig(
                api_key="test-key",
                model="gpt-5.2-codex",
                base_url="https://example.com/v1",
            ),
        )


if __name__ == "__main__":
    unittest.main()
