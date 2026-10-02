# DreamJourney B4 结构化差异补充修复报告

日期：2026-09-11\
依据：B4 候选审核页原始修复设计及结构化差异补充修复设计\
结论：B4-S01、B4-S02 已修复，本轮 A 补充门槛通过；B4 真机和生产写入闭环仍为 `NOT_RUN`。

## 1. 修复前证据

本轮先由后端真实 `build_memory_changeset` 生成 proposal，再输入实际 Swift 合同和可审核性逻辑。未通过修改 operationKind 伪造覆盖。

修复前结果为 0 PASS / 2 FAIL：

1. B4-S01：真实 `temporalChange` 的 `changedFields=["qualifiers"]` 被判为 `incomplete`，合法时间变化不能审核。
2. B4-S02：正文完全相同、`validTime.expression` 从 2016 年变为 2018 年时，客户端前后展示仍完全相同。

原始结果保留在 `pre-fix/b4-structured-diff-counterexamples-run2.xcresult`，fixture 保留在 `pre-fix/backend-builder-proposals.json`。

## 2. 根因与修改

### 2.1 摘要代替了完整结构差异

旧实现从对象中取到 `statement/event/value` 后就提前返回，普通嵌套 `qualifiers` 无法展示，`facets` 的证据方式与可信度也会丢失。

现增加 `OwnerTruthStructuredDiffPresentation`，对 before/after 成对递归：对象键稳定合并，数组保留顺序和重复值，null/缺失/空值显式区分，facet atom 展示值、证据方式和可信度。深度、节点和字符预算超限时返回明确不完整原因并禁止提交。

### 2.2 可读性和“确有变化”混为一谈

上下文字段可以完整展示但值未改变，不能因此把合法操作误判为不可读；反过来，`correct/refine/temporalChange/dispute` 又必须至少存在一个真实变化。

现将 `isComplete` 与 `hasActualChange` 分离，并按八种 operation 的真实 target、before/after、activation 语义逐项校验。关联组忽略拒绝成员的写入 proposal，但所有非拒绝成员必须可审核。

### 2.3 三个入口展示不一致

详情、更正、关联组现在共用同一字段差异格式。详情页完整显示所有 operation；更正和关联组改为专用可滚动预览，完整正文可滚动，返回/提交操作固定。提交仍绑定用户看到的同一个不可变 proposal；未知或不完整内容没有提交按钮。

小屏超大字号首次复测发现系统 `UIAlertController` 会截断关联组长内容，这一真实遗漏已修复并新增 UIQA。最终截图可直接看到“有效时间｜确认前：2016年｜确认后：2018年”。

## 3. 变化文件

| 文件 | 主要变化 |
|---|---|
| `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift` | 完整结构 diff、八操作 reviewability、关联组提交前校验、V5 facets 属性保留 |
| `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift` | 详情/更正/关联组共享格式；可滚动预览；全部成员与操作展示；UIQA 入口 |
| `DreamJourneyTests/OwnerTruthContractsTests.swift` | 两个反例、八 builder fixture、边界、UIKit、绑定、乱序、UseCase 回归 |
| `DreamJourneyTests/Fixtures/OwnerTruth/b4-structured-diff-builder-proposals.json` | 真实 builder 的八操作确定性 fixture |
| `Scripts/QA/prd-stitch-ui/run-owner-truth-candidate-v5-detail-smoke.sh` | V5 详情、结构化关联预览、截图及结果断言 |

最终 SHA-256：

- `OwnerTruthContracts.swift`：`b0e9a3923a607bc9d463873f46d01f0c23fc63228583be34bcf46ed61f21fcd6`
- `MemoryArchiveViewController.swift`：`a0f37bafe06c28bcaf4a5b9bb9ce2b7c70e9ef162064ffa446f6b0f6cf6c4d05`
- `OwnerTruthContractsTests.swift`：`4c72fdacd24df6cb3c98f96bc24de350965af2dc08cfefd235dcf1fe95fffb1e`
- builder fixture：`4f816de0b7b30897b1b2d9d4d631403170e2c9846d94d74281f35f559c9261f7`
- 通用 iOS 设备构建可执行文件：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 4. 测试与构建

### 4.1 iOS 合同与 UIKit

环境：macOS 26.6.2、iPhone 17 Pro Simulator、iOS 26.5、arm64。\
结果：`308/308 PASS`，0 失败，0 跳过。\
证据：`post-fix/owner-truth-contracts-final.xcresult` 及 `post-fix/owner-truth-contracts-final-summary.json`。

覆盖真实 builder temporal/correct、八操作、null/空值、数组、类型、大数、预算关闭、实际详情页、实际更正预览、非首关联成员、binding、账号切换、异步乱序、推断 facet 保留及直接 UseCase 绕过。

### 4.2 实际模拟器 UIQA

- V5 小屏大字：`post-fix/uiqa/small-large-text/final-shared-preview/`，全部布尔断言为 true。
- 环境：iPhone 17e Simulator、iOS 26.5、`accessibility-extra-large`。
- 截图：`01-owner-truth-candidate-v5-detail.png`、`02-owner-truth-candidate-structured-group-preview.png`。
- 关联恢复：`post-fix/uiqa/related-resilience/final/`，重复点击、稳定重试、冲突重预览和原子应用一次均通过。
- V2 兼容：`post-fix/uiqa/v2-regression/final/`，候选、详情、提交、历史和正式记忆展示均通过。

### 4.3 后端 fixture 与回归

后端 builder 基线：`app/domain/owner_truth/memory_changeset.py` SHA-256 `bc068411...fb41`。两次确定性生成文件字节一致，SHA-256 均为 `4f816de0...261f7`。

相关后端回归：`38/38 PASS`，见 `post-fix/backend-changeset-review-unittest.log`。该结果是本地合同回归，不冒充 PostgreSQL 或生产写入验收。

### 4.4 构建

- 模拟器 Debug 构建：PASS，见小屏 UIQA `install/build.log`，包含 `BUILD SUCCEEDED`。
- 通用 iOS 设备目标、关闭签名编译：PASS，见 `post-fix/dreamjourney-device-build-final2.xcresult`。
- `git diff --check`：PASS。
- 仅有项目既存第三方 SDK/弃用 API 编译警告，本轮没有新增编译错误。

## 5. 后端与部署

本轮使用真实后端 builder 生成测试数据，但没有修改后端业务代码、接口或数据库迁移。因此无需后端部署，也未执行生产部署。两个仓库中的既有未提交修改均被保留。

## 6. 未完成项与状态

| 项目 | 状态 |
|---|---|
| B4-S01、B4-S02 | `PASS` |
| 本轮 A 补充实现、回归、UIQA、构建 | `PASS / READY_FOR_B` |
| 修复版真机审核页 | `NOT_RUN` |
| 用户真实候选确认、更正或拒绝 | `NOT_RUN` |
| 正式记忆、审核历史、投影、向量终态 | `NOT_RUN` |
| 文字及新 Live 对更新事实的回查 | `NOT_RUN` |
| B4 整体 | `FAIL`，等待 B4-0 至 B4-8 真机和后台证据 |

“清河/青禾”仅记录为既有证据差异，本轮未读取生产正文、未归因，也未修改真实字样。

## 7. 回退方案

如后续真机发现展示或 binding 异常，只按本报告五个 B4 文件逐项回退本轮局部修改；禁止重置仓库、整文件覆盖或回滚其他任务工作。立即停止候选确认，不通过数据库改写、自动确认、历史重放或清理数据补偿。

## 8. 下一步

用户连接并明确授权后，按原设计 B4-0 至 B4-8 执行。用户负责核对文字并亲自确认；执行者负责安装对应源码指纹的构建、脱敏日志、回执、正式 revision、投影、向量、文字问答和新 Live 会话核验。
