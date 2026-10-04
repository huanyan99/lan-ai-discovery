"""Standard DNS-SD protocol definition for local AI discovery.

Service type: ``_local-ai._tcp.local.``

Supports two API dialects:

* **openai** — OpenAI-compatible (``/v1/chat/completions``, ``Authorization: Bearer``)
* **anthropic** — Claude / Anthropic Messages (``/v1/messages``, ``x-api-key``)

TXT record schema (all values are UTF-8, ≤ 255 bytes per ``key=value``):

===========  ========  =========================================================
Key          Required  Meaning
===========  ========  =========================================================
v            yes       Protocol version, currently ``1``
api          yes       API dialect: ``openai`` | ``anthropic`` | ``custom``
auth         yes       Auth mode: ``none`` | ``api-key``
base         yes       Base path of the API root, e.g. ``/v1``
models       yes       Models list path, e.g. ``/v1/models``
id           yes       Stable instance UUID (survives renames)
status       yes       Health: ``up`` | ``degraded`` | ``down``
vendor       no        Vendor hint (see :data:`KNOWN_VENDORS`)
label        no        Human-readable display name
models_list  no        Comma-separated model IDs (optional shortcut)
===========  ========  =========================================================

Derived from ``api`` (not stored in TXT, computed client-side):

===========  ==============  ===========================================
api          chat_path       auth_header
===========  ==============  ===========================================
openai       /v1/chat/completions  Authorization: Bearer <key>
anthropic    /v1/messages    x-api-key: <key>  +  anthropic-version
custom       /v1/chat/completions  Authorization: Bearer <key>
===========  ==============  ===========================================

SRV record provides the hostname and port.  Clients combine
``http(s)://<host>:<port><base>`` to obtain the API root URL.
"""

from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

SERVICE_TYPE = "_local-ai._tcp.local."
PROTOCOL_VERSION = "1"

_TXT_MAX_BYTES = 255
_NAME_MAX_BYTES = 63


class AuthMode(str, enum.Enum):
    NONE = "none"
    API_KEY = "api-key"


class ServiceStatus(str, enum.Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class ApiDialect(str, enum.Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


# Auth header style per API dialect.
#   openai / custom  →  Authorization: Bearer <key>
#   anthropic        →  x-api-key: <key>  (+ anthropic-version header)
AUTH_HEADER_STYLE: dict[str, str] = {
    ApiDialect.OPENAI.value: "bearer",
    ApiDialect.ANTHROPIC.value: "x-api-key",
    ApiDialect.CUSTOM.value: "bearer",
}

# Chat completion path relative to base_path.
CHAT_PATH: dict[str, str] = {
    ApiDialect.OPENAI.value: "/chat/completions",
    ApiDialect.ANTHROPIC.value: "/messages",
    ApiDialect.CUSTOM.value: "/chat/completions",
}

# Required extra headers per dialect (besides auth).
EXTRA_HEADERS: dict[str, dict[str, str]] = {
    ApiDialect.ANTHROPIC.value: {"anthropic-version": "2023-06-01"},
}

DEFAULT_MODELS_PATH: dict[str, str] = {
    ApiDialect.OPENAI.value: "/models",
    ApiDialect.ANTHROPIC.value: "/models",
    ApiDialect.CUSTOM.value: "/models",
}


def chat_path_for(api: str, base_path: str = "/v1") -> str:
    """Return the full chat/completion path for *api*."""
    suffix = CHAT_PATH.get(api, CHAT_PATH[ApiDialect.CUSTOM.value])
    return base_path.rstrip("/") + suffix


def auth_header_for(api: str, key: str) -> dict[str, str]:
    """Return the auth header dict for *api* with *key*."""
    style = AUTH_HEADER_STYLE.get(api, "bearer")
    if style == "x-api-key":
        headers = {"x-api-key": key}
        headers.update(EXTRA_HEADERS.get(api, {}))
        return headers
    return {"Authorization": f"Bearer {key}"}


def extra_headers_for(api: str) -> dict[str, str]:
    """Return non-auth extra headers required by *api*."""
    return dict(EXTRA_HEADERS.get(api, {}))


# Known vendors across all supported dialects.
# Top-10 mainstream providers + common local inference servers.
# Aligned with Artificial Analysis Intelligence Index (latest).
KNOWN_VENDORS: frozenset[str] = frozenset(
    {
        # --- Top-10 mainstream ---
        "anthropic",    # Claude Opus / Sonnet / Fable
        "openai",       # GPT-6 Astra / Sol / Luna
        "google",       # Gemini 4 Argon / 3.8 Flash
        "deepseek",     # DeepSeek-V4
        "meta",         # Muse Spark / Glimmer
        "mistral",      # Mistral Medium 3.5
        "qwen",         # Qwen3.8
        "zhipu",        # GLM-5.3
        "moonshot",     # Kimi K3 / K2 Horizon
        "minimax",      # MiniMax-M3
        # --- Strong runners-up ---
        "xiaomi",       # MiMo-V2.6
        "xai",          # Grok 4.7
        "step",         # Step 5
        "nvidia",       # Nemotron 3
        "cohere",       # Command
        "ai21",         # Jamba
        "perplexity",   # Sonar
        # --- Local inference servers ---
        "ollama",
        "vllm",
        "llama-cpp",
        "text-generation-inference",
        "litellm",
        "one-api",
        "custom",
    }
)

# Map free-form vendor / model strings to canonical hints.
_VENDOR_ALIASES: dict[str, str] = {
    # Anthropic / Claude (ranked #1)
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude-opus": "anthropic",
    "claude-sonnet": "anthropic",
    "claude-fable": "anthropic",
    "claude-3": "anthropic",
    "claude-3-5": "anthropic",
    "claude-4": "anthropic",
    # OpenAI (ranked #2)
    "openai": "openai",
    "gpt": "openai",
    "gpt-6": "openai",
    "gpt-6-astra": "openai",
    "gpt-6-sol": "openai",
    "gpt-6-luna": "openai",
    "gpt-4o": "openai",
    "chatgpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    # Google (ranked #3)
    "google": "google",
    "gemini": "google",
    "gemini-4": "google",
    "gemini-4-argon": "google",
    "gemini-3-8": "google",
    "gemini-flash": "google",
    "palm": "google",
    # DeepSeek (ranked #4)
    "deepseek": "deepseek",
    "deepseek-v4": "deepseek",
    "deepseek-chat": "deepseek",
    "deepseek-coder": "deepseek",
    "deepseek-r1": "deepseek",
    # Meta (ranked #5)
    "meta": "meta",
    "muse": "meta",
    "muse-spark": "meta",
    "muse-glimmer": "meta",
    "llama": "meta",
    "llama-3": "meta",
    "llama-4": "meta",
    "llama-cpp": "llama-cpp",
    "llama.cpp": "llama-cpp",
    # Mistral (ranked #6)
    "mistral": "mistral",
    "mistral-medium": "mistral",
    "mixtral": "mistral",
    "codestral": "mistral",
    "ministral": "mistral",
    # Qwen (ranked #7)
    "qwen": "qwen",
    "qwen3": "qwen",
    "qwen3-8": "qwen",
    "qwen2-5": "qwen",
    "tongyi": "qwen",
    "alibaba": "qwen",
    "dashscope": "qwen",
    # Zhipu / GLM (ranked #8)
    "zhipu": "zhipu",
    "glm": "zhipu",
    "glm-5": "zhipu",
    "glm-5-3": "zhipu",
    "chatglm": "zhipu",
    # Moonshot / Kimi (ranked #9)
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "kimi-k3": "moonshot",
    "k2-horizon": "moonshot",
    "k2": "moonshot",
    # MiniMax (ranked #10)
    "minimax": "minimax",
    "minimax-m3": "minimax",
    "abab": "minimax",
    # Xiaomi / MiMo
    "xiaomi": "xiaomi",
    "mimo": "xiaomi",
    "mimo-v2": "xiaomi",
    # xAI / Grok
    "xai": "xai",
    "grok": "xai",
    "grok-4": "xai",
    "grok-4-7": "xai",
    # Step (阶跃星辰)
    "step": "step",
    "step-5": "step",
    "stepfun": "step",
    # NVIDIA
    "nvidia": "nvidia",
    "nemotron": "nvidia",
    "nemotron-3": "nvidia",
    # Cohere
    "cohere": "cohere",
    "command": "cohere",
    "command-r": "cohere",
    # AI21
    "ai21": "ai21",
    "jamba": "ai21",
    # Perplexity
    "perplexity": "perplexity",
    "sonar": "perplexity",
    # Local servers
    "ollama": "ollama",
    "vllm": "vllm",
    "tgi": "text-generation-inference",
    "litellm": "litellm",
    "one-api": "one-api",
    "oneapi": "one-api",
}

# Map vendor → default API dialect.
VENDOR_DEFAULT_API: dict[str, str] = {
    "anthropic": ApiDialect.ANTHROPIC.value,
    "openai": ApiDialect.OPENAI.value,
}


def vendor_hint(raw: str | None) -> str:
    """Normalise a free-form vendor string to a known hint, else ``custom``."""
    if not raw:
        return "custom"
    key = raw.strip().lower()
    return _VENDOR_ALIASES.get(key, key if key in KNOWN_VENDORS else "custom")


def vendor_from_model(model_id: str | None) -> str:
    """Guess a vendor hint from a model id like ``qwen2.5-7b-instruct``.

    Longest alias prefix wins (``chatglm3`` → zhipu, not ``glm``);
    falls back to :func:`vendor_hint` for exact / unknown strings.
    """
    if not model_id:
        return "custom"
    key = model_id.strip().lower()
    best_alias, best_vendor = "", ""
    for alias, vendor in _VENDOR_ALIASES.items():
        if key.startswith(alias) and len(alias) > len(best_alias):
            best_alias, best_vendor = alias, vendor
    return best_vendor or vendor_hint(key)


def _check_txt_limit(key: str, value: str) -> None:
    encoded = f"{key}={value}".encode("utf-8")
    if len(encoded) > _TXT_MAX_BYTES:
        raise ValueError(
            f"TXT record {key}={value!r} is {len(encoded)} bytes (max {_TXT_MAX_BYTES})"
        )


@dataclass(frozen=True)
class ServiceRecord:
    """One advertised local AI endpoint."""

    name: str
    port: int
    api: str = ApiDialect.OPENAI.value
    auth: str = AuthMode.NONE.value
    base_path: str = "/v1"
    models_path: str = "/v1/models"
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = ServiceStatus.UP.value
    vendor: str = "custom"
    label: str | None = None
    models_list: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    # ---- validation -------------------------------------------------

    def validate(self) -> None:
        if not self.name or len(self.name.encode("utf-8")) > _NAME_MAX_BYTES:
            raise ValueError(
                f"service name must be 1–{_NAME_MAX_BYTES} UTF-8 bytes, got {self.name!r}"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port must be 1–65535, got {self.port}")
        valid_apis = {d.value for d in ApiDialect}
        if self.api not in valid_apis:
            raise ValueError(f"api must be one of {sorted(valid_apis)}, got {self.api!r}")
        if self.auth not in {m.value for m in AuthMode}:
            raise ValueError(f"auth must be one of {[m.value for m in AuthMode]}")
        if self.status not in {s.value for s in ServiceStatus}:
            raise ValueError(
                f"status must be one of {[s.value for s in ServiceStatus]}"
            )
        for path_name, path in (("base_path", self.base_path), ("models_path", self.models_path)):
            if not path.startswith("/"):
                raise ValueError(f"{path_name} must start with /, got {path!r}")
        try:
            uuid.UUID(self.instance_id)
        except ValueError as exc:
            raise ValueError(f"instance_id must be a UUID, got {self.instance_id!r}") from exc
        # Eagerly check TXT byte limits so invalid records fail at construction.
        self.txt_properties()

    # ---- TXT encoding ----------------------------------------------

    def txt_properties(self) -> dict[str, str]:
        """Build the TXT key/value map."""
        props: dict[str, str] = {
            "v": PROTOCOL_VERSION,
            "api": self.api,
            "auth": self.auth,
            "base": self.base_path,
            "models": self.models_path,
            "id": self.instance_id,
            "status": self.status,
            "vendor": vendor_hint(self.vendor),
        }
        if self.label:
            props["label"] = self.label
        if self.models_list:
            props["models_list"] = self.models_list
        for key, value in props.items():
            _check_txt_limit(key, value)
        return props

    @property
    def instance_name(self) -> str:
        return f"{self.name}.{SERVICE_TYPE}"

    def with_status(self, status: str | ServiceStatus) -> ServiceRecord:
        return ServiceRecord(
            name=self.name,
            port=self.port,
            api=self.api,
            auth=self.auth,
            base_path=self.base_path,
            models_path=self.models_path,
            instance_id=self.instance_id,
            status=status.value if isinstance(status, ServiceStatus) else status,
            vendor=self.vendor,
            label=self.label,
            models_list=self.models_list,
        )


# ---- free functions used by both announcer and browser ---------------


def encode_txt(properties: Mapping[str, str]) -> dict[str, str]:
    """Validate and return a TXT map ready for zeroconf."""
    out: dict[str, str] = {}
    for key, value in properties.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError(f"invalid TXT key: {key!r}")
        _check_txt_limit(key, value)
        out[key] = str(value)
    return out


def decode_txt(properties: Mapping[Any, Any] | None) -> dict[str, str]:
    """Normalise a zeroconf TXT dict (bytes or str keys/values) to str."""
    if not properties:
        return {}
    out: dict[str, str] = {}
    for key, value in properties.items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        if value is True or value is None:
            out[k] = ""
        elif isinstance(value, bytes):
            out[k] = value.decode("utf-8", "replace")
        else:
            out[k] = str(value)
    return out
