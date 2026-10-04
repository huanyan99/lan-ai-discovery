"""High-availability mDNS service announcer.

Responsibilities
----------------
* Register / update / unregister DNS-SD service records via zeroconf.
* Automatic re-registration when the network address set changes.
* Periodic announce refresh so NAT / switch tables stay warm.
* Exponential-backoff retry on registration failures.
* Multi-endpoint support (several AI models / ports on one host).
"""

from __future__ import annotations

import logging
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .network import NetworkSnapshot
from .protocol import SERVICE_TYPE, ServiceRecord, encode_txt

logger = logging.getLogger(__name__)

__all__ = ["ServiceAnnouncer", "AnnouncerError"]

_ZEROCONF_IMPORT_ERROR: Exception | None
try:
    from zeroconf import NonUniqueNameException, ServiceInfo, Zeroconf  # type: ignore[import-untyped]

    _ZEROCONF_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    ServiceInfo = None  # type: ignore[assignment,misc]
    Zeroconf = None  # type: ignore[assignment,misc]
    NonUniqueNameException = None  # type: ignore[assignment,misc]
    _ZEROCONF_IMPORT_ERROR = exc

# Max rename attempts when another host already announces the same instance
# name (two machines running "Ollama" on one LAN is the normal case).
_MAX_NAME_SUFFIX = 9


def _jittered(interval_s: float, fraction: float = 0.1) -> float:
    """De-synchronise periodic loops (wake-from-sleep herds, NAT timeouts)."""
    return max(0.5, interval_s * (1.0 + random.uniform(-fraction, fraction)))


class AnnouncerError(RuntimeError):
    """Raised when zeroconf is unavailable or registration permanently fails."""


@dataclass
class _Entry:
    record: ServiceRecord
    info: object  # ServiceInfo
    registered: bool = False
    last_addresses: tuple[str, ...] = ()
    # mDNS instance name may gain a numeric suffix ("Ollama-2") when the
    # desired name is already taken on this LAN; the display name is kept.
    mdns_name: str = field(default="")


class ServiceAnnouncer:
    """Advertise one or more local AI endpoints over mDNS / DNS-SD."""

    def __init__(
        self,
        records: Sequence[ServiceRecord],
        *,
        hostname: str | None = None,
        refresh_interval_s: float = 120.0,
        max_retry_backoff_s: float = 60.0,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        if _ZEROCONF_IMPORT_ERROR is not None:
            raise AnnouncerError(
                "zeroconf is not installed; run: pip install zeroconf"
                " (if registration fails on Windows, also allow mDNS 5353/udp"
                " through the firewall)"
            ) from _ZEROCONF_IMPORT_ERROR
        if not records:
            raise ValueError("at least one ServiceRecord is required")

        self._records = list(records)
        self._hostname = (hostname or _local_hostname()).rstrip(".") + ".local."
        self._refresh_interval_s = max(10.0, refresh_interval_s)
        self._max_backoff = max(1.0, max_retry_backoff_s)
        self._on_status = on_status

        self._zc: object | None = None
        self._entries: list[_Entry] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._retry_delay = 1.0
        self._started = False

    # ---- public API --------------------------------------------------

    def start(self, addresses: Iterable[str]) -> None:
        with self._lock:
            if self._started:
                return
            self._start_locked(tuple(addresses))
            self._started = True

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop.set()
            for t in self._threads:
                t.join(timeout=timeout)
            self._threads.clear()
            self._unregister_all()
            self._close_zc()
            self._started = False

    def update_addresses(self, snapshot: NetworkSnapshot) -> None:
        """Called by NetworkMonitor on change — re-register all services."""
        with self._lock:
            if not self._started:
                return
            addresses = snapshot.addresses
            if not addresses:
                logger.warning("no addresses after network change; will retry")
                self._schedule_retry()
                return
            for entry in self._entries:
                self._register_entry(entry, addresses)

    def update_status(self, instance_id: str, status: str) -> None:
        """Update the ``status`` TXT field of one endpoint (from HealthChecker)."""
        with self._lock:
            if not self._started:
                return
            for entry in self._entries:
                if entry.record.instance_id == instance_id:
                    new_record = entry.record.with_status(status)
                    entry.record = new_record
                    addresses = entry.last_addresses or self._current_addresses()
                    self._register_entry(entry, addresses)
                    return

    def add_records(self, records: Sequence[ServiceRecord]) -> list[str]:
        """Announce additional records while running (e.g. late-detected servers).

        Idempotent per ``instance_id``; returns the names actually added.
        """
        added: list[str] = []
        with self._lock:
            if not self._started:
                return added
            known = {e.record.instance_id for e in self._entries}
            addresses = self._current_addresses() or self._learn_addresses()
            for record in records:
                if record.instance_id in known:
                    continue
                entry = _Entry(record=record, info=self._build_info(record, addresses or ()),
                               last_addresses=addresses or ())
                self._entries.append(entry)
                self._register_entry(entry, addresses or ())
                added.append(record.name)
        return added

    def refresh_all(self) -> None:
        """Force a re-announce (keeps mDNS caches fresh)."""
        with self._lock:
            if not self._started:
                return
            addresses = self._current_addresses()
            for entry in self._entries:
                self._register_entry(entry, addresses)

    @property
    def service_names(self) -> list[str]:
        return [e.mdns_name or e.record.instance_name for e in self._entries]

    # ---- internals ---------------------------------------------------

    def _start_locked(self, addresses: tuple[str, ...]) -> None:
        self._stop.clear()
        self._zc = Zeroconf()
        self._entries = []
        for record in self._records:
            info = self._build_info(record, addresses)
            self._entries.append(_Entry(record=record, info=info, last_addresses=addresses))

        for entry in self._entries:
            self._register_entry(entry, addresses)

        self._start_thread(self._refresh_loop, "local-ai-announce-refresh")
        self._start_thread(self._retry_loop, "local-ai-announce-retry")

        for entry in self._entries:
            logger.info(
                "advertising %s  host=%s  port=%s  status=%s",
                entry.record.instance_name,
                self._hostname,
                entry.record.port,
                entry.record.status,
            )
            self._emit("registered", entry.record.name)

    def _register_entry(self, entry: _Entry, addresses: Sequence[str]) -> None:
        if not addresses:
            logger.warning("skip register %s: no addresses", entry.record.name)
            return
        try:
            zc = self._zc
            if zc is None:
                raise AnnouncerError("zeroconf not started")

            attempts = 0
            while True:
                info = self._build_info(entry.record, addresses, entry.mdns_name)
                try:
                    if entry.registered:
                        try:
                            zc.update_service(info)  # type: ignore[attr-defined]
                        except Exception:
                            # Some zeroconf versions raise on update for
                            # unregistered services — fall back to re-register.
                            try:
                                zc.unregister_service(entry.info)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            zc.register_service(info)  # type: ignore[attr-defined]
                    else:
                        zc.register_service(info)  # type: ignore[attr-defined]
                    entry.registered = True
                    entry.info = info
                    entry.last_addresses = tuple(addresses)
                    self._retry_delay = 1.0
                    self._emit("updated", entry.record.name)
                    return
                except NonUniqueNameException:
                    if entry.registered or attempts >= _MAX_NAME_SUFFIX:
                        raise
                    attempts += 1
                    # First rename is "-2", matching avahi / Bonjour convention.
                    entry.mdns_name = f"{entry.record.name}-{attempts + 1}.{SERVICE_TYPE}"
                    logger.warning(
                        "name %s in use on this LAN — retrying as %s",
                        entry.record.name,
                        entry.mdns_name,
                    )
        except Exception as exc:
            logger.error("register %s failed: %s", entry.record.name, exc)
            self._emit("error", f"{entry.record.name}: {exc}")
            self._schedule_retry()

    def _unregister_all(self) -> None:
        zc = self._zc
        if zc is None:
            return
        for entry in self._entries:
            if entry.registered:
                try:
                    zc.unregister_service(entry.info)  # type: ignore[attr-defined]
                    logger.info("unregistered %s", entry.record.name)
                    self._emit("unregistered", entry.record.name)
                except Exception as exc:
                    logger.warning("unregister %s failed: %s", entry.record.name, exc)
                entry.registered = False

    def _close_zc(self) -> None:
        zc = self._zc
        self._zc = None
        if zc is not None:
            try:
                zc.close()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("zeroconf close failed")

    def _build_info(
        self,
        record: ServiceRecord,
        addresses: Sequence[str],
        mdns_name: str = "",
    ) -> object:
        props = encode_txt(record.txt_properties())
        return ServiceInfo(
            SERVICE_TYPE,
            mdns_name or record.instance_name,
            parsed_addresses=list(addresses),
            port=record.port,
            properties=props,
            server=self._hostname,
        )

    def _current_addresses(self) -> tuple[str, ...]:
        for entry in self._entries:
            if entry.last_addresses:
                return entry.last_addresses
        return ()

    def _learn_addresses(self) -> tuple[str, ...]:
        try:
            from .network import get_addresses

            return tuple(get_addresses().addresses)
        except Exception:
            return ()

    def _schedule_retry(self) -> None:
        # Retry loop handles backoff; nothing to do inline.
        pass

    def _emit(self, event: str, detail: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(event, detail)
            except Exception:
                pass

    def _start_thread(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    # ---- background loops --------------------------------------------

    def _refresh_loop(self) -> None:
        while not self._stop.wait(_jittered(self._refresh_interval_s)):
            try:
                self.refresh_all()
            except Exception:
                logger.exception("announce refresh failed")

    def _retry_loop(self) -> None:
        while not self._stop.wait(self._retry_delay):
            with self._lock:
                if not self._started:
                    return
                dirty = [e for e in self._entries if not e.registered or not e.last_addresses]
                if not dirty:
                    self._retry_delay = min(self._max_backoff, self._retry_delay * 2)
                    continue
                addresses = self._current_addresses()
                if not addresses:
                    # Try to re-learn addresses.
                    try:
                        from .network import get_addresses

                        addresses = get_addresses().addresses
                    except RuntimeError:
                        pass
                if not addresses:
                    self._retry_delay = min(self._max_backoff, self._retry_delay * 2)
                    continue
                for entry in dirty:
                    self._register_entry(entry, addresses)
                self._retry_delay = 1.0


def _local_hostname() -> str:
    hostname = socket.gethostname().rstrip(".")
    if hostname.lower().endswith(".local"):
        hostname = hostname[:-6]
    return hostname.split(".", 1)[0] or "local-ai"
