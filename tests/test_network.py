"""Tests for network snapshot helpers."""

from __future__ import annotations

import unittest

from lan_ai_discovery.network import NetworkSnapshot


class TestNetworkSnapshot(unittest.TestCase):
    def test_from_addresses_sorts(self) -> None:
        snap = NetworkSnapshot.from_addresses(
            ["192.168.1.5", "10.0.0.1", "fe80::1"]
        )
        self.assertEqual(snap.ipv4, ("10.0.0.1", "192.168.1.5"))
        self.assertEqual(snap.ipv6, ("fe80::1",))

    def test_fingerprint_stable(self) -> None:
        a = NetworkSnapshot.from_addresses(["1.2.3.4", "5.6.7.8"])
        b = NetworkSnapshot.from_addresses(["5.6.7.8", "1.2.3.4"])
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_fingerprint_differs(self) -> None:
        a = NetworkSnapshot.from_addresses(["1.2.3.4"])
        b = NetworkSnapshot.from_addresses(["1.2.3.5"])
        self.assertNotEqual(a.fingerprint, b.fingerprint)

    def test_bool(self) -> None:
        self.assertTrue(NetworkSnapshot.from_addresses(["1.1.1.1"]))
        self.assertFalse(NetworkSnapshot.from_addresses([]))

    def test_dedup(self) -> None:
        snap = NetworkSnapshot.from_addresses(["1.1.1.1", "1.1.1.1"])
        self.assertEqual(snap.addresses, ("1.1.1.1",))


class TestNetworkMonitorListeners(unittest.TestCase):
    def test_listener_notified_on_change(self) -> None:
        from lan_ai_discovery.network import NetworkMonitor

        snapshots = [
            NetworkSnapshot.from_addresses(["1.1.1.1"]),
            NetworkSnapshot.from_addresses(["2.2.2.2"]),
        ]
        calls: list[tuple] = []

        def fake_snapshot():
            return snapshots[0]

        monitor = NetworkMonitor(poll_interval_s=100, snapshot_fn=fake_snapshot)
        monitor.add_listener(lambda prev, curr: calls.append((prev, curr)))

        # Initial refresh — no change yet (previous is None).
        monitor.refresh_now()
        self.assertEqual(len(calls), 0)

        # Change the snapshot and refresh again.
        snapshots[0] = snapshots[1]
        monitor.refresh_now()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].fingerprint, "2.2.2.2")

    def test_listener_exception_isolation(self) -> None:
        from lan_ai_discovery.network import NetworkMonitor

        snap = NetworkSnapshot.from_addresses(["1.1.1.1"])

        def boom(prev, curr):
            raise RuntimeError("boom")

        monitor = NetworkMonitor(poll_interval_s=100, snapshot_fn=lambda: snap)
        monitor.add_listener(boom)
        monitor.refresh_now()
        snap2 = NetworkSnapshot.from_addresses(["2.2.2.2"])
        # Change snapshot_fn result
        monitor._snapshot_fn = lambda: snap2  # type: ignore[assignment]
        monitor.refresh_now()  # should not raise despite listener error


if __name__ == "__main__":
    unittest.main()
