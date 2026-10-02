# DreamJourney B4 更正预览双重绑定本地交付报告

日期：2026-09-13\
交付状态：`A_LOCAL_PASS / READY_FOR_DEPLOYMENT`\
范围：B4“更正预览 HTTP 200 后仍失败”的局部修复\
执行边界：未部署、未安装或测试 iPhone、未访问或修改生产数据、未 commit/push

## 1. 基线与工作区

| 工程 | 分支 | 基线 HEAD | 当前状态 |
|---|---|---|---|
| iOS | `feature/prd-stitch-ui-adaptation` | `11d0d0051b9be3cce57822dd059472d1e2536866` | 保留未提交修改，本轮新增 4 个修改文件和 1 个合成 fixture |
| 后端 | `main` | `a25b993922fc90dde1e689d19e51becb68fccdbf` | 保留未提交修改，本轮新增 3 个修改文件和 1 个 fixture 导出脚本 |

两端 `git diff --check` 均通过。未执行 reset、clean、checkout、commit 或 push。

## 2. 已证实根因

后端原有更正预览中的 `Proposal.candidateContentHash` 是更正后 canonical 内容哈希 `H1`。iOS 旧路径却把该字段与原候选哈希 `H0` 比较：

1. HTTP 200 的更正预览会在第一次接受时因 `H1 != H0` 被拒绝。
2. 即使只放开预览页，最终 `Binding.matches` 仍会重复执行同一错误校验，导致最终确认失败。
3. 旧响应没有同时证明“原候选身份 H0”和“更正结果 H1”，客户端无法在不放宽保护的前提下安全区分二者。

附带发现：真实后端 builder 产生的合法 V5 内容可包含 `createdAt: null`。iOS typed 解码器的错误文案允许 null，但实现此前仍拒绝 null，导致真实 producer fixture 在双重绑定修复后继续被解码层挡住；本轮同步修正为严格允许 null，其他错误类型仍 fail closed。

## 3. 合同与实现修改

### 3.1 后端

1. `app/services/owner_truth_candidate_review.py`
   - 新增不可变 `OwnerTruthCandidateChangeSetPreview`。
   - 在同一 repository 锁/数据库游标作用域内生成 Proposal 和 `correctionBinding`，避免跨读取拼装。
   - `correctionBinding` 固定为 `owner-truth-candidate-correction-binding-v1`，包含原候选版本、原哈希 H0、完整 typed 更正输入副本和解析后哈希 H1。
   - 内存与 PostgreSQL repository 共享 `preview_changeset_result` 合同。
2. `app/main.py`
   - 更正预览响应在保留原 `owner-truth-candidate-changeset-preview-v1` 外层和 Proposal v1 语义的前提下，附加 `correctionBinding`。
   - 普通无更正预览不返回该字段，避免重定义旧接受/拒绝合同。
3. `tests/test_owner_truth_candidate_review_api.py`
   - 使用真实 builder 构造 H0/H1 不同的合成反例。
   - 验证预览零业务写、错误更正输入被拒绝、正确更正产生 H0/H1/N+1 回执、同命令幂等。
4. `scripts/export-owner-truth-correction-preview-fixture.py`
   - 从真实后端 API/builder 导出纯合成 fixture，供 iOS 组合测试消费。
5. `scripts/backend-owner-truth-correction-preview-postgres-smoke.py`
   - 仅允许 localhost 且要求显式临时测试开关；创建随机数据库并应用完整迁移。
   - 实库验证预览审计副作用、篡改回滚、合法写入、幂等及只读结果查询，最终自动删除随机数据库。

### 3.2 iOS

1. `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
   - 新增 `OwnerTruthSourceCandidateBinding`、`OwnerTruthFrozenCorrectionInput`、`OwnerTruthCandidateCorrectionBinding`、typed envelope 和不可变 `OwnerTruthVerifiedCorrectionPreview`。
   - 校验顺序分别约束原候选 ID/版本/H0、Proposal 版本、完整 typed 输入、H1、正式记忆 revision 和 reviewability。
   - 更正最终确认必须携带同一个 Verified 对象；缺少新 binding 明确失败，不退回裸 Proposal 或普通接受路径。
   - 更正回执校验加入 expected H1；错误正整数版本或错误 H1 不显示成功。
   - `createdAt` 解码严格支持合法 null，错误字符串/数值/对象继续拒绝。
2. `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
   - 新增 typed `previewOwnerTruthCandidateCorrectionChangeSet`。
   - 使用真实 FeatureGate decision 与显式 AccountLease。
   - 更正预览 POST 禁止认证/策略刷新后的自动重发，发送结果未知不制造第二次写请求。
3. `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
   - 编辑页、二次展示页和最终确认闭包贯通同一个 `OwnerTruthVerifiedCorrectionPreview`。
   - 二次页展示冻结输入对应的 Proposal；用户继续编辑会使旧 Verified 上下文失效。
4. `DreamJourneyTests/OwnerTruthContractsTests.swift`
   - 增加缺 binding、字段错配、真实 BackendClient/FeatureGate/Lease/UIKit、错误 H1 回执等回归。
   - 旧“仅凭展示 Proposal 更正”的测试改为验证 fail closed：零审核 POST，返回 `changeSetPreviewUnavailable`。这是新安全合同要求，不是弱化断言。

## 4. 修复前后证据

### 4.1 修复前

- 后端真实反例：预览 HTTP 200 后响应没有 `correctionBinding`，新合同断言以 `KeyError` 失败。\
  证据：`evidence/pre-fix/backend-correction-binding-red.log`
- iOS/真机原始业务反例：更正“陈鑫”为“晨星”时预览接口 HTTP 200，但客户端报告“更正方案未生成”。\
  证据沿用且未覆盖：
  - `/Users/gaominge/Documents/liftora/outputs/2026-09-13-dreamjourney-b4-correction-preview-audit/2026-09-13-Astra-独立核查证据.md`
  - `/Users/gaominge/Documents/liftora/outputs/2026-09-13-dreamjourney-b4-device-retest/2026-09-13-DreamJourney-B4-陈鑫更正为晨星-真机失败记录.md`
- 早期 iOS 编译/fixture 失败包仅作为实现过程证据，不冒充业务红测。

### 4.2 修复后

- 真实 builder fixture 三项断言均为 true：H0 对应原候选、H1 对应 Proposal、H1 与 H0 不同。\
  证据：`evidence/post-fix/backend-real-builder-correction-fixture.json`
- 真实 producer fixture 经过 BackendClient、FeatureGate、AccountLease、UseCase 和 UIKit，进入二次确认页；第二次点击前审核写请求为 0，确认后仅 1 个 `.correct` POST。\
  结果包：`evidence/post-fix/ios-real-builder-uikit-final-03.xcresult`\
  截图：`evidence/post-fix/uikit-real-builder-final/D167C836-6531-4363-AF2D-2213543933AC.png`
- 定向双重绑定回归：5/5 PASS。\
  结果包：`evidence/post-fix/ios-correction-targeted-final-02.xcresult`

## 5. 测试环境与命令类别

环境：macOS 26.6.2，Xcode 26.6（17F113），iPhone 17 Pro Simulator / iOS 26.5 / arm64，Python 3.9.6。

执行的命令类别：

- 后端：`.venv/bin/python -m unittest` 运行 Candidate Review API、review service、changeset 和 group 合同；闭源试点门禁脚本；`py_compile`；真实 builder fixture 导出。
- iOS：`xcodebuild test` 运行双重绑定定向测试、OwnerTruth 完整回归、全量 DreamJourneyTests 和真实 builder/UIKit 组合测试。
- 构建：`xcodebuild build` 覆盖 iOS Simulator 与 `generic/platform=iOS`，通用设备目标禁用签名进行本地编译验证。

主要结果：

| 范围 | 结果 | 证据 |
|---|---:|---|
| 后端 Candidate/Changeset/Group 回归 | PASS，29/29 | `evidence/post-fix/backend-owner-truth-regression.log` |
| 后端 closed-pilot gate | PASS，76/76 | `evidence/post-fix/backend-closed-pilot-gate.log` |
| 后端 repository 合同保护 | PASS，4/4 | `evidence/post-fix/backend-postgres-contract-tests-02.log`；这是合同单测，不冒充真实 PG |
| 更正双重绑定 PostgreSQL | PASS | `evidence/post-fix/backend-correction-preview-postgres-smoke-04.log` |
| 单条 decision-result PostgreSQL | PASS | `evidence/post-fix/backend-candidate-decision-result-postgres-smoke.log` |
| 关联组原子事务 PostgreSQL | PASS | `evidence/post-fix/backend-memory-changeset-group-postgres-smoke.log` |
| iOS 更正绑定定向测试 | PASS，5/5 | `evidence/post-fix/ios-correction-targeted-final-02.xcresult` |
| OwnerTruth 完整回归 | PASS，401/401 | `evidence/post-fix/ios-owner-truth-full-02.xcresult` |
| iOS 全量单元/集成测试 | PASS，554/554 | `evidence/post-fix/ios-all-unit-tests-final.xcresult` |
| 真实 builder + UIKit | PASS，1/1 | `evidence/post-fix/ios-real-builder-uikit-final-03.xcresult` |
| 通用 iOS 设备目标编译 | PASS，0 error；1 个 Pods/Kingfisher Swift 6 前瞻警告 | `evidence/post-fix/ios-generic-device-build-final.xcresult` |
| iOS Simulator 编译 | PASS，由最终 554/554 测试重新编译覆盖 | `evidence/post-fix/ios-all-unit-tests-final.xcresult` |

第一次 PG 合同测试命令包含了一个不存在的测试模块，4 个实际测试通过后命令仍以模块加载错误结束；保留原文件 `backend-postgres-contract-tests.log`，修正命令后的结果为 `backend-postgres-contract-tests-02.log`，未删除或掩盖第一次记录。

## 6. T01-T22 验收表

| ID | 状态 | 证据与结论 |
|---|---|---|
| T01 | PASS | 真实 builder 产生 H1!=H0；真实 BackendClient/UseCase/UIKit 进入二次确认，第二次点击前无审核写 |
| T02 | PASS | 缺失或畸形 `correctionBinding` 明确 fail closed，零 decisions POST |
| T03 | PASS | 普通接受/拒绝继续使用原候选 H0 严格绑定；OwnerTruth 完整回归覆盖 |
| T04 | PASS | 原 source hash、候选版本、Proposal 版本逐项破坏均拒绝，reason 可区分 |
| T05 | PASS | resolved H1、完整 typed 输入回显、候选错配和 facet-only 错配均拒绝 |
| T06 | PASS | null/缺失/数组/数字/bool/中文/换行及 0.721/0.724 typed 判等与精度回归通过 |
| T07 | PASS | revision 漂移废弃旧预览，不静默更新 Binding；已有 CAS/刷新回归通过 |
| T08 | PASS | reviewability 与未知 operation 保持 fail closed，无确认能力 |
| T09 | PASS | envelope/JSON/Proposal/依赖错配拒绝；真实 producer 的合法 `createdAt:null` 可解码 |
| T10 | PASS | 真实 FeatureGate evaluator 与 AccountLease 组合测试通过，未使用恒定放行 gate |
| T11 | PASS | 同主体凭据更新和跨主体切换保持旧预览/闭包失效，不跨主体发送 |
| T12 | PASS | generation、页面生命周期、迟到回调及候选并发保护通过 |
| T13 | PASS | 连点、超时、401/503 和结果未知路径有界收尾，更正预览 POST 不自动重发 |
| T14 | PASS | UIKit 二次页和最终确认使用同一冻结输入/Proposal；继续编辑使旧确认失效 |
| T15 | PASS | 隔离 PG 中换 correctedValue、候选版本、revision、ChangeSet ID、Proposal hash 共 5 类篡改均拒绝；候选、回执、MemoryVersion 和 revision 原子回滚 |
| T16 | PASS | 合法更正从 N=1 推进到 N+1=2，正式 revision 0→1；重复命令返回同一回执且所有业务行计数不变 |
| T17 | PASS | found/notObserved 只读查询均为零业务写；错误写不补发，持久结果保留 H0/H1 和正式 revision |
| T18 | PASS | 实库预览仅新增 1 个不可变审计 Proposal；候选保持 pending/version 1，无回执、Memory、MemoryVersion 或 revision 推进；重复预览幂等 |
| T19 | PASS | 正文与显式 facet 使用真实 ontology 输出；未编辑推断属性不升级为 ownerStated |
| T20 | PASS | 迁移到 0121；旧 v1/组级/两个 decision-result 路由合同通过；单条结果查询与关联组故障回滚、重放、并发均取得隔离 PG 证据 |
| T21 | PASS | trace/attempt 保护回归通过；修改文件扫描未发现 token/key；日志仅使用白名单 reason |
| T22 | PASS | OwnerTruth 401/401、全量 iOS 554/554、后端门禁及 simulator/generic build 通过；Live 代码未修改 |

## 7. PostgreSQL 隔离验证

机器最初没有可用 PostgreSQL 运行时；该原始状态保留在 `evidence/post-fix/postgres-environment-blocker.txt`。

随后使用官方 PostgreSQL 16 临时运行时，在 `/tmp` 初始化隔离集群并仅监听 localhost:55439。所有 smoke 均创建随机数据库、应用迁移到 0121、使用合成数据，并在 `finally` 删除随机数据库。测试结束后已停止服务并卸载只读镜像。没有访问生产数据库。

通过结果：

1. 更正双重绑定：预览审计-only、5 类篡改原子回滚、合法 N→N+1、重复命令幂等、结果查询零写。
2. 单条 decision-result：账号/库/命令作用域、未提交不可见、回滚、并发幂等、历史结果稳定。
3. 关联组：decision/activation/effect 三类故障全回滚，重放无重复，并发仅一个事务获胜；pgvector 0.8.6 可用。

证据：`evidence/post-fix/postgres-environment-resolution.txt` 及其中列出的三份 smoke 日志。

仓库已有的全量 OwnerTruth PG smoke 在“投影 worker 终态化”处失败；同一失败已用不含本轮修改的 HEAD 归档快照复现。因此它是独立基线问题，不是本轮双重绑定回归。两份失败日志均保留，未将其计为本轮 PASS。

## 8. 部署与真机状态

- 本轮确有后端业务合同修改，生产部署时必须先发布兼容 `correctionBinding` 的 API，再安装匹配 iOS 版本。
- 前轮两个 decision-result 接口仍未部署，该依赖继续保留，不能用本轮本地测试替代。
- 本轮按要求没有部署、没有迁移、没有安装/测试 iPhone。
- B4-1 至 B4-7：保留既有 PASS，不由本轮重复声明。
- B4-8：仍为 FAIL。
- “陈鑫→晨星”真实更正写入：仍为 FAIL，等待后端发布和后续明确授权的真机复测。
- 正式记忆版本、投影、向量、文字问答及新 Live 回查：本轮 NOT_RUN。

## 9. 残余风险与回退

残余风险：

1. 全量 OwnerTruth PG smoke 的既有投影 worker 终态化失败仍需单独跟踪；HEAD 基线可复现，本轮未扩大范围修复。
2. 生产仍运行旧 API 合同时，新 iOS 会因缺少 `correctionBinding` 正确 fail closed；这是兼容保护，不是可直接发布的最终状态。
3. 真机现场的更正预览、最终审核写入及后续投影/向量闭环尚未复测。

局部回退范围：

- 后端仅回退 `app/main.py`、`app/services/owner_truth_candidate_review.py`、对应测试和 fixture 导出脚本。
- iOS 仅回退 `OwnerTruthContracts.swift`、`DreamJourneyBackendClient.swift`、`MemoryArchiveViewController.swift`、对应测试和合成 fixture。
- 不回退前序账号隔离、未决写保护、typed 差异、精度、八种操作、关联组、CAS 或 Live 修复。

## 10. 证据索引

- 最终源码与构建指纹：`evidence/post-fix/final-fingerprints.txt`
- 修复前真实后端反例：`evidence/pre-fix/backend-correction-binding-red.log`
- 真实 builder 合成 fixture：`evidence/post-fix/backend-real-builder-correction-fixture.json`
- UIKit 截图：`evidence/post-fix/uikit-real-builder-final/D167C836-6531-4363-AF2D-2213543933AC.png`
- UIKit 结果包：`evidence/post-fix/ios-real-builder-uikit-final-03.xcresult`
- OwnerTruth 结果包：`evidence/post-fix/ios-owner-truth-full-02.xcresult`
- iOS 全量结果包：`evidence/post-fix/ios-all-unit-tests-final.xcresult`
- 通用设备构建：`evidence/post-fix/ios-generic-device-build-final.xcresult`
- PostgreSQL 环境阻断：`evidence/post-fix/postgres-environment-blocker.txt`
- PostgreSQL 阻断解除与收尾：`evidence/post-fix/postgres-environment-resolution.txt`
- 更正事务 PG：`evidence/post-fix/backend-correction-preview-postgres-smoke-04.log`
- 单条结果查询 PG：`evidence/post-fix/backend-candidate-decision-result-postgres-smoke.log`
- 关联组 PG：`evidence/post-fix/backend-memory-changeset-group-postgres-smoke.log`
- 全量旧 smoke 当前/HEAD 基线对照：`evidence/post-fix/backend-owner-truth-postgres-smoke.log`、`evidence/post-fix/backend-owner-truth-postgres-smoke-head-baseline.log`

最终结论：双重绑定代码、真实 builder、typed 解码、UIKit 二次确认、隔离 PostgreSQL 事务、回归和本地构建均已闭环，本轮达到 `A_LOCAL_PASS / READY_FOR_DEPLOYMENT`。本轮未部署，且 B4 整体仍未通过，必须等待后端发布与后续明确授权的真机复测。
