"""Health probing for local AI endpoints."""

from __future__ import annotations

import enum
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

__all__ = ["HealthStatus", "HealthResult", "HealthChecker"]


def _jittered(interval_s: float, fraction: float = 0.1) -> float:
    return max(1.0, interval_s * (1.0 + random.uniform(-fraction, fraction)))


class HealthStatus(str, enum.Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class HealthResult:
    status: HealthStatus
    latency_ms: float | None = None
    models: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is HealthStatus.UP


class HealthChecker:
    """Periodically probe ``GET <models_path>`` on a local endpoint.

    Classification:
      * HTTP 200 + parseable JSON list  → ``up``
      * HTTP 200 but unparseable body   → ``degraded``
      * Connection refused / timeout    → ``down``
      * HTTP 4xx/5xx                    → ``down`` (except 401/403 → ``degraded``:
        the server is alive but auth is required — still discoverable)
    """

    def __init__(
        self,
        *,
        port: int,
        models_path: str = "/v1/models",
        base_path: str = "/v1",
        host: str = "127.0.0.1",
        scheme: str = "http",
        timeout_s: float = 3.0,
        interval_s: float = 30.0,
        api: str = "openai",
        auth_key: str | None = None,
        auth_header: str | None = None,
        extra_headers: dict[str, str] | None = None,
        on_change: Callable[[HealthResult], None] | None = None,
    ) -> None:
        from .protocol import auth_header_for, extra_headers_for

        self._port = port
        self._models_path = models_path
        self._base_path = base_path
        self._host = host
        self._scheme = scheme
        self._timeout_s = max(0.1, timeout_s)
        self._interval_s = max(1.0, interval_s)
        self._api = api
        # Prefer explicit auth_header; else build from api dialect + auth_key.
        if auth_header is not None:
            self._headers = {"Authorization": auth_header}
        elif auth_key:
            self._headers = auth_header_for(api, auth_key)
        else:
            self._headers = {}
        self._headers.update(extra_headers or extra_headers_for(api))
        self._on_change = on_change

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._result = HealthResult(status=HealthStatus.DOWN, error="not-checked")

    # ---- accessors ---------------------------------------------------

    @property
    def result(self) -> HealthResult:
        with self._lock:
            return self._result

    @property
    def status(self) -> HealthStatus:
        return self.result.status

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"local-ai-health-{self._port}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def probe_once(self) -> HealthResult:
        result = self._probe()
        with self._lock:
            previous = self._result
            self._result = result
        if previous.status != result.status and self._on_change is not None:
            try:
                self._on_change(result)
            except Exception:
                logger.exception("health on_change callback failed")
        return result

    # ---- internals ---------------------------------------------------

    @property
    def _models_url(self) -> str:
        # IPv6 hosts need RFC 3986 bracket syntax in URLs.
        host = self._host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        root = f"{self._scheme}://{host}:{self._port}"
        base = "/" + self._base_path.strip("/")
        models = "/" + self._models_path.strip("/")
        # Absolute models path already containing the base ("/v1" + "/v1/models")
        # wins; otherwise it is relative to the base ("/v1" + "/models").
        if models == base or models.startswith(base + "/"):
            return root + models
        return root + base + models

    def _probe(self) -> HealthResult:
        url = self._models_url
        started = time.perf_counter()
        headers = {"Accept": "application/json", **self._headers}
        request = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                latency = (time.perf_counter() - started) * 1000
                body = response.read(64 * 1024)
                status_code = getattr(response, "status", 200)
                if status_code in (401, 403):
                    return HealthResult(
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        error=f"auth required (HTTP {status_code})",
                    )
                if status_code >= 400:
                    return HealthResult(
                        status=HealthStatus.DOWN,
                        latency_ms=latency,
                        error=f"HTTP {status_code}",
                    )
                models = _extract_models(body)
                if models is None:
                    return HealthResult(
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        error="unparseable models response",
                    )
                return HealthResult(
                    status=HealthStatus.UP, latency_ms=latency, models=models
                )
        except urllib.error.HTTPError as exc:
            latency = (time.perf_counter() - started) * 1000
            if exc.code in (401, 403):
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    error=f"auth required (HTTP {exc.code})",
                )
            return HealthResult(
                status=HealthStatus.DOWN, latency_ms=latency, error=f"HTTP {exc.code}"
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            latency = (time.perf_counter() - started) * 1000
            reason = getattr(exc, "reason", exc)
            return HealthResult(
                status=HealthStatus.DOWN, latency_ms=latency, error=str(reason)
            )

    def _run(self) -> None:
        # Immediate first probe so status is meaningful right after start().
        self.probe_once()
        while not self._stop.wait(_jittered(self._interval_s)):
            self.probe_once()


def _extract_models(body: bytes) -> tuple[str, ...] | None:
    """Parse an OpenAI-style ``/models`` response into a tuple of IDs."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            ids: list[str] = []
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"])
                elif isinstance(item, str):
                    ids.append(item)
            return tuple(ids)
        # Some servers return {"models": [...]}
        models = payload.get("models")
        if isinstance(models, list):
            return tuple(str(m) for m in models)
        return ()
    if isinstance(payload, list):
        return tuple(str(m) for m in payload)
    return None
