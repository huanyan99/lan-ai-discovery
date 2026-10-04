"""Redundancy tests: mDNS name conflicts must auto-suffix, never crash."""

from __future__ import annotations

import unittest
from unittest import mock

from local_ai_discovery.protocol import ServiceRecord


class TestNameConflictSuffixing(unittest.TestCase):
    def _make_announcer(self, zc_instance, **kwargs):
        from local_ai_discovery import announcer as ann

        record = ServiceRecord(name="Ollama", port=11434, vendor="ollama")
        patcher = mock.patch.object(ann, "Zeroconf", return_value=zc_instance)
        patcher.start()
        self.addCleanup(patcher.stop)
        announcer = ann.ServiceAnnouncer([record], hostname="testhost", **kwargs)
        return announcer, record

    def test_conflict_retries_with_numeric_suffix(self) -> None:
        from local_ai_discovery import announcer as ann
        from zeroconf import NonUniqueNameException

        zc = mock.MagicMock()
        # First register attempt collides, second succeeds.
        zc.register_service.side_effect = [NonUniqueNameException(), None]

        announcer, record = self._make_announcer(zc)
        announcer.start(["192.168.1.10"])

        names = [call.args[0].name for call in zc.register_service.call_args_list]
        self.assertEqual(names[0], "Ollama._local-ai._tcp.local.")
        self.assertEqual(names[1], "Ollama-2._local-ai._tcp.local.")
        # The display name stays untouched — only the mDNS instance is suffixed.
        self.assertEqual(announcer.service_names[0], "Ollama-2._local-ai._tcp.local.")
        announcer.stop()

    def test_permanent_conflict_surfaces_as_error_event(self) -> None:
        from local_ai_discovery import announcer as ann
        from zeroconf import NonUniqueNameException

        zc = mock.MagicMock()
        zc.register_service.side_effect = NonUniqueNameException()
        events: list[tuple[str, str]] = []

        with mock.patch.object(ann, "Zeroconf", return_value=zc):
            announcer = ann.ServiceAnnouncer(
                [ServiceRecord(name="Ollama", port=11434)],
                hostname="testhost",
                on_status=lambda e, d: events.append((e, d)),
            )
            announcer.start(["192.168.1.10"])

        kinds = [e for e, _ in events]
        self.assertIn("error", kinds)
        # Must not crash the process; retry loop keeps it alive.
        self.assertGreaterEqual(zc.register_service.call_count, ann._MAX_NAME_SUFFIX + 1)
        announcer.stop()

    def test_add_records_is_idempotent(self) -> None:
        from local_ai_discovery import announcer as ann

        zc = mock.MagicMock()
        record = ServiceRecord(name="GLM", port=8000, vendor="zhipu")

        with mock.patch.object(ann, "Zeroconf", return_value=zc):
            announcer = ann.ServiceAnnouncer([record], hostname="testhost")
            announcer.start(["192.168.1.10"])
            registered_first = zc.register_service.call_count
            added = announcer.add_records([record])
            self.assertEqual(added, [])
            self.assertEqual(zc.register_service.call_count, registered_first)
            announcer.stop()

    def test_add_records_announces_new(self) -> None:
        from local_ai_discovery import announcer as ann

        zc = mock.MagicMock()
        first = ServiceRecord(name="GLM", port=8000, vendor="zhipu")
        second = ServiceRecord(name="MiMo", port=8001, vendor="xiaomi")

        with mock.patch.object(ann, "Zeroconf", return_value=zc):
            announcer = ann.ServiceAnnouncer([first], hostname="testhost")
            announcer.start(["192.168.1.10"])
            added = announcer.add_records([second])
            self.assertEqual(added, ["MiMo"])
            names = [call.args[0].name for call in zc.register_service.call_args_list]
            self.assertIn("MiMo._local-ai._tcp.local.", names)
            announcer.stop()


if __name__ == "__main__":
    unittest.main()
