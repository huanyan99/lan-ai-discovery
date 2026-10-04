"""Tests for config loading."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lan_ai_discovery.config import AppConfig, EndpointConfig, load_config
from lan_ai_discovery.protocol import ServiceRecord


class TestEndpointConfig(unittest.TestCase):
    def test_to_record(self) -> None:
        ep = EndpointConfig(
            name="MyAI",
            port=8080,
            vendor="deepseek",
            label="DS",
            models_list="deepseek-chat,deepseek-coder",
        )
        record = ep.to_record()
        self.assertIsInstance(record, ServiceRecord)
        self.assertEqual(record.name, "MyAI")
        self.assertEqual(record.port, 8080)
        self.assertEqual(record.vendor, "deepseek")
        self.assertEqual(record.label, "DS")
        self.assertEqual(record.models_list, "deepseek-chat,deepseek-coder")

    def test_to_record_with_instance_id(self) -> None:
        ep = EndpointConfig(name="X", port=1)
        rid = "12345678-1234-1234-1234-123456789abc"
        record = ep.to_record(instance_id=rid)
        self.assertEqual(record.instance_id, rid)


class TestLoadConfig(unittest.TestCase):
    def test_defaults_from_env(self) -> None:
        env = {"LOCAL_AI_NAME": "TestHost", "LOCAL_AI_PORT": "9999"}
        config = load_config(environ=env)
        self.assertEqual(len(config.endpoints), 1)
        self.assertEqual(config.endpoints[0].name, "TestHost")
        self.assertEqual(config.endpoints[0].port, 9999)
        self.assertEqual(config.endpoints[0].api, "openai")
        self.assertEqual(config.health_interval_s, 30.0)

    def test_env_overrides_auth_and_vendor(self) -> None:
        env = {
            "LOCAL_AI_NAME": "A",
            "LOCAL_AI_AUTH": "api-key",
            "LOCAL_AI_VENDOR": "minimax",
        }
        config = load_config(environ=env)
        self.assertEqual(config.endpoints[0].auth, "api-key")
        self.assertEqual(config.endpoints[0].vendor, "minimax")

    def test_yaml_single_endpoint_original_schema(self) -> None:
        text = """
service:
  name: "Local-AI-01"
api:
  port: 11434
  type: "openai"
  auth: "none"
  base_path: "/v1"
  models_path: "/v1/models"
"""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            path = fh.name
        try:
            config = load_config(path, environ={})
            self.assertEqual(len(config.endpoints), 1)
            self.assertEqual(config.endpoints[0].name, "Local-AI-01")
            self.assertEqual(config.endpoints[0].port, 11434)
        finally:
            os.unlink(path)

    def test_yaml_multi_endpoint(self) -> None:
        text = """
discovery:
  health_interval_s: 15
  announce_refresh_s: 60
endpoints:
  - service:
      name: "GLM"
      label: "ChatGLM"
    api:
      port: 8001
      vendor: "glm"
      models_path: "/v1/models"
  - service:
      name: "DeepSeek"
    api:
      port: 8002
      vendor: "deepseek"
      auth: "api-key"
"""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            path = fh.name
        try:
            config = load_config(path, environ={})
            self.assertEqual(len(config.endpoints), 2)
            self.assertEqual(config.endpoints[0].name, "GLM")
            self.assertEqual(config.endpoints[0].vendor, "glm")
            self.assertEqual(config.endpoints[1].name, "DeepSeek")
            self.assertEqual(config.endpoints[1].auth, "api-key")
            self.assertEqual(config.health_interval_s, 15.0)
            self.assertEqual(config.announce_refresh_s, 60.0)
        finally:
            os.unlink(path)

    def test_env_overrides_first_endpoint(self) -> None:
        text = """
endpoints:
  - service:
      name: "Original"
    api:
      port: 1111
"""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            path = fh.name
        try:
            env = {"LOCAL_AI_NAME": "Overridden", "LOCAL_AI_PORT": "2222"}
            config = load_config(path, environ=env)
            self.assertEqual(config.endpoints[0].name, "Overridden")
            self.assertEqual(config.endpoints[0].port, 2222)
            self.assertEqual(len(config.endpoints), 1)
        finally:
            os.unlink(path)

    def test_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/path.yaml", environ={})

    def test_invalid_port(self) -> None:
        with self.assertRaises(ValueError):
            load_config(environ={"LOCAL_AI_PORT": "not-a-number"})

    def test_invalid_endpoints_type(self) -> None:
        text = 'endpoints: "not-a-list"'
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            path = fh.name
        try:
            with self.assertRaises(ValueError):
                load_config(path, environ={})
        finally:
            os.unlink(path)

    def test_discovery_knobs_validation(self) -> None:
        text = """
discovery:
  health_interval_s: 0
"""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            path = fh.name
        try:
            with self.assertRaises(ValueError):
                load_config(path, environ={})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
