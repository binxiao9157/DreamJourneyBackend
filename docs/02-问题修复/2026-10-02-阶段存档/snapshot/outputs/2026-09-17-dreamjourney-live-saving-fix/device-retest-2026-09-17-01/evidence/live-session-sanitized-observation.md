# DJ-LIVE-SAVING-01 真机 Live 脱敏观察

- 设备：Minge的iPhone，iPhone 14 Pro Max，iOS 27.0
- App：1.0.0 (1)
- 业务正文未写入本证据。

## 停止前

- Live 正常进入聆听。
- owner complete 与 assistant complete 均在本地 canonical capture 中出现。
- 助手有声音，回答后恢复聆听。

## 手动停止后的安全日志序列

```text
captureStateChanged from=syncPaused to=saving ownerTurnCount=1 persistedOwnerTurnCount=0 queuedTurnCount=2
canonicalCoverageObserved stage=closeIntentPersisted ... deliveryCount=2 ... partialTurns=0 registeredMembers=2
closingProgressBlocked nextOwner=closingDeadline reason=existingUseCaseNotSafelyResumable
closingProgressDeadlineExceeded coordinates=localOnly lastProgressStage=existingUseCaseBlocked outcome=notSent handoffCount=0 pendingPersistenceCount=0 queueCount=2
captureStateChanged from=saving to=syncPaused ownerTurnCount=1 persistedOwnerTurnCount=0 queuedTurnCount=2
```

## UI 观察

- 刚停止：`正在保存本次对话`
- 5 秒：仍为 `正在保存本次对话`
- 20 秒：`本次对话仍在本机保存，联网后会继续同步`
- 没有永久 saving、报错或持续转圈。

## 冷启动观察

- 冷启动后显示：`上次对话已进入待确认记忆`
- 页面显示：`核实整理状态`
- 未自动启动麦克风。
- 当前新增 workflow 被扫描为 `closingOutboxMissingCheckpoint`；历史 workflow 分别提交 pendingReview/actionRequired。
- 候选真实 GET 两次均为 HTTP 200、typed decode 成功、candidateCount=45。
- 用户只读核对：候选刷新前后均 45，当前测试标记候选为 0。

## 证据边界

冷启动的 pendingReview 不能归属于本场。新增 workflow 的判断来自测试前后 blocked workflow 数由 11 增至 12，且新 workflow hash 首次出现；这是脱敏关联推断，不包含原始业务身份。
