# DreamJourney Live 长会话认证同步本地修复报告

日期：2026-09-18\
状态：**A_LOCAL_INCOMPLETE / DEVICE_NOT_RUN**

## 1. 范围和边界

- 按《2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案》继续当前脏工作树。
- 未 reset/clean，未覆盖其他任务修改；未部署、未安装或启动 iPhone、未访问生产数据、未处理历史任务、未 commit/push。
- 本报告只声明本地实际证据。编译通过、HTTP 200 或测试总数均不替代长会话闭环。

## 2. 基线与指纹

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- `OwnerTruthContracts.swift`：`9baf348d1e5ceadd51b4bbee1045670be931fc5bba04e6dfea6070deb427055f`
- `EchoViewController.swift`：`d390dee2431c8e989c04147a7c4065b0eda0684ce799f9fea0501d8e107e4250`
- `OwnerTruthContractsTests.swift`：`b790dc0d2b1f7d53a6d3c41427afe344641f4a3e634f063c0cd60f1d2628f5e4`
- 两端开工前已有大量未提交成果；本轮未把这些历史差异归为本轮新增。

## 3. 本轮确认并修复的问题

### 3.1 严格持久化绑定使用了写盘前时间戳

位置：`OwnerTruthInterviewLiveTurnOutboxStore.enqueue`、`upsertCanonicalTurn`。

原子 JSON 写盘使用 ISO-8601 编解码，磁盘中的 `capturedAt` 会规范化；方法却返回写盘前内存快照。随后严格比较内存命令与磁盘 envelope 时会触发 `invalidPersistenceEnvelope`，阻断确认水位推进。

修改：写入成功后从同一 envelope 重新读取并返回持久化快照。未删除或放宽 Date、hash、command、Binding 比较。

红证据：`evidence/development/live-auth-focused-green-01.xcresult` 中同一耐久性断言修前失败，失败层为 `invalidPersistenceEnvelope`。\
绿证据：

- `evidence/development/live-auth-durability-green-02.xcresult`：1/1 PASS。
- `evidence/development/live-auth-durability-ack-green-03.xcresult`：1/1 PASS；确认后 command/evidence/retry claim 均清空。

### 3.2 安全诊断补齐

位置：`EchoViewController.acknowledgePersistedTurn`。

新增 `turnAcknowledgementFailed`，仅记录白名单原因 `invalidPersistenceEnvelope/storageFailure/unknown`，不输出正文、token、原始 ID 或业务 hash。

### 3.3 组合测试装配校正

位置：`OwnerTruthContractsTests.testManagerEchoRealGateBackendExplicitDenyKeepsDiskTurnWithoutMessagePost`。

- FeatureGate 与协调器改用同一注入时钟。
- FeatureGate 账号代际改为生产式认证 `sessionId` 派生来源。
- 启动断言不再把协调器初始 `.live` 当作 start 已完成，而要求观察真实 start POST。

该测试仍为 FAIL，保留为当前待修业务反例，不降低断言。

## 4. 已通过证据

### 4.1 当前源码定向场景

`evidence/post-fix/live-auth-focused-green-02.xcresult`：7/7 PASS，覆盖：

- append 认证证据和重试预算跨 Store 重建；
- retry-exposed 重建后只读、零第三次 append；
- 逻辑 20 分钟精确水位；
- 逻辑 20 分钟一次 pre-handler 认证恢复；
- start 前认证刷新及发送时钟复核；
- Live GET successor 与 fresh headers。

70/48 曾在同一修改阶段独立通过两次：

- `evidence/development/live-70-48-diagnostic-15.xcresult`
- `evidence/development/live-70-48-green-16.xcresult`

但最终源码串行重跑失败，因此 DEV-06 最终状态仍按 FAIL 计。

### 4.2 后端认证边界

命令：

```text
.venv/bin/python -m unittest tests.test_owner_truth_interview_input_api.OwnerTruthInterviewInputAPITests.test_expired_bearer_rejection_happens_before_live_append_is_applied
```

结果：1/1 PASS。过期 bearer 在 Live append 业务入口前拒绝，未证明全部 PostgreSQL 门禁。

### 4.3 构建

- 模拟器目标：PASS，`evidence/build/simulator-build.xcresult`
- 通用 iOS 无签名目标：PASS，`evidence/build/generic-ios-build.xcresult`

## 5. 当前失败和阻断

### 5.1 70/48 最终源码重跑失败

证据：`evidence/development/live-70-48-serial-24.xcresult`。

实际结果：仅 49 次 append 请求，确认水位停在 48，队列 22；关闭后从 `saving` 进入 `syncPaused` 再到 `unavailable`，未产生 end/ACK/admit/status 完整闭环。最终 9 个断言失败。不得沿用较早绿色结果宣布 DEV-06 通过。

### 5.2 明确策略 deny 组合反例仍失败

证据：`evidence/development/explicit-deny-ready-green-23.xcresult`。

测试未观察到真实 start POST，随后本地正文进入 `syncPaused`，策略刷新计数未达到 1。当前尚未证明这是生产启动调度缺陷还是 Controller 测试装配仍缺少登录主体隔离；保持 FAIL，下一步需从 `ensureNaturalInputSession → withLiveRequestAuthority` 的阶段诊断继续定位。

### 5.3 OwnerTruth 完整回归失败

证据：`evidence/regression/ownertruth-full.xcresult`。

结果：517 项执行，29 个 assertion failure，涉及 9 个方法，包括 Live delivery failure/status timeout/partial/read-only status、Manager 70/48、明确 deny，以及若干旧 Interview use case。未逐项关闭前 DEV-09/DEV-10 不通过。

### 5.4 非业务工具链失败

- `clock-aligned-protection-green-17.xcresult`：`OS:latest` 解析到 27.0，但 `iPhone 17 Pro` 仅有 26.5，未启动测试。
- `explicit-deny-clock-green-19.xcresult`：两个 xcodebuild 并发抢占同一模拟器，测试进程在 bootstrap 前被 kill。

以上不计业务红测，也不计通过。

## 6. DEV-01 至 DEV-10

| ID | 状态 | 结论 |
|---|---|---|
| DEV-01 | PASS | 两个独立逻辑 20 分钟场景在最终聚焦结果包中通过。 |
| DEV-02 | FAIL | start 前刷新和一次认证恢复通过；并发/CAS/超时完整矩阵未全部单列。 |
| DEV-03 | FAIL | 两个 GET successor/fresh headers 通过；真实 auth refresh 与完整负例仍不足。 |
| DEV-04 | FAIL | 后端 pre-handler 零业务应用用例通过；客户端全部形状/绑定负例未完整映射。 |
| DEV-05 | PASS | 原 append、证据、预算跨磁盘重建和确认清理通过。 |
| DEV-06 | FAIL | 最终源码 70/48 串行重跑未排空 22 段，关闭链未完成。 |
| DEV-07 | FAIL | retry-exposed 重建零第三次 POST 通过；真实两次 HTTP 后崩溃组合尚不足。 |
| DEV-08 | FAIL | 聚焦持续采集用例通过；最终 70/48 停止闭环失败。 |
| DEV-09 | FAIL | OwnerTruth 完整回归存在 29 个 assertion failure；音频/Echo 全量未形成最终绿包。 |
| DEV-10 | FAIL | 两类构建 PASS，但完整回归和 diff 门禁未满足。 |

## 7. 部署与设备判断

- 本轮没有确认需要新增后端业务合同或迁移，不部署。
- **当前不可进入真机验收。** 状态保持 `A_LOCAL_INCOMPLETE / DEVICE_NOT_RUN`。
- 真实 iPhone、真实 SDK 顺序、物理 20 分钟：NOT_RUN。

## 8. 下一步最小工作

1. 为 start/current-session 授权阶段补充安全阶段诊断，确认 explicit-deny 场景为何未到达刷新闭包。
2. 在 70/48 中锁定第 49 段从 `authenticationRejected → status read → retry claim → fresh auth/policy → append` 的首个断点。
3. 修复后用相同断言重跑 explicit deny、70/48、7 项聚焦及 OwnerTruth 全量。
4. 再运行 Echo/音频保持性回归和两类构建；全部通过后才能改为 `LOCAL_PASS / DEVICE_PENDING`。

## 9. 局部回退

- 可按代码块回退 Store “写后重读返回持久化快照”及对应测试/诊断，不触碰账号隔离、未知写只读规则和历史 B8 成果。
- 不建议回退为宽松 Date/hash 比较；那会掩盖 command 与磁盘 Binding 不一致。
