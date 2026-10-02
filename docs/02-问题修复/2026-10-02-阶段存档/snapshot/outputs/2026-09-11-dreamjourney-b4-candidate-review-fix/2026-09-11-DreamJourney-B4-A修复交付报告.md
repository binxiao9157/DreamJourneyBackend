# DreamJourney B4 候选审核页 A 阶段修复交付报告

日期：2026-09-11\
执行范围：设计文档 A 阶段，本地开发、自动化、模拟器 UIQA 和后端既有合同回归\
阶段结论：`READY_FOR_B`\
整体结论：B4 仍为 `FAIL`；修复版尚未进行真机候选审核和正式记忆写入闭环。

## 1. 执行边界

- 完整读取设计文档、原问题记录和截图，并核对当前 iOS、后端代码及未提交修改。
- 保留其他任务已有修改，未 reset、未回退、未覆盖无关文件。
- 未确认或拒绝生产候选，未修改正式记忆，未清理历史，未重放 Dead Letter。
- 未修改火山原生 Live、连续聆听、打断、`sessionSnapshot` 或文字问答语音边界。
- 未 commit、未 push、未安装或测试 iPhone。
- 本轮没有后端代码或迁移变化，因此未进行后端部署。

## 2. 根因与修复

### 2.1 占位符直接显示

**根因**：审核详情页的六处文案使用了被转义的插值写法，运行时展示变量名而非值。

**修复**：保留并验证六处修正，覆盖正式记忆修订号、目标版本、确认前内容、确认后内容、字段变化和关联数量。验证不只停留在 helper，而是进入真实 UIKit 详情页。

### 2.2 owner-truth-v5 线索被误判为不支持

**根因**：候选合同已升级到 `owner-truth-v5`，客户端 facets resolver 仍只接受旧 schema。

**修复**：V5 使用保留的 facets envelope 解析并展示人物、地点、关系、情绪等线索；同时区分有内容、合法空值、缺失、畸形和未来版本，不以伪造空数组表示成功。

### 2.3 ChangeSet 语义和异常边界不完整

**根因**：未知 operation 会回退为泛化“更新”，缺 target 的操作可能被显示为“新建”；复杂 after 结构没有可读校验。

**修复**：对八种操作 `add`、`addEvidence`、`refine`、`temporalChange`、`correct`、`dispute`、`duplicate`、`noPersonalFact` 建立精确语义。未知类型、缺失目标、缺失必要 before/after 或不可读结构一律关闭确认入口；合法“无个人事实”和“重复”则给出明确非新增说明。

### 2.4 展示内容与提交内容可能漂移

**根因**：页面动作主要按 candidate ID 回查当前 ViewState。用户看见方案 A 后，列表刷新为方案 B 时，旧详情页可能提交新方案。

**修复**：增加不可变 `OwnerTruthCandidateReviewBinding`，绑定 candidate ID/version/content hash、proposal hash、changeSet ID 和 base revision。确认、更正、拒绝均提交页面实际展示的 binding；任一字段漂移、账号切换或预览过期都会 fail closed，并要求重新预览。

### 2.5 正文更正会升级未修改的推断线索

**根因**：旧逻辑会把页面中所有 facet 文本重新提交为 Owner 更正，即使用户只改正文，也可能把系统推断项改成“本人表达”。

**修复**：仅提交用户实际修改的 facet 组。未修改的 inferred 值、证据和整体 confidence 保持原样；V2/V4 的兼容行为保留在旧合同分支。

### 2.6 多操作和关联组展示不完整

**根因**：更正预览只显示前三项，关联候选成员只展示第一项 operation，但确认时会提交完整 ChangeSet。

**修复**：页面展示全部 operation、全部字段差异和全部关联成员；2 条和 3 条关联候选数量准确，长内容和小屏可滚动到绑定后的确认按钮。

## 3. 代码变化

### iOS 实现

1. `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
   - `ownerCorrectedJSONValue(...)`：仅更正发生变化的 facet，保留 V5 未改推断属性。
   - V5 facets resolver：支持 `owner-truth-v5`，严格区分空、缺失、畸形和未来 schema。
   - ChangeSet reviewability：八种操作及字段差异的可读性/完整性校验。
   - `OwnerTruthCandidateReviewBinding`：固定展示快照与提交请求。
   - review use case：提交前再次核对 binding 和 proposal 可审核性。

2. `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
   - 保留六处真实字符串插值。
   - 展示全部 ChangeSet operation、字段差异和关联成员。
   - 未知或畸形 proposal 关闭确认按钮并显示原因。
   - V5 facets 正常展示；更正只提交实际变化。
   - `correctionPreviewGeneration` 丢弃旧的异步更正预览结果。
   - 增加 V5 详情页和小屏滚动 UIQA 入口。

### 测试与工具

3. `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`
   - 增加 V5 真实 UIKit、八操作、畸形合同、绑定漂移、乱序、更正属性、多操作、2/3 条关联、小屏滚动等回归。

4. `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/Scripts/QA/prd-stitch-ui/run-owner-truth-candidate-v5-detail-smoke.sh`
   - 新增 HTTP-shaped V5 fixture 到真实详情页、关联预览和本地收据链路的模拟器烟测。

后端代码未修改。

## 4. 测试环境与结果

### 4.1 iOS 合同与 UIKit 测试

环境：Xcode 26.6（17F113），iPhone 17 Pro Simulator，iOS 26.5。

执行方式：

```bash
xcodebuild test \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests
```

结果：`295/295 PASS`，无失败、无跳过。

原始证据：

- `owner-truth-full-regression-final.xcresult`
- `owner-truth-full-regression-final-tests.json`

### 4.2 V5 真实页面 UIQA

结果：`PASS`。

已验证：真实修订号、服务端 after、证据摘要、V5 facets、关联数量、完整操作、确认按钮可达。

证据：

- `uiqa-v5-detail/normal/owner-truth-candidate-related-group-uiqa-result.json`
- `uiqa-v5-detail/normal/01-owner-truth-candidate-v5-detail.png`
- `uiqa-v5-detail/small-large-text/owner-truth-candidate-related-group-uiqa-result.json`

小屏/大字环境：iPhone 17e Simulator，iOS 26.5，`accessibility-medium`。文本无重叠，页面可滚动到操作区。

### 4.3 兼容和恢复测试

结果：`PASS`。

- V2 页面回归：`uiqa-v2-regression/regression/owner-truth-candidate-inbox-uiqa-result.json`
- 关联组恢复：`uiqa-related-resilience/regression/owner-truth-candidate-related-group-resilience-uiqa-result.json`
- 已覆盖重复点击抑制、稳定重试、冲突后强制重预览、命令轮换和原子应用一次。

### 4.4 后端既有合同回归

环境：项目 `.venv`，`STORE_BACKEND=memory`；本项验证既有 API/ChangeSet/Review 合同，本轮没有后端变更。

```bash
env PYTHONDONTWRITEBYTECODE=1 STORE_BACKEND=memory ./.venv/bin/python -m unittest \
  tests.test_owner_truth_memory_changeset \
  tests.test_owner_truth_memory_changeset_review \
  tests.test_owner_truth_memory_changeset_group_review \
  tests.test_owner_truth_candidate_review \
  tests.test_owner_truth_candidate_review_api
```

结果：`36/36 PASS`。证据：`backend-36-regression.txt`。

说明：本测试不是生产 PostgreSQL 写入证明；生产写入属于 B 阶段，当前保持 `NOT_RUN`。

### 4.5 构建与静态检查

- iOS `build-for-testing`：`PASS`。
- `git diff --check`：`PASS`。
- 本轮未安装真机。

## 5. 构件指纹

| 构件 | SHA-256 |
|---|---|
| `OwnerTruthContracts.swift` | `ac142d61fc88a38e9ef6185b0757eadac217135d296f74b1c60c776587c1241c` |
| `MemoryArchiveViewController.swift` | `ec47e8df8540e4517b92b5decdeac2e0f4d4dfff6bd817116ac1dacae28c6a7c` |
| `OwnerTruthContractsTests.swift` | `e79d8810db8f967577f5fc0704ce25f6388175fde3ff54f75b2b1fcb20da7b3c` |
| V5 UIQA 脚本 | `10ebe08cf8456873f520210581b2ff08c90aa5dc5d94e0431ab22acd2958e3c3` |
| 模拟器 App 可执行文件 | `49c9f93cbe2ab00c1433ddfd4d8574d63cb85e70e00d3337449da50a5fcc76d6` |

本轮 tracked diff 另存为 `2026-09-11-B4-iOS-local-changes.patch`。新增 UIQA 脚本当前为未跟踪文件，因此单独按上述路径和哈希交付。

## 6. “青禾”与“清河”核对

已确认的证据只有：

- 用户真机截图显示：测试阅读计划代号为“清河”，并写明“以清河为准，蓝桥作废”。
- 旧问题记录文字写成“青禾”。

本轮无法仅凭这两份材料证明差异发生在原始转写、会后整理、候选生成还是人工记录阶段。为避免污染事实，本轮未读取或改写生产正文，未把任一文字自动纠正为另一文字。B 阶段需在用户授权查看对应候选时，只核对安全元数据和屏幕实际内容；若要追溯来源，需另行限定只读范围。

## 7. 状态与残余风险

| 项目 | 状态 | 说明 |
|---|---|---|
| A 阶段实现、自动化和模拟器 UIQA | `PASS` | 可进入 B 阶段 |
| 修复版真机审核页显示 | `NOT_RUN` | 等用户明确连接并授权安装 |
| 生产候选确认写入 | `NOT_RUN` | 必须由用户本人核对并点击 |
| 审核历史生成 | `NOT_RUN` | 不能由本地模拟结果替代 |
| 正式记忆修订、投影、向量更新 | `NOT_RUN` | 需后台核对真实终态 |
| 文字和 Live 查询新事实 | `NOT_RUN` | 需在写入成功后执行 |
| 整体 B4 | `FAIL` | 沿用进入本轮时的真实状态，不能提前改为 PASS |

残余风险：

1. 真机字体、系统版本和当前生产候选数据形态可能暴露模拟器 fixture 未覆盖的展示问题。
2. 本轮后端回归使用 memory backend，不构成 PostgreSQL 生产写入证明。
3. “青禾/清河”的来源差异尚未定位，不能在没有只读证据时归因。
4. 工作区包含其他任务未提交修改，后续构建和提交时必须继续按文件核对，不能整体回滚或整体提交。

## 8. B 阶段入口与回退

B 阶段开始前先重新核对双仓 HEAD、工作区、后端版本、账号/vault 和生产候选 ID；确认无串账号风险后，再安装上述哈希对应的诊断构建。随后按 B1-B8 逐项执行，用户只负责屏幕核对和本人确认，日志、请求绑定、审核历史、正式版本、投影及查询结果由执行者核验。

回退方式：仅回退本报告列出的 B4 文件变更；不得用 `git reset --hard`，不得覆盖工作区其他任务修改。若真机发现 binding 或展示异常，立即停止确认操作并恢复上一可用 App 构建，不对生产候选做补偿性自动确认。
