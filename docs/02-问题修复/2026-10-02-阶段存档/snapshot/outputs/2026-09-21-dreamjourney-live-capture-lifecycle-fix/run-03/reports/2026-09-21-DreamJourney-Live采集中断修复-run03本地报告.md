# DreamJourney Live 采集中断修复 run-03 本地报告

## 结论

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

R2-01 至 R2-06、主指导新增第 12 节短场强制门禁及受影响回归已在最终源码上完成。本轮没有连接或等待手机，没有调用真实 Provider，没有部署、访问生产或处理历史失败场，也没有 commit/push。

## 修改与根因

1. **R2-01：旧 generation 先污染助手缓存。** `DialogProviderCanonicalIngressRouter` 在任何 assistant mutable state 前校验 generation；被拒绝事件为零状态影响。
2. **R2-02：完成 reply 身份被 32 项无序截断。** 完成身份在本场 binding 生命周期内保持；只有进行中的正文 buffer 有界，150 replies 后旧片段不能重开。
3. **R2-03：manifest 与正文提交存在两个竞争窗口。** Store 使用精确 handoff identity 原子提交正文/partial/conflict/disposition 与 handled 事实；close manifest 与 handled 集合求差，登记本身不再代表已完成。
4. **R2-04：真实 Controller 没有释放。** durable handoff 后释放 retained capture ownership、canonical ingress 与诊断 pin。随后回归发现释放也切断同场迟到 partial 的 UI 摘要；现将“采集所有权”与“不持有对象的展示 coordinator ID”分离，新场 ID 仍隔离旧回调。
5. **R2-05：活动首错可被更新 critical 淘汰。** 诊断 Store 明确 pin 当前活动场首错，结束后释放并回收有界配额。
6. **R2-06：原证据只有 2+9 轮。** 新测试以真实 Controller/Coordinator/磁盘/FeatureGate/BackendClient/HTTP/Worker/隔离 PostgreSQL 完成 150 用户 + 150 助手回合，并执行候选审核、正式记忆与 Store 重建。

后端生产长场语义整理未重写。本轮后端新增差异仅为本地 smoke 的受控 relation 响应遵守生产批内 `existingIndex < incomingIndex` 合同；生产合同保持严格。

## 红绿证据

- 修前 R2 六项：`red/r2-core-business-red-v2.xcresult`，6/6 FAIL；失败分别命中旧 generation、旧 reply 重开、manifest 两窗口、Controller retained 释放及活动首错配额。
- 修后同断言：`green/r2-core-post-ui-fix.xcresult`，6/6 PASS。
- 保持性红绿：首次 OwnerTruth 全量在 `testEchoCoverageCopyRefreshesWhenLatePartialBodyPersistsWithoutGapCountChange` 失败；`green/late-partial-green.xcresult` 与最终全量均 PASS。
- 短场：`green/cap15-short-gate-final.xcresult` + `green/cap15-short-gate-receipt-v4.json`。
- 150 轮：`green/cap15-long-150-final.xcresult` + `green/cap15-bidirectional-evidence-v4.json`。

## 短场与 150 轮结果

短场先运行且完成两轮 Source、候选正文/补充、source/sourceRefs、无重复、审核、正式记忆和重建回查。长场入口校验同 run 的源码及配置指纹；无有效 receipt 会在网络前失败。

最终组合证据：2 个 Source，turn counts 为 4 与 300；长场 150 用户轮、150 助手轮；后半段含跨批重复、补充、纠正、撤回及尾段；短场 1 候选、长场 16 候选，审核后 17 条正式记忆；Store 重建一致；Python 重绑客户端命令为 0。

## 回归与构建

- OwnerTruth 全量：561 total，559 PASS，2 SKIP，0 FAIL。两项 SKIP 是未配置外部 CAP15；同一最终源码已单独运行这两个入口且均 PASS。
- Echo/音频：42/42 PASS。
- Backend 受影响门禁：192/192 PASS。
- UIKit：真实 `EchoViewController` + Coordinator + 磁盘 + BackendClient 组合在 iOS Simulator 执行；R2 及迟到 partial 页面状态断言 PASS。没有用截图代替业务断言。
- build-for-testing、通用 iOS Simulator、通用 iOS device unsigned：PASS。
- 两端 `git diff --check`：PASS。
- 隔离 PostgreSQL schema head `0122`；本轮实例已停止，未接触生产。

## 修改文件

- iOS：`DialogEngineManager.swift`、`OwnerTruthContracts.swift`、`EchoViewController.swift`、`ConversationMemoryManager.swift`、`OwnerTruthContractsTests.swift`。
- Backend 测试基础设施：`scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`。
- 验收工具与证据：本 run 的 `tools/cap15_gated_bidirectional_server.py`、xcresult、receipt 和 evidence。

工作区还包含前序任务的大量未提交修改；本轮未 reset/clean，也没有回退或覆盖它们。完整 dirty 列表见 `baseline/`。

## 部署、风险与回退

- 后端生产代码相对 run-02 没有本轮新增业务合同；本轮不要求为 relation 夹具单独部署。未来整体发布仍需按累计改动计划迁移 schema `0122`、后端，再 iOS。
- 真实 Provider 的 SDK 时序、真机短场、物理 20 分钟与 65 分钟仍为 NOT_RUN，本地结果不能关闭现场缺陷。
- 历史 run04 首个触发点仍不能唯一归因，且未重放/清理。
- 回退应按 R2 相关代码块成组执行；不得恢复 generation 后验校验、32 项完成身份截断、非原子 manifest、未知写重放或放宽 B7/权限/Binding/CAS/hash/revision。

## 后续真机清单

1. 先跑两轮短场，第二轮补充第一轮；验证待确认候选正文和来源，再由用户审核、正式记忆读取、重启回查且不重复。
2. 短场通过后再跑物理 20 分钟，覆盖首/中/后半/尾段、重复、补充、纠正、撤回和纯问题；完成同样四段闭环。
3. 未来物理 65 分钟同样先过短场，并至少覆盖 150 用户轮与五项首中尾事实。
4. 独立验证断网、认证恢复、账号切换及退出重进；未知写只读核实且业务 POST 不增加。

源码与构建指纹见 `artifacts/source-build-sha256.txt`。
