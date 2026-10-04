<div align="center">

# Local AI Discovery

**Every AI service on your LAN, remembered by the network itself**

A highly-redundant, highly-available, vendor-agnostic auto-discovery module
for local AI endpoints — built on standard mDNS / DNS-SD

[![CI](https://github.com/huanyan99/local-ai-discovery/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-_local--ai._tcp-v1)](docs/PROTOCOL.md)
[![IPv6](https://img.shields.io/badge/IPv6-supported-8a2be2)](docs/PROTOCOL.md)

English · [简体中文](README.zh-CN.md)

[Highlights](#-highlights) · [Architecture](#-architecture--design) · [Quick Start](#-quick-start) · [Protocol Spec](docs/PROTOCOL.md) · [Integration](#-integrate-into-your-tool)

</div>

## 📖 About

Anyone running LLMs locally hits the same problem: **the model server is up,
but no other device knows where it is.**

Hard-coded IPs break on DHCP renewal. A central registry is one more thing to
deploy. Manually configuring every client does not scale.

`local-ai-discovery` solves this with the standard answer networks have had
for two decades: **mDNS / DNS-SD**. The provider broadcasts "who I am, where
I listen, which protocol I speak, whether I'm healthy" onto the LAN
(`_local-ai._tcp.local.`); any device on the same network — laptop, phone,
agent tooling — discovers it with zero configuration. The same way a printer
shows up in your print dialog the moment you plug it in.

This repository is **pure infrastructure**: discovery only. No web portal,
no inference proxy, no key management.

## ✨ Highlights

- **📜 The protocol is the spec** — TXT record schema, API dialects, state
  machine, and versioning policy all live in
  [docs/PROTOCOL.md](docs/PROTOCOL.md), guarded by the machine-readable
  conformance suite
  [`tests/test_protocol_conformance.py`](tests/test_protocol_conformance.py):
  changing any assertion there is a protocol change. Implementations in any
  language can interoperate from the spec alone.
- **🛡️ Highly redundant** — multiple endpoints per host, multi-host
  coexistence, automatic name-conflict suffixing (`-2`/`-3`, matching the
  Avahi/Bonjour convention), no central registry (every announcing host is
  an independent source; no single point of failure).
- **🔁 Highly available** — automatic re-registration on network changes
  (Wi-Fi roaming / DHCP renewal / VPN toggling), periodic refresh with
  jitter (no thundering herd), exponential-backoff retries, graceful
  goodbye on exit, client-side staleness aging and mDNS-stack self-healing.
- **🏷️ Vendor-agnostic** — you broadcast the model you deployed: GLM, MiMo,
  MiniMax, DeepSeek, Qwen, Kimi, Llama, GPT… mapped from model IDs by
  longest-prefix matching; unknown vendors normalize to `custom`.
- **🔀 Dual API dialects** — OpenAI-compatible and Anthropic (Claude)
  Messages, declared explicitly by the TXT `api` field; clients never guess.
- **🌐 IPv6 support** — dual-stack A/AAAA advertising; RFC 3986 bracketed
  URLs; address preference that shrugs off virtual adapters (WSL / VPN-TUN).
- **📦 Zero-dependency deployment** — pure Python (python-zeroconf; no
  system Bonjour/Avahi needed), Windows / macOS / Linux, two runtime deps.

## 🏗️ Architecture & Design

```text
┌────────────────────────────────────────────────────────────┐
│  Your app / agent / script                                 │
│    discover()  ·  DiscoveryWatcher  ·  ServiceAnnouncer    │
├────────────────────────────────────────────────────────────┤
│  app.py        config-driven orchestration (+ health link) │
├──────────────┬──────────────────┬──────────────────────────┤
│  announcer   │  browser         │  health                  │
│  HA announce │  discovery +     │  probing → status field  │
│              │  self-healing    │                          │
├──────────────┴──────────────────┴──────────────────────────┤
│  protocol.py   wire format (pure data, zero I/O) — single  │
│                authoritative definition                     │
├────────────────────────────────────────────────────────────┤
│  network.py    address snapshot / change monitoring         │
├────────────────────────────────────────────────────────────┤
│  zeroconf (pure-Python mDNS)       UDP 5353 multicast       │
└────────────────────────────────────────────────────────────┘
```

Every layer is importable on its own; `protocol.py` performs no I/O and is
the single authoritative definition of the wire format.

### High-availability design

| Layer | Mechanism | Failure scenario covered |
|---|---|---|
| Announcer | Re-register on network change | Wi-Fi roaming, DHCP renewal, VPN toggle |
| Announcer | Periodic refresh (±10% jitter) | NAT/switch cache aging; wake-up storms |
| Announcer | Exponential backoff (1s→2s→…→60s) | mDNS temporarily unavailable |
| Announcer | Name-conflict auto-suffix `-2`/`-3` | Two hosts announcing the same name |
| Health | Dedicated probe thread, status written into TXT | Model process dies / recovers |
| Client | 900s staleness aging marks `down` | Power loss loses the goodbye packet |
| Client | Automatic browser recreation | zeroconf internal thread crash |
| Client | Address preference (see below) | Virtual adapters / multi-homed hosts |

### Address selection & IPv6

Announcers register **all** local interface addresses (virtual adapters
included), so clients prefer by reachability:

```text
RFC1918 IPv4 (192.168/16 → 10/8 → 172.16/12)
  → Global IPv6 (2000::/3)
  → Unique-local IPv6 (fc00::/7)
  → remaining IPv4
  → SRV hostname
```

IPv6 addresses are bracketed per RFC 3986 in URLs:
`http://[2001:db8::5]:8000/v1`. Link-local addresses (`fe80::/10`) carry a
scope and cannot live stably in URLs — never registered, never relied upon.

## 🚀 Quick Start

### Install

```bash
pip install local-ai-discovery
```

### Announce (provider side, 30 seconds to onboard)

```bash
# Multiple endpoints: YAML config (template in config.example.yaml)
local-ai-discovery announce --config config.yaml

# Single endpoint: environment variables
LOCAL_AI_NAME="My-DeepSeek" LOCAL_AI_PORT=8000 LOCAL_AI_VENDOR=deepseek \
  local-ai-discovery announce
```

Then forget about it: network changes re-register automatically, name
conflicts resolve themselves, Ctrl-C unregisters gracefully.

### Browse (consumer side)

```bash
local-ai-discovery browse                          # human-readable
local-ai-discovery browse --json                   # script-friendly
local-ai-discovery browse --vendor deepseek --status up
```

The protocol is standard, so built-in OS tooling works too:

```bash
dns-sd -B _local-ai._tcp            # macOS
avahi-browse -rtd _local-ai._tcp    # Linux
```

### As a library

```python
from local_ai_discovery import (
    discover, discover_one, DiscoveryWatcher,
    ServiceAnnouncer, HealthChecker, DiscoveryService,
)

# One-shot discovery
svc = discover_one(vendor="zhipu")
print(svc.base_url, svc.models_list, svc.status)
# http://192.168.0.5:8000/v1  ('glm-5.3',)  up

# Long-running watch: add/update/remove events, staleness marking,
# mDNS-stack self-healing
watcher = DiscoveryWatcher(lambda name, svc: print("update:", name, svc))
watcher.start()

# Config-driven announce + health linkage (network-change resilient)
service = DiscoveryService.from_config_file("config.yaml")
service.start()
```

## 🧩 Integrate into Your Tool

Adding "LAN models" to an agent tool, IDE plugin, or model picker takes
three steps:

1. Browse `_local-ai._tcp` with any language's mDNS library
   (Python: just depend on this package);
2. Parse the TXT per [docs/PROTOCOL.md](docs/PROTOCOL.md): `id` as the
   stable key, `api` to pick the dialect, `auth` to decide whether to ask
   for a key, `status` to gray out;
3. Map the result onto your tool's provider/model entries.

The conformance suite is your acceptance test — pass it and you
interoperate.

## 🗺️ Status & Roadmap

| | Item | Status |
|---|---|---|
| ✅ | Protocol v1 + conformance guard | Released |
| ✅ | HA announcer (self-heal / refresh / backoff / renaming) | Released |
| ✅ | Self-healing discovery client (events / staleness / rebuild) | Released |
| ✅ | IPv6 (AAAA advertising, bracketed URLs, address preference) | Released |
| 🔜 | Reference consumers (minimal Node.js / Rust clients) | Planned |
| 🔜 | 24h multi-service soak test report | Planned |
| 💬 | Protocol v2 (dual-announce transition) | Reserved |

## ⚙️ Development

```bash
pip install -e .[dev]
python -m unittest discover -s tests -v   # or pytest
```

CI runs the full matrix on Linux / macOS / Windows × Python 3.10–3.13 and
builds sdist + wheel.

PRs welcome: new vendor aliases, protocol reference implementations, and
real-world network issue reports are all high-value contributions.

## License

[MIT](LICENSE)
