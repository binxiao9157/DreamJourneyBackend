# DreamJourney B4 审核未决结果与恢复边界本地交付报告

日期：2026-09-13\
结论：`A_LOCAL_PASS / READY_FOR_B4_RETEST`\
范围：S1-S6 本地实现、自动化、模拟器/UIKit、隔离 PostgreSQL 证据复核及两类 iOS 编译

## 1. 执行边界

- 未部署后端，未访问生产服务或生产数据库。
- 未安装或测试 iPhone，未卸载或清理 App 数据。
- 未读取、确认、拒绝、修改或清理生产候选、正式记忆、审核历史和 Dead Letter。
- 未 commit、未 push，未 reset、stash、checkout 或覆盖既有脏工作区。
- 未改变火山原生 Live 的声音、低延迟、持续聆听、打断和正式记忆快照绑定。
- 未恢复 ASR -> DeepSeek -> TTS 旧串行 Live；文字问答仍由 DeepSeek 生成且不朗读。

## 2. 基线与环境

### iOS

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支/HEAD：`feature/prd-stitch-ui-adaptation` / `5fd061fd869edbe1fc13e8535a47880826581934`
- tracked diff SHA-256：`096d9ab2758512fcead44b52a01de9ff744e6db451bf53848d4ed3c69ca95ed9`
- Xcode：26.6（17F113）；Swift：6.3.3；CocoaPods：1.16.2。
- 工作区原本包含大量 B4、Live、OwnerTruth 未提交成果；本轮只在相关生产路径和测试上增量修改。

### Backend

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 分支/HEAD：`main` / `be9670b6ec05e73ab9562943f402e5a9e1346988`
- tracked diff SHA-256：`3862527d75ae3c622f3d47eca83a3effcf236202bbfb8a3a00810be1b686ad04`
- Python：3.9.6。
- 本轮没有新增后端业务修改或迁移；只复核前轮 decision-result API/service 及其测试、隔离 PG 证据。

两端 `git diff --check` 均通过。

## 3. 六项根因与修改

### S1 未决结果可产生第二个审核写

根因：单条 submit 仅检查当前内存中的 active operation/lookup，notObserved 后详情恢复普通写交互；跨页面和关联组也可能绕过未决记录。

修改：

- `OwnerTruthContracts.swift` 的 `OwnerTruthCandidateReviewUseCase` 在同一串行域读取受保护 pending store，再创建 command。
- 同 vault 的 `pending/outcomeUnknown/checkingResult` 阻断 accept/correct/reject、另一候选和关联组新写。
- notObserved 保留原 command，只允许结果 GET；重建 UseCase 也从持久记录恢复核实。
- `MemoryArchiveViewController.swift` 区分 unknown 与 notSent：unknown 写按钮保持禁用，返回入口恢复可用。

### S2 按 Error 名称猜测是否发送

根因：账号变化等错误可能发生在 transport 前或 HTTP 完成后，旧分类仅看 Error 类型，存在删除已发送命令记录的风险。

修改：

- `DreamJourneyBackendClient.swift` 为审核 POST 贯通结构化 exposure：只有可靠证明未交 transport 才是 `notSent`。
- transport 已暴露后的断连、取消、超时、账号变化、401、解码失败或回执错配均为 `outcomeUnknown`，保留原主体 command。
- 明确业务拒绝与 committed 独立分类；审核 POST 仍禁止认证/策略自动重发。
- 恢复合法原主体后只调用 decision-result GET；新主体不能看到旧主体私有正文或回执。

### S3 本地 guard 让详情永久等待

根因：详情先进入 in-flight，candidate/Binding/gate/store 等本地提前返回只更新列表状态，没有完成对应详情意图。

修改：

- 新增轻量 review intent/event/outcome，将 intentID、candidateID、preview epoch 和最终分类绑定。
- 所有 guard、命令构造、pending store、transport、lookup 与持久成功出口统一恰好完成一次对应 UI 意图。
- 普通 notSent 恢复详情和返回；冲突需新预览；unknown 允许返回但禁止任何新审核写。
- 重复点击合并到原操作，不能用第二次失败回调解除第一次写的禁用状态。

### S4 版本追赶计数被清零

根因：snapshot-behind 计数在 `completeInboxRefresh` 中被清零，内部重读又被当成新意图，持续旧 revision 可无限追赶。

修改：

- 版本追赶预算留在同一读取意图中，内部重试沿用 trace、所有权和总体预算。
- 首次旧 revision 仅追加一次 GET；第二次仍旧时停止并显示版本变化，候选不可审核。
- 只有用户发起的新读取意图才重置预算；旧 attempt 不释放新 attempt 的 requestID。

### S5 未校验服务端实际 candidateVersion

根因：旧 binding 校验比较 command 与本地 pending version，却未要求服务端 receipt 的候选行版本等于 N+1。

修改：

- 正常 POST 与只读 found 均验证 `receipt.candidateVersion == expectedCandidateVersion + 1`。
- N、N+2、99 等正整数错版本不显示成功、不删除未决记录，进入受控不一致核实。
- 保留 candidate/action/beforeHash/proposal/activation、八操作、Binding 与 CAS 的既有严格校验。

### S6 策略恢复未走真实网络且预算分裂

根因：旧 T10 直接替换测试快照；生产策略 HTTP 没有完整继承候选读取的 root trace、attempt、deadline 和组合恢复预算，晚到低版本策略还可能覆盖新缓存。

修改：

- `DreamJourneyBackendClient.swift` 的 `CandidateInboxReadAttemptState` 统一 intent、root trace、attempt、deadline、一次 policy fetch、一次 auth recovery 和最多两次候选 GET。
- 策略恢复走真实 `fetchReleasePolicy`、decode、`ReleasePolicyStore.save`、重新 evaluator/revalidate，再发送候选 GET。
- 同 scope 策略请求单飞；超时释放所有权；账号切换/旧回调不能提交 UI。
- ReleasePolicyStore 更新增加 revision 单调保护，旧或冲突策略不能回滚较新缓存。

## 4. 红绿证据

| 证据 | 修复前 | 修复后 |
|---|---|---|
| S1/S3/S4/S5 四个核心反例 | `run-2026-09-13/evidence/red/S1-S3-S4-S5-red.xcresult`：0/4，失败断言分别是第二 command、详情无完成、自动 GET 超限、错版本被接受 | `run-2026-09-13/evidence/green/S1-S3-S4-S5-green.xcresult`：4/4 PASS |
| 关联组绕过未决单条 | `.../U02-U22-pending-scope.xcresult`：1/2，关联组仍可开始 | `.../U02-U22-pending-scope-after-fix.xcresult`：2/2 PASS |
| 晚到策略回滚 | `.../S6-policy-boundaries-valid-fixture.xcresult`：2/4，disabled/timeout 场景暴露旧策略覆盖 | `.../S6-policy-boundaries-after-monotonic-fix.xcresult`：4/4 PASS |
| S2 实际 transport 时序 | 修复在本轮第一个独立 S2 结果包保存前已落地，没有事后伪造红包 | `.../S2-S22-transport-storage.xcresult`：4/4 PASS；覆盖发送前、已暴露后账号变化、401、解码失败和存储保护 |

说明：早期两个策略结果包包含无效测试 fixture，不作为业务红测；U19 的首包失败是测试把本地预算拒绝 attempt=4 错当成已发网络请求，修正 oracle 后生产逻辑未放宽。完整说明见 [U01-U24 执行清单](U01-U24-执行清单.md)。

## 5. 自动化、UI 与编译

### iOS

- U01-U24 完整映射：[U01-U24 执行清单](U01-U24-执行清单.md)。
- OwnerTruth 完整回归：`run-2026-09-13/evidence/green/U01-U24-ownertruth-full-final.xcresult`，372/372 PASS。
- Echo/音频保持性：`run-2026-09-13/evidence/green/affected-audio-echo-regression.xcresult`，5/5 PASS。
- 真实策略链：`run-2026-09-13/evidence/green/S6-real-policy-chain.xcresult`，真实 Controller/UseCase/BackendClient/evaluator/store/URLProtocol 组合 PASS；观察到 policy 后 candidate 两次受控 HTTP 请求、同 trace、最终 attempt=2。
- UIQA 结果：`run-2026-09-13/evidence/uiqa/owner-truth-v5-final/owner-truth-candidate-related-group-uiqa-result.json`，全部布尔门禁为 true。
- UIQA 截图：`01-owner-truth-candidate-v5-detail.png`、`02-owner-truth-candidate-structured-group-preview.png`；实际显示候选版本、修订、前后值、时间、0.721 -> 0.724、证据、八操作/关联组确认边界。
- 模拟器构建：`run-2026-09-13/evidence/build/simulator-build.xcresult`，succeeded，0 error，51 warning。
- 通用 iOS 设备构建：`run-2026-09-13/evidence/build/generic-ios-build.xcresult`，succeeded，0 error，70 warning，`CODE_SIGNING_ALLOWED=NO`，未安装设备。
- 警告来自既有第三方依赖、空值标注和弃用 API；本轮未为消除无关警告扩大修改范围。

### Backend 与 PostgreSQL

- 定向：`run-2026-09-13/evidence/green/backend-review-targeted-unittest.log`，12 项 PASS。
- 完整：`run-2026-09-13/evidence/green/backend-full-gate.log`，2581 项 unittest 通过，后续合同、FastAPI、smoke 与 diff gate 通过。
- 隔离 PostgreSQL：沿用前轮同日证据 `2026-09-13-dreamjourney-b4-review-policy-recovery-fix/evidence/postgres/B4-PG-decision-result-green.log`。其 schema head=0121，使用随机测试库和合成 owner/vault/candidate，测试结束删除测试库，未连接生产。
- 当前 PG smoke、`app/main.py`、review service、API tests 和 PG contract test 的 SHA-256 与该次真实 PG 执行报告完全一致，因此该证据仍对应当前后端实现；本轮未以 Mock 替代 U23。

## 6. 指纹

### iOS 关键文件 SHA-256

- `OwnerTruthContracts.swift`：`0080af3d49b9c77a421ef5938246448fa0a67e518778948196065919ea0204b2`
- `MemoryArchiveViewController.swift`：`2bf102888c1082acdc91a076517d25d7ecdac5173f222c4d226b8b850df678a1`
- `DreamJourneyBackendClient.swift`：`71d6ca08795b29a2b270fcabaf71da4bf4b7c017a75f00b7034be0cc5073d143`
- `EchoViewController.swift`：`0be3df58611c62dc1a2f6bffd32e22ee21b5792f1c52230967f4960470fe686e`
- `OwnerTruthContractsTests.swift`：`cd9582186c7ad578fd3278f1087a8c60671a3a853a21b44261984c408494abdd`

### Backend 决定结果链 SHA-256

- `app/main.py`：`dbd2479f913762f07c1cebdfdf89db0e5907314f98ebc567811ba44cab45c55c`
- `owner_truth_candidate_review.py`：`bbbbe27e5fa45fb64ec77316256aa860877e5bc2cb01e70df45bb91eeb896c97`
- API tests：`e4461139616535fdf2c82e2a13afc4107ba80ede551684552c2605e676d01237`
- PG contract test：`6c890c40a690fce9538d3bc2d2745dafa6842322d623d6920769926e84e0dc1b`
- PG smoke：`ca4216f45ccdc7753e3aabf38a9604aa38d93de2daae0bca2898fccf9268d310`

## 7. 接口与部署判断

- decision-result 实际合同使用：`GET /v2/vaults/{vaultId}/candidates/{candidateId}/decision-result`，command 通过 `X-DreamJourney-Review-Command-Id` header 传递；未改回 query 参数，避免 command 进入 URL 访问日志。
- 本轮没有新增后端业务代码或迁移，因此本轮本身无需部署。
- 但前轮新增的 decision-result API 仍未部署。后续授权真机测试 unknown 分支前，必须对受控发布范围单独部署并验证该 API；不能把整个后端脏工作区未经核对一起发布。

## 8. 状态与残余风险

| 项目 | 状态 | 说明 |
|---|---|---|
| S1-S6 本地实现与验收 | PASS | 实际生产调用路径、UIKit/受控网络、完整回归和编译均通过。 |
| U01-U24 | PASS | 精确证据见执行清单；U23 使用当前代码同指纹的真实隔离 PG 证据。 |
| 原 T01-T27 | PASS | 本轮按实际断言复核，见 [T01-T27 复核映射](T01-T27-复核映射.md)。 |
| B4-1 至 B4-7 | PASS | 保留既有真机证据，本轮未重跑。 |
| B4-8 | FAIL | 仍需后续真机同场闭环。 |
| 连续审核第二条真实写入 | FAIL | 等待部署/安装授权后的 D3-D5。 |
| 首次策略过期自动恢复 | FAIL | 等待自然到期现场 D6，不能用本地 200 替代。 |
| 历史三条候选重复性 | NOT_RUN | 未访问生产候选。 |
| 新查询产生候选语义处理 | NOT_RUN | 不属于本轮局部修复。 |
| F3 生产历史来源与全面影响 | NOT_RUN | 本轮未读取或操作生产历史。 |
| decision-result API 生产状态 | NOT_RUN | 尚未部署。 |

残余风险：本地受控 transport 不能替代真实设备网络和生产策略自然轮换；API 未部署前，unknown 现场只能保持待核实，不能完成只读闭环。S2 缺少单独保存的修复前 xcresult，但修复后已用真实 BackendClient/URLProtocol 路径覆盖，报告未伪造历史红测。

## 9. 局部回退

- 仅回退本轮 iOS 审核未决互斥、intent completion、snapshot 预算、N+1 校验和策略恢复接线，不回退前序连续审核刷新、typed 差异、Binding/CAS、关联组原子提交、会后 checkpoint 或 Live 修复。
- 若策略自动恢复回归，可局部关闭该只读恢复并准确提示刷新失败；不得放宽 gate。
- 若审核路径异常，应停用受影响确认入口并保留 pending 记录；不得删除未决命令、自动重发 unknown 写或修改正式记忆恢复表面可用。

## 10. 结论

本轮 A 补充修复的本地门禁已满足：未知结果不能再次 POST，发送后账号变化不丢记录，本地拒绝不锁详情，旧版本追赶有上限，错版本回执不能成功，策略恢复经过真实网络/缓存且共享 trace 与预算。

准确状态为：`A_LOCAL_PASS / READY_FOR_B4_RETEST`。这不是 B4 整体通过；等待用户后续明确授权部署、安装和真机复测。
