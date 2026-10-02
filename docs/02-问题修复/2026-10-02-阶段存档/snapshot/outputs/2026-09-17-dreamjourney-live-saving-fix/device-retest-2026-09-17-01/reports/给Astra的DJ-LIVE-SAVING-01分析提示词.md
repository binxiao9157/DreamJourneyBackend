# 给 Astra 的分析提示词

请独立分析 DreamJourney DJ-LIVE-SAVING-01 修复版的真机失败，不要直接把客户端汇总文案当作根因。

请先完整阅读：

1. `/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机问题过程记录.md`
2. `/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机复测报告.md`
3. `/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/live-session-sanitized-observation.md`
4. `/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/cold-recovery-device-console.log`
5. 原设计：`/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-DJ-LIVE-SAVING-01-会后保存停滞问题分析与局部修复设计.md`

随后核对当前 iOS 源码和真实调用路径，重点分析：

- 为什么新 Live 停止前 capture state 已是 syncPaused；
- 为什么已有 NaturalInput UseCase 被判定为 `existingUseCaseNotSafelyResumable`；
- 当前场是否错误复用了历史 UseCase，还是本场 current/start 的某个前置分支失败；
- 为什么 owner/assistant 正文均完整落盘后，关闭坐标仍为 localOnly；
- 为什么冷启动时本场 workflow 被分类为 `closingOutboxMissingCheckpoint`，没有进入只读恢复计划；
- 历史 workflow 的 pendingReview 为什么覆盖了本场 blocked 状态。

请把问题拆开：

1. 同进程关闭链未完成；
2. 本场冷启动恢复坐标缺失；
3. 历史 workflow UI 状态覆盖本场。

不要把三个现象预设为同一根因。先给出代码证据和可确定复现的失败反例，再输出最小局部修复设计与验收矩阵。

必须保留：

- 未知写只能只读核实，不能重发 start/append/end/ACK/admit；
- 明确 notSent 只能在原意图、原命令、原场次、当前合法授权下首次发送；
- 不删除恢复坐标，不清理历史数据；
- 不放宽账号、authority、FeatureGate、binding、revision 或 CAS；
- 不修改 Live 音频、B7 过滤或正式记忆内容；
- 不把 saving 有界退出当作完整关闭成功；
- 不把历史 pendingReview 当作本场结果。

请输出：已确认根因、待验证假设、修改文件和函数、先红后绿测试、真实 Controller→Coordinator→Store→UseCase→BackendClient→FeatureGate→网络组合验收、冷启动和 UI 归属验收、回退方案及真机最小复测步骤。本轮只输出设计，不部署、不修改生产数据。
