# DreamJourney B4 R1-R3 最终边界补充修复报告

日期：2026-09-13\
交付状态：`A_LOCAL_PASS / READY_FOR_B4_RETEST`\
执行范围：仅本地代码修改、自动化验证、隔离 PostgreSQL、模拟器 UIQA 与本地编译\
未执行：生产部署、生产数据访问或修改、iPhone 安装与测试、Git commit/push

## 1. 结论

本轮独立核对后确认 R1、R2、R3 均为真实遗漏，不沿用上一轮 READY 结论直接放行。三个问题已补充失败反例、生产代码修复和配对绿测，相关 OwnerTruth、Echo/音频保持性、UIKit/UIQA、隔离 PostgreSQL、后端完整门禁及两类 iOS 构建均通过。

本轮本地门禁结论为：

| 项目 | 状态 | 结论 |
| --- | --- | --- |
| R1 关联组未知结果只能查询 | PASS | 可能已发送的组审核不再自动或手动补发；单条与关联组双向共享未决写保护；新增组级只读结果查询 |
| R2 账号失效后详情等待收尾 | PASS | 发送后、查询前及查询返回时账号失效均结束原详情等待，同时保留原主体未决记录 |
| R3 读取整体期限约束在途请求 | PASS | 到期只收尾一次并释放占用；迟到响应不能更新过期 UI、覆盖新请求或释放新所有权 |
| 本地综合门禁 | PASS | 详见第 6 节 |
| 生产部署 | NOT_RUN | 按要求未部署；单条及关联组 decision-result 合同尚未进入生产 |
| iPhone 复测 | NOT_RUN | 按要求未安装、未测试 |

因此可以进入下一阶段准备，但 **真机复测前必须先取得授权并部署本轮后端只读接口及对应路由变更**。未部署时，不能以本地 READY 代表生产已具备审核结果核实能力。

## 2. 基线与保护

### 2.1 代码基线

- iOS：`feature/prd-stitch-ui-adaptation`，HEAD `5fd061fd869edbe1fc13e8535a47880826581934`，相对远端 ahead 1。
- 后端：`main`，HEAD `be9670b6ec05e73ab9562943f402e5a9e1346988`，相对远端 ahead 4。
- 两个仓库进入本轮前均已有大量未提交修改。本轮没有 reset、revert、stash、清理或覆盖其他任务成果。
- 本轮没有改动 Live 音频生成、持续聆听、打断、正式记忆快照绑定及文字问答链路。

### 2.2 失败反例取证方式

主工作区在本轮开始时已有部分前序修改。为获得可复核的红绿证据，同时不破坏脏工作区，失败反例在 `/private/tmp` 的隔离当前源码副本中运行：每次只撤去对应 R1、R2 或 R3 的生产修复，保留同一测试和同一业务断言。主仓库、生产数据和旧证据均未改动。

最早的 `R1-unknown-group-write-red.xcresult` 仅证明接口合同尚不存在，属于编译合同红测，不作为业务缺陷通过证据。有效业务红测使用带 `-02` 的证据包。

## 3. R1：关联组未知结果只能查询

### 3.1 根因

1. 关联组审核写入沿用普通请求恢复语义，POST 交给传输层后若超时、断连或收到 401，仍可能再次发送。
2. 关联组 UseCase 没有持久化与 Proposal/Binding/作用域绑定的未决记录，重新进入页面后无法只读核实原命令。
3. 旧测试把暂时性失败后的第二次 POST 当作正确行为。
4. 单条与关联组未决写保护不对称，存在从另一审核入口绕过的可能。
5. 后端只有单条 command 的只读查询，缺少组级原子事务结果查询。

### 3.2 修改

#### iOS

- `OwnerTruthContracts.swift`
  - 新增 `OwnerTruthMemoryChangeSetGroupBinding`，把组 Proposal、成员与不可变审核上下文绑定。
  - 新增组审核传输结果：`notSent`、`outcomeUnknown`、`serverRejected`、`committed`。
  - 新增组未决记录及持久存储；未知结果保留原命令、作用域和 Binding，只允许只读查询。
  - 单条与关联组审核双向检查未决写；任一未决结果都阻止另一类审核写。
  - 明确未发送允许用户在同一不可变预览上主动重试；可能已发送绝不产生新 POST。
  - 组查询回执必须匹配原 Binding 和成员集合，`notObserved` 不解除未决保护。
- `DreamJourneyBackendClient.swift`
  - 关联组审核 POST 关闭认证刷新后的自动重发。
  - 根据是否已经交给传输层区分 `notSent` 与 `outcomeUnknown`。
  - 新增组级只读查询：`GET /v2/vaults/{vault_id}/memory-changeset-groups/decision-result`。
- `MemoryArchiveViewController.swift`
  - 未知结果显示“等待核实”，不提供再次提交动作；页面可安全返回。
  - UIQA 的可重试失败明确模拟为发送前 `notSent`，不再把发送后未知错误当作重试入口。

#### 后端

- `app/services/owner_truth_memory_changeset_group_review.py`
  - 新增无写副作用的 `lookup_result`，按 vault、主体、command 和组 Binding 返回组级事务结果。
- `app/main.py`
  - 新增组级 decision-result GET。
- `app/services/release_policy.py`、`app/services/route_ownership.py`
  - 接入既有审核能力门禁和路由归属，不放宽权限。
- `scripts/backend-owner-truth-memory-changeset-group-postgres-smoke.py`
  - 增加确认前 notObserved、确认后完整命中、错误 command notObserved、查询前后表计数不变验证。

### 3.3 红绿证据

- 修复前：`evidence/pre-fix/R1-unknown-group-write-red-02.xcresult`
  - 1 个测试失败，3 个业务断言失败。
  - 关键反例：发送后未知错误被当作可重试；再次操作产生第二个 POST，实际写次数 2，期望 1。
- 修复后：`evidence/post-fix/R1-R3-targeted-green-02.xcresult`
  - `testRelatedCandidateGroupUnknownOutcomeNeverRepostsReviewWrite` PASS。
  - `testRelatedCandidateGroupUnknownOutcomePersistsAcrossReentryAndBlocksSingleWrite` PASS。
  - `testRelatedCandidateGroupUnknownOutcomeCommitsOnlyAfterBoundGroupLookup` PASS。
  - `testRealHTTPRelatedGroup401NeverRefreshesOrRepostsAndUsesReadOnlyLookup` PASS。

## 4. R2：发送后账号失效时详情等待可靠收尾

### 4.1 根因

单条审核写已能返回 `outcomeUnknown`，但在进入只读查询前或查询回调返回时，如果账号租约已经失效，部分分支只更新候选列表状态后直接返回，没有完成发起该操作的详情页 intent。结果是按钮和返回入口持续禁用；与此同时不能丢弃原主体可能已写入的未决记录。

### 4.2 修改

- `OwnerTruthContracts.swift`
  - `receiveReviewTransportOutcome`、`lookupPendingReviewResult`、`receivePendingReviewLookup` 的账号失效出口统一调用 `finishReviewIntent`。
  - 保留原主体未决记录，不向新主体显示旧详情，不自动查询旧主体，也不补发审核写。
  - 使用候选、operation generation 和 request 身份隔离迟到回调，旧回调不能结束或解锁另一条候选详情。

### 4.3 红绿证据

- 修复前：`evidence/pre-fix/R2-account-loss-detail-wait-red.xcresult`
  - 2 个测试失败，6 个断言失败。
  - 覆盖发送后、查询前及查询在途时账号失效，详情等待未结束。
- 修复后：`evidence/post-fix/R1-R3-targeted-green-02.xcresult`
  - `testCandidateInboxUIKitEndsDetailWaitWhenAccountChangesAfterWriteBeforeLookup` PASS。
  - `testCandidateInboxUIKitEndsDetailWaitWhenAccountChangesWhileLookupIsInFlight` PASS。

## 5. R3：30 秒整体读取期限覆盖在途请求

### 5.1 根因

原读取期限主要阻止到期后创建新恢复请求，不能主动结束已经发出的候选 GET。若 GET 长期无回调，UseCase 的刷新所有权一直占用；旧响应晚到后还可能更新过期 UI 或影响新的刷新。

最终代码审计还发现一次较小竞态：最终读取上下文分别加锁读取 attempt 与各恢复计数，可能组合出不同瞬间的快照。

### 5.2 修改

- `DreamJourneyBackendClient.swift`
  - 新增 `CandidateInboxReadCompletionGate`，整个读取意图只有一个终态。
  - 整体期限到达后主动完成本次读取并释放 UseCase 占用，不等待系统网络超时。
  - 策略、认证恢复和候选 GET 共用同一读取意图、trace、单调 attempt 与恢复预算。
  - 所有迟到策略、认证和 GET 结果经过 completion gate 丢弃，不能提交过期 UI、覆盖新 attempt 或释放新请求所有权。
  - `CandidateInboxReadAttemptState.context()` 在同一锁内一次性捕获 attempt、GET、策略与认证恢复计数，保证最终上下文原子一致。
- `OwnerTruthContracts.swift`
  - 保留 requestID 所有权和有界尾随刷新；到期后新的合法刷新可以独立发送。

### 5.3 红绿证据

- 修复前：`evidence/pre-fix/R3-inflight-deadline-red.xcresult`
  - 1 个测试失败，7 个断言失败。
  - GET 已发出但不返回时，整体期限没有收尾；旧响应能影响后续状态。
- 修复后：
  - `evidence/post-fix/R1-R3-targeted-green-02.xcresult`：`testCandidateInboxOverallDeadlineFinishesInFlightReadAndLateResponseCannotOwnRefresh` PASS。
  - `evidence/post-fix/R3-atomic-context-green.xcresult`：最终上下文原子快照复核 1/1 PASS。

## 6. 本地验证

### 6.1 iOS 定向与完整回归

环境：Xcode 当前本机工具链，iPhone 17 Pro 模拟器，iOS 26.5。

主要命令形式：

```bash
xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5' \
  test -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath <evidence-path>
```

| 验证 | 状态 | 证据 |
| --- | --- | --- |
| R1–R3 定向组合 | PASS | `evidence/post-fix/R1-R3-targeted-green-02.xcresult`，7/7 |
| R3 原子上下文复核 | PASS | `evidence/post-fix/R3-atomic-context-green.xcresult`，1/1 |
| OwnerTruth 完整回归 | PASS | `evidence/post-fix/OwnerTruth-full-regression-02.xcresult`，378/378 |
| 受影响 Echo/音频保持性 | PASS | `evidence/post-fix/affected-audio-echo-regression.xcresult`，5/5 |

### 6.2 真实模拟器 UIKit/UIQA

| 场景 | 状态 | 证据 |
| --- | --- | --- |
| 候选列表与结构化详情 | PASS | `evidence/post-fix/candidate-inbox-uiqa/20260913-134100/` |
| V5 typed 差异、时间与可信度精度、关联组预览 | PASS | `evidence/post-fix/v5-detail-uiqa/20260913-134147/` |
| 组审核明确未发送后的安全重试、冲突与成功状态 | PASS | `evidence/post-fix/group-resilience-uiqa-02/20260913-133836/` |

首次组 resilience UIQA 证据 `group-resilience-uiqa/20260913-133736/` 为 FAIL，暴露了明确未发送时错误废弃同一预览/command 的问题；修正后使用 `-02` 目录的相同场景通过。旧失败证据已保留。

### 6.3 隔离 PostgreSQL + pgvector

执行脚本：`scripts/backend-owner-truth-memory-changeset-group-postgres-smoke.py`，连接一次性隔离 PostgreSQL，未使用生产数据库。

结果：`evidence/post-fix/postgres-group-decision-result.log`，PASS。

已验证：

- 121 个迁移可应用；pgvector 0.8.6 可用。
- 组审核提交原子性；故障注入无部分写入。
- 幂等重放不产生额外版本或副作用。
- 并发同组审核仅一个 created，另一个 conflict。
- 确认前查询 notObserved，确认后命中同一组回执和成员。
- 错误 command 为 notObserved。
- 所有结果查询前后业务表计数不变，证明查询零写副作用。

### 6.4 后端完整门禁

命令：`./scripts/verify_backend.sh`

- 首次：`evidence/post-fix/backend-full-gate.log`，FAIL。
  - 新增组查询路由后，5 个路由总数断言仍为旧值；这是新增合同引起的真实完整门禁失败。
- 修正路由登记与对应断言后：`evidence/post-fix/backend-full-gate-02.log`，PASS。
  - Python 单元测试 2581/2581 通过，后续合同、迁移、脚本及 diff 门禁全部结束为成功。
  - 日志中的 `synthetic evaluator failure` 是既有故障注入用例的预期诊断，不是门禁失败。

### 6.5 编译与差异检查

| 验证 | 状态 | 证据 |
| --- | --- | --- |
| iOS 模拟器构建 | PASS | `evidence/post-fix/simulator-build-02.xcresult` |
| 通用 iOS 设备目标构建 | PASS | `evidence/post-fix/generic-ios-build-02.xcresult` |
| iOS `git diff --check` | PASS | 无空白错误 |
| 后端 `git diff --check` | PASS | 无空白错误 |
| 输出及本轮改动敏感信息扫描 | PASS | 未发现真实令牌、密钥、正文或转写；仅命中测试占位符及标识符 |

构建警告仅来自 Kingfisher 的 Swift 6 空白提示及第三方 SpeechEngineToB 符号信息，不是本轮业务或编译错误。

## 7. 指纹

### 7.1 iOS 源码 SHA-256

- `OwnerTruthContracts.swift`: `52f143e5870062a1bcb39e11d2e90ca9915e1f0b69f61ee0c96113b4b9b245b9`
- `DreamJourneyBackendClient.swift`: `4239b2b76eedc84b1126d6ccc2f1e3c28a7ba308049865cf657d747d3c56d288`
- `MemoryArchiveViewController.swift`: `f44b52cb9dede8775215ad65c2999a7deed525ea41d3e00ad848350109e10c22`
- `OwnerTruthContractsTests.swift`: `606c5c508c65e1e7604fef9499015d193a92496089f4c041d693d628a8f756b5`

### 7.2 后端源码 SHA-256

- `app/main.py`: `4242fc079bba69b8c26b8f85c4c882bb546ef7b8e85de204f6bd9edf724cb712`
- `owner_truth_memory_changeset_group_review.py`: `e659d3a25ee19a8b1ef338d794b8e57269e8c6492dbcf4886d89e6e50f898380`
- `release_policy.py`: `e7eb0c8ab21c39ce0db294f4859eb84c76ebdc0d0693260f14a75c4e22651191`
- `route_ownership.py`: `5fd33d215ed6eb6dba15bd4038d2d9fe3abab6d1aa2b4f07669ca2d5ffa61abb`
- PostgreSQL 组审核 smoke：`f5668fabf6d75b7d14e1e696f74aad23540e62445300eadb05705bc6bf2a20b2`

### 7.3 构建产物 SHA-256

- 模拟器 DreamJourney：`3ca18528808498a75c8087cb9b06948360eef2232434f42313a51d21ce9e12b8`
- 通用设备 DreamJourney：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 8. 总体验收状态

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| R1 | PASS | 本地代码、受控真实客户端、UIKit 和隔离 PG 闭环 |
| R2 | PASS | 三个账号失效窗口的详情收尾与未决记录保护已验证 |
| R3 | PASS | 在途 GET 整体期限、迟到隔离、新刷新所有权及原子上下文已验证 |
| B4-1 至 B4-7 | PASS | 保留此前已取得的真机证据，不用本轮本地测试替代 |
| B4-8 | FAIL | 仍需部署后真机会后状态、真实候选读取和来源关联联合证据关闭 |
| 连续审核第二条真实写入 | FAIL | 等待后续真机复测 |
| 首次策略过期现场自动恢复 | FAIL | 等待自然或受控真机场景复测 |
| 历史三条候选重复性 | NOT_RUN | 本轮未访问生产候选 |
| 新查询产生候选的语义处理 | NOT_RUN | 本轮未操作生产数据 |
| F3 历史检查点实际影响 | NOT_RUN | 保持前轮未验证结论 |
| 单条 decision-result 生产接口 | NOT_RUN | 前轮已实现但尚未部署 |
| 关联组 decision-result 生产接口 | NOT_RUN | 本轮新增但尚未部署 |

## 9. 部署依赖与下一步

本轮后端存在业务合同变更，因此真机复测前需要另行授权并受控部署：

1. 单条 decision-result API 的前轮未部署变更。
2. 本轮关联组 decision-result API、release policy 映射及 route ownership 更新。
3. 与已验证源码一致的 API 构建物；无需新增数据库迁移。
4. 部署后先做健康、路由鉴权、只读零副作用及版本指纹检查，再覆盖安装已验证 iOS 版本。

未部署前，不执行真实未知结果核实或连续审核复测，以免客户端调用不存在的生产合同。

## 10. 残余风险

- 受控 URLProtocol 可以证明客户端不会重发和状态机边界，但不能替代生产网络中真实超时发生率的观测。
- 本地 PostgreSQL 已证明组级查询和事务合同，生产组件尚未部署，当前生产行为仍是旧版本。
- 真机 B4-8、连续第二条审核和首次策略过期现场恢复仍没有新证据，维持 FAIL。
- 工作区包含大量其他任务的未提交修改；后续部署必须按文件和构建指纹核对，不能直接把“当前所有脏改动”笼统当成本轮发布范围。

## 11. 局部回退方案

如本轮修改需要回退，只回退以下本轮边界变更，不重置仓库、不覆盖其他脏文件：

- iOS：组级 Binding/未决记录/只读查询接线、R2 详情 intent 收尾、R3 completion gate 和原子上下文。
- 后端：组级 decision-result GET、service lookup、发布策略和路由归属登记，以及对应测试/PG smoke。

回退后必须同时撤销客户端组查询调用与后端路由，避免合同半发布；不得回退前序 typed 差异、精度、八操作、CAS、哈希、单条未决写保护及 Live 体验修复。

## 12. 最终停止点

本轮三个遗漏及本地门禁已闭环：`A_LOCAL_PASS / READY_FOR_B4_RETEST`。

当前停止，不部署、不安装 iPhone、不开始真机测试、不 commit/push。等待用户另行授权后，先部署 decision-result 后端合同，再进入原 B4 真机复测步骤。
