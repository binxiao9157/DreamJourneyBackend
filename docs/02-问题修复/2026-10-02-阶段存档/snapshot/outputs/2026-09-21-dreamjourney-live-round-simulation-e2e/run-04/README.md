# DreamJourney Live 逐轮模拟与待确认记忆全链验收 run-04

状态：`LOCAL_PASS / DEVICE_NOT_RUN / REAL_PROVIDER_NOT_RUN / DEPLOYMENT_NOT_RUN`

本次在最终源码、构建和配置指纹下，实际按以下顺序完成：

1. `logical20-short`
2. 逻辑 20 分钟、110 个用户回合、110 个助手回合
3. `logical65-short`
4. 逻辑 65 分钟、150 个用户回合、150 个助手回合

两次 short 均经过真实 iOS Controller、Coordinator、磁盘、FeatureGate、BackendClient、HTTP、后端 Worker 和隔离 PostgreSQL，并完成客户端候选列表读回、隔离审核、正式记忆、Store 重建读取。每条 long 只接受同一最终源码/构建/配置生成的一次性 short receipt。

主要入口：

- [本地修复与验收报告](reports/2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告.md)
- [SIM/GATE/IDEMP 清单](checklists/SIM-GATE-IDEMP执行清单.md)
- [可复制运行命令](reports/运行命令.md)
- [源码与构建指纹](artifacts/source-build-fingerprints.json)
- [逻辑20分钟全链结果](green/logical20-full-chain.json)
- [逻辑65分钟全链结果](green/logical65-full-chain.json)
- [逻辑20分钟脱敏逐轮账本](artifacts/logical20-ledgers/logical20-turn-ledger.json)
- [逻辑65分钟脱敏逐轮账本](artifacts/logical65-ledgers/logical65-turn-ledger.json)

边界：未连接手机、未调用真实 Provider、未访问生产、未部署、未处理历史任务、未 commit/push。
