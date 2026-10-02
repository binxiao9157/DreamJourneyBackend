# DreamJourney 正式记忆读取发布策略过期失败：Astra 分析输入

日期：2026-09-14\
问题编号：`FM-POLICY-01`\
当前状态：`FAIL / ROOT_CAUSE_CONFIRMED / FIX_NOT_IMPLEMENTED`

## 1. 请 Astra 回答的问题

请基于本文证据和当前实际代码，分析并输出一份局部修复设计。目标是让正式记忆只读入口在发布策略缓存过期时，沿用已有账号租约、策略单飞、共享预算、整体期限和旧回调隔离，完成一次有界恢复后继续原 GET。

不要把本问题解释为正式记忆写入失败。本次更正写入、正式记忆 revision、投影、向量、文字问答和 Live 快照均已独立验证通过。

## 2. 现场环境

- iPhone 真机，生产配置，无 QA 旁路、合成候选注入或故障注入。
- iOS HEAD 基线：`11d0d0051b9be3cce57822dd059472d1e2536866`，存在已核验的未提交 B4 修改。
- 安装包可执行文件 SHA-256：`9c037cdd137fbdca0c59066b298e0928bf759388eb979a7c28101ddc5c46c344`。
- 生产 API 镜像：`dreamjourney-b4-correction:20260914-0055`，healthy，restart=0。
- 数据库 schema head：`0121`。
- 测试数据为用户主动创建并亲自审核的合成测试信息。

## 3. 前置成功事实

1. 用户将合成候选中的代号从“陈鑫”更正为“晨星”。
2. 更正预览 HTTP 200、typed decode 和 `correctionBinding` 校验均成功。
3. 用户在二次预览中亲自点击一次最终确认。
4. 候选列表从 40 条变为 39 条，原候选消失，无错误或结果未知。
5. 生产数据库只读核验：candidate 与 receipt 均为 `corrected`，更正值审计存在，正式记忆 revision `69 → 70`。
6. 新 MemoryVersion 为 current；memory projection revision 70、search document projection 和 embedding job 均为 `ready`。

因此，后续正式记忆列表失败不能归因于写入、投影或向量未完成。

## 4. 可复现步骤

1. 在合法登录态下完成一次候选审核，或保持 App 运行至发布策略缓存过期。
2. 不先进入候选列表刷新，直接进入 `记忆档案 → 记忆记录`。
3. 页面显示：`正式记忆读取失败，功能请求已被发布策略拦截`。
4. 返回 `待确认记忆`，点击刷新一次。
5. 候选读取自动执行策略恢复，随后正常显示 39 条候选。
6. 立即返回 `记忆记录`，正式记忆列表恢复可读；搜索“晨星”成功，内容、版本和时间正确。

## 5. 现场证据

### 5.1 正式记忆失败

- UI 结果：`formalMemoryReadBlockedByReleasePolicy`。
- 最后成功阶段：客户端 FeatureGate。
- 正式记忆 GET 是否创建：否。
- HTTP 状态：不存在。
- 页面能够结束加载并显示错误，没有伪造 HTTP 失败。

### 5.2 候选列表随后成功恢复

同一候选读取意图的脱敏诊断序列：

```text
inboxRefreshRequested attempt=1
policyRefreshStarted reason=expiredPolicyCache attempt=2
requestCreated attempt=2
transportCompleted httpStatus=200 attempt=2
policyRefreshCompleted attempt=2
candidateClientEntered attempt=2
taskResumed attempt=2
responseReceived httpStatus=200 attempt=2
inboxContractDecoded candidateCount=39 attempt=2
uiCommitted phase=ready candidateCount=39 attempt=2
```

这证明实际原因是 `expiredPolicyCache`，且账号、策略接口、网络、候选 GET 和 UIKit 提交在同一现场均可用。

## 6. 当前代码对比

### 6.1 已正确恢复的候选读取

文件：`DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`

- `fetchOwnerTruthCandidateInbox`，当前约 8418 行开始。
- 8436–8449：识别 `expiredPolicyCache`、`capturedPolicyExpired`、`policyVersionChanged`。
- 8439–8453：使用 `allowsPolicyRecapture`、`attemptState.claimPolicyRefresh()`、账号租约和同一读取上下文启动策略恢复。
- 8464–8476：策略成功后继续原候选 GET，关闭再次 recapture。
- 已有整体期限、读取预算、策略单飞、attempt 递增及迟到结果隔离。

### 6.2 失败的正式记忆读取

同文件：

- `fetchOwnerTruthFormalMemories`，当前约 8978 行开始。
- 8983–8994：同步取得 `requestFeatureDecision`；只要不允许就直接返回 `featurePolicyDenied`。
- 9009–9018：只有门禁已允许时才进入 `requestJSON`。
- 没有账号租约参数、读取意图上下文、整体 deadline、策略 recapture、单飞或 attempt 传递。

UIKit 文件：`DreamJourney/Sources/Modules/Archive/OwnerTruthFormalMemoryViewControllers.swift`

- `OwnerTruthFormalMemoryListViewController.load(reset:)`，当前约 690 行开始。
- 页面有 `requestGeneration` 和账号 UI 校验，但调用的是旧 `OwnerTruthFormalMemoryClient` 结果合同。
- 失败后只展示错误；没有可核验的 trace/attempt，也不能区分请求前策略拒绝与网络/HTTP/解码失败。

## 7. 已证实与待核查

### 已证实

- 触发原因是 `expiredPolicyCache`。
- 正式记忆列表在发送 GET 前被客户端拒绝。
- 候选列表已有可工作的有界策略恢复实现。
- 通过候选列表刷新策略后，正式记忆列表立即恢复。
- 正式记忆事实、投影和向量没有损坏。

### 需要 Astra 独立核查

- `fetchOwnerTruthFormalMemory`、memory profile、version history、source record 等其他只读 OwnerTruth API 是否存在同类旧门禁路径。
- 应抽取只读恢复协调器，还是只在 FormalMemory list/detail UseCase 增加局部上下文；需避免复制候选模块的私有类型形成第二套规则。
- 现有策略单飞能否安全服务多个只读调用者，以及取消某个等待者时如何避免误伤其他请求。
- 列表搜索、分页、刷新交错时如何保持同一意图预算和 request ownership。

## 8. 修复设计必须保持的边界

1. 只允许只读 GET 在可信账号作用域内进行一次有界恢复。
2. 不增加候选审核、更正、拒绝、正式记忆修订等写请求的自动重试。
3. 不放宽 FeatureGate、账号租约、CAS、Binding、hash、revision 或权限检查。
4. 一个读取意图使用同一 trace；每次实际尝试使用单调 attempt。
5. 区分 requestDenied、taskCreated、taskResumed、transportCompleted、HTTP response、typed decode 和 UI commit。
6. 整体期限覆盖策略刷新、认证恢复和正式记忆 GET；到期后只完成一次并释放占用。
7. 迟到响应不能覆盖新页面、新搜索、新账号或新 attempt，也不能释放新请求的所有权。
8. 前后台、重进页面和手动刷新必须有界，不产生并行读取风暴。
9. 不修改火山原生 Live、声音、持续聆听、打断、sessionSnapshot 或文字问答链路。
10. 日志只记录白名单阶段、原因、耗时、状态和随机 trace，不记录正文、转写、token、请求头、完整响应或原始业务 ID/hash。

## 9. 建议验收矩阵

| 用例 | 预期 |
|---|---|
| 缓存有效，正式记忆首次读取 | 单次 GET，UI 正常提交 |
| 首次进入即 `expiredPolicyCache` | 一次策略刷新后自动继续原 GET |
| 已有 successor 策略可采用 | 不重复刷新，继续原 GET |
| 策略刷新失败或超时 | UI 结束等待并显示准确错误，后续可重试 |
| 策略恢复后 GET 401 | 共享预算内按既有认证规则处理，不无限递归 |
| 策略恢复与认证恢复组合 | 同一 trace、单调 attempt、总预算有界 |
| GET 在整体期限内不返回 | 到期一次收尾，释放占用，迟到 200 被丢弃 |
| 过期后再次刷新 | 能发起新合法读取，旧响应不覆盖 |
| 搜索/分页/刷新交错 | 旧结果不覆盖新 query，单飞所有权正确 |
| 账号切换或租约失效 | 不向新主体显示旧结果，不自动查询旧主体 |
| 页面退出或前后台切换 | 不永久 loading，不误取消共享策略请求 |
| 正式记忆详情读取 | list/detail 使用一致安全恢复规则 |

测试需贯穿真实 UIKit Controller、FeatureGate evaluator、BackendClient、受控 URLProtocol 和隔离策略缓存；不能只测试 helper，也不能使用常开 QA gate。修复前后应保留同一业务断言的红绿证据。

## 10. 当前发布判断

- 本次 correctionBinding、更正写入、投影、向量、文字及 Live 回查均 PASS。
- `FM-POLICY-01` 尚未修改或部署，整体 B4 继续保持 FAIL。
- 不应通过延长 TTL、进入候选页预热、自动循环刷新或提前显示成功掩盖问题。
- 修复应先本地完成自动化、模拟器 UIKit/UIQA 和通用 iOS 设备目标编译；取得授权后再安装真机复测。

## 11. 关联证据

- 完整真机报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md`
- 失败状态：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/formal-memory-policy-expiry-failure.txt`
- 写入/投影/向量状态：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/correction-write-readonly-status.txt`
- 部署后状态：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/deployment-postcheck.txt`
