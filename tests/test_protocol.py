"""Tests for the protocol layer."""

from __future__ import annotations

import unittest
import uuid

from local_ai_discovery.protocol import (
    PROTOCOL_VERSION,
    SERVICE_TYPE,
    AuthMode,
    ServiceRecord,
    ServiceStatus,
    ApiDialect,
    auth_header_for,
    chat_path_for,
    extra_headers_for,
    decode_txt,
    encode_txt,
    vendor_hint,
)


class TestServiceRecord(unittest.TestCase):
    def _make(self, **kwargs) -> ServiceRecord:
        defaults = dict(name="TestAI", port=11434)
        defaults.update(kwargs)
        return ServiceRecord(**defaults)

    def test_defaults(self) -> None:
        record = self._make()
        self.assertEqual(record.api, "openai")
        self.assertEqual(record.auth, AuthMode.NONE.value)
        self.assertEqual(record.base_path, "/v1")
        self.assertEqual(record.models_path, "/v1/models")
        self.assertEqual(record.status, ServiceStatus.UP.value)
        self.assertEqual(record.vendor, "custom")
        # instance_id is a valid UUID
        uuid.UUID(record.instance_id)

    def test_instance_name(self) -> None:
        record = self._make()
        self.assertEqual(record.instance_name, f"TestAI.{SERVICE_TYPE}")

    def test_txt_properties(self) -> None:
        record = self._make(vendor="deepseek", label="My DeepSeek")
        props = record.txt_properties()
        self.assertEqual(props["v"], PROTOCOL_VERSION)
        self.assertEqual(props["api"], "openai")
        self.assertEqual(props["auth"], "none")
        self.assertEqual(props["base"], "/v1")
        self.assertEqual(props["models"], "/v1/models")
        self.assertEqual(props["vendor"], "deepseek")
        self.assertEqual(props["label"], "My DeepSeek")
        self.assertIn("id", props)
        self.assertIn("status", props)

    def test_invalid_name_empty(self) -> None:
        with self.assertRaises(ValueError):
            self._make(name="")

    def test_invalid_name_too_long(self) -> None:
        with self.assertRaises(ValueError):
            self._make(name="x" * 64)

    def test_invalid_port(self) -> None:
        with self.assertRaises(ValueError):
            self._make(port=0)
        with self.assertRaises(ValueError):
            self._make(port=70000)

    def test_invalid_auth(self) -> None:
        with self.assertRaises(ValueError):
            self._make(auth="oauth")

    def test_invalid_status(self) -> None:
        with self.assertRaises(ValueError):
            self._make(status="zombie")

    def test_invalid_base_path(self) -> None:
        with self.assertRaises(ValueError):
            self._make(base_path="v1")

    def test_invalid_instance_id(self) -> None:
        with self.assertRaises(ValueError):
            self._make(instance_id="not-a-uuid")

    def test_with_status(self) -> None:
        record = self._make()
        down = record.with_status(ServiceStatus.DOWN)
        self.assertEqual(down.status, "down")
        self.assertEqual(down.instance_id, record.instance_id)

    def test_txt_byte_limit(self) -> None:
        with self.assertRaises(ValueError):
            self._make(label="x" * 300)

    def test_service_type(self) -> None:
        self.assertEqual(SERVICE_TYPE, "_local-ai._tcp.local.")


class TestVendorHint(unittest.TestCase):
    def test_known_aliases(self) -> None:
        self.assertEqual(vendor_hint("deepseek"), "deepseek")
        self.assertEqual(vendor_hint("GLM"), "zhipu")
        self.assertEqual(vendor_hint("glm"), "zhipu")
        self.assertEqual(vendor_hint("ChatGLM"), "zhipu")
        self.assertEqual(vendor_hint("kimi"), "moonshot")
        self.assertEqual(vendor_hint("minimax"), "minimax")
        self.assertEqual(vendor_hint("mimo"), "xiaomi")
        self.assertEqual(vendor_hint("gpt"), "openai")
        self.assertEqual(vendor_hint("ollama"), "ollama")
        self.assertEqual(vendor_hint("llama.cpp"), "llama-cpp")

    def test_top10_vendors(self) -> None:
        # 1. Anthropic (Claude Opus / Sonnet / Fable)
        self.assertEqual(vendor_hint("claude-opus"), "anthropic")
        self.assertEqual(vendor_hint("claude-sonnet"), "anthropic")
        self.assertEqual(vendor_hint("claude-fable"), "anthropic")
        # 2. OpenAI (GPT-6 Astra / Sol / Luna)
        self.assertEqual(vendor_hint("gpt-6"), "openai")
        self.assertEqual(vendor_hint("gpt-6-astra"), "openai")
        self.assertEqual(vendor_hint("gpt-6-sol"), "openai")
        # 3. Google (Gemini 4 Argon / 3.8 Flash)
        self.assertEqual(vendor_hint("gemini-4-argon"), "google")
        self.assertEqual(vendor_hint("gemini"), "google")
        # 4. DeepSeek (DeepSeek-V4)
        self.assertEqual(vendor_hint("deepseek-v4"), "deepseek")
        # 5. Meta (Muse Spark / Glimmer)
        self.assertEqual(vendor_hint("muse-spark"), "meta")
        self.assertEqual(vendor_hint("muse-glimmer"), "meta")
        # 6. Mistral (Medium 3.5)
        self.assertEqual(vendor_hint("mistral-medium"), "mistral")
        # 7. Qwen (Qwen3.8)
        self.assertEqual(vendor_hint("qwen3-8"), "qwen")
        self.assertEqual(vendor_hint("tongyi"), "qwen")
        # 8. Zhipu (GLM-5.3)
        self.assertEqual(vendor_hint("glm-5-3"), "zhipu")
        # 9. Moonshot (Kimi K3 / K2 Horizon)
        self.assertEqual(vendor_hint("kimi-k3"), "moonshot")
        self.assertEqual(vendor_hint("k2-horizon"), "moonshot")
        # 10. MiniMax (M3)
        self.assertEqual(vendor_hint("minimax-m3"), "minimax")

    def test_runners_up(self) -> None:
        self.assertEqual(vendor_hint("grok-4-7"), "xai")
        self.assertEqual(vendor_hint("mimo-v2"), "xiaomi")
        self.assertEqual(vendor_hint("step-5"), "step")
        self.assertEqual(vendor_hint("nemotron-3"), "nvidia")
        self.assertEqual(vendor_hint("command-r"), "cohere")
        self.assertEqual(vendor_hint("litellm"), "litellm")
        self.assertEqual(vendor_hint("one-api"), "one-api")

    def test_unknown(self) -> None:
        self.assertEqual(vendor_hint("weird-vendor"), "custom")
        self.assertEqual(vendor_hint(None), "custom")
        self.assertEqual(vendor_hint(""), "custom")

    def test_preserves_known(self) -> None:
        self.assertEqual(vendor_hint("vllm"), "vllm")


class TestApiDialect(unittest.TestCase):
    def test_openai_auth_header(self) -> None:
        headers = auth_header_for("openai", "sk-abc")
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], "Bearer sk-abc")

    def test_anthropic_auth_header(self) -> None:
        headers = auth_header_for("anthropic", "sk-ant-abc")
        self.assertIn("x-api-key", headers)
        self.assertEqual(headers["x-api-key"], "sk-ant-abc")
        self.assertNotIn("Authorization", headers)

    def test_anthropic_extra_headers(self) -> None:
        extra = extra_headers_for("anthropic")
        self.assertIn("anthropic-version", extra)

    def test_openai_extra_headers_empty(self) -> None:
        self.assertEqual(extra_headers_for("openai"), {})

    def test_chat_path_openai(self) -> None:
        self.assertEqual(chat_path_for("openai", "/v1"), "/v1/chat/completions")

    def test_chat_path_anthropic(self) -> None:
        self.assertEqual(chat_path_for("anthropic", "/v1"), "/v1/messages")

    def test_chat_path_custom_fallback(self) -> None:
        self.assertEqual(chat_path_for("custom", "/v1"), "/v1/chat/completions")

    def test_anthropic_record(self) -> None:
        record = ServiceRecord(
            name="Claude",
            port=8000,
            api=ApiDialect.ANTHROPIC.value,
            vendor="anthropic",
            auth="api-key",
        )
        props = record.txt_properties()
        self.assertEqual(props["api"], "anthropic")
        self.assertEqual(props["vendor"], "anthropic")

    def test_invalid_api_dialect(self) -> None:
        with self.assertRaises(ValueError):
            ServiceRecord(name="X", port=8000, api="gRPC")


class TestEncodeDecodeTxt(unittest.TestCase):
    def test_roundtrip(self) -> None:
        original = {"v": "1", "api": "openai", "auth": "none"}
        self.assertEqual(decode_txt(encode_txt(original)), original)

    def test_decode_bytes(self) -> None:
        raw = {b"v": b"1", b"api": b"openai"}
        self.assertEqual(decode_txt(raw), {"v": "1", "api": "openai"})

    def test_decode_none(self) -> None:
        self.assertEqual(decode_txt(None), {})
        self.assertEqual(decode_txt({}), {})

    def test_decode_bool_value(self) -> None:
        self.assertEqual(decode_txt({"flag": True}), {"flag": ""})

    def test_invalid_key(self) -> None:
        with self.assertRaises(ValueError):
            encode_txt({"bad key!": "value"})

    def test_byte_limit(self) -> None:
        with self.assertRaises(ValueError):
            encode_txt({"k": "x" * 300})


if __name__ == "__main__":
    unittest.main()
