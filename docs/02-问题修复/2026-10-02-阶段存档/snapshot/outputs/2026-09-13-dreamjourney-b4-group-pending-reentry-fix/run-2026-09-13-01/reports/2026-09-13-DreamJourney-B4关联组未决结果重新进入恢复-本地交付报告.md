# DreamJourney B4 关联组未决结果重新进入恢复 - 本地交付报告

日期：2026-09-13\
执行范围：仅本地 iOS 代码、自动化、模拟器、UIQA、编译和交付证据\
未执行：生产部署、生产数据访问或修改、iPhone 安装与测试、Git commit/push

## 1. 结论

本轮唯一目标已在本地闭环：关联组审核结果未知后，即使旧页面、旧
`UseCase`、旧 `Proposal` 和旧确认闭包均已销毁，用户从正常的“待确认记忆”
入口重新进入，页面会从当前账号/vault 作用域的磁盘未决记录中取得原
`commandID` 与不可变 `Binding`，只读查询原审核结果，不重新预览、不调用
`confirm`、不发送审核 `POST`。

结果为 `found` 时，客户端先校验账号作用域、凭据代次和服务端回执绑定，再仅
移除匹配 command 的未决记录，并要求候选列表达到提交后的正式记忆版本；写入
操作在刷新到最新快照前仍被保护。`notObserved`、网络/HTTP 失败、超时、账号
变化、磁盘读取或删除失败均保留原记录，允许安全返回和再次只读核实。

本轮状态：**A_LOCAL_PASS / READY_FOR_B4_RETEST**。

这不表示 B4 整体通过，也不表示生产已具备只读核实能力。前序实现的单条和
关联组 `decision-result` API 尚未部署，真机前仍需另行授权受控部署。

## 2. 基线与边界

### 2.1 工程基线

- iOS：`feature/prd-stitch-ui-adaptation`，HEAD
  `5fd061fd869edbe1fc13e8535a47880826581934`。
- Backend：`main`，HEAD `be9670b6ec05e73ab9562943f402e5a9e1346988`。
- 两端均存在前序任务的未提交和未跟踪文件；本轮未重置、覆盖或回滚。
- 本轮后端业务代码、迁移和接口均无新增修改。

### 2.2 保留的既有行为

- R1-R3 的审核写防重发、未知结果只读核实、在途读取期限和迟到回调隔离保留。
- V5 typed 原始结构判等、数值显示精度、八种操作、关联组原子性、CAS、哈希、
  Binding 和连续审核保护保留。
- 火山原生 Live 的声音、持续聆听、低延迟、打断和正式记忆快照绑定未修改。
- 没有恢复 ASR -> DeepSeek -> TTS 串行 Live 链路。

## 3. 已证实根因

1. `OwnerTruthCandidateReviewUseCase` 的正常列表入口只扫描单条未决记录，未扫描
   `groupRecords`。关联组记录虽然已持久化，重新进入页面时却不可发现。
2. `OwnerTruthCandidateRelatedGroupReviewUseCase` 的旧恢复方法要求调用方继续持有
   完整 `Proposal`，因此旧测试只能证明“旧页面仍在时可查询”，不能证明冷启动
   恢复。
3. 页面没有与组级未决恢复状态绑定的生命周期入口和“核实结果”动作。
4. 初版修复补测后另发现：同主体凭据合法轮换期间，旧查询回调可能按旧租约落地；
   已增加 commit 阶段租约检查和合法 successor 处理，旧结果不能删除未决记录或
   恢复旧确认权。

## 4. 代码修改

### 4.1 `OwnerTruthContracts.swift`

文件：`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

- 新增 `OwnerTruthCandidateGroupPendingRecoveryState`：`none/checking/refreshing/
  unresolved/blocked`，除 `none` 外统一保护审核写。
- `resumePendingReviewResultIfNeeded` 接到真实恢复入口；没有未决记录时不额外发送
  普通候选 GET，避免 `viewWillAppear` 产生重复读取。
- `recoverPendingReviewResultIfNeeded` 同时读取单条和关联组磁盘记录；多条未决并存
  时 fail closed，不选择性解除整个 vault 的写保护。
- `lookupPendingGroupReviewResult` 只使用磁盘中的账号作用域、原 command 和 Binding
  调用组级只读 GET；包含单飞身份、30 秒整体期限和安全关联日志。
- `receivePendingGroupReviewLookup` 校验 request 身份、账号/vault、commit 租约、同
  主体凭据轮换和 typed 结果。旧/迟到结果不能更新新页面或移除别的记录。
- `found` 后仅移除完全匹配的组记录，并以提交版本作为候选快照下限；旧 revision
  不开放写入。
- `notObserved`、错误、超时、磁盘错误、账号错误均保留原记录和明确未决/阻断状态。
- 关联组低层只读核实改为使用 `proposalID` 完成旧页面内的回调归属；正常重新进入
  路径不需要也不会构造完整 Proposal。

### 4.2 `MemoryArchiveViewController.swift`

文件：`DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`

- `viewDidLoad` 的原刷新入口会先发现磁盘未决记录并自动只读核实一次。
- 后续 `viewWillAppear` 调用恢复入口，不重新触发无关候选 GET。
- 手动刷新仍进入同一恢复调度；未知结果弹窗新增“稍后查看”和只读“核实结果”。
- 页面状态区新增可访问的“核实结果”按钮，identifier：
  `owner-truth-candidate-inbox-verify-group-result`。
- `checking/refreshing/unresolved/blocked` 期间禁用候选审核与关联组写入口，但返回
  导航保持可用。

### 4.3 测试

文件：`DreamJourneyTests/OwnerTruthContractsTests.swift`

- 新增真实 UIKit + BackendClient + URLProtocol + 磁盘 store 的 G01 页面重建测试。
- 新增分两次运行、实际关停并重启模拟器的 G02 持久恢复测试。
- 新增 G03-G16 的作用域、空列表、过滤、失败、超时、账号切换、凭据轮换、迟到
  回调、Binding 错配、磁盘失败、版本下限、多未决、可访问性与隐私测试。
- 补充原账号合法返回后的正常入口恢复，以及断连/503/404 后同 command 只读重试。

Backend 无本轮修改。

## 5. 红绿证据

### 5.1 G01 主反例

- 修复前：`evidence/pre-fix/G01-red-v2.xcresult`，1 项 FAIL。旧页面释放后新页面只
  发候选 GET，未发组结果 GET，磁盘记录未解决。
- 修复后：`evidence/post-fix/G01-green-v2.xcresult`，1/1 PASS。旧 Controller 与
  UseCase 已释放；新页面从磁盘恢复，网络序列为组级 GET -> 候选 GET，业务
  POST=0。
- 页面截图与可访问性附件：`evidence/post-fix/screenshots/targeted/`。

### 5.2 凭据轮换补充反例

- 修复前：`evidence/pre-fix/G09-credential-rotation-red.xcresult`，1 项 FAIL。旧回调
  未按新凭据代次保持未决状态。
- 修复后：`evidence/post-fix/G09-G12-green.xcresult`，2/2 PASS。旧回调被隔离，
  磁盘读取失败也 fail closed。

## 6. G01-G16 验收矩阵

| ID | 状态 | 测试/证据与实际断言 |
| --- | --- | --- |
| G01 | PASS | `testRelatedGroupPendingResultRecoversFromDiskThroughRecreatedInboxPage`；真实 UIKit、BackendClient、URLProtocol、磁盘 store，旧对象释放，GET/GET、POST=0。 |
| G02 | PASS | `testG02ASeedPendingGroupForSimulatorProcessRestart` 写入磁盘；关停并重启模拟器后，`testG02BRecoversPendingGroupAfterSimulatorProcessRestart` 从正常页面恢复。证据分别为 `G02-seed-before-process-restart.xcresult`、`G02-recover-after-simulator-restart.xcresult`。 |
| G03 | PASS | `testG03G04G05PendingGroupRecoveryIsScopeBoundNotListBoundAndRetriesReadOnly`；列表为空、原成员不在列表仍能核实。 |
| G04 | PASS | 同上；不同 `sourceIDFilter` 不绕过 vault 写保护。 |
| G05 | PASS | 同上及 `testG05G16InboxPageOffersReadonlyVerificationAfterNotObserved`；notObserved 保留记录，再次核实同一 command 后 found，POST=0。 |
| G06 | PASS | `testG06G07G10PendingGroupRecoveryExpiresOnceAndDropsLateFound` 及 `testG06PendingGroupRecoveryKeepsOriginalCommandAcrossTransportAndHTTPFailures`；整体超时、断连、503、404 均保留记录并可同 command 只读重试。 |
| G07 | PASS | G06/G07/G10 组合测试与 G01 页面生命周期；初始化/出现/刷新合并为单飞核实，无自动循环。 |
| G08 | PASS | `testG08G09PendingGroupRecoveryRetainsOriginalScopeAcrossAccountChange` 和 `testG08PendingGroupRecoveryResumesReadOnlyWhenOriginalAccountReturns`；新主体无跨账号结果，原主体回来后正常入口只读恢复。 |
| G09 | PASS | `testG09PendingGroupRecoveryDropsOldCallbackAfterSameSubjectCredentialRotation`；旧回调不能落地，合法新凭据可重新只读核实，旧审核上下文失效。 |
| G10 | PASS | G06/G07/G10 组合测试；期限后的 found 迟到不移除记录、不刷新列表、不释放新请求所有权。 |
| G11 | PASS | `testG11G12PendingGroupRecoveryFailsClosedForMismatchAndStorageFailure`；回执/Binding 不匹配不视为成功，POST=0。 |
| G12 | PASS | G11/G12 及 `testG12PendingGroupRecoveryFailsClosedWhenDiskRecordsCannotBeRead`；读取和删除失败均保留保护，不按零记录放行。 |
| G13 | PASS | `testG13FoundGroupWaitsForCandidateSnapshotAtCommittedRevision`；旧快照被丢弃，达到提交 revision 后才解除保护。 |
| G14 | PASS | `testG14MultiplePendingRecordsKeepWholeVaultWriteProtected`；多未决不选择一个自动解决，不产生查询风暴或新写。 |
| G15 | PASS | 完整 392 项回归中的关联组确认、明确未发送、401 不重发、单条核实，以及 `related-group-resilience` UIQA；原子性和分类未回归。 |
| G16 | PASS | `testG05G16InboxPageOffersReadonlyVerificationAfterNotObserved`、导出截图/可访问性树及日志；日志仅有散列关联标识，无 command 原文、正文、令牌或响应。 |

定向总包：`evidence/post-fix/G01-G16-targeted-v2.xcresult`，10/10 PASS；后续增加的
G08 原主体返回和 G06 三类错误分别保存在
`G08-original-account-return-green-v2.xcresult` 与 `G06-errors-green.xcresult`，并已
进入最终完整 392 项回归。

## 7. UIQA、回归与编译

### 7.1 实际模拟器 UIQA

- V5 详情、typed 时间/可信度差异、关联组完整预览：
  `evidence/post-fix/uiqa/v5-detail/`，全部断言为 true，包含两张页面截图。
- 关联组明确未发送重试、重复点击抑制、冲突后重新预览、最终原子提交：
  `evidence/post-fix/uiqa/related-group-resilience/`，全部断言为 true。
- 新恢复页的 notObserved、“核实结果”、found 后刷新截图及 VoiceOver 树：
  `evidence/post-fix/screenshots/targeted/`。

### 7.2 自动化与保持性

| 门禁 | 状态 | 证据 |
| --- | --- | --- |
| G01-G16 定向及补充 | PASS | 上述结果包；主定向 10/10，G08 补充 1/1，G06 补充 1/1 |
| OwnerTruth 完整回归 | PASS | `evidence/post-fix/OwnerTruthContracts-full-v4.xcresult`，392/392 |
| Echo/音频保持性 | PASS | `evidence/post-fix/AudioOwnerLeaseModelTests.xcresult`，5/5 |
| iOS Simulator build | PASS | `evidence/post-fix/build-simulator.xcresult` |
| generic iOS device build | PASS | `evidence/post-fix/build-generic-ios.xcresult` |
| iOS `git diff --check` | PASS | 无空白错误 |
| Backend `git diff --check` | PASS | 无空白错误 |

说明：尝试额外运行未纳入 Xcode 工程测试目标的 SwiftPM
`OwnerTruthCoreContractTests` 时，干净重编译被既有 Package 分层问题阻断：
`OwnerTruthContracts.swift` 的 App 层 `DreamJourneyBackendClient`/诊断依赖未包含在
`DreamJourneyCore` target，且 Swift 6 对既有共享 store 报并发安全错误。证据：
`evidence/post-fix/OwnerTruthCoreContractTests-swiftpm-clean-v2.log`。该目标不是本手册
指定的 `DreamJourney.xcworkspace/DreamJourney` 本地放行路径，且修复它会扩大到
Core/App 分层重构，因此记为独立 `BLOCKED`，没有使用旧构建产物冒充通过。

## 8. 指纹

### 8.1 iOS 源码 SHA-256

- `OwnerTruthContracts.swift`：`29f5058c8208c4f744b046ecf6a04a66b4e3134a8135953c549ecc916bd315f7`
- `MemoryArchiveViewController.swift`：`297176d1942c38927a5de25fb1c7f9a4c57f4f64e7d113a651fe50d5e82a606d`
- `OwnerTruthContractsTests.swift`：`1849eaa95fde1bcf08d12f86a4402682f848d085b202b8bb2402f8c25ff0d637`
- `DreamJourneyBackendClient.swift`（本轮未改）：`4239b2b76eedc84b1126d6ccc2f1e3c28a7ba308049865cf657d747d3c56d288`

### 8.2 构建产物 SHA-256

- Simulator `DreamJourney.debug.dylib`：`2cfabc5d500277fadf3ded766c74763d4c5e4f08f222a0f63bbfe48fdbc50b7b`
- generic iOS `DreamJourney.debug.dylib`：`36e3b5262fd877bb50cbb39a6bb37d8c3025d9d44b719ea0ee7b17df3e9f204f`

### 8.3 后端同指纹证据复核

本轮后端没有变化。当前组级 decision-result 实现与前序隔离 PostgreSQL 证据保持
同指纹：

- `app/main.py`：`4242fc079bba69b8c26b8f85c4c882bb546ef7b8e85de204f6bd9edf724cb712`
- `owner_truth_memory_changeset_group_review.py`：`e659d3a25ee19a8b1ef338d794b8e57269e8c6492dbcf4886d89e6e50f898380`
- 组审核 PG smoke：`f5668fabf6d75b7d14e1e696f74aad23540e62445300eadb05705bc6bf2a20b2`

复用证据：
`/Users/gaominge/Documents/liftora/outputs/2026-09-13-dreamjourney-b4-final-boundary-fix/run-2026-09-13-01/evidence/post-fix/postgres-group-decision-result.log`。
它验证 schema 0121、组审核原子性、回滚、并发幂等、确认前 notObserved、确认后
found 和查询零写副作用。本轮未连接或操作生产数据库。

## 9. 部署判断与未完成状态

本轮没有新增后端合同或迁移，因此本轮代码本身不触发新的后端部署范围。但前序
两个只读合同仍未部署，真机 B4 未决恢复前必须另行授权并发布：

- `GET /v2/vaults/{vault_id}/candidates/{candidate_id}/decision-result`
- `GET /v2/vaults/{vault_id}/memory-changeset-groups/decision-result`

两者均通过 `X-DreamJourney-Review-Command-Id` header 传递 command，不放入 URL。

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 本轮 G01-G16 与本地门禁 | PASS | 本报告证据齐备 |
| B4-1 至 B4-7 | PASS | 保留既有现场结论，本轮未重复清零 |
| B4-8 | FAIL | 仍需部署后真机会后状态、真实候选读取和来源关联联合证据 |
| 连续审核第二条真实写入 | FAIL | 仍需用户本人真机再次预览并确认 |
| 首次策略过期现场恢复 | FAIL | 本地合同通过，生产自然场景未验证 |
| 历史三条候选重复性 | NOT_RUN | 本轮不操作生产候选 |
| 新查询产生候选的语义处理 | NOT_RUN | 本轮不生成生产数据 |
| F3 历史检查点实际影响 | NOT_RUN | 仍无生产因果证据 |
| 单条/组 decision-result 生产部署 | NOT_RUN | 等待单独授权 |
| iPhone 安装与真机复测 | NOT_RUN | 按要求停止 |
| SwiftPM DreamJourneyCore 独立目标 | BLOCKED | 既有 Core/App target 分层缺口，非本轮 workspace 放行路径 |

## 10. 回退方案

若后续复测发现恢复调度异常，只回退本轮以下局部内容：

1. `OwnerTruthCandidateReviewUseCase` 的组级磁盘发现、组级恢复状态、只读调度和
   found 后 revision 刷新。
2. 候选页生命周期恢复接线、状态文案和“核实结果”按钮。
3. 本轮 G01-G16 测试。

回退时必须保留磁盘未决记录和只读提示，并关闭受影响审核入口；不得回退成自动
补发审核 POST、删除未决记录、放宽 Binding/CAS、清空工作区或破坏 R1-R3。

## 11. 下一步停止点

当前停止，不部署、不安装 iPhone、不访问生产数据、不 commit/push。

取得下一步授权后，应先受控部署两个 decision-result 合同并核对生产版本、路由、
鉴权和零写副作用；然后原位覆盖安装本报告指纹对应的 iOS 构建，再按手册 B0-B5
逐步复测。若生产没有自然形成的关联组未决记录，该真机场景保持 `NOT_RUN`，不得
人为制造生产未知写入。
