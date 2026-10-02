# DJ-LIVE-DEVICE-01 关闭交接内容未封存

## 状态

FAIL（真机稳定复现）

## 范围

仅记录同一 Live 场次的原始事件采集、关闭排空和候选交接。不包含冷启动 UI 状态归属问题。

## 复现环境

- 设备：iPhone15,3，iOS 27.0
- App：`com.gaominge.dreamjourney.app` `1.0.0 (1)`
- 可执行文件 SHA-256：`3ec91819ca5ae488a013b5637deaae0cc2046bda166c01802ae8753263132b47`
- 日期：2026-09-17

## 确定性步骤

1. 候选列表真实 GET 基线为 45 条。
2. 开启 Live，输入第一段合成信息，收到正确语音回答并自动恢复聆听。
3. 在同一 Live 输入第二段合成信息，说完最后一字后立即手动停止。
4. 等待 15 秒，刷新候选列表并核对两段内容。

## 期望

- 已被 SDK 接收/冻结的原场事件交给原场。
- 关闭水位包含停止前已登记内容。
- 两段合法新信息在同一场中整理，不丢失、不重复、不串场。

## 实际

- 第二段 ASR final 已到达，且已听到助手回答开头“了解了”。
- 手动停止正常中断后续声音。
- 页面稳定显示：“已保存收到的内容，部分内容尚未完整记录”。
- 停止时诊断：`persistedOwnerTurnCount=0`、`queuedTurnCount=0`。
- 随后诊断：`coverageGapPresented unsealedTurnCount=5`。
- 候选列表两次真实 GET 均返回 45 条，两段合成内容均为 0 条。

## 已排除

- 不是候选页缓存：经过 `taskResumed -> HTTP 200 -> typedDecode -> uiCommitted`。
- 不是用户未说完第一段：第一段已有完整回答和恢复聆听。
- 不是第二段完全未被 SDK 观察：ASR final 已记录。

## 未确认根因

尚不能仅凭真机日志断定是 Manager 冻结、Manager→Echo deliver、Echo 登记、Outbox 持久化或关闭排空的哪一层首先失败。需求解决方案时应沿同一事件身份和接收序号继续定位，不得把 ASR final 直接当成已持久化证据。

## 证据

- 总记录：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-真机复测记录.md`
- 真机构建结果：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/evidence/Device-Build.xcresult`
