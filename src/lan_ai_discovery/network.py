"""Network interface discovery and change detection."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

__all__ = ["NetworkSnapshot", "get_addresses", "NetworkMonitor"]


@dataclass(frozen=True)
class NetworkSnapshot:
    """Immutable view of usable non-loopback addresses."""

    addresses: tuple[str, ...]
    fingerprint: str

    @classmethod
    def from_addresses(cls, addresses: Iterable[str]) -> NetworkSnapshot:
        sorted_addrs = sorted(
            set(addresses),
            key=lambda a: (ipaddress.ip_address(a).version, a),
        )
        return cls(addresses=tuple(sorted_addrs), fingerprint="|".join(sorted_addrs))

    @property
    def ipv4(self) -> tuple[str, ...]:
        return tuple(a for a in self.addresses if ipaddress.ip_address(a).version == 4)

    @property
    def ipv6(self) -> tuple[str, ...]:
        return tuple(a for a in self.addresses if ipaddress.ip_address(a).version == 6)

    def __bool__(self) -> bool:
        return bool(self.addresses)


def get_addresses() -> NetworkSnapshot:
    """Return all usable non-loopback addresses (global, private, ULA).

    Excludes loopback, unspecified and link-local (APIPA / fe80::) addresses.
    Raises ``RuntimeError`` if none are found — callers should treat this
    as "network not ready yet" and retry.
    """
    try:
        import ifaddr
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ifaddr is required for address discovery") from exc

    found: set[str] = set()
    for adapter in ifaddr.get_adapters():
        for adapter_ip in adapter.ips:
            raw = adapter_ip.ip[0] if isinstance(adapter_ip.ip, tuple) else adapter_ip.ip
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified or address.is_link_local:
                continue
            found.add(str(address))

    if not found:
        raise RuntimeError("no usable non-loopback network address found")
    return NetworkSnapshot.from_addresses(found)


class NetworkMonitor:
    """Poll the network and fire callbacks when the address set changes.

    Designed for HA: the announcer re-registers its mDNS records whenever
    the set of usable IPs changes (Wi-Fi roam, DHCP renew, VPN up/down).
    """

    def __init__(
        self,
        *,
        poll_interval_s: float = 10.0,
        snapshot_fn: Callable[[], NetworkSnapshot] = get_addresses,
    ) -> None:
        self._poll_interval_s = max(1.0, poll_interval_s)
        self._snapshot_fn = snapshot_fn
        self._listeners: list[Callable[[NetworkSnapshot, NetworkSnapshot], None]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._current: NetworkSnapshot | None = None
        self._lock = threading.Lock()

    # ---- listeners ---------------------------------------------------

    def add_listener(
        self, fn: Callable[[NetworkSnapshot, NetworkSnapshot], None]
    ) -> None:
        self._listeners.append(fn)

    def remove_listener(
        self, fn: Callable[[NetworkSnapshot, NetworkSnapshot], None]
    ) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    # ---- lifecycle ---------------------------------------------------

    @property
    def current(self) -> NetworkSnapshot | None:
        return self._current

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="local-ai-network-monitor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def refresh_now(self) -> NetworkSnapshot:
        """Force an immediate snapshot and notify on change."""
        snapshot = self._safe_snapshot()
        with self._lock:
            previous = self._current
            self._current = snapshot
        if previous is not None and previous.fingerprint != snapshot.fingerprint:
            self._notify(previous, snapshot)
        return snapshot

    # ---- internals ---------------------------------------------------

    def _safe_snapshot(self) -> NetworkSnapshot:
        try:
            return self._snapshot_fn()
        except RuntimeError as exc:
            logger.warning("network snapshot failed: %s", exc)
            return NetworkSnapshot.from_addresses([])

    def _notify(self, previous: NetworkSnapshot, current: NetworkSnapshot) -> None:
        logger.info(
            "network changed: %s -> %s",
            previous.fingerprint or "(none)",
            current.fingerprint or "(none)",
        )
        for fn in list(self._listeners):
            try:
                fn(previous, current)
            except Exception:
                logger.exception("network listener failed")

    def _run(self) -> None:
        # Initial snapshot without notify.
        self._current = self._safe_snapshot()
        while not self._stop.wait(self._poll_interval_s):
            snapshot = self._safe_snapshot()
            with self._lock:
                previous = self._current
                self._current = snapshot
            if previous is not None and previous.fingerprint != snapshot.fingerprint:
                self._notify(previous, snapshot)
