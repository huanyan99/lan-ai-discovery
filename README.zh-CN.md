<div align="center">

# Local AI Discovery

**让局域网里的每一个 AI 服务，都被网络自己记住**

基于 mDNS / DNS-SD 的局域网 AI 服务自动发现模块 —— 高冗余、高可用、厂商无关、双协议方言

[![CI](https://github.com/huanyan99/local-ai-discovery/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-_local--ai._tcp-v1)](docs/PROTOCOL.md)
[![IPv6](https://img.shields.io/badge/IPv6-supported-8a2be2)](docs/PROTOCOL.md)

[English](README.md) · 简体中文

[核心特性](#-核心特性) · [架构设计](#-架构设计) · [快速开始](#-快速开始) · [协议规范](docs/PROTOCOL.md) · [集成](#-集成到你的工具)

</div>

## 📖 关于

本地部署大模型的人都会遇到同一个问题：**模型服务起来了，但别的设备不知道它在哪。**

写死 IP 会在 DHCP 续约后失效，中心注册表需要额外部署，手动配置每个客户端更是不可维护。

`local-ai-discovery` 用网络里已经存在了二十年的标准答案解决这个问题：
**mDNS / DNS-SD**。服务端把「我是谁、地址在哪、说什么协议、是否在线」广播到
局域网（`_local-ai._tcp.local.`）；任何同网设备——电脑、手机、Agent 工具——
都能零配置发现它。就像打印机插上网线就出现在打印对话框里一样。

这个仓库是**纯粹的基础设施**：只负责发现，不含门户、不做推理代理、不管密钥。

## ✨ 核心特性

- **📜 协议即规范** — TXT 记录模式、API 方言、状态机、版本化承诺全部写进
  [docs/PROTOCOL.md](docs/PROTOCOL.md)，并由 [`tests/test_protocol_conformance.py`](tests/test_protocol_conformance.py)
  机器可读守卫：改动其中任何断言即构成协议变更。任何语言都能按规范独立实现。
- **🛡️ 高冗余** — 单机多端点、多机同网互见、名字冲突自动改名（`-2`/`-3`，
  与 Avahi/Bonjour 惯例一致）、无中心注册表（每台广播机都是独立信源，无单点）。
- **🔁 高可用** — 网络变化自动重注册（Wi-Fi 漫游 / DHCP 续约 / VPN 拔插）、
  周期刷新带抖动防雷群、指数退避重试、优雅退出 goodbye、客户端静默老化与
  mDNS 栈自愈。
- **🏷️ 厂商无关** — 广播什么模型就是什么厂商：GLM、MiMo、MiniMax、DeepSeek、
  Qwen、Kimi、Llama、GPT……由模型 ID 最长前缀自动映射，未知厂商归一为 `custom`。
- **🔀 双协议方言** — OpenAI-compatible 与 Anthropic (Claude) Messages 由
  TXT 的 `api` 字段显式声明，客户端不猜。
- **🌐 IPv6 支持** — A / AAAA 双栈通告；URL 自动使用 RFC 3986 方括号语法；
  地址择优覆盖虚拟网卡干扰（WSL / VPN-TUN）。
- **📦 零依赖部署** — 纯 Python（python-zeroconf，无需系统 Bonjour/Avahi），
  Windows / macOS / Linux 全平台，仅两个运行时依赖。

## 🏗️ 架构设计

```text
┌────────────────────────────────────────────────────────────┐
│  你的应用 / Agent / 脚本                                    │
│    discover()  ·  DiscoveryWatcher  ·  ServiceAnnouncer    │
├────────────────────────────────────────────────────────────┤
│  app.py        配置驱动编排（announce + health 联动）        │
├──────────────┬──────────────────┬──────────────────────────┤
│  announcer   │  browser         │  health                  │
│  高可用广播   │  发现客户端 + 自愈 │  探活 → status 字段       │
├──────────────┴──────────────────┴──────────────────────────┤
│  protocol.py   线缆格式（纯数据，零 I/O）—— 单一权威定义      │
├────────────────────────────────────────────────────────────┤
│  network.py    地址快照 / 变化监视                           │
├────────────────────────────────────────────────────────────┤
│  zeroconf（纯 Python mDNS）        UDP 5353 多播            │
└────────────────────────────────────────────────────────────┘
```

每一层都可以单独 import 使用；`protocol.py` 不做任何 I/O，是协议的唯一权威定义。

### 高可用设计

| 层 | 机制 | 对应失效场景 |
|---|---|---|
| 广播端 | 网络变化自动重注册 | Wi-Fi 漫游、DHCP 续约、VPN 拔插 |
| 广播端 | 周期刷新（±10% 抖动） | NAT/交换机缓存老化；避免唤醒风暴 |
| 广播端 | 指数退避重试（1s→2s→…→60s） | mDNS 服务暂时不可用 |
| 广播端 | 名字冲突自动后缀 `-2`/`-3` | 两台机器广播同名服务 |
| 探活端 | 独立健康线程，状态实时写入 TXT | 模型进程挂掉/恢复 |
| 客户端 | 静默 900s 老化标记 `down` | 异常断电丢失 goodbye 包 |
| 客户端 | mDNS 栈死亡自动重建 | zeroconf 内部线程异常 |
| 客户端 | 地址择优（见下） | 虚拟网卡/多宿主主机干扰 |

### 地址选择与 IPv6

广播方注册本机**全部**接口地址（含虚拟网卡），因此客户端按可达性择优：

```text
RFC1918 IPv4 (192.168/16 → 10/8 → 172.16/12)
  → 全局 IPv6 (2000::/3)
  → 唯一本地 IPv6 (fc00::/7)
  → 其余 IPv4
  → SRV 主机名
```

IPv6 地址进入 URL 时自动加 RFC 3986 方括号：`http://[2001:db8::5]:8000/v1`。
link-local 地址（`fe80::/10`）因 scope 无法稳定进入 URL，不注册、不依赖。

## 🚀 快速开始

### 安装

```bash
pip install local-ai-discovery
```

### 广播（服务提供方，30 秒接入）

```bash
# 多端点：YAML 配置（模板见 config.example.yaml）
local-ai-discovery announce --config config.yaml

# 单端点：环境变量
LOCAL_AI_NAME="My-DeepSeek" LOCAL_AI_PORT=8000 LOCAL_AI_VENDOR=deepseek \
  local-ai-discovery announce
```

之后不需要再管它：换网自动重注册，名字冲突自动改名，Ctrl-C 优雅注销。

### 发现（消费方）

```bash
local-ai-discovery browse                          # 人类可读
local-ai-discovery browse --json                   # 脚本友好
local-ai-discovery browse --vendor deepseek --status up
```

协议是标准的，系统自带工具同样有效：

```bash
dns-sd -B _local-ai._tcp            # macOS
avahi-browse -rtd _local-ai._tcp    # Linux
```

### 作为库嵌入

```python
from local_ai_discovery import (
    discover, discover_one, DiscoveryWatcher,
    ServiceAnnouncer, HealthChecker, DiscoveryService,
)

# 一次性发现
svc = discover_one(vendor="zhipu")
print(svc.base_url, svc.models_list, svc.status)
# http://192.168.0.5:8000/v1  ('glm-5.3',)  up

# 长期监听：add/update/remove 事件、掉线标记、mDNS 栈自愈
watcher = DiscoveryWatcher(lambda name, svc: print("update:", name, svc))
watcher.start()

# 配置驱动的广播 + 探活联动（网络变化自愈）
service = DiscoveryService.from_config_file("config.yaml")
service.start()
```

## 🧩 集成到你的工具

把「局域网模型」做进 Agent 工具、IDE 插件或模型选择器，只需要三步：

1. 用任意语言的 mDNS 库 browse `_local-ai._tcp`（Python 直接依赖本包）；
2. 按 [docs/PROTOCOL.md](docs/PROTOCOL.md) 解析 TXT：`id` 做稳定主键、
   `api` 选协议方言、`auth` 决定是否要密钥、`status` 做置灰；
3. 把结果映射为你工具里的 provider/model 条目。

协议一致性测试就是你的验收标准——通过它即互操作。

## 🗺️ 状态与路线图

| | 项 | 状态 |
|---|---|---|
| ✅ | 协议 v1 + 一致性测试守卫 | 已发布 |
| ✅ | 高可用广播端（自愈/刷新/退避/改名） | 已发布 |
| ✅ | 自愈发现客户端（事件流/老化/重建） | 已发布 |
| ✅ | IPv6（AAAA 通告、方括号 URL、择优） | 已发布 |
| 🔜 | 协议参考实现（Node.js / Rust 最小客户端） | 规划中 |
| 🔜 | 24h 多服务浸泡测试报告 | 规划中 |
| 💬 | 协议 v2（双通告过渡） | 预留，按需启动 |

## ⚙️ 开发

```bash
pip install -e .[dev]
python -m unittest discover -s tests -v   # 或 pytest
```

CI 在 Linux / macOS / Windows × Python 3.10–3.13 全矩阵运行，
并构建 sdist + wheel。

欢迎 PR：新厂商别名、协议参考实现、真实网络环境的问题报告，都是高价值贡献。

## License

[MIT](LICENSE)
