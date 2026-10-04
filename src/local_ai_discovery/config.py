"""Configuration loading and validation for the discovery module."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .protocol import AuthMode, ServiceRecord, vendor_hint

__all__ = ["EndpointConfig", "AppConfig", "load_config"]


def _short_hostname() -> str:
    hostname = socket.gethostname().rstrip(".")
    if hostname.lower().endswith(".local"):
        hostname = hostname[:-6]
    return hostname.split(".", 1)[0] or "local-ai"


@dataclass(frozen=True)
class EndpointConfig:
    """One local AI endpoint to advertise."""

    name: str
    port: int
    api: str = "openai"
    auth: str = AuthMode.NONE.value
    base_path: str = "/v1"
    models_path: str = "/v1/models"
    vendor: str = "custom"
    label: str | None = None
    models_list: str | None = None

    def to_record(self, instance_id: str | None = None) -> ServiceRecord:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "port": self.port,
            "api": self.api,
            "auth": self.auth,
            "base_path": self.base_path,
            "models_path": self.models_path,
            "vendor": vendor_hint(self.vendor),
        }
        if instance_id is not None:
            kwargs["instance_id"] = instance_id
        if self.label is not None:
            kwargs["label"] = self.label
        if self.models_list is not None:
            kwargs["models_list"] = self.models_list
        return ServiceRecord(**kwargs)


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    endpoints: tuple[EndpointConfig, ...] = field(default_factory=tuple)
    health_interval_s: float = 30.0
    announce_refresh_s: float = 120.0
    network_poll_s: float = 10.0
    health_timeout_s: float = 3.0
    retry_max_backoff_s: float = 60.0


# ---- helpers ---------------------------------------------------------


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_str(value: Any, label: str, default: str | None = None) -> str:
    if value is None:
        if default is None:
            raise ValueError(f"{label} is required")
        return default
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    return text


def _require_int(value: Any, label: str, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not lo <= number <= hi:
        raise ValueError(f"{label} must be {lo}–{hi}, got {number}")
    return number


def _require_float(value: Any, label: str, default: float, lo: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if number < lo:
        raise ValueError(f"{label} must be ≥ {lo}, got {number}")
    return number


def _opt_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_endpoint(raw: Mapping[str, Any], index: int) -> EndpointConfig:
    api_section = _require_mapping(raw.get("api"), f"endpoints[{index}].api")
    service_section = _require_mapping(raw.get("service"), f"endpoints[{index}].service")

    name = _require_str(
        service_section.get("name"),
        f"endpoints[{index}].service.name",
        default=_short_hostname(),
    )
    port = _require_int(
        api_section.get("port"), f"endpoints[{index}].api.port", 11434, 1, 65535
    )
    api = _require_str(api_section.get("type", api_section.get("api")), f"endpoints[{index}].api.type", "openai")
    auth = _require_str(api_section.get("auth"), f"endpoints[{index}].api.auth", "none")
    base_path = _require_str(api_section.get("base_path"), f"endpoints[{index}].api.base_path", "/v1")
    models_path = _require_str(
        api_section.get("models_path"), f"endpoints[{index}].api.models_path", "/v1/models"
    )
    vendor = _require_str(api_section.get("vendor"), f"endpoints[{index}].api.vendor", "custom")
    label = _opt_str(service_section.get("label"), f"endpoints[{index}].service.label")
    models_list = _opt_str(api_section.get("models_list"), f"endpoints[{index}].api.models_list")

    return EndpointConfig(
        name=name,
        port=port,
        api=api,
        auth=auth,
        base_path=base_path,
        models_path=models_path,
        vendor=vendor,
        label=label,
        models_list=models_list,
    )


def _default_endpoint_from_env(env: Mapping[str, str]) -> EndpointConfig:
    """Single-endpoint mode driven by LOCAL_AI_* env vars (backwards compatible)."""
    return EndpointConfig(
        name=_require_str(env.get("LOCAL_AI_NAME"), "LOCAL_AI_NAME", _short_hostname()),
        port=_require_int(env.get("LOCAL_AI_PORT"), "LOCAL_AI_PORT", 11434, 1, 65535),
        api=_require_str(env.get("LOCAL_AI_API"), "LOCAL_AI_API", "openai"),
        auth=_require_str(env.get("LOCAL_AI_AUTH"), "LOCAL_AI_AUTH", "none"),
        base_path=_require_str(env.get("LOCAL_AI_BASE_PATH"), "LOCAL_AI_BASE_PATH", "/v1"),
        models_path=_require_str(
            env.get("LOCAL_AI_MODELS_PATH"), "LOCAL_AI_MODELS_PATH", "/v1/models"
        ),
        vendor=_require_str(env.get("LOCAL_AI_VENDOR"), "LOCAL_AI_VENDOR", "custom"),
        label=_opt_str(env.get("LOCAL_AI_LABEL"), "LOCAL_AI_LABEL"),
        models_list=_opt_str(env.get("LOCAL_AI_MODELS_LIST"), "LOCAL_AI_MODELS_LIST"),
    )


def load_config(
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load config from optional YAML file, then overlay env vars.

    Precedence (highest wins):
      1. ``LOCAL_AI_*`` environment variables (single-endpoint overrides)
      2. YAML file
      3. Built-in defaults
    """
    env = dict(os.environ if environ is None else environ)
    raw: Mapping[str, Any] = {}

    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"config file not found: {config_path}")
        try:
            import yaml  # lazy: only needed when a file is given
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required when --config is used") from exc
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw = _require_mapping(loaded, "config root")

    # --- top-level knobs ---
    discovery = _require_mapping(raw.get("discovery"), "discovery")
    app = AppConfig(
        health_interval_s=_require_float(
            discovery.get("health_interval_s"), "discovery.health_interval_s", 30.0, 1.0
        ),
        announce_refresh_s=_require_float(
            discovery.get("announce_refresh_s"),
            "discovery.announce_refresh_s",
            120.0,
            10.0,
        ),
        network_poll_s=_require_float(
            discovery.get("network_poll_s"), "discovery.network_poll_s", 10.0, 1.0
        ),
        health_timeout_s=_require_float(
            discovery.get("health_timeout_s"), "discovery.health_timeout_s", 3.0, 0.1
        ),
        retry_max_backoff_s=_require_float(
            discovery.get("retry_max_backoff_s"),
            "discovery.retry_max_backoff_s",
            60.0,
            1.0,
        ),
    )

    # --- endpoints ---
    endpoints_raw = raw.get("endpoints")
    endpoints: list[EndpointConfig] = []

    if endpoints_raw is not None:
        if isinstance(endpoints_raw, Sequence) and not isinstance(endpoints_raw, (str, bytes)):
            for index, item in enumerate(endpoints_raw):
                endpoints.append(_parse_endpoint(_require_mapping(item, f"endpoints[{index}]"), index))
        else:
            raise ValueError("endpoints must be a list")

    # Single-endpoint shorthand: top-level "service" + "api" (original schema).
    if not endpoints and ("service" in raw or "api" in raw):
        endpoints.append(_parse_endpoint(raw, 0))

    # Env-driven single endpoint when nothing else configured.
    if not endpoints:
        endpoints.append(_default_endpoint_from_env(env))

    # Env overlay for the first endpoint (backwards compatibility).
    first = endpoints[0]
    endpoints[0] = EndpointConfig(
        name=_require_str(env.get("LOCAL_AI_NAME"), "LOCAL_AI_NAME", first.name),
        port=_require_int(env.get("LOCAL_AI_PORT"), "LOCAL_AI_PORT", first.port, 1, 65535),
        api=_require_str(env.get("LOCAL_AI_API"), "LOCAL_AI_API", first.api),
        auth=_require_str(env.get("LOCAL_AI_AUTH"), "LOCAL_AI_AUTH", first.auth),
        base_path=_require_str(env.get("LOCAL_AI_BASE_PATH"), "LOCAL_AI_BASE_PATH", first.base_path),
        models_path=_require_str(
            env.get("LOCAL_AI_MODELS_PATH"), "LOCAL_AI_MODELS_PATH", first.models_path
        ),
        vendor=_require_str(env.get("LOCAL_AI_VENDOR"), "LOCAL_AI_VENDOR", first.vendor),
        label=_opt_str(env.get("LOCAL_AI_LABEL"), "LOCAL_AI_LABEL") or first.label,
        models_list=_opt_str(env.get("LOCAL_AI_MODELS_LIST"), "LOCAL_AI_MODELS_LIST")
        or first.models_list,
    )

    return AppConfig(
        endpoints=tuple(endpoints),
        health_interval_s=app.health_interval_s,
        announce_refresh_s=app.announce_refresh_s,
        network_poll_s=app.network_poll_s,
        health_timeout_s=app.health_timeout_s,
        retry_max_backoff_s=app.retry_max_backoff_s,
    )
