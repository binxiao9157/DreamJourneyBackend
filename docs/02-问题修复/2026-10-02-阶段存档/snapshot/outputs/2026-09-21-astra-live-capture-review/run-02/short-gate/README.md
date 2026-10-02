# Astra 独立本地短场门禁复跑

2026-09-21 当前工作区，状态：`LOCAL_SHORT_GATE_PASS / PROVIDER_NOT_RUN / DEVICE_NOT_RUN / LONG_NOT_RUN`。

## 实际执行结果

- 仅运行 2 个用户回合：“我小学参加过校园合唱演出。”和“补充：那次演出的测试代号是松塔七号。”；另有 2 个受控助手回合。
- 原生产 Manager、真实 Echo Controller / Capture Coordinator、磁盘 Outbox、FeatureGate 和 BackendClient；原 iOS URLSession 通过 localhost Uvicorn 双向接收实际后端响应。
- 新建隔离 PostgreSQL，迁移至 0122；原 Backend admission / Worker / proposal / confirmation / activation / memory read 路径实际执行。模型HTTP响应受控，不调用真实Provider。
- XCTest 1/1 PASS、0 skip、0 fail；客户端实际 `queued → pendingReview`，ownerTurnCount=2、persistedOwnerTurnCount=2、queuedTurnCount=0。
- 服务端 1 个 Source、4 个 conversationTurns；1 条待确认候选，原事实和第二轮补充共同位于候选 `content.event`，`sourceTurnIndices=[1,3]`，`sourceId/sourceRefs` 均指向本场 Source。未重复成2条。
- 该候选经本地真实审核和激活接口进入正式记忆；关闭并重建 PostgresStore 后回读仍为1条，原事实和补充均保留。Source正文重建回读相等。
- 只运行短场，未运行原脚本9轮场次，也未运行150轮长场。2次模型请求均为受控HTTP。
- 检查1613个既有产品源码指纹，复跑前后无变化。只改变临时测试副本和编译产物；原产品源码、原Sol证据未修改。
- 测试后服务器正常退出、退出码0；本次新建PG实例已正常停止。共享Postgres卷保留，未干扰其他任务。

## 证据

- `short-gate.xcresult` 与 `xcresult-summary.json`：独立XCTest执行结果。
- `short-gate-test.log`：Controller即时状态与断言。
- `server-evidence.json`、`server.log`：Worker、Source、候选、审核及正式记忆重建验证。
- `pending-short-candidate.json`：仅合成正文的实际候选响应；`pending-content-structured-check.json`：结构化正文二次核验。
- `short-only-test.diff`、`short-only-server.diff`、`swift-overlay.json`：短场隔离方式，原断言保留且增强正文检查。
- `harness-sha256.txt`、`product-before.sha256`、`product-unchanged.json`：测试与产品指纹。
- `commands.md`、build/pg日志：复现与环境停止记录。

## 边界

这是当前源码的本地受控模型、模拟器真实组件及数据库闭环通过，不是 iPhone、火山原生SDK完整分派、真实DeepSeek、物理20/65分钟或150轮同源链通过。今后产品源码发生变化，长场测试前必须重新完成同版本短场内容门禁。
