# DreamJourney B4 真机新问题记录（Astra 设计交接）

## 1. 交接目的

请 Astra 基于本报告和原始证据，输出局部修复设计文档。当前不要把任何推测直接写成根因，也不要重构已经通过的 Live、正式记忆、候选 V5 展示、不可变 binding 或 CAS 链路。

完整真机报告：

`/Users/gaominge/Documents/liftora/outputs/2026-09-12-dreamjourney-b4-device-retest/2026-09-12-DreamJourney-B4真机复测报告.md`

## 2. 测试基线

- iOS HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- 未提交差异指纹：`e4febb15561260111569ced2b0cfd7483a34e5f716a14f828b6deed7fedc7376`
- 真机 App 二进制 SHA-256：`37f06d43912c9a6231ce091dd256536c735e872b40dbb9d218d0c4af24653621`
- Bundle：`com.gaominge.dreamjourney.app`，1.0.0 (1)
- 设备：iPhone 14 Pro Max，原位覆盖安装，保留原有 App 数据
- 后端：生产环境，只读观测；本轮未部署
- QA 旁路、合成候选和故障注入均未进入生产连接配置

## 3. 问题一：连续审核第二条显示成功，但没有提交和写入

### 3.1 用户可见现象

1. 一场 Live 会后生成 2 条候选，列表从 36 条增加到 38 条。
2. 用户依次打开两条候选，并亲自点击“确认并纳入正式记忆”。
3. 两次点击后，页面都明确显示成功。
4. 正式记忆中实际只能找到第一条；列表剩余 37 条。

### 3.2 已证实的技术事实

- 第一条：服务端收到 `POST /candidates/{id}/decisions`，返回 `201 Created`。
- 第一条数据库状态：`accepted`，存在审核回执，生成 1 个当前 MemoryVersion。
- 第二条：服务端没有收到审核写请求。
- 第二条数据库状态：`pending`，无审核回执、无 MemoryVersion。
- 第二次操作期间，iOS 安全日志只有新的 `useCaseGateChecked`，随后 `uiCommitted phase=failed`；没有对应 `requestCreated/taskCreated/taskResumed/responseReceived`。
- 因此第二次不是“服务端写入后客户端漏刷新”，而是写请求未发送，UI 却向用户显示了成功。

### 3.3 当前不能直接认定的假设

以下仅为待核查假设：

- 第一条审核完成后，`operationGeneration` 或审核上下文代次变化，使旧列表中第二条 Candidate/Proposal/Binding 失效。
- 第二条使用了已经失效的确认闭包，UseCase 在发送前拒绝。
- 页面级成功状态、Toast 或 Banner 没有绑定到本次 candidate/request，复用了第一条成功状态。
- 失败状态没有覆盖或清除已有成功提示，导致视觉上的“假成功”。

### 3.4 Astra 设计必须回答

1. 成功提示应绑定哪些不可变标识：Candidate、Proposal、Binding、requestID、operationGeneration、审核回执或正式版本。
2. 第一条审核完成后，列表内其他候选如何安全失效、刷新和重新预览。
3. 第二次发送前拒绝时，如何确保页面只显示准确失败，不沿用上一条成功状态。
4. 已发送写请求与未发送写请求如何区分；不得自动重试审核写入。
5. 迟到回调、快速连续审核、列表刷新、账号/凭据更新、版本冲突下，如何防止旧结果覆盖新状态。

### 3.5 必须补的反例

- 已有两条候选 → 预览两条 → 确认第一条成功 → 不刷新直接确认第二条。
- 第二条发送前因上下文失效被拒绝：不得发送 POST，不得显示成功，候选仍 pending。
- 第一条响应晚到、第二条预览晚到、列表刷新乱序的所有关键排列。
- 重新刷新并重新预览第二条后，合法提交可正常发送且成功状态只属于第二条。
- UIKit 实际页面必须断言可见提示和列表数量，不能只测试 UseCase 返回值。

## 4. 问题二：候选页首次进入被过期策略缓存挡在发送前

### 4.1 用户可见现象

- 进入“待确认记忆”后显示“暂时无法读取候选记忆”。
- 点击右上角刷新后，列表恢复并显示 37 条。

### 4.2 已证实的技术事实

- 失败 trace 记录：
  - `requestDenied reason=expiredPolicyCache stage=featureInitial`
  - `requestFailed reason=permissionUnavailable stage=useCaseCompletion`
  - `uiCommitted phase=failed`
- 失败 trace 没有 `requestCreated`、`taskCreated`、`taskResumed` 或 HTTP 响应。
- 对应时段服务端没有候选 GET，因此这是发送前拒绝，不是 DNS/TLS/HTTP/JSON/V5 解码失败。
- 用户手动刷新后：发布策略请求返回 200，候选 GET 返回 200，12.497 秒后完成 JSON、V5 解码和 UI 提交，候选数为 37。
- 随后退出重进及冷启动均能用新 trace 完成真实 GET 并显示 37 条。

### 4.3 当前不能直接认定的假设

- FeatureGate evaluator 发现缓存过期后，只返回拒绝，没有把同一读取意图接入有界策略恢复。
- 策略恢复发生在其他页面/全局刷新中，而候选页首次读取没有等待或重接恢复结果。
- 页面首次读取与发布策略刷新存在时序竞争。

### 4.4 Astra 设计必须回答

1. `expiredPolicyCache` 是否属于可恢复的读取前置状态；若是，恢复责任位于 UI、UseCase、BackendClient 还是 FeatureGate。
2. 如何保留同一读取 trace，并为策略恢复后的网络尝试生成正确 attempt。
3. 如何保持单飞、有界次数、账号/vault/authority 作用域检查和旧结果隔离。
4. 恢复失败时如何区分策略服务失败、权限真实关闭、账号切换和请求取消。
5. 为什么手动刷新能恢复，而首次进入没有自动恢复；需以真实调用路径证明。
6. 审核写请求不得复用此只读自动恢复策略，不得增加写入自动重试。

### 4.5 必须补的反例

- 页面首次进入时策略缓存刚好过期，合法同主体策略刷新成功，候选读取自动继续并显示列表。
- 策略刷新失败或功能确实关闭时，页面显示准确原因且不发送候选 GET。
- 策略恢复与认证恢复组合时，attempt 不重置、不重复、不突破现有上限。
- 旧策略回调晚到不能覆盖新列表或恢复旧审核确认权。
- UIKit 页面验证真实错误、恢复中和成功状态，不只验证 helper。

## 5. 问题三：历史关闭任务缺少检查点

### 5.1 已证实事实

- App 冷启动和部分页面切换时重复记录：
  - `recoveryBlocked reason=closingOutboxMissingCheckpoint`
- 标识始终指向同一个脱敏历史 productSession。
- 本轮新 Live 使用独立 session 和 batch；没有把新表达接到该历史场次，也没有重复生成本轮候选。
- 本轮未清理、猜测、重放或修补该历史记录。

### 5.2 Astra 设计需要判断

- 该组合是合法历史兼容状态、迁移遗漏，还是崩溃窗口造成的不完整记录。
- 信息不足时应如何保持 blocked 并向用户/运维提供可处理状态。
- 是否需要受控的本地迁移、只读诊断入口或明确的终止策略。
- 不得凭空补 checkpoint，不得把状态直接改 ready，不得清理用户历史来掩盖问题。

## 6. 观察项：查询已存在事实仍产生待确认候选

### 6.1 已证实事实

- 一次文字问答和一次新 Live 都只询问刚刚已进入正式记忆的事实。
- 两个会话分别结束后，各生成 1 条 `experience:pending` 候选。
- 两条候选均未打开预览、未审核、未删除或合并。

### 6.2 尚未验证

- 候选内容是否与现有正式记忆语义重复。
- builder 最终会生成 `duplicate`、`addEvidence`、`refine` 或其他 operation。
- 候选是否错误吸收了提问句或助手回答。

因此该项保持 **NOT_RUN**，不能仅凭候选数量判定重复。Astra 可在设计中补充安全验证方案，但不要把它与前两个已证实缺陷混为同一根因。

## 7. 已通过且必须保留的行为

- 候选列表正常状态下可完成真实 GET、V5 解码和 UI 提交。
- 原生 Live 能正确采用正式记忆，回答有声音。
- 朗读中可立即打断，随后自动恢复聆听，约 10 轮连续交流正常。
- 会后 `saving -> queued -> organizing -> pendingReview`，重复 ended receipt 被忽略。
- 本场 2 条候选与同一 review batch/source 可关联。
- 第一条经用户审核后真实写入 MemoryVersion，并进入正式投影、搜索文档和 bge-m3 向量。
- 文字回响回答正确且不朗读，引用记录包含新 MemoryVersion。
- 新 Live 回查与文字回答一致，并使用独立新会话。
- 前后台、退出重进及冷启动后，合法读取可恢复。

设计不得恢复 `ASR -> DeepSeek -> TTS` 旧串行 Live 链路，不得改变正式记忆唯一事实来源、用户审核要求、不可变 binding、CAS、账号隔离和写入不自动重试边界。

## 8. 当前验收状态

- B4-1 至 B4-7：保留前序 PASS。
- B4-8：**FAIL**。
- 候选首次生产读取：**FAIL，发送前失败层已定位**。
- 审核闭环：**FAIL，一条真实成功，一条 UI 假成功且未写入**。
- 单条正式记忆写入、投影、向量、文字及新 Live 回查：PASS。
- 三条历史候选重复性：NOT_RUN。
- 本轮新增两条查询候选的语义操作：NOT_RUN。
- 合法凭据轮换下旧审核权失效：NOT_RUN，本轮未强制制造轮换。

## 9. Astra 输出要求

请输出一份局部修复设计文档，至少包含：

1. 两项已证实缺陷的独立根因分析和真实调用链。
2. 需求到代码到测试到证据的映射表。
3. 明确修改文件、类、函数和状态所有权。
4. 红绿反例、UIKit 场景、受控网络组合及回归范围。
5. 不改变 Live、正式记忆、账号隔离、CAS 和审核写保护的说明。
6. 生产部署判断、真机复测步骤、失败回退方案。
7. 对问题三和观察项分别标明“需修复”“需进一步诊断”或“NOT_RUN”，不得把推测写成已证实根因。

## 10. 证据位置

- 真机完整报告：`2026-09-12-DreamJourney-B4真机复测报告.md`
- 设备脱敏日志：`evidence/device/app-live-filtered.log`
- 设备关键事件：`evidence/device/key-events.log`
- 服务端脱敏访问日志：`evidence/server/api-live-sanitized.log`
- 审核、版本、投影、搜索、向量和引用的只读核对：`evidence/server/read-only-integrity-check.txt`
- 构建日志：`evidence/preflight/device-build.log`
- 安装结果：`evidence/preflight/install-result.json`

## 11. 操作边界

- 当前只要求 Astra 输出设计，不直接修改生产数据。
- 不替用户审核第二条 pending 候选。
- 不删除候选、正式记忆、审核历史或 Dead Letter。
- 不重放历史任务，不清 App 数据，不轮换密钥。
- 不 commit、不 push，除非用户后续明确授权。
