"""Tests for health probing (no real network — uses mocked urlopen)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from lan_ai_discovery.health import HealthChecker, HealthResult, HealthStatus


def _make_checker(**kwargs) -> HealthChecker:
    defaults = dict(port=11434, timeout_s=1.0, interval_s=100.0)
    defaults.update(kwargs)
    return HealthChecker(**defaults)


class TestHealthProbe(unittest.TestCase):
    def test_up_with_models(self) -> None:
        body = json.dumps(
            {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-coder"}]}
        ).encode()

        class FakeResponse:
            status = 200

            def read(self, n):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        checker = _make_checker()
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse()
        ):
            result = checker.probe_once()
        self.assertEqual(result.status, HealthStatus.UP)
        self.assertEqual(result.models, ("deepseek-chat", "deepseek-coder"))
        self.assertIsNone(result.error)
        self.assertTrue(result.ok)

    def test_degraded_on_unparseable_body(self) -> None:
        class FakeResponse:
            status = 200

            def read(self, n):
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        checker = _make_checker()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = checker.probe_once()
        self.assertEqual(result.status, HealthStatus.DEGRADED)
        self.assertFalse(result.ok)

    def test_degraded_on_auth_required(self) -> None:
        class FakeResponse:
            status = 401

            def read(self, n):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        checker = _make_checker()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = checker.probe_once()
        self.assertEqual(result.status, HealthStatus.DEGRADED)

    def test_down_on_connection_error(self) -> None:
        import urllib.error

        checker = _make_checker()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            result = checker.probe_once()
        self.assertEqual(result.status, HealthStatus.DOWN)
        self.assertIsNotNone(result.error)

    def test_down_on_http_500(self) -> None:
        import urllib.error

        checker = _make_checker()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="http://x", code=500, msg="err", hdrs=None, fp=None
            ),
        ):
            result = checker.probe_once()
        self.assertEqual(result.status, HealthStatus.DOWN)

    def test_on_change_callback_fires(self) -> None:
        calls: list[HealthResult] = []
        checker = _make_checker(on_change=calls.append)

        class FakeResponse:
            status = 200

            def read(self, n):
                return json.dumps({"data": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            checker.probe_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, HealthStatus.UP)

    def test_no_callback_when_status_unchanged(self) -> None:
        calls: list[HealthResult] = []
        checker = _make_checker(on_change=calls.append)

        class FakeResponse:
            status = 200

            def read(self, n):
                return json.dumps({"data": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            checker.probe_once()
            checker.probe_once()
        # Only the first status change (not-checked → up) fires.
        self.assertEqual(len(calls), 1)

    def test_models_url(self) -> None:
        checker = _make_checker(models_path="/v1/models")
        self.assertIn("/v1/models", checker._models_url)
        self.assertIn("11434", checker._models_url)


class TestExtractModels(unittest.TestCase):
    def test_openai_shape(self) -> None:
        from lan_ai_discovery.health import _extract_models

        body = json.dumps({"data": [{"id": "a"}, {"id": "b"}]}).encode()
        self.assertEqual(_extract_models(body), ("a", "b"))

    def test_list_shape(self) -> None:
        from lan_ai_discovery.health import _extract_models

        body = json.dumps(["m1", "m2"]).encode()
        self.assertEqual(_extract_models(body), ("m1", "m2"))

    def test_models_key_shape(self) -> None:
        from lan_ai_discovery.health import _extract_models

        body = json.dumps({"models": ["x"]}).encode()
        self.assertEqual(_extract_models(body), ("x",))

    def test_invalid(self) -> None:
        from lan_ai_discovery.health import _extract_models

        self.assertIsNone(_extract_models(b"garbage"))
        self.assertIsNone(_extract_models(b"\xff\xfe"))


if __name__ == "__main__":
    unittest.main()


class TestIPv6Url(unittest.TestCase):
    def test_v6_host_is_bracketed(self) -> None:
        from lan_ai_discovery.health import HealthChecker

        checker = HealthChecker(port=8000, host="2001:db8::5")
        self.assertEqual(checker._models_url, "http://[2001:db8::5]:8000/v1/models")

    def test_v6_relative_models_path(self) -> None:
        from lan_ai_discovery.health import HealthChecker

        checker = HealthChecker(port=8000, host="::1",
                                base_path="/v1", models_path="/models")
        self.assertEqual(checker._models_url, "http://[::1]:8000/v1/models")
