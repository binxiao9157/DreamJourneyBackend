# DreamJourney B4 “陈鑫更正为晨星”真机失败记录

- 记录时间：2026-09-13 22:31 CST
- 设备：Minge 的 iPhone，iOS 26.4.1
- Bundle：`com.gaominge.dreamjourney.app`
- 运行配置：生产 API，本轮未启用 QA 候选或故障注入
- 数据边界：用户亲自更正合成测试候选；未确认写入，未修改其他生产记忆

### 版本基线

- iOS 分支：`feature/prd-stitch-ui-adaptation`，相对远端 ahead 1。
- iOS HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`。
- iOS 工作区：存在前序 B4/Live 未提交修改和未跟踪测试资产，本轮未覆盖、回退或修改。
- 真机安装二进制 SHA-256：`518028449b4c88c800302df6ef67403f44be15b7a817fd1b92f94b4f5103e169`。
- App：`1.0.0 (1)`，SDK platform 26.5，minimum iOS 15.0。
- 生产 API 镜像：`dreamjourney-live-b1:20260911-0200`。
- 生产 API image ID：`sha256:7c355bafeb9a0807abcddfd69bc1d46cf7a64cb90ad81334bc2ed1317a636bea`，当时 `running/healthy`。
- 本地后端 HEAD：`be9670b6ec05e73ab9562943f402e5a9e1346988`，相对远端 ahead 4，存在未部署改动。因此“生产返回合同与当前 iOS 预期是否同版”必须作为实际核查项，不能先验认定为根因。

## 1. 测试操作与现象

1. 候选列表真实刷新：40 条 -> 40 条，“陈鑫”候选存在，页面无错误或卡顿。
2. 打开候选详情：
   - 基于正式记忆第 69 次修订快照。
   - 确认后内容：“用户说本次记忆系统测试使用的信息中，其测试阅读清单代号是陈鑫”。
   - 记忆线索完整，无异常提示，三个审核按钮可用。
3. 选择“更正后确认”，更正编辑页能够打开；用户仅将“陈鑫”改为“晨星”，未修改结构化线索。
4. 更正编辑页展示：
   - 更正前：“用户说本次记忆系统测试使用的信息中，其测试阅读清单代号是陈鑫。”
   - 更正后：“用户说本次记忆系统测试使用的信息中，其测试阅读清单代号是晨星。”
   - 未显示结构化字段变化；本次仅修改正文，该现象本身不单独判定为缺陷。
5. 点击“提交更正并确认”后失败，真正的“核对更正方案”二次确认页未打开，页面显示：
   - “更正方案未生成，未写入任何正式记忆。请重新载入后再试。”

## 2. 客户端证据

- 候选列表刷新 trace：`sha256:c5378fda51069041`
- 链路：`requestCreated -> taskCreated -> taskResumed -> HTTP 200 -> JSON decoded -> V5 typed decode -> UI ready`
- 解码候选数：40。
- 更正失败时，页面最终进入 `uiCommitted phase=failed`。
- 现有 CandidateInbox 日志没有为更正预览单独输出解码或 Binding 校验的白名单失败原因，无法仅凭设备日志区分解码失败、候选 Binding 不匹配、正式记忆修订不匹配或 reviewability 失败。

## 3. 服务端只读证据

- 2026-09-13 22:26:26 CST，服务端收到：
  - `POST /v2/vaults/<redacted>/candidates/8049A048-AE1A-5296-9386-E9EDAD367500/changeset-preview`
  - HTTP 200
- 因此本次不是简单的“请求未发送”或“服务端 HTTP 失败”。
- 现有证据支持的失败范围：客户端收到 HTTP 200 后的 JSON/V5 合同解码、Candidate/Proposal/Binding 一致性、正式记忆修订绑定或 reviewability 校验。

## 4. 验收状态

| 项目 | 状态 | 说明 |
|---|---|---|
| 候选列表真实刷新 | PASS | GET 200，40 条解码并提交 UI |
| 原候选详情展示 | PASS | 修订号、正文和线索可见 |
| 更正内容编辑 | PASS | “陈鑫 -> 晨星”显示正确 |
| 更正 ChangeSet 预览接受 | FAIL | 服务端 HTTP 200，客户端仍显示方案未生成 |
| 最终确认写入 | NOT_RUN | 依赖预览通过，本轮停止 |
| 候选移除/正式记忆修订 | NOT_RUN | 未写入 |
| 投影/向量 | NOT_RUN | 未写入 |
| 文字与新 Live 回查 | NOT_RUN | 未写入 |

## 5. 当前结论与下一步

- B4 仍为 **FAIL**，不能关闭。
- 不应点击普通“确认并纳入正式记忆”，否则会绕过“陈鑫 -> 晨星”的用户更正意图。
- 需先对 `previewCorrection` 的 HTTP 200 后合同解码和四项绑定校验增加脱敏原因证据，用相同生产响应形状建立失败反例后局部修复。
- 当前生产仍未部署单条和关联组两个 decision-result 查询接口，后续真机未决写恢复仍受此依赖限制。

## 6. 当前代码的失败出口

当前 iOS 的 `previewCorrection` 会在以下层次将结果转为统一的页面失败文案：

1. 请求前：账号租约/权限、Candidate 不存在、非 V5、更正内容无效。
2. BackendClient：发布策略拒绝、网络或 HTTP 失败、缺失 `proposedChangeSet`、V5 Proposal 解码失败。
3. UseCase 回调：
   - `operationGeneration` 变化。
   - commit 时账号租约无效。
   - 当前 Candidate 版本或 content hash 变化。
   - Proposal 的 candidateVersion、candidateContentHash、baseMemoryRevision 或 reviewability 与当前展示上下文不匹配。
4. UIKit：上述任一 `.failure` 都被折叠为“更正方案未生成”，本次现场日志未记录白名单 reason，所以尚不能在四项 Proposal Binding 校验中精确指定哪一项失败。

相关代码位置：

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:17035`
- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9369`
- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift:8990`

## 7. 已证实、已排除与待验证

### 已证实

- 候选列表是生产 GET 的真实解码结果，不是只显示本地缓存。
- 更正预览 POST 已进入服务端并获得 HTTP 200。
- 客户端未打开最终“核对更正方案”页，也未执行审核写入。
- 用户的更正意图为“陈鑫 -> 晨星”，不应退化成普通接受原候选。

### 已排除

- 不是单纯无网、DNS 失败或请求未创建。
- 不是预览接口的 HTTP 4xx/5xx。
- 不能用“列表有 40 条”或“HTTP 200”代替 Proposal 合同与 Binding 验收。

### 待验证假设（不得直接写成根因）

1. 生产 `dreamjourney-live-b1:20260911-0200` 的 Proposal payload 与当前 iOS 未提交 V5 解码合同不同版。
2. Proposal 可解码，但 candidateVersion 或 candidateContentHash 与列表中展示 Candidate 不匹配。
3. Proposal 的 baseMemoryRevision 与列表的 `memoryRevision=69` 不匹配。
4. Proposal 解码成功，但结构完整性规则将它标记为 unreviewable。
5. 预览在途期间 `operationGeneration`、账号租约或列表上下文发生变化，导致返回后被旧结果隔离。

## 8. Astra 需要交付的分析与设计

1. 先用当前生产响应的脱敏结构特征复现失败，明确是 BackendClient 解码失败还是 UseCase Binding 校验失败。
2. 对每一个失败出口设计白名单、脱敏、可关联的 reason 日志；不记录正文、凭据、完整请求响应或原始 hash。
3. 如属前后端合同版本不匹配，给出双端最小修复、发布顺序、兼容窗口和回退方案；不得放宽 Binding/CAS/reviewability 来兼容旧响应。
4. 如属客户端上下文失效，设计 Candidate/Proposal/Binding 不可变所有权和仅读重新获取流程，不得自动审核写入。
5. 先红后绿，至少覆盖：当前响应形状、版本/hash/revision/reviewability 四项错配、操作代次变化、账号租约变化、真实 UIKit 编辑页到二次预览页。
6. 说明是否需要后端改动与部署，以及两个尚未部署 decision-result 接口的依赖关系。
7. 保持 B4-8、更正写入、投影、向量及文字/Live 回查为 FAIL/NOT_RUN，直到新的真机证据闭环。
