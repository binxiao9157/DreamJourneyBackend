# 2026-09-17 覆盖摘要 UI 刷新最小真机观察

## 环境

- iPhone 14 Pro Max：已连接、解锁、配对。
- Bundle ID：`com.gaominge.dreamjourney.app`。
- 安装方式：同包名原位覆盖；未卸载、未清数据。
- 日志方式：`devicectl --console`，无断点、无暂停式调试。

## 尝试 1：场景未命中

1. Live 正常进入，AI 完成回答后用户停止。
2. 页面显示“正在保存本次对话”，至少 45 秒未变化；无报错。
3. 安全日志显示正文完整：`membersWithoutBody=0 partialTurns=0 hasPersistedLocalText=true`。
4. 关闭并重启 App 后，页面显示“上次对话已进入待确认记忆”。

结果：`NOT_RUN`，未命中覆盖缺口，不用于判断摘要刷新修复。

## 尝试 2：partial 缺口

1. Live 正常进入，用户在合成句最后几个字仍在说时停止。
2. 未听到后续 AI 回答，无报错。
3. 页面显示“已保存收到的内容，部分内容尚未完整记录”，15 秒后保持一致。
4. 脱敏事件字段：

```text
canonicalCoverageObserved stage=turnUpsertAccepted
registeredMembers=1
membersWithoutBody=0
partialTurns=1
unsealedTurnCount=1
hasPersistedLocalText=true
firstGapReason=partialBody
firstGapRole=owner
```

结果：partial 覆盖缺口展示 `PASS`。

## 未观察项

真机未肉眼捕获“尚无法确认本次内容已完整保存”到“已保存收到的内容，部分内容尚未完整记录”的两阶段切换；partial 正文在页面首次可见前已经落盘。该精确切换标记 `NOT_OBSERVED`，不以本次真机结果替代本地真实控制器红绿证据。
