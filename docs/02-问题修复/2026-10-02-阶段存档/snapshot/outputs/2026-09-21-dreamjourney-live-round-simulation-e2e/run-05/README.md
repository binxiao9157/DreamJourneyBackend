# DreamJourney Live 逐轮模拟 run-05

状态：`LOCAL_PASS / DEVICE_NOT_RUN`

本运行仅收尾 run-04 独立复核中的 A-D 验收缺口，没有重做保存系统。最终源码按固定顺序执行：

1. logical20-short
2. logical20（逻辑 20 分钟，110 个用户回合）
3. logical65-short
4. logical65（逻辑 65 分钟，150 个用户回合）

四段均通过，顺序证据见 `artifacts/runner-complete.json`。

## 交付文件

- `reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md`
- `reports/A-D精确验收矩阵.md`
- `reports/运行命令.md`
- `reports/修前红测与修后结果.md`
- `artifacts/source-and-build-fingerprints.json`

## 核心原始证据

- `green/logical20-full-chain.json`
- `green/logical65-full-chain.json`
- `artifacts/logical20-ledgers/`
- `artifacts/logical65-ledgers/`
- `negative/logical20-gate-negatives.json`
- `negative/logical65-gate-negatives.json`
- `negative/logical20-harness-negatives.json`
- `negative/logical65-harness-negatives.json`
- `artifacts/source-build-manifest.json`
- `regression/ownertruth-full-final.xcresult`
- `regression/echo-audio-account-final.xcresult`
- `builds/round-simulation-build.xcresult`
- `builds/generic-ios/Build/Products/Debug-iphoneos/DreamJourney.app`

真实模型、部署、生产、历史数据、物理 iPhone 和物理 20/65 分钟均未执行。
