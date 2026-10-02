# DreamJourney B4 原始结构判等与显示精度修复报告

日期：2026-09-11\
设计依据：`2026-09-11-Sol-B4原始结构判等与显示精度修复设计.md`\
执行边界：仅本地 iOS、测试及证据；未安装 iPhone，未操作生产数据，未提交或推送 Git。

## 1. 基线与隔离

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- 后端工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 后端 HEAD：`be9670`（短哈希）
- 两个仓库在开始时均已有其他任务的未提交修改；本轮没有重置、覆盖或回退这些成果。
- 反例和 UIQA 只使用合成学校、饮食、年份及固定 UUID，不读取或改写生产正式记忆。

## 2. 修复前反例

使用真实后端 `enrich_memory_payload_v5` 与 `build_memory_changeset_proposal` 构造两份 `correct` proposal：

1. `confidenceOnly`：仅把 inferred 习惯线索可信度由 `0.721` 改为 `0.724`。
2. `timePlusConfidence`：正文不变，同时把有效时间 `2016年 -> 2018年`、可信度 `0.721 -> 0.724`。

生成两次的 fixture 完全相同，SHA-256 均为：

`6fce538f67662882754604ce1b3795ae4c8a18ec176072b5fbdd5c24e8cfb71b`

修复前 Xcode 结果为 `0 PASS / 2 FAIL`：第一例找不到 confidence 变化，第二例只有部分结构化变化可见。原始证据保存在：

- `pre-fix/backend-builder-precision-proposals-a.json`
- `pre-fix/backend-builder-precision-proposals-b.json`
- `pre-fix/b4-raw-value-precision-counterexamples.xcresult`

## 3. 根因

旧实现混用了“原始结构是否变化”和“格式化文字是否变化”：

- `hasChanged` 由 `beforeText != afterText` 推导，两个不同 Double 一旦格式化成相同百分比，便被误判为没变化。
- facet object 被过早压成一行展示原子，导致其中 confidence 的细微变化无法成为独立字段行。
- 数组用拼接文本表达，会让单项包含分隔符与多个数组项发生文字碰撞，也弱化顺序和数量边界。

所以提交仍绑定完整 proposal，但页面可能漏掉其中真实的结构化修改。

## 4. 代码修改

### 4.1 原始 typed 结构判等

文件：`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

- `OwnerTruthStructuredFieldChange.hasChanged` 改为比较原始 `OwnerTruthJSONValue` 后保存的明确布尔值。
- object 按完整 key 并集递归，array 按索引、顺序和数量递归，不再用格式化后的整段文本判等。
- 只有展示文案发生类型碰撞时才追加类型边界；若仍不能区分，则 fail-closed，不允许审核。
- 数字使用 Swift 的 shortest round-trip 文本，`0.721`、`0.724` 及更小差异均可辨，不固定截成整数百分比或一位小数。

### 4.2 三入口共享显示规则

文件：`DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`

- 数组路径 `facets.habits[0].confidence` 映射为用户可理解的“习惯与偏好可信度”。
- 详情、更正预览、关联组继续消费同一 `structuredPresentation`，没有单独放宽任何入口。
- UIQA 的合成更正同时包含 `2016年 -> 2018年` 与 `0.721 -> 0.724`，并把两项都列为成功条件。

### 4.3 回归与真实页面证据

文件：`DreamJourneyTests/OwnerTruthContractsTests.swift`

- 固定两个真实 builder 反例。
- 增加小精度、类型、null、数组分隔符/顺序/数量冲突测试。
- 通过实际 UIKit controller 覆盖详情、更正预览和非首关联成员。
- 验证 displayed binding 在提交时未被替换，CAS、版本锁和不可变 proposal 规则保持不变。

文件：`Scripts/QA/prd-stitch-ui/run-owner-truth-candidate-v5-detail-smoke.sh`

- UIQA 必须同时观察到时间和 confidence 两项变化，否则脚本失败。

## 5. 测试结果

### 5.1 定向反例与完整审核回归

- 修复后定向测试：`6 PASS / 0 FAIL`
- `OwnerTruthContractsTests` 完整集合：`316 PASS / 0 FAIL`
- 环境：iPhone 17 Pro Simulator，iOS 26.5，arm64
- 结果：`post-fix/b4-precision-focused-final.xcresult`
- 结果：`post-fix/owner-truth-contracts-full.xcresult`

### 5.2 后端 builder 合同

- `tests.test_owner_truth_memory_changeset`：`10 PASS / 0 FAIL`
- fixture 确认来自真实 builder，未手工伪造 operationKind。
- 证据：`post-fix/backend-builder-contract-result.txt`

### 5.3 实际模拟器 UIQA

主 V5 流程全部为 true，包括：

- `previewShowsStructuredTimeChange`
- `previewShowsStructuredConfidenceChange`
- `v5ActionsBound`
- `atomicCommitAppliedOnce`

截图中可以直接看到：

- `习惯与偏好可信度｜确认前：0.721｜确认后：0.724`
- `有效时间｜确认前：2016年｜确认后：2018年`

证据：

- `uiqa/final/owner-truth-candidate-related-group-uiqa-result.json`
- `uiqa/final/02-owner-truth-candidate-structured-group-preview.png`
- `uiqa/final/install/build.log`

关联恢复 UIQA：重复点击、失败重试、重新预览、旧命令隔离和原子提交均通过。旧候选入口 UIQA 也通过：

- `uiqa-resilience/final/owner-truth-candidate-related-group-resilience-uiqa-result.json`
- `uiqa-inbox-v2/final/owner-truth-candidate-inbox-uiqa-result.json`

### 5.4 编译

- 模拟器：随 316 项测试和实际 UIQA 构建成功。
- 通用 iOS 设备目标：`BUILD SUCCEEDED`，0 error，未签名、未安装。
- 结果：`post-fix/generic-ios-device-build.xcresult`
- 现有依赖/弃用警告共 70 条，不属于本轮逻辑错误；详情见结果包。

## 6. 源码与产物指纹

| 项目 | SHA-256 |
|---|---|
| `OwnerTruthContracts.swift` | `33834c86b620df20ad8d0a86a8e494c7845af6f2fdea9d886baf3d293b460fe8` |
| `MemoryArchiveViewController.swift` | `13951fee74fd8e6f1f990def1fa24e64cb0f726b3681939f9207396a4b80b1d3` |
| `OwnerTruthContractsTests.swift` | `a5261db0f9a9293b37a0edb91f052830bfba2c9ea74730708f02b5699ce6040d` |
| 精度 fixture | `6fce538f67662882754604ce1b3795ae4c8a18ec176072b5fbdd5c24e8cfb71b` |
| 模拟器可执行文件 | `7d728c743e5bc105a7eb722b91b7089fdd0b4ca67af979eecd07e716bfa67afb` |
| 通用设备可执行文件 | `1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792` |

`git diff --check` 通过。

## 7. 部署、未执行项与结论

- 后端仅用于生成合成 fixture 和运行既有 builder 合同测试，没有业务代码变化，因此未迁移、未部署。
- 未安装或测试 iPhone。
- 未操作生产候选、正式记忆、审核历史、投影、向量或 Dead Letter。
- 未 commit、未 push。
- B4-0 至 B4-8、真实审核写入、投影/向量更新、文字和新 Live 回查均为 `NOT_RUN`。
- 因此 B4 整体仍为 `FAIL`；本报告只确认本轮 A 精度修复具备进入 B4 复测的条件。

## 8. 局部回退

如真机复测发现本轮新增问题，只回退以下局部行为：原始 typed diff 判等、精度展示、字段标题、精度 fixture/测试和 UIQA confidence 断言。不得整文件恢复、重置脏工作区、改写候选或正式记忆，也不得放松 binding、CAS 或版本保护。
