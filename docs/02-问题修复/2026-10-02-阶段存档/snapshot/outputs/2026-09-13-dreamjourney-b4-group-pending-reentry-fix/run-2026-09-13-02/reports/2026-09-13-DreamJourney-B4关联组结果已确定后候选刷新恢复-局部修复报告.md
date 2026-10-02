# DreamJourney B4 关联组结果已确定后候选刷新恢复局部修复报告

日期：2026-09-13\
状态：`A_LOCAL_PASS / READY_FOR_DEPLOYMENT`\
范围：仅 iOS 关联组审核结果已确定、磁盘未决记录已移除后，候选列表刷新失败的恢复入口。

## 1. 边界与基线

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- 基线 HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- Xcode：26.6（17F113）
- macOS：26.6.2（25G83）
- 模拟器：iPhone 17 Pro，iOS 26.5，UDID `67D3337E-0623-4578-9479-31F4CD9033DA`
- 工作区原本包含多项未提交 B4、Echo 和后端修改。本轮没有重置、回退或覆盖这些成果。
- 本轮实际修改文件只有：
  - `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - `DreamJourneyTests/OwnerTruthContractsTests.swift`
- 未部署、未安装或测试 iPhone、未访问生产数据、未提交或推送 Git。

## 2. 已证实根因

`recoverPendingReviewResultIfNeeded()` 原先只返回布尔值。当关联组结果已经由只读
`decision-result` 查询确认为 `found` 后，客户端会安全删除磁盘未决记录、保存最低
正式记忆版本，并设置 `requiresFreshInboxAfterGroupResolution`。

若随后候选 GET 失败，用户点击“核实结果”或页面重新进入时，恢复函数扫描到磁盘记录
为空，只把 `groupPendingRecoveryState` 改成 `refreshing` 后返回。调用方把该返回值理解成
“恢复已处理”，因此没有进入现有 `refresh()` 候选读取流程。页面出现了正在刷新状态，
网络却没有新的候选 GET。

修复前真实失败序列：

1. `GET /memory-changeset-groups/decision-result` -> `found`
2. `GET /candidates` -> 失败
3. 点击“核实结果”
4. 没有第 3 个 GET；页面只改变本地状态

该问题不是服务端写入失败，也不是仍需重复查询已删除的审核结果。

## 3. 修改内容

### 3.1 明确恢复分流

在 `OwnerTruthCandidateReviewUseCase` 内增加内部恢复结果：

- `handled`：仍有未知审核结果，继续只读查询原 command。
- `requiresFreshInbox`：审核结果已确定，只需读取新候选快照。
- `none`：没有待恢复工作。

`resumePendingReviewResultIfNeeded()` 与 `refresh()` 依据该结果分流，不再把“磁盘记录已
删除但候选快照仍未刷新”误判为已经处理完毕。

### 3.2 复用既有候选读取链

新增 `resumeFreshInboxAfterGroupResolutionIfNeeded()`，只在：

- `requiresFreshInboxAfterGroupResolution == true`
- 当前没有有效候选读取在途

时调用既有 `refresh(checkPendingResults:false, continuingReadContext:...)`。因此继续复用：

- 账号租约和 vault 作用域
- FeatureGate 与策略恢复
- 同一 trace、单调递增 attempt
- 最低正式记忆版本保护
- 读取单飞、尾随刷新和旧回调隔离

该路径不调用 preview、confirm 或审核 POST，也不会重新查询已经移除的关联组未决记录。

### 3.3 失败状态准确收尾

`receiveInbox` 对以下分支统一保留“仍需新候选快照”的保护，并回到可重试的
`unresolved` 状态：

- 请求失败或超时
- 回调代次失效
- vault 不匹配
- 响应缺少必须的 `memoryRevision`
- 低于已确认提交版本且有界重试耗尽
- 候选标识重复

只有取得合法、版本不低于 `minimumKnownMemoryRevision` 的新快照后，才清除
`requiresFreshInboxAfterGroupResolution` 和审核保护。旧 Proposal、Binding、选择与确认
闭包不会恢复。

## 4. 红绿证据

### 4.1 修复前红测

- 测试：`testResolvedGroupInboxFailureVerifyActionRetriesCandidateReadWithoutRelookupOrWrite`
- 结果：1 FAIL / 0 PASS
- 证据：`evidence/pre-fix/resolved-group-inbox-retry-red.xcresult`
- 断言：点击“核实结果”后等待真实第三个候选 GET，修复前超时失败。

### 4.2 修复后核心组合测试

结果：5 PASS / 0 FAIL\
证据：`evidence/post-fix/resolved-group-inbox-retry-targeted-v3.xcresult`

覆盖：

1. `found -> candidate GET 失败 -> 点击核实 -> candidate GET 成功`。
2. 同一失败状态销毁旧页面并从正常入口重建，自动恢复候选读取。
3. 重试再次失败时显示明确失败，释放读取占用，下一次仍可重试。
4. 连续点击、页面重新进入与在途读取交错时保持单飞，不产生读取风暴。
5. 低版本响应不解除保护；同一 trace 的 attempt 按 `1 -> 2 -> 3` 递增，达到最低版本后才恢复页面。

共同断言：审核 POST=0、confirm=0、重新 preview=0；关联组 decision-result 在 `found`
后不再重复查询；旧结果不能覆盖新读取。

UIKit 截图与可访问性证据：

- `evidence/post-fix/ui-attachments-v2/0F9A6AED-7964-4575-BC21-429B28F6E8AD.png`
- `evidence/post-fix/ui-attachments-v2/0273A93D-24DE-4394-A50F-F0F9C7959D22.txt`
- `evidence/post-fix/ui-attachments-v2/manifest.json`

## 5. 回归与构建结果

| 项目 | 状态 | 结果与证据 |
| --- | --- | --- |
| 局部 UIKit + UseCase + BackendClient + URLProtocol | PASS | 5/5；`resolved-group-inbox-retry-targeted-v3.xcresult` |
| 受影响 G01-G16 与新增恢复场景 | PASS | 17/17；`G01-G16-plus-fresh-inbox.xcresult` |
| G02 页面对象/磁盘恢复 | PASS | 本轮全量回归重新执行；此前分离测试进程并关停、重启模拟器的证据继续保留在 `run-2026-09-13-01/evidence/post-fix/G02-seed-before-process-restart.xcresult` 与 `G02-recover-after-simulator-restart.xcresult` |
| OwnerTruthContracts 完整回归 | PASS | 397/397；`OwnerTruthContracts-full.xcresult` |
| Echo/音频受影响保持性 | PASS | 80/80；`Echo-Audio-affected-regression.xcresult` |
| 音频租约定向保持性 | PASS | 5/5；`Echo-Audio-retention.xcresult` |
| iOS 模拟器构建 | PASS | 0 error，1 个既有 Kingfisher Swift 6 提示；`build-simulator.xcresult` |
| 通用 iOS 设备目标构建 | PASS | 0 error，6 个既有依赖/弃用提示；`build-generic-ios.xcresult` |
| `git diff --check` | PASS | 无空白错误 |
| 后端测试/PostgreSQL | NOT_RUN | 本轮无后端合同、事务或数据修改，不扩大范围 |
| 生产部署 | NOT_RUN | 按用户边界暂停 |
| iPhone 安装及真机复测 | NOT_RUN | 按用户边界暂停 |

## 6. 源码与构建指纹

- `OwnerTruthContracts.swift`：`c02d947c62e830f653dbad8b8a3bf39ebadf55fa2a37f5d8fb7bf45ecef6a16e`
- `OwnerTruthContractsTests.swift`：`ffee6d85e86faa77ce9b93a1fa839eb28a4571f60b1763ff91603af5d5ff679a`
- 模拟器主程序：`3047fc71511c7792fa7daddbdee0a934ca9cdc83290376655810bdb7a54196aa`
- 通用 iOS 设备主程序：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 7. 尚未关闭的项目

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 两个 decision-result 接口生产部署 | NOT_RUN | 单条及关联组只读查询接口仍需后续部署授权；本轮不能据本地通过宣称真机接口可用 |
| B4-8 | FAIL | 保留现场状态，等待部署后同场会后保存、真实候选读取及来源关联联合证据 |
| 连续审核第二条真实写入 | FAIL | 未进行生产审核写入 |
| 首次策略过期现场自动恢复 | FAIL | 未进行真机生产连接验证 |
| 历史三条候选重复性 | NOT_RUN | 未访问或处理生产历史 |
| 新查询产生候选的语义处理 | NOT_RUN | 未进行生产数据操作 |
| F3 对生产历史的实际影响 | NOT_RUN | 保留待验证，不据本地测试推断 |

因此本轮局部修复达到 `A_LOCAL_PASS`，可进入后续受控部署准备；在两个
decision-result 接口完成部署及部署后验证前，不标记整体 `READY_FOR_B4_RETEST`，更不
宣称 B4 已通过。

## 8. 残余风险与回退

残余风险主要在生产环境：接口尚未部署，真机网络、真实账号合法凭据更新及生产候选
快照版本尚未取得本轮证据。

局部回退仅需撤销本轮在 `OwnerTruthContracts.swift` 中的三态恢复分流、候选刷新恢复
入口和失败收尾，以及 `OwnerTruthContractsTests.swift` 中对应五个测试。不得回退整个
脏文件，不得破坏前序关联组磁盘恢复、未决写保护、typed 差异、数值精度、八操作、
CAS、Binding、认证恢复或诊断链路。
