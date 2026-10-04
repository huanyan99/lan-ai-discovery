"""Conformance tests for the `_local-ai._tcp.local.` wire protocol (v1).

These pin the externally visible contract documented in docs/PROTOCOL.md:
TXT schema and byte budgets, name rules, API dialects, auth headers and
vendor-hint mapping.  Changing any assertion here is a protocol change.
"""

from __future__ import annotations

import unittest

from lan_ai_discovery.protocol import (
    PROTOCOL_VERSION,
    SERVICE_TYPE,
    AuthMode,
    ServiceRecord,
    ServiceStatus,
    auth_header_for,
    chat_path_for,
    decode_txt,
    encode_txt,
    vendor_from_model,
    vendor_hint,
)


class TestProtocolIdentity(unittest.TestCase):
    def test_service_type_and_version(self) -> None:
        self.assertEqual(SERVICE_TYPE, "_local-ai._tcp.local.")
        self.assertEqual(PROTOCOL_VERSION, "1")

    def test_txt_roundtrip(self) -> None:
        record = ServiceRecord(
            name="RuLei-Ollama",
            port=11434,
            api="openai",
            auth="none",
            base_path="/v1",
            models_path="/v1/models",
            instance_id="12345678-1234-1234-1234-123456789abc",
            status="up",
            vendor="ollama",
            label="Ollama",
            models_list="qwen3.8,llama-4",
        )
        props = record.txt_properties()
        self.assertEqual(props["v"], "1")
        decoded = decode_txt(encode_txt(props))
        self.assertEqual(decoded["api"], "openai")
        self.assertEqual(decoded["auth"], "none")
        self.assertEqual(decoded["base"], "/v1")
        self.assertEqual(decoded["models"], "/v1/models")
        self.assertEqual(decoded["id"], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(decoded["status"], "up")
        self.assertEqual(decoded["vendor"], "ollama")
        self.assertEqual(decoded["models_list"], "qwen3.8,llama-4")
        self.assertEqual(decoded["label"], "Ollama")

    def test_decode_tolerates_zeroconf_types(self) -> None:
        decoded = decode_txt({b"api": b"openai", b"auth": None, "status": True, "base": "/v1"})
        self.assertEqual(decoded["api"], "openai")
        self.assertEqual(decoded["auth"], "")
        self.assertEqual(decoded["status"], "")
        self.assertEqual(decoded["base"], "/v1")

    def test_decode_empty(self) -> None:
        self.assertEqual(decode_txt(None), {})


class TestProtocolLimits(unittest.TestCase):
    def test_name_max_63_utf8_bytes(self) -> None:
        ServiceRecord(name="名" * 21, port=8000)  # 63 bytes exactly
        with self.assertRaises(ValueError):
            ServiceRecord(name="名" * 22, port=8000)

    def test_models_list_max_255_bytes(self) -> None:
        with self.assertRaises(ValueError):
            ServiceRecord(name="X", port=8000, models_list="m" * 300)

    def test_instance_id_must_be_uuid(self) -> None:
        with self.assertRaises(ValueError):
            ServiceRecord(name="X", port=8000, instance_id="not-a-uuid")

    def test_txt_key_charset(self) -> None:
        with self.assertRaises(ValueError):
            encode_txt({"bad key!": "v"})

    def test_record_is_immutable(self) -> None:
        record = ServiceRecord(name="X", port=8000, status="up")
        down = record.with_status(ServiceStatus.DOWN)
        self.assertEqual(record.status, "up")
        self.assertEqual(down.status, "down")
        with self.assertRaises(Exception):
            record.status = "down"  # type: ignore[misc]


class TestDialects(unittest.TestCase):
    def test_chat_paths(self) -> None:
        self.assertEqual(chat_path_for("openai", "/v1"), "/v1/chat/completions")
        self.assertEqual(chat_path_for("anthropic", "/v1"), "/v1/messages")
        self.assertEqual(chat_path_for("custom", "/v1"), "/v1/chat/completions")
        self.assertEqual(chat_path_for("openai", "/api/v1/"), "/api/v1/chat/completions")

    def test_auth_headers(self) -> None:
        self.assertEqual(
            auth_header_for("openai", "sk-x"),
            {"Authorization": "Bearer sk-x"},
        )
        anthropic = auth_header_for("anthropic", "sk-ant-x")
        self.assertEqual(anthropic["x-api-key"], "sk-ant-x")
        self.assertEqual(anthropic["anthropic-version"], "2023-06-01")

    def test_auth_modes(self) -> None:
        self.assertEqual({m.value for m in AuthMode}, {"none", "api-key"})


class TestVendorHints(unittest.TestCase):
    """Deploy-the-model-and-be-discovered: model ids map to vendor hints."""

    def test_mainstream_model_ids(self) -> None:
        table = {
            "glm-5.3": "zhipu",
            "glm-5.3-flash": "zhipu",
            "chatglm3-6b": "zhipu",
            "mimo-v2.6": "xiaomi",
            "MiMo-7B-RL": "xiaomi",
            "minimax-m3": "minimax",
            "abab-7": "minimax",
            "deepseek-v4": "deepseek",
            "deepseek-r1-distill": "deepseek",
            "gpt-oss-20b": "openai",
            "gpt-4o-mini": "openai",
            "qwen3.8-27b-instruct": "qwen",
            "kimi-k3": "moonshot",
            "llama-4-scout": "meta",
            "mistral-medium-3.5": "mistral",
            "grok-4.7": "xai",
            "claude-sonnet-4": "anthropic",
            "gemini-3.8-flash": "google",
        }
        for model_id, expected in table.items():
            self.assertEqual(vendor_from_model(model_id), expected, model_id)

    def test_longest_prefix_wins(self) -> None:
        # "chatglm…" must map via the longer alias, not "glm".
        self.assertEqual(vendor_from_model("chatglm4-9b"), "zhipu")

    def test_unknown_model_is_custom(self) -> None:
        self.assertEqual(vendor_from_model("totally-custom-weights"), "custom")
        self.assertEqual(vendor_from_model(None), "custom")

    def test_vendor_hint_normalisation(self) -> None:
        self.assertEqual(vendor_hint("ChatGLM"), "zhipu")
        self.assertEqual(vendor_hint("deepseek"), "deepseek")
        self.assertEqual(vendor_hint("nonsense"), "custom")
        self.assertEqual(vendor_hint(None), "custom")


if __name__ == "__main__":
    unittest.main()
