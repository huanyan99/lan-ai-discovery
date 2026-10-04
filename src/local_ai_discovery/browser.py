"""mDNS service browser / discovery client.

Any machine on the same LAN can call :func:`discover` to enumerate all
local AI endpoints, or :class:`ServiceBrowser` for continuous watching.
"""

from __future__ import annotations

import logging
import ipaddress
import socket
import threading
import time
import uuid
from dataclasses import replace as dataclass_replace
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .protocol import (
    SERVICE_TYPE,
    AuthMode,
    ServiceRecord,
    ServiceStatus,
    auth_header_for,
    chat_path_for,
    decode_txt,
    extra_headers_for,
    vendor_hint,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DiscoveredService",
    "ServiceBrowser",
    "DiscoveryWatcher",
    "discover",
    "discover_one",
]

# Consumer-LAN IPv4 ranges, most-likely-reachable first.
_RFC1918_NETS = (
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
)


@dataclass(frozen=True)
class DiscoveredService:
    """A local AI endpoint found via mDNS."""

    instance_name: str
    name: str
    host: str
    port: int
    addresses: tuple[str, ...]
    api: str = "openai"
    auth: str = AuthMode.NONE.value
    base_path: str = "/v1"
    models_path: str = "/v1/models"
    instance_id: str = ""
    status: str = ServiceStatus.UP.value
    vendor: str = "custom"
    label: str | None = None
    models_list: tuple[str, ...] = ()
    properties: dict[str, str] = field(default_factory=dict)
    discovered_at: float = field(default_factory=time.time)

    # ---- convenience -------------------------------------------------

    def best_address(self) -> str:
        """Address most likely reachable from other LAN devices, IPv4 or IPv6.

        Preference order:

        1. RFC1918 IPv4 (192.168/16, 10/8, 172.16/12) — the consumer LAN;
        2. global IPv6 (2000::/3);
        3. unique-local IPv6 (fc00::/7);
        4. any remaining IPv4;
        5. the SRV hostname.

        Announcers register every interface, including virtual ones (WSL
        ``172.23.x``, VPN-TUN ``198.18.x``) and link-local has no scope here,
        so this ordering picks addresses other devices can actually dial.
        """
        ipv4: list[str] = []
        v6_global: list[str] = []
        v6_ula: list[str] = []
        for raw in self.addresses:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if ip.version == 4:
                ipv4.append(str(ip))
            elif ip.is_global:
                v6_global.append(str(ip))
            else:
                v6_ula.append(str(ip))
        for network in _RFC1918_NETS:
            for addr in ipv4:
                try:
                    if ipaddress.ip_address(addr) in network:
                        return addr
                except ValueError:
                    continue
        if v6_global:
            return v6_global[0]
        if v6_ula:
            return v6_ula[0]
        if ipv4:
            return ipv4[0]
        return self.addresses[0] if self.addresses else self.host

    def _host_port(self) -> str:
        addr = self.best_address()
        if ":" in addr and not addr.startswith("["):
            addr = f"[{addr}]"
        return f"{addr}:{self.port}"

    @property
    def base_url(self) -> str:
        """API root URL (first address)."""
        return f"http://{self._host_port()}{self.base_path}"

    @property
    def models_url(self) -> str:
        return f"http://{self._host_port()}{self.models_path}"

    @property
    def chat_url(self) -> str:
        """Full URL of the chat/completion endpoint for this API dialect."""
        return f"http://{self._host_port()}{self.chat_path}"

    @property
    def chat_path(self) -> str:
        """Chat completion path: ``/v1/chat/completions`` or ``/v1/messages``."""
        return chat_path_for(self.api, self.base_path)

    @property
    def auth_header_name(self) -> str:
        """Auth header name: ``Authorization`` or ``x-api-key``."""
        return "x-api-key" if self.api == "anthropic" else "Authorization"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        """Build the full auth + extra headers dict for *api_key*."""
        headers = auth_header_for(self.api, api_key)
        headers.update(extra_headers_for(self.api))
        return headers

    @property
    def is_up(self) -> bool:
        return self.status == ServiceStatus.UP.value

    def is_stale(self, max_age_s: float = 900.0, *, now: float | None = None) -> bool:
        """True when no announcement refreshed this entry within *max_age_s*.

        mDNS goodbye packets get lost when a host powers off abruptly, so
        clients must age out silent entries instead of trusting them forever.
        """
        current = time.time() if now is None else now
        return (current - self.discovered_at) > max_age_s

    def matches(
        self,
        *,
        vendor: str | None = None,
        api: str | None = None,
        status: str | None = None,
    ) -> bool:
        if vendor and vendor_hint(self.vendor) != vendor_hint(vendor):
            return False
        if api and self.api != api:
            return False
        if status and self.status != status:
            return False
        return True

    def to_record(self) -> ServiceRecord:
        """Convert back into a broadcastable :class:`ServiceRecord`.

        Used when re-announcing services discovered on the LAN (relay mode)
        or feeding them into other tools.  A missing / malformed instance id
        is replaced by a stable UUID derived from the instance name.
        """
        try:
            instance_id = self.instance_id
            uuid.UUID(instance_id)
        except ValueError:
            instance_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-ai:{self.instance_name}"))
        name = self.name
        if len(name.encode("utf-8")) > 63:
            name = name.encode("utf-8")[:63].decode("utf-8", "ignore")
        models = list(self.models_list)
        while models and len(",".join(models).encode("utf-8")) > 255:
            models.pop()
        models_list = ",".join(models) or None
        return ServiceRecord(
            name=name,
            port=self.port,
            api=self.api,
            auth=self.auth,
            base_path=self.base_path,
            models_path=self.models_path,
            instance_id=instance_id,
            status=self.status,
            vendor=self.vendor,
            label=self.label,
            models_list=models_list,
        )


class _BrowserDelegate:
    """zeroconf ServiceBrowser listener → DiscoveredService."""

    def __init__(self, on_update: Callable[[DiscoveredService], None]) -> None:
        self._on_update = on_update
        self._seen: dict[str, DiscoveredService] = {}
        self._lock = threading.Lock()

    # zeroconf callbacks (name, zeroconf)
    def add_service(self, zc, type_: str, name: str) -> None:
        self._resolve(zc, name)

    def update_service(self, zc, type_: str, name: str) -> None:
        self._resolve(zc, name)

    def remove_service(self, zc, type_: str, name: str) -> None:
        with self._lock:
            self._seen.pop(name, None)

    def _resolve(self, zc, name: str) -> None:
        try:
            from zeroconf import ServiceInfo  # noqa: F401

            info = zc.get_service_info(SERVICE_TYPE, name, timeout=3000)
        except Exception as exc:
            logger.debug("resolve %s failed: %s", name, exc)
            return
        if info is None:
            return
        service = _to_discovered(info)
        if service is None:
            return
        with self._lock:
            self._seen[name] = service
        try:
            self._on_update(service)
        except Exception:
            logger.exception("discovery callback failed")


def _to_discovered(info) -> DiscoveredService | None:
    """Convert zeroconf ServiceInfo → DiscoveredService (tolerant of missing fields)."""
    try:
        props = decode_txt(getattr(info, "properties", None) or {})
        name = str(info.name)
        # Instance name is "<instance>.<type>."
        short = name.split(".", 1)[0] if "." in name else name
        host = str(getattr(info, "server", "") or "").rstrip(".")
        addresses = tuple(info.parsed_addresses() or ())  # type: ignore[attr-defined]
        port = int(getattr(info, "port", 0) or 0)

        models_list: tuple[str, ...] = ()
        if props.get("models_list"):
            models_list = tuple(m.strip() for m in props["models_list"].split(",") if m.strip())

        return DiscoveredService(
            instance_name=name,
            name=short,
            host=host,
            port=port,
            addresses=addresses,
            api=props.get("api", "openai"),
            auth=props.get("auth", AuthMode.NONE.value),
            base_path=props.get("base", "/v1"),
            models_path=props.get("models", "/v1/models"),
            instance_id=props.get("id", ""),
            status=props.get("status", ServiceStatus.UP.value),
            vendor=vendor_hint(props.get("vendor", "custom")),
            label=props.get("label"),
            models_list=models_list,
            properties=props,
        )
    except Exception:
        logger.exception("failed to convert ServiceInfo")
        return None


class ServiceBrowser:
    """Continuously watch for local AI services on the LAN."""

    def __init__(
        self,
        on_update: Callable[[DiscoveredService], None],
        *,
        on_remove: Callable[[str], None] | None = None,
    ) -> None:
        self._on_update = on_update
        self._on_remove = on_remove
        self._zc = None
        self._browser = None
        self._delegate: _BrowserDelegate | None = None
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            from zeroconf import Zeroconf

            self._zc = Zeroconf()
            self._delegate = _BrowserDelegate(self._on_update)
            self._browser = self._zc.add_service_listener(  # type: ignore[attr-defined]
                SERVICE_TYPE, self._delegate
            )
            self._started = True
            logger.info("browsing %s", SERVICE_TYPE)

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            if self._zc is not None:
                try:
                    self._zc.remove_service_listener(self._delegate)  # type: ignore[attr-defined]
                except Exception:
                    pass
                try:
                    self._zc.close()  # type: ignore[attr-defined]
                except Exception:
                    logger.exception("browser zeroconf close failed")
            self._zc = None
            self._browser = None
            self._delegate = None
            self._started = False


def discover(
    timeout_s: float = 3.0,
    *,
    vendor: str | None = None,
    api: str | None = None,
    status: str | None = None,
) -> list[DiscoveredService]:
    """One-shot discovery: return all matching services found within *timeout_s*."""
    from zeroconf import Zeroconf

    found: dict[str, DiscoveredService] = {}
    lock = threading.Lock()

    def _on_update(service: DiscoveredService) -> None:
        if not service.matches(vendor=vendor, api=api, status=status):
            return
        with lock:
            found[service.instance_name] = service

    zc = Zeroconf()
    try:
        listener = _BrowserDelegate(_on_update)
        zc.add_service_listener(SERVICE_TYPE, listener)  # type: ignore[attr-defined]
        time.sleep(max(0.5, timeout_s))
    finally:
        try:
            zc.close()
        except Exception:
            pass
    return sorted(found.values(), key=lambda s: s.name)


def discover_one(
    timeout_s: float = 3.0,
    *,
    vendor: str | None = None,
    api: str | None = None,
    status: str | None = "up",
) -> DiscoveredService | None:
    """Return the first matching service, or ``None``."""
    results = discover(timeout_s, vendor=vendor, api=api, status=status)
    return results[0] if results else None


# ---- continuous, self-healing watcher ---------------------------------

_MAINTENANCE_INTERVAL_S = 60.0


class DiscoveryWatcher:
    """Continuous LAN watcher: add/update/remove + staleness + self-healing.

    The client-side primitive for long-running integrations (agents, model
    pickers, tooling).  Guarantees, beyond the one-shot :func:`discover`:

    * every add / update / removal of any service triggers ``on_change``
      exactly once with ``(instance_name, service_or_None)``;
    * entries silent for ``stale_after_s`` are re-reported with
      ``status="down"`` (mDNS goodbyes are lost on abrupt power-off);
    * a dead zeroconf browser is recreated automatically with backoff.

    Example::

        watcher = DiscoveryWatcher(
            lambda name, svc: print("online" if svc else "gone", name)
        )
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        on_change: Callable[[str, DiscoveredService | None], None],
        *,
        stale_after_s: float = 900.0,
        restart_backoff_s: float = 5.0,
        browser_factory: Callable[
            [Callable[[DiscoveredService], None], Callable[[str], None]], object
        ]
        | None = None,
    ) -> None:
        self._on_change = on_change
        self._stale_after_s = max(30.0, stale_after_s)
        self._restart_backoff_s = max(1.0, restart_backoff_s)
        self._factory = browser_factory or self._default_factory
        self._services: dict[str, DiscoveredService] = {}
        self._browser: object | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
        self._restart_browser()
        self._thread = threading.Thread(
            target=self._maintenance_loop, name="local-ai-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop.set()
            browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("watcher browser stop failed", exc_info=True)

    # ---- reads ------------------------------------------------------------

    def snapshot(self) -> list[DiscoveredService]:
        """All currently known services (including stale-down ones)."""
        with self._lock:
            return sorted(self._services.values(), key=lambda s: s.name)

    def get(self, instance_name: str) -> DiscoveredService | None:
        with self._lock:
            return self._services.get(instance_name)

    # ---- internals ----------------------------------------------------------

    @staticmethod
    def _default_factory(on_update, on_remove) -> ServiceBrowser:
        return ServiceBrowser(on_update, on_remove=on_remove)

    def _restart_browser(self) -> None:
        with self._lock:
            browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("old browser stop failed", exc_info=True)
        try:
            self._browser = self._factory(self._ingest, self._forget)
            logger.info("discovery browser (re)started")
        except Exception as exc:
            logger.warning("discovery browser unavailable: %s (will retry)", exc)

    def _ingest(self, service: DiscoveredService) -> None:
        with self._lock:
            self._services[service.instance_name] = service
        self._emit(service.instance_name, service)

    def _forget(self, instance_name: str) -> None:
        with self._lock:
            self._services.pop(instance_name, None)
        self._emit(instance_name, None)

    def _emit(self, instance_name: str, service: DiscoveredService | None) -> None:
        try:
            self._on_change(instance_name, service)
        except Exception:
            logger.exception("watcher on_change callback failed")

    def _maintenance_loop(self) -> None:
        while not self._stop.wait(_MAINTENANCE_INTERVAL_S):
            with self._lock:
                needs_restart = self._browser is None
            if needs_restart:
                self._restart_browser()
            self._mark_stale()

    def _mark_stale(self) -> None:
        now = time.time()
        with self._lock:
            stale = [
                s
                for s in self._services.values()
                if s.is_stale(self._stale_after_s, now=now)
                and s.status != ServiceStatus.DOWN.value
            ]
        for old in stale:
            fresh = dataclass_replace(
                old, status=ServiceStatus.DOWN.value, discovered_at=now
            )
            with self._lock:
                self._services[old.instance_name] = fresh
            logger.debug("marking stale service down: %s", old.name)
            self._emit(old.instance_name, fresh)
