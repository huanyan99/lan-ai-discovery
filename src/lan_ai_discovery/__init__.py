"""mDNS / DNS-SD service discovery for local AI endpoints — core infrastructure.

Standard protocol: ``_local-ai._tcp.local.`` (see docs/PROTOCOL.md).
Supports OpenAI-compatible and Anthropic (Claude) API dialects.

Layers, each usable standalone:

* :mod:`lan_ai_discovery.protocol` — wire format (pure data, no I/O)
* :class:`lan_ai_discovery.ServiceAnnouncer` — high-availability broadcast
* :func:`lan_ai_discovery.discover` / :class:`DiscoveryWatcher` — clients
* :class:`lan_ai_discovery.HealthChecker` — keeps the ``status`` field honest
* :class:`lan_ai_discovery.DiscoveryService` — config-driven orchestrator
"""

from .protocol import (
    PROTOCOL_VERSION,
    SERVICE_TYPE,
    AuthMode,
    ServiceRecord,
    ServiceStatus,
    ApiDialect,
    AUTH_HEADER_STYLE,
    CHAT_PATH,
    EXTRA_HEADERS,
    auth_header_for,
    chat_path_for,
    extra_headers_for,
    vendor_hint,
    vendor_from_model,
    decode_txt,
    encode_txt,
)
from .config import EndpointConfig, load_config
from .announcer import ServiceAnnouncer
from .browser import (
    DiscoveredService,
    DiscoveryWatcher,
    ServiceBrowser,
    discover,
    discover_one,
)
from .health import HealthChecker, HealthResult, HealthStatus
from .app import DiscoveryService

__all__ = [
    "PROTOCOL_VERSION",
    "SERVICE_TYPE",
    "AuthMode",
    "ServiceRecord",
    "ServiceStatus",
    "ApiDialect",
    "AUTH_HEADER_STYLE",
    "CHAT_PATH",
    "EXTRA_HEADERS",
    "auth_header_for",
    "chat_path_for",
    "extra_headers_for",
    "vendor_hint",
    "vendor_from_model",
    "decode_txt",
    "encode_txt",
    "EndpointConfig",
    "load_config",
    "ServiceAnnouncer",
    "DiscoveredService",
    "DiscoveryWatcher",
    "ServiceBrowser",
    "discover",
    "discover_one",
    "HealthChecker",
    "HealthResult",
    "HealthStatus",
    "DiscoveryService",
]

__version__ = "1.0.0"
