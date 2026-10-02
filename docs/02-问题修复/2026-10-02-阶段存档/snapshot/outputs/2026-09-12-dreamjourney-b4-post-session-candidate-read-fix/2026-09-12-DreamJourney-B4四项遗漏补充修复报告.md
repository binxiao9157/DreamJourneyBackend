# DreamJourney B4 四项遗漏补充修复报告

日期：2026-09-12\
本轮结论：`A_LOCAL_PASS / READY_FOR_B4_RETEST`\
整体 B4：尚未关闭；B4-1 至 B4-7 保持 PASS，B4-8 仍为 FAIL\
执行边界：未安装或测试 iPhone，未部署生产，未访问或修改生产候选、正式记忆、历史数据或 Dead Letter，未 commit/push

## 1. 基线

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- Xcode：26.6（17F113）
- 工作区在本轮开始前已有未提交修改；本轮未重置、回滚、暂存或覆盖其他任务成果。
- 后端业务代码、接口合同和数据库迁移均未修改，因此本轮无需部署。

## 2. 四项结论

| 项目 | 修复前结果 | 修改 | 修复后结果 |
|---|---|---|---|
| 列表刷新永久占用 | FAIL：已有列表发起延迟 GET 后提交审核，旧 GET 因审核代次变化被丢弃，后续刷新仍被单飞标志吞掉；红测请求数 2，预期 3 | 读取请求使用独立 `requestID`；只有持有当前读取权的完成回调可释放占用；先交还读取权，再判断审核代次；保留一次有界尾随刷新 | PASS：旧结果不能覆盖新状态，后续合法 GET 正常发送 |
| 检查点未接真实生命周期 | FAIL：受控旧行为下 end/ack/admit 六阶段均发现 0 个协调器；旧关闭中 outbox 无明确阻断 | 页面恢复统一枚举 checkpoint、follow-up、outbox，按 productSessionID 去重；恢复旧命令与批次；缺少安全恢复信息的旧记录明确阻断；新 Live 不复用关闭中场次 | PASS：六阶段均由真实恢复入口发现并完成；未关闭和信息不足记录不会开启麦克风或创建替代场次 |
| 关联组旧审核上下文仍可提交 | FAIL：合法凭据更新后旧 preview 仍可确认，并发送审核写请求 | 校验同主体、vault、generationId、authorityEpoch；凭据更新使旧 Proposal、Binding、选择和确认权失效；异步 preview/confirm 使用上下文代次；迟到回调不能恢复缓存或忙碌状态 | PASS：不刷新直接确认被阻断且无写请求；重新预览后可按新上下文提交 |
| 安全 trace 未贯通且伪造 HTTP 响应 | FAIL：受控 DNS 失败仍产生 `responseReceived` | 同一读取意图共享 trace；重试递增 attempt；区分 created、started、completed、HTTP response、decode、UI commit；只有真实 HTTP 响应存在才记录状态码；AFError 解包后分类 DNS/TLS/离线/取消 | PASS：成功与 401 恢复保持同 trace、不同 attempt；DNS 失败只有 transportCompleted，无 responseReceived、无状态码；UIKit 提交沿用同 trace |

## 3. 修改位置

### 3.1 读取单飞与关联组上下文

`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

- `OwnerTruthCandidateReviewUseCase.refresh/receiveInbox/reset`：以 `activeInboxRefreshRequestID` 管理读取所有权，不再用无法区分新旧请求的全局布尔值。
- `OwnerTruthCandidateInboxReadContext`、`OwnerTruthCandidateInboxDiagnosticEvent`：承载随机 trace 和 attempt，不含正文或凭据。
- `OwnerTruthCandidateRelatedGroupReviewUseCase.beginRequest/preview/confirm/invalidate`：增加合法 successor 校验、上下文代次和迟到完成隔离。
- `OwnerTruthInterviewLiveTurnOutboxStore.snapshots(for:)`：只读枚举当前合法账号范围内的无正文工作流坐标。

`DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`

- 关联组页面使用请求所有者 ID 释放忙碌状态。
- 上下文失效后清空旧展示和确认闭包；旧回调不得清除新请求占用。
- 候选列表 UI 的 `uiCommitted` 使用 UseCase 同一 trace 和 attempt。

### 3.2 会后恢复

`DreamJourney/Sources/Modules/Echo/EchoViewController.swift`

- `EchoLiveMemoryCompletionCheckpointStore.records(for:)`：枚举当前账号作用域中的检查点。
- `EchoLiveMemoryRecoveryService.resumePendingWorkflows`：合并 checkpoint、follow-up、outbox，按场次单飞恢复并输出明确阻断原因。
- `resumePendingLiveMemoryOrganizationsIfNeeded`：页面生命周期接入统一恢复服务。
- `configureVoiceRuntimeThenStart`：后台恢复与新 Live 分离，不把旧关闭场次作为新会话身份，不因恢复任务开启麦克风或索取新语音会话。

### 3.3 网络诊断

`DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`

- 候选读取将 UI 创建的 trace 贯穿门禁、认证、传输、HTTP、解码和完成。
- `requestJSON` 记录 `transportCreated`、`transportStarted`、`transportCompleted`；仅在 `response.response` 存在时记录 `responseReceived`。
- 401 或策略有界恢复保留 trace、增加 attempt；不会自动重试审核写入。
- 日志仅含白名单阶段、原因、耗时、状态码和随机标识。

### 3.4 回归测试

`DreamJourneyTests/OwnerTruthContractsTests.swift`

- 新增刷新与审核交错、关联组凭据轮换和迟到 preview、六阶段生命周期恢复、旧 outbox 阻断、HTTP 失败分类、trace 贯通及实际 UIKit 提交场景。
- 测试走真实 UseCase、恢复服务、FeatureGate/AccountLease、`DreamJourneyBackendClient` 和 UIKit controller；未以单独 helper 或 token 200 代替行为验收。

## 4. 反例与回归证据

### 4.1 修复前

- `supplemental-2026-09-12/evidence/pre-fix/red-refresh-related-group.log`
  - 刷新交错：请求数 2 而非 3，列表未更新。
  - 关联组轮换：确认未抛错，写请求已发送。
- `supplemental-2026-09-12/evidence/pre-fix/red-recovery-diagnostics.log`
  - endPrepared、ended、acknowledgementPrepared、acknowledged、admissionPrepared、admitted 六阶段均未发现恢复协调器。
  - DNS 失败错误出现 `responseReceived`。
- `supplemental-2026-09-12/evidence/pre-fix/reproduction-notes.md`
  - 说明隔离副本和仅恢复两段旧行为的范围，主工作区未回退。

### 4.2 修复后定向测试

| 场景 | 状态 | 证据 |
|---|---|---|
| 刷新/审核交错与关联组轮换 | PASS，2/2 | `supplemental-2026-09-12/evidence/post-fix/green-refresh-related-group.log` |
| 六阶段恢复、旧 outbox 阻断、迟到 preview | PASS，3/3 | `supplemental-2026-09-12/evidence/post-fix/green-recovery-related-late.log` |
| 401 同 trace 多 attempt、DNS 无伪 HTTP、UIKit 同 trace | PASS，3/3 | `supplemental-2026-09-12/evidence/post-fix/green-diagnostics.log` |

### 4.3 完整回归与构建

- OwnerTruth 完整测试：334/334 PASS，0 failure；日志 `supplemental-2026-09-12/evidence/post-fix/full-owner-truth-tests.log`，结果包 `supplemental-2026-09-12/evidence/post-fix/OwnerTruthContractsTests.xcresult`。
- 模拟器目标：PASS，`supplemental-2026-09-12/evidence/post-fix/build-simulator.log`。
- 通用 iOS 设备目标：PASS，`supplemental-2026-09-12/evidence/post-fix/build-generic-ios.log`。
- `git diff --check`：PASS，无输出。
- 源码和 App 主二进制指纹：`supplemental-2026-09-12/evidence/post-fix/fingerprints.txt`。

## 5. UIKit/UIQA

| 场景 | 状态 | 证据 |
|---|---|---|
| V5 详情及关联组结构化预览 | PASS | `supplemental-2026-09-12/evidence/uiqa/v5-detail/`；页面明确显示 `0.721 -> 0.724` 和 `2016年 -> 2018年` |
| 关联组失效、重新预览、重复点击和冲突 | PASS | `supplemental-2026-09-12/evidence/uiqa/related-group-resilience/` |
| 候选列表、详情、提交、审核记录和正式记忆呈现 | PASS | `supplemental-2026-09-12/evidence/uiqa/candidate-inbox/` |

以上为模拟器隔离数据。UIQA 中的提交只写测试替身，不触及生产候选或正式记忆。

## 6. 状态修订

| 项目 | 状态 | 说明 |
|---|---|---|
| 四项 Astra 补充缺口 | PASS | 均有局部反例、真实调用路径回归和本地构建证据 |
| A 本地门禁 | A_LOCAL_PASS | 本轮可独立完成的代码、测试、UIKit/UIQA 和两类编译已完成 |
| B4-1 至 B4-7 | PASS | 保留前序有效证据，本轮无回退 |
| B4-8 | FAIL | 仍需真机取得会后状态、候选真实读取和页面联合证据 |
| 生产候选读取原始失败层 | NOT_RUN | 本地诊断能力已具备，但真实失败现场尚未重新采集，不能宣称已查明 |
| 新出现 3 条候选是否重复 | NOT_RUN | 未操作生产候选 |
| 真实候选审核写入、投影、向量及问答回查 | NOT_RUN | 等用户连接 iPhone 并明确开始后执行 |

## 7. 部署与影响

- 后端无业务改动、无迁移，因此无需部署；本轮也未部署。
- iOS 改动只影响候选读取/审核上下文、会后恢复发现及脱敏诊断。
- 未修改火山原生 Live 的声音、持续聆听、低延迟、打断或正式记忆绑定；未恢复 ASR -> DeepSeek -> TTS。
- 未修改正式记忆事实、整理规则、检索或向量模型。

## 8. 残余风险与真机复测重点

1. 生产候选读取此前失败的实际层级仍未知；真机必须用同一 trace 对齐 UI、门禁、网络、HTTP、解码和提交，不能由候选数量反推。
2. 真机中若出现合法凭据更新，需要验证旧页面立即失效且只能重新读取/预览；审核写请求不得自动补发。
3. 需要分别在 Live 结束后、切后台/重开 App 后确认同一 batch 恢复，且新 Live 获得独立场次。
4. 用户亲自确认后，再核对正式记忆版本、投影、向量及文字/新 Live 回查；未执行前保持 NOT_RUN。

## 9. 局部回退方案

如真机出现新回归，只撤销本报告涉及的局部能力：

1. 回退候选读取 requestID 所有权与 trace 接线，但不得恢复旧审核 Binding 或自动重试写请求。
2. 回退统一恢复服务接线，但保留检查点数据，不清理或猜测旧场次。
3. 回退关联组 successor/代次隔离时必须同时阻断旧确认入口，不能回到“旧预览可提交”。
4. 不回退 typed 原始结构判等、显示精度、八种操作、CAS、正式记忆唯一事实源或原生 Live 音频链路。
5. 不通过清数据、删除候选、放宽鉴权或修改正式记忆规避问题。

## 10. 下一停止点

本轮 A 补充已通过，状态恢复为 `A_LOCAL_PASS / READY_FOR_B4_RETEST`。等待用户明确连接 iPhone 并要求开始后，再执行 B4 真机复测；B4 整体在 B4-8、审核写入、投影、向量和问答闭环取得真实证据前不得关闭。
