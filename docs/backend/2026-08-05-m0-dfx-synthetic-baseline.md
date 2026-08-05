# M0 DFX 合成基线（P0-S4）

## 目标与边界

本基线为 M0 的 Context、Projection 与 Candidate/媒体任务提供一份可重复的非真机
回归证据。它用于比较同一固定合成负载在不同后端提交上的合同结果；它不是生产压测，
不建立延迟 SLO，也不代表真实对象存储、Provider 或设备表现。

报告 schema 为 `m0-dfx-baseline-v1`。报告只包含提交号、环境机器码、固定 fixture 规模、
并发、时间窗口、聚合计数、延迟和机器原因码；不得包含 Source 正文、用户标识、对象 key、
URL、Provider log 或凭据。

## 固定 Probe

| Probe | 固定验证内容 |
| --- | --- |
| `contextPacket` | 双 Owner 的固定检索负载、Context 包大小、持续和突发请求延迟。 |
| `stage2MediaCandidateProjection` | 私有媒体处理、Candidate 确认、Projection 前阻断、Projection 后 Context、删除/重放/死信。 |
| `crossVaultRevocation` | 跨 Vault/Owner 拒绝、过期或脱敏对象过滤、撤权后的 fail-closed 行为。 |

媒体 Probe 在 Candidate 被确认后、Projection worker 重建前显式请求 Context。新确认的
媒体 Source 必须尚未进入 `selectedContext`；重建后才允许被引用。这是防止旧投影或异步
时序导致提前暴露的回归断言。

三个 Probe 都使用一次性 PostgreSQL 数据库并自行清理。它们保持独立库，以复用已有的
隔离 smoke；报告会把这一拓扑明确记录为
`independentDisposablePostgresProbes`，不会声称它们是同一个生产负载会话。

## 指标可用性

当前报告明确区分已测和未测：

| 指标 | 状态 | 说明 |
| --- | --- | --- |
| `latencyMs` | 已测 | 子 Probe 墙钟耗时及 Context 容量预检 P95。 |
| `failureDenominator` | 已测 | 每个 Probe 的样本、失败和成功计数。 |
| `contextBytes` | 已测 | 固定 Context 预检的 P95 包大小。 |
| `queueAgeMs` | 未测 | 一次性 fixture 不保留可靠入队时钟。 |
| `sqlStatementCount` | 未测 | 合成 Probe 未挂接 SQL 计数器。 |
| `processResource` | 未测 | 合成 Probe 未挂接 CPU/内存采样器。 |
| `projectionLagMs` | 未测 | 已验证 Projection 前阻断，但 worker 回执不提供可比较的时钟。 |

未测项必须持续显示为 `notMeasured`；禁止用零、空字符串或“通过”伪装为已测。任何阈值
仅是合同回归门，`latencySloEnforced=false`，不得据此对外作性能承诺。

## 运行方式

仅运行静态合同门：

```bash
./scripts/run-backend-m0-dfx-baseline-gate.sh
```

在具备创建临时 PostgreSQL 数据库权限的环境运行完整基线：

```bash
RUN_M0_DFX_BASELINE=1 \
M0_DFX_BUILD_ID="$(git rev-parse --short HEAD)" \
DATABASE_URL="$DATABASE_URL" \
./scripts/run-backend-m0-dfx-baseline-gate.sh
```

部署后，在 API 容器内运行：

```bash
RUN_M0_DFX_DEPLOYED_SMOKE=1 \
M0_DFX_BUILD_ID="<deployed-commit>" \
./scripts/run-backend-m0-dfx-baseline-deployed-smoke.sh
```

完整报告仅输出到调用方标准输出，便于 CI 或 QA 存档；运行器不会把子脚本的原始输出
写入报告，避免意外扩散测试数据。

## 未覆盖边界

该基线不替代以下独立验收：真实对象 ACL/删除回执、OCR/ASR/视觉解析质量、队列和容器
重启下的资源表现、生产 SQL 画像、外部 Provider 处理、真机媒体传输或设备性能。后续为
这些能力增加测量时，必须以新的 `metricAvailability` 状态和独立证据补充，而非重写本基线
的结论。
