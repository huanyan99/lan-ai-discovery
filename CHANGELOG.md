# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning: SemVer.

## [1.0.0] — 2026-10-04

First open-source baseline: the discovery core, nothing else.

Scope: the `_local-ai._tcp.local.` wire protocol (v1) with its conformance
suite, the high-availability announcer, the self-healing discovery client,
health probing, and the `announce` / `browse` CLI. Deliberately excluded:
web portal, skill documents, agent-tool config generators, auto-detection of
running servers, install scripts — those are product layers, not
infrastructure, and live outside this repository.

### Added

- **IPv6 support**: dual-stack A/AAAA advertising, RFC 3986 bracketed URLs
  for IPv6 hosts in health probes and `DiscoveredService.base_url`, and an
  extended address preference (RFC1918 IPv4 → global IPv6 → ULA IPv6).
- **Protocol v1** (`docs/PROTOCOL.md` + `tests/test_protocol_conformance.py`):
  TXT schema and byte budgets, OpenAI / Anthropic dialects, status semantics,
  lifecycle and versioning policy, vendor-hint mapping from model ids
  (GLM / MiMo / MiniMax / DeepSeek / Qwen / Kimi / Llama / GPT …).
- **`ServiceAnnouncer`**: network-change re-registration, jittered periodic
  refresh, exponential-backoff retries, name-conflict auto-suffixing
  (`-2`, `-3`, …), graceful goodbye, runtime `add_records()`.
- **`DiscoveryWatcher`**: add/update/remove events, staleness marking for
  lost goodbyes (default 900s), automatic browser recreation;
  `DiscoveredService.best_address()` prefers consumer-LAN IPv4 over virtual
  adapters (WSL / VPN-TUN).
- **`HealthChecker`**: model-endpoint probing with `up` / `degraded` / `down`
  classification and jittered intervals.
- **CLI**: `announce` (config/env driven) and `browse` (human / JSON).
- CI: Linux / macOS / Windows × Python 3.10–3.13, sdist+wheel build.
