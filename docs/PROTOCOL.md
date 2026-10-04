# LAN AI Discovery — 通讯协议规范 v1

> 服务类型：`_local-ai._tcp.local.` · 协议版本：`1`
>
> 本文档是本模块广播协议（wire protocol）的唯一权威定义。任何实现——
> 本包、第三方客户端、avahi/dns-sd 命令行——只要遵守本文即可互通。
> 修改本文中任何**必须（MUST）**条款都属于协议破坏性变更，需要升级 `v` 版本号。

## 1. 设计目标

- **零配置**：局域网内部署的 AI 推理服务（GLM、MiMo、MiniMax、DeepSeek、
  Qwen、Kimi、Llama、GPT 兼容层等）无需手工登记即可被任何同网设备发现。
- **厂商无关**：只约定"如何描述一个 API 端点"，不关心推理引擎是谁。
- **弱网高可用**：掉线、换网、异常断电都能自愈或被客户端正确识别。
- **数据不出内网**：协议只广播元数据；密钥永不入网。

## 2. 传输层

| 项目 | 值 |
|---|---|
| 发现机制 | Multicast DNS (mDNS) / DNS-SD，RFC 6762 / RFC 6763 |
| 服务类型 | `_local-ai._tcp.local.` |
| 传输 | UDP 5353（多播），由 mDNS 栈负责（本实现用 python-zeroconf，纯 Python，无需系统 Bonjour/Avahi） |
| SRV 记录 | 提供 `hostname` 与 `port` |
| TXT 记录 | 协议元数据，见下节 |

## 3. TXT 记录模式（v1）

所有值为 UTF-8，单条 `key=value` 不得超过 **255 字节**。

| Key | 必填 | 类型 | 约束 | 说明 |
|---|---|---|---|---|
| `v` | ✓ | 版本 | 当前恒为 `1` | 协议版本；实现 MUST 忽略不认识的更高版本，或按兼容规则降级处理 |
| `api` | ✓ | 枚举 | `openai` \| `anthropic` \| `custom` | API 方言（见 §5） |
| `auth` | ✓ | 枚举 | `none` \| `api-key` | 认证模式；**永不广播密钥本身** |
| `base` | ✓ | 路径 | 以 `/` 开头 | API 根路径，如 `/v1` |
| `models` | ✓ | 路径 | 以 `/` 开头 | 模型列表端点，如 `/v1/models` |
| `id` | ✓ | UUID | RFC 4122 格式 | 实例稳定标识；改名、换端口后 MUST 保持不变 |
| `status` | ✓ | 枚举 | `up` \| `degraded` \| `down` | 健康状态（见 §6） |
| `vendor` | ✗ | 提示 | ≤255 字节 | 厂商提示（见 §7），仅作展示/过滤，互通不依赖它 |
| `label` | ✗ | 文本 | ≤255 字节 | 人类可读名称，如「工位 Ollama」 |
| `models_list` | ✗ | 列表 | 逗号分隔，总长 ≤255 字节 | 模型 ID 快照（可选捷径；权威来源始终是 `models` 端点） |

实例名（SRV 的 Name 字段）≤ **63 字节**；与 LAN 上已有实例同名时，后注册方
MUST 依次改名为 `<name>-2`、`<name>-3`…（与 Avahi/Bonjour 惯例一致）。

## 4. URL 组装与地址选择

```
API 根   = http://<SRV 地址>:<SRV 端口><base>
模型列表 = http://<SRV 地址>:<SRV 端口><models>
```

- **IPv6**：地址进入 URL 时 MUST 使用 RFC 3986 方括号语法
  （`http://[2001:db8::5]:8000/v1`）。SRV/附加记录中的 AAAA（全局或 ULA）
  与 A 记录地位相同。
- **多地址择优**：广播方会注册本机全部接口地址（含虚拟网卡）。客户端 SHOULD
  按「RFC1918 IPv4 → 全局 IPv6 → ULA IPv6 → 其余」的顺序挑选尝试地址；
  link-local 地址（`169.254/16`、`fe80::/10`）因携带 scope 而无法稳定进入
  URL，广播方不注册、客户端不应依赖。
- 本实现的发现客户端内置上述择优（`DiscoveredService.best_address()`）。

## 5. API 方言

| | `openai` / `custom` | `anthropic` |
|---|---|---|
| Chat 端点 | `POST {base}/chat/completions` | `POST {base}/messages` |
| 认证头 | `Authorization: Bearer <key>` | `x-api-key: <key>` |
| 额外头 | — | `anthropic-version: 2023-06-01` |
| 模型列表 | `GET {models}` 返回 `{"data":[{"id":…}]}` | 同左 |

`custom` 按 OpenAI 兼容对待。客户端 MUST 依据 `api` 字段选择方言，
而不是从端口或 vendor 猜测。

## 6. 状态机

| 状态 | 语义 | 进入条件（本实现的判定） |
|---|---|---|
| `up` | 可正常服务 | 模型端点 HTTP 200 且可解析 |
| `degraded` | 存活但受限 | 端点 401/403（需要密钥），或响应不可解析 |
| `down` | 不可用 | 连接拒绝/超时；或客户端侧静默超时（goodbye 丢失兜底） |

服务端 MUST 在状态变化后的下一个广播周期内更新 TXT；客户端 MUST 对
静默条目做老化处理（本实现默认 900 秒无刷新视为 `down`）。

## 7. 厂商提示（vendor）

`vendor` 是展示性提示，由部署的**模型**决定（而非推理引擎）：
本地 vLLM 部署 `glm-5.3` 即为 `zhipu`，Ollama 跑 `deepseek-r1` 即为 `deepseek`。
规范化规则：小写、最长前缀匹配别名表。已知值包括：
`anthropic, openai, google, deepseek, meta, mistral, qwen, zhipu, moonshot,
minimax, xiaomi, xai, step, nvidia, cohere, ollama, vllm, llama-cpp,
text-generation-inference, litellm, one-api, custom`。
未知值 SHOULD 原样保留或归一为 `custom`，MUST NOT 因此拒绝服务。

## 8. 生命周期与高可用规则

服务端（ announcer ）：

1. **注册**：启动后注册全部端点；名字冲突按 §3 改名。
2. **刷新**：周期性重发通告（默认 120s ± 10% 抖动），防止缓存老化。
3. **网络自愈**：监听本机地址集合变化，变化即重注册（Wi-Fi 漫游、DHCP 续约、VPN）。
4. **失败重试**：注册失败按指数退避重试（1s → 2s → 4s → … → 60s 封顶）。
5. **优雅退出**：SIGINT/SIGTERM 时发送 goodbye 注销记录。

客户端（ browser / watcher ）：

1. **持续监听**：add / update / remove 三类事件都回调一次。
2. **老化**：`stale_after_s`（默认 900s）无刷新 → 标记 `down` 并回调。
3. **自愈**：mDNS 栈异常死亡后自动重建浏览（退避重试）。
4. **一次性发现**：简单场景可用限时 `discover()`，超时即返回。

## 9. 互通示例

```bash
# macOS
dns-sd -B _local-ai._tcp
# Linux (avahi)
avahi-browse -rtd _local-ai._tcp
# 本包
lan-ai-discovery browse --json
```

```python
from lan_ai_discovery import discover, discover_one

svc = discover_one(vendor="zhipu")     # 局域网里部署了 GLM 的那台机器
print(svc.base_url, svc.models_list)   # http://192.168.1.5:8000/v1  ('glm-5.3',)
```

## 10. 安全考量

- 密钥/令牌**永不**进入 TXT 或任何广播字段；`auth=api-key` 只声明"需要密钥"。
- 本协议把服务暴露给整个二层网络；部署方应依赖局域网边界与推理服务自身
  的访问控制。跨网段（如访客 Wi-Fi → 服务器网段）需路由器放行 mDNS 组播。
- 客户端 MUST 信任 `status`/`vendor` 前**先验证端点可用**（模型端点探活）。

## 11. 版本化与兼容承诺

- `v=1` 为当前版本。向后兼容的增量（新增可选 TXT 键、新增枚举值之外的
  信息）可以提升小版本号而无须改 `v`。
- 任何必填语义变化、字段删除/改名 → `v=2`，且 v2 实现应同时广播 v1 记录
  一个过渡期（双通告）。
- 本包中 `tests/test_protocol_conformance.py` 是本规范的机器可读守卫。
