# Live 长对话分批整理 run-04 复核结论

日期：2026-09-20。

## 结论

**本次指定范围符合预期，可以认可 LOCAL_PASS。** run-03 留下的 R02-B-PARA 语义缺口与 F-65 同源完整链证据缺口均已补齐；本次复核未发现新的本地阻断，不要求继续扩大业务修改。

独立边界继续保留：`PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`。截图中的 `READY_FOR_LIVE_DEVICE_RETEST` 仅表示本地修复准备完成，不表示真实 Provider、兼容部署或设备验收已经通过。

## 1. R02-B-PARA：通过

复核当前代码并独立执行真实 adapter + MockTransport 的五项探针。原来失败的正常释义场景现为 12 个 atom 经合法补充形成 11 条候选，Run 到达 readyToPublish；另外四项既有正向场景继续通过。

原文证据 ID、范围和 hash 与生成后的表述已分离。分页复核声明本页负责的 atom，全场覆盖仍由 manifest 与 atom 去向检查负责。修复没有以删除完整性校验换取通过。

独立执行 pipeline 26/26 通过；额外七项正负对照通过：页外上下文不误报、本页 atom 遗漏拒绝、责任回执不全拒绝、无支持事实拒绝、范围越界拒绝、hash 篡改拒绝、重复原句仅靠旧字面匹配时歧义拒绝。

- [五项探针复跑](</Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-04/evidence/semantic-five-rerun.log>)
- [26 项 pipeline 复跑](</Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-04/evidence/semantic-pipeline-unittest-rerun.log>)
- [七项责任与证据正负对照](</Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-04/evidence/semantic-owned-atom-negative-controls.log>)

## 2. F-65 同一大 Source 完整链：通过

核对当前脚本和 Sol 留存的 PostgreSQL 最终记录，未重新启动数据库：

- 同一个 HTTP admission 的 Source：73,728 字、301 turn、150 user turn。
- 该 Source 经实际 Worker 生成 149 条候选，并逐项审核、激活为正式记忆。
- 最终 190 条正式记忆 = 短场 1 + 另一长场 40 + 本次 F-65 149。
- Store 重建后核对正式记忆内容、Source 正文及 Memory/Version 数量；确认与激活命令重放后没有重复。
- 末尾纠正保留，早期被纠正值未发布；候选数确实超过 32。
- 真实 DeepSeek 请求构造和 parser 仍执行，单次数量合同没有绕过。

准确范围是“真实 Runtime 显式注入生产同类 extractor、受控 HTTP、隔离 PostgreSQL 的 F-65 本地完整链”。外部 HTTP 回应是合成真值，不能证明真实模型质量或物理 65 分钟稳定性。无注入默认装配及私密预整理交接由另一 PostgreSQL 场景验证，不混称为同一个大样本。

- [F-65 最终数据库记录](</Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/postgres-formal-memory-chain-final.log>)
- [同源处理与审核脚本](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:736>)

## 3. 回归、构建与证据绑定

- 实际解析 run-04 xcresult：OwnerTruth 532/532、音频租约 5/5、音频行为 25/25，失败及跳过均为 0。
- 累计源码指纹按 run-02、run-03、run-04 顺序合并核对：后端 31 个、iOS 19 个，全部与当前源码一致。
- 五个构建产物指纹全部匹配，包括 Simulator/generic iOS 的业务 dylib 与 Simulator 测试 bundle，已补齐上轮启动 stub 不能代表业务版本的问题。
- 报告保留的 Core/API `PoolClosed` 是错误测试配置下的执行结果。本次用 `STORE_BACKEND=memory APP_ENV=test` 独立重跑 `tests.test_core_services`，165/165 通过，无产品修改。它不替代 PostgreSQL 事务门禁。
- relay 报告已明确 3900 秒合成负载的假设和公式，不再将估算称为真实 SDK 精确流量。

- [累计源码指纹核对](</Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-04/evidence/current-source-fingerprints-check.log>)
- [Core 165 项复跑](</Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-04/evidence/core-services-memory-rerun.log>)
- [Sol 完整本地交付报告](../../../outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md)

## 4. 后续阶段

本地开发和验证阶段可以正常结束，无需连接或等待手机。后续分别安排真实 Provider 的合成样本合同验证、兼容部署与设备验收；本次复核不自动开始这些工作，也不重放历史失败任务。

真机仍由用户主动发起，按短场完整闭环、物理 20 分钟及密集场景、未来至少 65 分钟逐步验证。短长场都必须检查候选内容完整、跨批去重/补充/纠正/撤回、用户审核进入正式记忆、重启回查，并验证已通过的声音与保存链不回归。不能仅凭保存提示、候选数量或 HTTP 成功判定闭环通过。

本次未修改产品代码，未调用真实 Provider，未启动设备，未部署，未操作生产或历史数据，未 commit/push。
