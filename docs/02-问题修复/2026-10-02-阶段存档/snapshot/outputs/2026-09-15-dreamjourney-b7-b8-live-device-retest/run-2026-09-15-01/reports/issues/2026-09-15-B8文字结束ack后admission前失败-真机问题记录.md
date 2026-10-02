# B8 文字结束在 ack 后、admission 前失败

问题编号：DJ-B8-DEVICE-01\
状态：FAIL\
范围：B8 文字会话结束与会后整理交接\
记录日期：2026-09-15

## 现象

用户通过真实文字入口发送一条新的合成测试信息，收到正常文字回复后点击结束整理。页面最终稳定显示：

> 当前无法继续整理，原对话已保留

状态没有被其他历史任务覆盖，也没有持续转圈，但本场未进入可确认的会后整理终态。

## 复现步骤

1. 在回响页进入文字回响。
2. 发送一条包含全新临时代号的合成事实。
3. 等待文字回复完成。
4. 点击结束并整理。
5. 观察结束状态和设备诊断链路。

## 实际结果

- 文字消息发送成功，回复成功。
- 本场 owner turn 已记录并持久化。
- end 请求获得 HTTP 201，并完成 typed decode。
- 状态从 `saving` 进入 `queued`。
- 重复的 ended receipt 被幂等忽略。
- ack 请求获得 HTTP 201，并完成 typed decode。
- 随后状态从 `queued` 进入 `unavailable`。
- 本轮捕获日志中未观察到 ack 后的 admission `requestCreated` 或 `taskResumed` 事件。
- 页面显示“当前无法继续整理，原对话已保留”。

安全诊断摘要：

```text
captureStateChanged live -> saving; ownerTurnCount=1; persistedOwnerTurnCount=1
end: HTTP 201; typed decode succeeded
captureStateChanged saving -> queued
duplicate ended receipt ignored
ack: HTTP 201; typed decode succeeded
captureStateChanged queued -> unavailable
```

## 期望结果

- ack 成功后，在原会话、原命令、原账号作用域和原关闭意图仍合法时继续 admission 准备与提交。
- 如果 admission 不能开始，应记录可定位的失败阶段和白名单原因，并保留可恢复坐标。
- 页面应显示与真实业务阶段一致的状态；不能仅给不可定位的 unavailable。
- 对可能已暴露给网络的写请求不得自动重发，只能只读核实。

## 已证实与未证实

已证实：

- 本次失败发生在 ack 已接受之后。
- 页面没有无限等待，原对话被保留。
- 本轮日志未记录 admission 网络生命周期事件。

尚未证实：

- admission 是在本地门禁、策略、租约、磁盘准备还是其他步骤停止。
- 未观察到 admission 网络事件不能单独证明请求绝对未发送。
- 不应把相邻策略请求、认证请求或 HTTP 状态直接认定为根因。

保持性补充：该失败之后，用户仍能开启新的原生 Live，完成约 1 分钟有声回答、主动打断和正式记忆“晨星”回查。此子项支持新 Live 可独立启动，但不能关闭原文字会话的失败状态。

## 对 Astra 的分析要求

1. 沿真实文字关闭入口追踪 end -> ack -> admission 的调用路径和阶段所有权。
2. 在 ack 完成到 admission `requestCreated` 之间增加不含敏感数据的明确失败分层。
3. 校验账号租约、FeatureGate、命令绑定、磁盘 checkpoint/outbox 和取消状态。
4. 保持 unknown write 只读恢复边界，禁止用重发 admission 掩盖问题。
5. 建立真实 UIKit、协调器、BackendClient、FeatureGate、磁盘和受控网络的先红后绿反例。

## 修复验收

- 同一文字会话按原 command 完成 end、ack、admit，且不会重复创建批次或候选。
- ack 后任何失败都能定位到明确阶段；页面可安全返回并可只读恢复。
- App 重建后只凭磁盘坐标核实，不重放未知写。
- 新旧会话身份严格隔离。
- B8 真机必须重新闭环；本记录不能因本地测试或 HTTP 201 直接改为 PASS。

## 证据

- 设备日志入口：`/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/device-logs/device-console.log`
- 测试版本与指纹：`/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/issues/README.md`
