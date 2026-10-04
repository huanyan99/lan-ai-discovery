"""Orchestrator: wire announcer + health + network monitor into one service."""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Callable, Sequence

from .announcer import ServiceAnnouncer
from .config import AppConfig, EndpointConfig, load_config
from .health import HealthChecker, HealthResult
from .network import NetworkMonitor, get_addresses
from .protocol import ServiceRecord

logger = logging.getLogger(__name__)

__all__ = ["DiscoveryService"]


class DiscoveryService:
    """Full-stack local AI discovery: announce, health-check, adapt to network changes.

    Typical usage::

        service = DiscoveryService.from_config_file("config.yaml")
        service.start()
        ...
        service.stop()
    """

    def __init__(
        self,
        endpoints: Sequence[EndpointConfig],
        *,
        health_interval_s: float = 30.0,
        announce_refresh_s: float = 120.0,
        network_poll_s: float = 10.0,
        health_timeout_s: float = 3.0,
        retry_max_backoff_s: float = 60.0,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        self._endpoints = list(endpoints)
        self._health_interval_s = health_interval_s
        self._announce_refresh_s = announce_refresh_s
        self._network_poll_s = network_poll_s
        self._health_timeout_s = health_timeout_s
        self._retry_max_backoff_s = retry_max_backoff_s
        self._on_status = on_status

        self._announcer: ServiceAnnouncer | None = None
        self._monitor: NetworkMonitor | None = None
        self._checkers: dict[str, HealthChecker] = {}  # instance_id -> checker
        self._records: list[ServiceRecord] = []
        self._lock = threading.Lock()
        self._started = False

    # ---- constructors ------------------------------------------------

    @classmethod
    def from_config_file(
        cls,
        path: str | None = None,
        *,
        on_status: Callable[[str, str], None] | None = None,
    ) -> DiscoveryService:
        config = load_config(path)
        return cls(
            config.endpoints,
            health_interval_s=config.health_interval_s,
            announce_refresh_s=config.announce_refresh_s,
            network_poll_s=config.network_poll_s,
            health_timeout_s=config.health_timeout_s,
            retry_max_backoff_s=config.retry_max_backoff_s,
            on_status=on_status,
        )

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        on_status: Callable[[str, str], None] | None = None,
    ) -> DiscoveryService:
        return cls(
            config.endpoints,
            health_interval_s=config.health_interval_s,
            announce_refresh_s=config.announce_refresh_s,
            network_poll_s=config.network_poll_s,
            health_timeout_s=config.health_timeout_s,
            retry_max_backoff_s=config.retry_max_backoff_s,
            on_status=on_status,
        )

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return

            # Build service records.
            self._records = [ep.to_record() for ep in self._endpoints]
            if not self._records:
                raise ValueError("no endpoints configured")

            # Snapshot network (retry a few times — may be mid-boot).
            snapshot = self._wait_for_addresses()
            if snapshot is None:
                raise RuntimeError("no usable network address after retries")

            # Start announcer.
            self._announcer = ServiceAnnouncer(
                self._records,
                refresh_interval_s=self._announce_refresh_s,
                max_retry_backoff_s=self._retry_max_backoff_s,
                on_status=self._on_status,
            )
            self._announcer.start(snapshot.addresses)

            # Network monitor → re-register on change.
            self._monitor = NetworkMonitor(poll_interval_s=self._network_poll_s)
            self._monitor.add_listener(self._on_network_change)
            self._monitor.start()

            # Health checkers per endpoint.
            for record, endpoint in zip(self._records, self._endpoints):
                checker = HealthChecker(
                    port=endpoint.port,
                    models_path=endpoint.models_path,
                    base_path=endpoint.base_path,
                    timeout_s=self._health_timeout_s,
                    interval_s=self._health_interval_s,
                    on_change=self._make_health_handler(record),
                )
                checker.start()
                self._checkers[record.instance_id] = checker

            self._started = True
            logger.info(
                "discovery service started: %d endpoint(s)",
                len(self._records),
            )

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._started:
                return
            for checker in self._checkers.values():
                checker.stop(timeout=timeout)
            self._checkers.clear()
            if self._monitor is not None:
                self._monitor.stop(timeout=timeout)
                self._monitor = None
            if self._announcer is not None:
                self._announcer.stop(timeout=timeout)
                self._announcer = None
            self._started = False
            logger.info("discovery service stopped")

    def run_forever(self) -> None:
        """Start and block until SIGINT / SIGTERM."""
        self.start()
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except ValueError:
                # Not in main thread — callers should use start/stop themselves.
                pass
        try:
            while not stop.wait(1.0):
                pass
        finally:
            self.stop()

    # ---- introspection -----------------------------------------------

    @property
    def records(self) -> list[ServiceRecord]:
        return list(self._records)

    @property
    def health_snapshot(self) -> dict[str, HealthResult]:
        return {rid: c.result for rid, c in self._checkers.items()}

    # ---- internals ---------------------------------------------------

    def _wait_for_addresses(self, retries: int = 5, delay_s: float = 1.0):
        for attempt in range(retries):
            try:
                snapshot = get_addresses()
                if snapshot:
                    return snapshot
            except RuntimeError:
                pass
            if attempt < retries - 1:
                time.sleep(delay_s)
        return None

    def _on_network_change(self, previous, current) -> None:
        announcer = self._announcer
        if announcer is not None:
            announcer.update_addresses(current)

    def _make_health_handler(self, record: ServiceRecord):
        def _handler(result: HealthResult) -> None:
            announcer = self._announcer
            if announcer is not None:
                announcer.update_status(record.instance_id, result.status.value)
            if self._on_status is not None:
                try:
                    self._on_status("health", f"{record.name}: {result.status.value}")
                except Exception:
                    pass

        return _handler
