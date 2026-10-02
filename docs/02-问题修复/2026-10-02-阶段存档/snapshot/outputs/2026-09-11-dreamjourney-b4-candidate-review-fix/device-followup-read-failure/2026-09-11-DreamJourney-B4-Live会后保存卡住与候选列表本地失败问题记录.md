# DreamJourney B4 Live 会后保存卡住与候选列表本地失败问题记录

日期：2026-09-11\
用途：交给 Astra 独立复核根因并输出局部修复设计\
当前结论：本轮 Live 的正式记忆回答、声音、打断和后端候选生成已经成功；B4-8 因 iOS 保存状态卡住及候选列表未发出请求而失败，尚未审核本轮新候选。

## 1. 执行边界

1. 不修改、删除或替用户审核生产候选和正式记忆。
2. 不清理 App 数据，不重放历史 Dead Letter。
3. 保留火山原生 Live 的连续聆听、低延迟、声音和打断体验。
4. 文字问答继续由 DeepSeek 回复且不朗读。
5. 本记录只区分已证实事实、代码疑点和待验证假设，不把时间相关性当作根因。

## 2. 版本与环境

| 组件 | 当前信息 |
|---|---|
| iOS 分支 | `feature/prd-stitch-ui-adaptation` |
| iOS HEAD | `5fd061fd869edbe1fc13e8535a47880826581934`，另有大量未提交有效修改 |
| 后端分支 | `main` |
| 后端 HEAD | `be9670b6ec05e73ab9562943f402e5a9e1346988`，另有大量未提交有效修改 |
| 生产 API | `dreamjourney-live-b1:20260911-0200`，容器健康 |
| 真机 | iPhone 14 Pro Max，iOS 26.4.1，原位安装，未清除数据 |
| App 可执行文件 SHA-256 | `6607b9f47993557bf24f1cf6bbdb643fc374e74c487d5304a284b8c1829a7946` |

## 3. 本轮已通过的前置链路

用户在正式记忆中确认了合成测试事实“测试阅读计划代号是清河，蓝桥作废”，随后完成文字和 Live 回查：

1. 文字问答回答“清河，蓝桥已作废”，不朗读。
2. Live 第一次回答“清河”。
3. Live 第二次回答“蓝桥已经作废”。
4. 四次 Live 回答均有声音。
5. 插话后声音立即停止，随后自动恢复聆听。
6. 手动停止后退出 Live，回到麦克风页面。
7. 用户已经在正式记忆搜索和“第四章 经验、知识与理解”中看到该事实。

因此，本轮新问题不是 Live 正式记忆未采用，也不是音频或打断回归。

## 4. 缺陷一：手动停止后长期显示“正在保存本次对话”

### 4.1 用户现象

用户手动停止 Live 后，页面持续显示“正在保存本次对话”，长时间没有切换为“整理完成”“可审核”“无新增”或明确失败状态。

### 4.2 后端已证实事实

同一轮会话的后端流程实际上已经完成：

1. Live 会话成功结束。
2. review batch acknowledgement 成功。
3. candidate proposal admission 成功。
4. Candidate Extraction 第 1 次执行成功。
5. `candidateCount=3`。
6. `candidateOutcome=pendingReview`。
7. `extractionStatus=succeeded`。
8. `jobState=succeeded`，operation 已完成。

因此，“正在保存”不是后端仍在写作或整理，也不是 DeepSeek 仍在运行，而是 iOS 没有消费已经到达的终态。

### 4.3 已证实的 iOS 状态机问题

设备日志出现以下状态顺序：

```text
captureStateChanged from=live to=saving
captureStateChanged from=saving to=queued
captureStateChanged from=queued to=saving
```

随后不再进入终态。

对应代码：

- `EchoViewController.swift:808-817`：`clearCompletedOutboxThenBeginOrganization` 无条件先写入 `.saving`。
- `EchoViewController.swift:819-828`：`beginPendingMemoryOrganization` 在 `didBeginOrganization == true` 时直接返回。
- `EchoViewController.swift:944-949`：候选状态回调只在 `state.isAwaitingOrganizationStatus` 为真时继续处理。

当重复收到同一 ended receipt 时，代码先把已有 `.queued/.organizing` 回退为 `.saving`，随后因 `didBeginOrganization` 已为真而不再启动新流程；已有轮询回调又因 `.saving` 不属于等待整理状态而被丢弃。该路径可以解释“后端成功、页面永久保存中”。

### 4.4 修复必须保持的语义

1. 重复 ended receipt 必须幂等，不能让 UI 从 `.queued/.organizing/.retryWaiting` 回退到 `.saving`。
2. 第一次合法结束仍需进入持久化、acknowledgement、admission 和 status observation。
3. 不能提前显示成功；只有读取到 `reviewReady/noCandidates/terminalFailure/quarantined` 才进入对应终态。
4. 不能删除 durable outbox 或 follow-up 记录来掩盖状态问题。
5. App 被关闭或重开后仍应按同一 review batch 恢复观察。

## 5. 缺陷二：待确认记忆无法读取，刷新仍失败

### 5.1 用户现象

在后端已经生成 3 条新候选后，用户进入“记忆档案 → 待确认记忆”，页面显示：

> 暂时无法读取候选记忆，请点右上角重新载入。

用户点击右上角刷新后，页面仍保持该错误，候选列表没有显示。

### 5.2 已证实的后端事实

1. 生产 PostgreSQL 中该账号当前有 36 条 pending Candidate：
   - `owner-truth-v2`：1 条；
   - `owner-truth-v4`：26 条；
   - `owner-truth-v5`：9 条。
2. 此前页面曾成功加载 33 条候选；本次 Live 整理后总数增加为 36，增量正好为 3。
3. 生产 API 和 PostgreSQL 容器处于健康状态。
4. 此前 23:03 的 `GET /v2/vaults/{vault}/candidates` 返回 200。
5. 此前 23:06 的候选确认请求返回 201。
6. 本次出现错误并点击刷新后，生产 API 日志中没有新的 `GET /v2/vaults/{vault}/candidates`。

所以当前失败发生在 iOS 真正发出候选列表 HTTP 请求之前，不能归因于“候选尚未生成”、列表为空或该接口返回 500。

### 5.3 与认证刷新相关的已知时间线

生产 API 日志显示：

```text
23:18:07 GET  /v2/in-app-messages/{owner}?limit=50  -> 401
23:18:07 POST /auth/refresh                         -> 200
23:18:07 GET  /v2/in-app-messages/{owner}?limit=50  -> 200
23:18:08 GET  /v2/release-policy?...                -> 200
23:23:18 POST /auth/refresh                         -> 200
23:23:18 GET  /v2/in-app-messages/{owner}?limit=50  -> 200
23:23:18 GET  /v2/release-policy?...                -> 200
```

这证明访问令牌确实在相邻时间发生过刷新，且刷新接口成功。但目前没有足够证据证明 token 轮换就是候选列表失败根因，也不能排除 AccountLease、release policy 二次校验或本地 recovery gate 的影响。

### 5.4 当前代码中的失真点

候选页面在 `MemoryArchiveViewController.swift:6988-7004` 的 `viewDidLoad` 中直接触发 `.refresh`，右上角按钮也只是再次调用同一个 use case。

候选客户端在 `DreamJourneyBackendClient.swift:7946-7984` 中先检查发布策略，再进入通用 `requestJSON`。`requestJSON` 在真正创建网络请求前还有多处本地拒绝点：

- 私有 UI 不可进入；
- 当前认证会话不存在或不满足私有访问；
- session/user scope 不一致；
- BackendAccountLease 不允许；
- App AccountLease 捕获或校验失败；
- recovery runtime policy 拒绝；
- feature decision 二次校验拒绝。

位置：`DreamJourneyBackendClient.swift:13769-13942`。

但 `OwnerTruthCandidateReviewUseCase` 目前把绝大多数错误统一映射为 `.requestFailed`：

- `OwnerTruthContracts.swift:15923-15977`；
- `OwnerTruthContracts.swift:16168-16219`。

最终页面只显示同一句“暂时无法读取”，没有安全的失败阶段和错误分类，因此仅凭 UI 无法区分认证、租约、策略、恢复门禁、网络或解码失败。

### 5.5 待验证假设，不能直接当根因

1. 页面持有的不可变 AccountLease 在认证令牌轮换后变旧，刷新仍复用旧 use case。
2. route decision 在 request 前二次校验时因 account generation 或 policy authority 变化被拒绝。
3. recovery runtime policy 在本地拒绝候选路径。
4. 私有访问或认证会话在刷新竞态中短暂失效，候选请求在网络前返回。
5. 请求实际由网络层失败但未到达 API；当前证据不足以排除 DNS/TLS/连接错误。

“候选 JSON 解码失败”当前优先级较低，因为生产 API 没有收到本次 GET；但修复时仍应保留合同解码失败的 fail-closed 行为。

## 6. Astra 需要输出的修复设计

### 6.1 保存状态机

1. 给出重复 ended receipt 的精确状态转移表。
2. 明确首次结束、重复回执、恢复启动、轮询回调乱序和 App 前后台切换的幂等规则。
3. 先增加失败反例，再修复 `.queued → .saving` 回退和轮询停止。
4. 验证后端已经成功时，iOS 最终稳定显示“可在待确认记忆中审核”。

### 6.2 候选列表读取

1. 在不记录 token、正文、Prompt 或完整账号的前提下，增加 `stage/errorClass/backendCode/statusCode/leaseReason/policyReason` 等安全诊断。
2. 用真实客户端覆盖“请求前失败”和“请求后失败”，证明到底停在哪一层。
3. 设计用户显式刷新时对同主体、同 vault、合法新 AccountLease 的安全恢复方式；必须丢弃旧页面中的 proposal/binding 和提交状态，不能跨租约沿用可提交方案。
4. 账号真的切换时必须 fail closed，不能仅凭 subject 字符串相同就继续提交旧 proposal。
5. 认证刷新成功后应能重新获取候选，但不得无限重试、静默重新登录或绕过发布策略。
6. 错误 UI 应区分“账号状态变化，请重新进入”“权限暂不可用”“网络失败”“合同异常”，同时不暴露内部敏感信息。

## 7. 必须固定为回归测试的反例

1. 重复 ended receipt 到达时，已经处于 `.queued` 的状态不得回退到 `.saving`。
2. 重复 ended receipt 后，已有 status observation 仍能消费 `reviewReady` 并进入终态。
3. App 重开后根据 durable follow-up 恢复同一 review batch，不重复 admission，不重复生成候选。
4. 候选页创建后发生合法认证刷新，显式刷新能够使用新租约重新读取列表。
5. 认证刷新与第一次列表请求并发时，不应永久停留在通用失败状态。
6. 真实账号切换时旧页面不能读取或提交新账号候选。
7. 旧 Candidate/Proposal/Binding 在租约恢复后必须失效并重新拉取。
8. 本地门禁失败时不发送 HTTP；网络、HTTP 和解码失败分别保留可核验的安全分类。
9. 候选列表实际发出后，真实 V2/V4/V5 混合数据仍能解码；异常 proposal 继续 fail closed。
10. 连续点击刷新不产生无界并发请求，晚到回调不能覆盖较新的列表状态。

## 8. 当前验收状态

| 项目 | 状态 | 证据 |
|---|---|---|
| B4-0 匹配版本与数据安全预检 | PASS | 原位安装，未清数据 |
| B4-1 审核预览真实值 | PASS | 真机截图，无占位符 |
| B4-2 用户核对“清河”字样 | PASS | 用户确认语音原始识别即为“清河” |
| B4-3 用户单次确认及回执 | PASS | 决策接口 201 |
| B4-4 正式记忆写入与可见 | PASS | 正式记忆搜索及章节可见 |
| B4-5 投影与向量 | PASS | projection 完成，66 个搜索文档全部 ready |
| B4-6 文字回查 | PASS | 正确回答且不朗读 |
| B4-7 新 Live 回查 | PASS | 事实正确、有声音、可打断并恢复 |
| B4-8 会后沉淀与新候选审核入口 | FAIL | 后端生成 3 条候选，但 iOS 卡保存且候选列表不发请求 |
| 本轮 3 条新候选是否重复 | NOT_RUN | 列表不可读，不能凭数量判断内容 |
| 本轮候选审核和正式写入 | NOT_RUN | 未替用户审核 |

## 9. 证据索引

### 9.1 真机截图

- `device-b4/B4-1-production-candidate-preview.jpg`
- `device-b4/B4-3-confirmation-success.jpg`
- `device-b4/B4-4-formal-memory-search.jpg`
- `device-b4/B4-4-life-record-updated.jpg`
- `device-b4/B4-6-text-echo-answer.jpg`

根目录：

`/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-b4-candidate-review-fix/raw-value-precision-fix/`

### 9.2 关联设计与报告

- `/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-11-Sol-B4候选审核页占位符修复设计.md`
- `/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-11-Sol-B4结构化差异展示补充修复设计.md`
- `/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-11-Sol-B4原始结构判等与显示精度修复设计.md`
- `/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-b4-candidate-review-fix/raw-value-precision-fix/2026-09-11-DreamJourney-B4原始结构判等与显示精度修复报告.md`

## 10. 当前停止点

1. 未修改代码。
2. 未部署后端。
3. 未操作本轮 3 条待确认候选。
4. 未提交或推送 Git。
5. B4 整体保持 FAIL，不能因 B4-1 至 B4-7 通过而关闭。
