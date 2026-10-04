"""Tests for the announcer (mocked zeroconf)."""

from __future__ import annotations

import unittest
from unittest import mock

from lan_ai_discovery.network import NetworkSnapshot
from lan_ai_discovery.protocol import ServiceRecord


class TestServiceAnnouncer(unittest.TestCase):
    def _make_record(self, name="TestAI", port=11434, **kwargs) -> ServiceRecord:
        return ServiceRecord(name=name, port=port, **kwargs)

    def test_requires_records(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer

        with self.assertRaises(ValueError):
            ServiceAnnouncer([])

    def test_build_info(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer, _Entry

        record = self._make_record(vendor="deepseek")
        with mock.patch("lan_ai_discovery.announcer.Zeroconf") as MockZC:
            MockZC.return_value = mock.MagicMock()
            announcer = ServiceAnnouncer([record], hostname="testhost")
            info = announcer._build_info(record, ("192.168.1.1",))
            self.assertIsNotNone(info)

    def test_start_register_and_stop_unregister(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer

        record = self._make_record()
        events: list[tuple[str, str]] = []

        with mock.patch("lan_ai_discovery.announcer.Zeroconf") as MockZC:
            zc_instance = mock.MagicMock()
            MockZC.return_value = zc_instance

            announcer = ServiceAnnouncer(
                [record],
                hostname="testhost",
                on_status=lambda e, d: events.append((e, d)),
            )
            announcer.start(["192.168.1.10"])
            zc_instance.register_service.assert_called()

            names = announcer.service_names
            self.assertEqual(len(names), 1)
            self.assertIn("TestAI", names[0])

            announcer.stop()
            zc_instance.unregister_service.assert_called()
            zc_instance.close.assert_called()

        self.assertTrue(any(e == "registered" for e, _ in events))
        self.assertTrue(any(e == "unregistered" for e, _ in events))

    def test_update_status(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer

        record = self._make_record()
        with mock.patch("lan_ai_discovery.announcer.Zeroconf") as MockZC:
            zc_instance = mock.MagicMock()
            MockZC.return_value = zc_instance
            announcer = ServiceAnnouncer([record], hostname="testhost")
            announcer.start(["192.168.1.10"])
            announcer.update_status(record.instance_id, "down")
            # update_service should have been called (or re-register path)
            self.assertTrue(
                zc_instance.update_service.called or zc_instance.register_service.called
            )
            announcer.stop()

    def test_update_addresses_re_registers(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer

        record = self._make_record()
        with mock.patch("lan_ai_discovery.announcer.Zeroconf") as MockZC:
            zc_instance = mock.MagicMock()
            MockZC.return_value = zc_instance
            announcer = ServiceAnnouncer([record], hostname="testhost")
            announcer.start(["192.168.1.10"])
            zc_instance.reset_mock()
            announcer.update_addresses(
                NetworkSnapshot.from_addresses(["10.0.0.5"])
            )
            self.assertTrue(
                zc_instance.update_service.called or zc_instance.register_service.called
            )
            announcer.stop()

    def test_multi_endpoint(self) -> None:
        from lan_ai_discovery.announcer import ServiceAnnouncer

        records = [
            self._make_record(name="GLM", port=8001),
            self._make_record(name="DeepSeek", port=8002),
            self._make_record(name="MiMo", port=8003),
        ]
        with mock.patch("lan_ai_discovery.announcer.Zeroconf") as MockZC:
            zc_instance = mock.MagicMock()
            MockZC.return_value = zc_instance
            announcer = ServiceAnnouncer(records, hostname="testhost")
            announcer.start(["192.168.1.10"])
            self.assertEqual(len(announcer.service_names), 3)
            self.assertEqual(zc_instance.register_service.call_count, 3)
            announcer.stop()
            self.assertEqual(zc_instance.unregister_service.call_count, 3)


class TestProtocolMultiVendor(unittest.TestCase):
    """End-to-end record construction for each supported vendor."""

    def test_vendor_records(self) -> None:
        vendors = ["deepseek", "glm", "minimax", "mimo", "openai", "ollama"]
        for vendor in vendors:
            record = ServiceRecord(
                name=f"AI-{vendor}", port=11434, vendor=vendor
            )
            props = record.txt_properties()
            self.assertEqual(props["v"], "1")
            self.assertEqual(props["api"], "openai")
            self.assertTrue(props["base"].startswith("/"))


if __name__ == "__main__":
    unittest.main()
