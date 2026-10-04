"""Tests for the self-healing DiscoveryWatcher (client-side HA)."""

from __future__ import annotations

import time
import unittest

from local_ai_discovery.browser import DiscoveredService, DiscoveryWatcher


class FakeBrowser:
    """Stands in for the zeroconf-backed ServiceBrowser."""

    def __init__(self, on_update, on_remove):
        self.on_update = on_update
        self.on_remove = on_remove
        self.stopped = False

    def stop(self):
        self.stopped = True


def make_service(name: str, *, age_s: float = 0.0, status: str = "up") -> DiscoveredService:
    return DiscoveredService(
        instance_name=f"{name}._local-ai._tcp.local.",
        name=name,
        host="10.0.0.5",
        port=8000,
        addresses=("10.0.0.5",),
        instance_id=f"11111111-1111-1111-1111-{abs(hash(name)) % 10**12:012d}",
        status=status,
        vendor="ollama",
        discovered_at=time.time() - age_s,
    )


class TestDiscoveryWatcher(unittest.TestCase):
    def _watcher(self, stale_after_s: float = 30.0):
        events: list[tuple[str, DiscoveredService | None]] = []
        watcher = DiscoveryWatcher(
            lambda name, svc: events.append((name, svc)),
            stale_after_s=stale_after_s,
            browser_factory=lambda on_update, on_remove: FakeBrowser(on_update, on_remove),
        )
        return watcher, events

    def test_ingest_and_forget_drive_callbacks(self) -> None:
        watcher, events = self._watcher()
        svc = make_service("GLM")
        watcher._ingest(svc)
        self.assertEqual(events[-1], (svc.instance_name, svc))
        self.assertEqual(len(watcher.snapshot()), 1)

        watcher._forget(svc.instance_name)
        self.assertEqual(events[-1], (svc.instance_name, None))
        self.assertEqual(watcher.snapshot(), [])
        self.assertIsNone(watcher.get(svc.instance_name))

    def test_stale_entries_marked_down(self) -> None:
        watcher, events = self._watcher(stale_after_s=30.0)
        fresh = make_service("Fresh")
        old = make_service("Old", age_s=3600.0)
        watcher._ingest(fresh)
        watcher._ingest(old)

        watcher._mark_stale()

        downed = watcher.get(old.instance_name)
        assert downed is not None
        self.assertEqual(downed.status, "down")
        self.assertEqual(watcher.get(fresh.instance_name).status, "up")  # type: ignore[union-attr]
        last = events[-1]
        self.assertEqual(last[0], old.instance_name)
        assert last[1] is not None
        self.assertEqual(last[1].status, "down")

    def test_stale_marking_does_not_refire(self) -> None:
        watcher, events = self._watcher(stale_after_s=30.0)
        watcher._ingest(make_service("Old", age_s=3600.0))
        watcher._mark_stale()
        count = len(events)
        watcher._mark_stale()
        self.assertEqual(len(events), count)

    def test_recreates_dead_browser(self) -> None:
        attempts: list[FakeBrowser] = []

        def factory(on_update, on_remove):
            browser = FakeBrowser(on_update, on_remove)
            attempts.append(browser)
            return browser

        watcher = DiscoveryWatcher(
            lambda name, svc: None, browser_factory=factory
        )
        watcher.start()
        self.assertEqual(len(attempts), 1)
        first = attempts[0]

        # Simulate the maintenance loop restarting a dead browser: the old
        # one must be stopped before the new one is created.
        watcher._restart_browser()
        self.assertTrue(first.stopped)
        self.assertEqual(len(attempts), 2)

        watcher.stop()
        self.assertTrue(attempts[1].stopped)

    def test_start_is_idempotent(self) -> None:
        attempts: list[FakeBrowser] = []

        def factory(on_update, on_remove):
            browser = FakeBrowser(on_update, on_remove)
            attempts.append(browser)
            return browser

        watcher = DiscoveryWatcher(lambda n, s: None, browser_factory=factory)
        watcher.start()
        watcher.start()
        self.assertEqual(len(attempts), 1)
        watcher.stop()

    def test_callback_exceptions_do_not_kill_watcher(self) -> None:
        def boom(name, svc):
            raise RuntimeError("callback bug")

        watcher = DiscoveryWatcher(
            boom, browser_factory=lambda u, r: FakeBrowser(u, r)
        )
        watcher._ingest(make_service("GLM"))  # must not raise
        watcher.stop()


if __name__ == "__main__":
    unittest.main()


class TestBestAddress(unittest.TestCase):
    def test_consumer_lan_wins_over_virtual_adapters(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(
            svc, "addresses", ("172.23.0.1", "192.168.0.5", "198.18.0.1")
        )
        self.assertEqual(svc.best_address(), "192.168.0.5")
        # base_url must be built from the reachable address
        self.assertTrue(svc.base_url.startswith("http://192.168.0.5:"))

    def test_fallback_order(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(svc, "addresses", ("198.18.0.1",))
        self.assertEqual(svc.best_address(), "198.18.0.1")
        object.__setattr__(svc, "addresses", ())
        self.assertEqual(svc.best_address(), "10.0.0.5")  # falls back to host


class TestIPv6AddressSelection(unittest.TestCase):
    def test_global_v6_preferred_over_ula_and_foreign_ipv4(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(
            svc, "addresses", ("198.18.0.1", "fd00::5", "2606:4700::1111")
        )
        self.assertEqual(svc.best_address(), "2606:4700::1111")

    def test_ula_v6_before_foreign_ipv4(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(svc, "addresses", ("198.18.0.1", "fd00::5"))
        self.assertEqual(svc.best_address(), "fd00::5")

    def test_rfc1918_ipv4_still_wins_over_global_v6(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(svc, "addresses", ("2606:4700::1111", "192.168.0.5"))
        self.assertEqual(svc.best_address(), "192.168.0.5")

    def test_v6_base_url_gets_brackets(self) -> None:
        svc = make_service("GLM")
        object.__setattr__(svc, "addresses", ("2001:db8::5",))
        self.assertEqual(svc.base_url, "http://[2001:db8::5]:8000/v1")
