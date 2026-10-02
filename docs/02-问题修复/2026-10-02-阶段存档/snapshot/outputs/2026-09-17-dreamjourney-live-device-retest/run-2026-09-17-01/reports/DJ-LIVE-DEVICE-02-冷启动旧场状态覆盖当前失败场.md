# DJ-LIVE-DEVICE-02 冷启动旧场状态覆盖当前失败场

## 状态

FAIL（真机稳定复现）

## 范围

仅记录 Echo 冷启动后多个历史/当前工作流的 UI 状态归属与优先级。不把它当作当前 Live 内容未封存的根因。

## 前置事实

- 当前 Live 关闭后，两段合成内容均未出现在候选列表。
- 重启前 Echo 正确显示：“上次对话已保存，尚未确认开始整理，可再次核实”。
- 重启前页面未自动开麦，无卡住。

## 复现步骤

1. 保持上述失败场次的本地坐标与原始记录。
2. 彻底终止 App。
3. 重新启动同一已安装版本，不传入旧 Controller、闭包或 productSessionID。
4. 不点击麦克风或“核实整理状态”，观察 10 秒。

## 期望

- UI 展示最近当前失败场的可证实状态。
- 历史场次的 `pendingReview` 不能覆盖当前场的阻断/未完整状态。
- 信息不足时保持可核实、不自动业务写、不自动开麦。

## 实际

- 冷启动 UI 稳定显示：“上次对话已进入待确认记忆”。
- 实际候选列表仍不包含当前场的两段内容。
- 页面显示“核实整理状态”，不自动开麦，无卡住。
- 冷启动扫描：`blockedCount=8`。
- 当前新增阻断坐标：`closingOutboxMissingCheckpoint`。
- 同时多个历史工作流返回 `pendingReview`，最终 UI 采用了历史成功文案。

## 已证实缺口

冷启动时能发现当前阻断坐标，但 UI 仲裁未将“当前最近场”与“历史已完成场”分离，导致历史 `pendingReview` 成功文案覆盖当前场的阻断事实。

## 未确认根因

尚需核查 Controller 订阅、Registry 列举顺序、workflow 时间/世代比较与最终 UI reducer 的优先级。不得通过删除历史坐标或把所有状态强制设为失败来修复。

## 证据

- 总记录：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-真机复测记录.md`
- 真机构建结果：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/evidence/Device-Build.xcresult`
