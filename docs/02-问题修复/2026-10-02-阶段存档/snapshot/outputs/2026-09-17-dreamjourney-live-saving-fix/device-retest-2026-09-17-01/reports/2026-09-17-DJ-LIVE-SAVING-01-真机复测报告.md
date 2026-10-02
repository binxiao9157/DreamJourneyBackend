# DJ-LIVE-SAVING-01 真机复测报告

- 日期：2026-09-17
- 结论：`DEVICE_FAIL`
- 本地状态保留：`LOCAL_PASS`
- 设备：Minge的iPhone，iPhone 14 Pro Max，iOS 27.0
- 安装方式：原位覆盖安装，保留 App 数据
- App：`1.0.0 (1)`
- 安装包 executable SHA-256：`acd0a63e7f7830cd15a302671782bedeed404880872a8463bb2331effb8e4eff`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`

## 1. 测试目的

验证完整 Live 会话手动停止后，当前进程能否独立完成正文同步、end、ACK、admit、同场只读状态核实和页面更新，并在失败时有界退出 saving、保留恢复坐标。

## 2. 用户操作与结果

1. 安装后保持登录，起始页面为“回响”，无报错。
2. 测试前历史状态为“上次对话已进入待确认记忆”，按钮为“核实整理状态”。
3. 开启 Live，输入一条新的合成测试信息；正常识别、声音回答并自动恢复聆听。
4. 手动停止后：
   - 立即：`正在保存本次对话`
   - 5 秒：仍为 `正在保存本次对话`
   - 20 秒：`本次对话仍在本机保存，联网后会继续同步`
   - 无报错、转圈或永久 saving。
5. 当前页面最终只有“文字回响”，未自动恢复聆听。
6. 彻底关闭并重开 App 后显示“上次对话已进入待确认记忆”和“核实整理状态”。
7. 待确认记忆真实刷新前后均为 45 条，本场测试标记候选为 0 条。

## 3. 分项判定

| 验收项 | 状态 | 证据 |
|---|---|---|
| saving 有界退出 | PASS | 约 15 秒期限后由 saving 进入 syncPaused，UI 显示本机保存/联网继续同步 |
| 当前进程完成正文同步 | FAIL | `persistedOwnerTurnCount=0`、`queuedTurnCount=2` |
| 当前进程完成 end/ACK/admit | FAIL | 在 `existingUseCaseNotSafelyResumable` 阶段停止推进，未形成同场候选结果 |
| 当前进程显示同场终态 | FAIL | 最终为 syncPaused，不是本场 pendingReview/noChange |
| 恢复坐标保留 | PARTIAL PASS | 冷启动扫描到新增 blocked workflow，但分类为 `closingOutboxMissingCheckpoint` |
| 冷启动只读恢复本场 | FAIL | 新增 workflow 未进入恢复 plan；页面 pendingReview 来自其他历史 workflow |
| 页面状态与本场绑定 | FAIL | 页面显示“已进入待确认记忆”，但真实候选列表没有本场测试标记 |
| 未永久卡住 saving | PASS | 20 秒内退出 saving，无持续转圈 |

## 4. 已确认现场路径

本次真机不是“网络断开后自然待同步”。网络可正常完成历史 status GET 和候选 GET；候选 GET 为 HTTP 200、typed decode 成功。

当前场停止时的关键序列为：

```text
syncPaused -> saving
closingProgressBlocked reason=existingUseCaseNotSafelyResumable
closingProgressDeadlineExceeded coordinates=localOnly outcome=notSent queueCount=2
saving -> syncPaused
```

因此，本轮修复解决了“saving 永久无主”这一表现，但没有满足“当前进程继续完成同场关闭”的主要目标。真实设备仍存在：开始 Live 时本场协调器携带不可安全恢复的非 ready NaturalInput 用例，停止后只能由 deadline 收尾，正文没有进入服务端投递链。

## 5. 冷启动状态归属

冷启动日志显示：

- 扫描 `blockedCount=12`，其中新增 workflow 被标记为 `closingOutboxMissingCheckpoint`；
- 只有 4 个旧 workflow 进入 read-only plan；
- 旧 workflow 分别提交 `pendingReview` 或 `actionRequired`；
- 当前测试前 blockedCount 为 11，测试后为 12。由此推断新增 blocked workflow 属于本场；
- 候选列表两次真实 GET 均返回 45 条，本场候选为 0。

所以“上次对话已进入待确认记忆”不能作为本场成功证据。这同时暴露了历史状态覆盖当前场结果的范围外 UI 仲裁问题；本轮仅记录，不在真机测试中修改。

## 6. 证据路径

- 冷启动完整安全日志：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/cold-recovery-device-console.log`
- 本场脱敏序列：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/live-session-sanitized-observation.md`
- 本报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机复测报告.md`

日志检查未发现正文、token、密钥、Authorization header 或完整响应正文。

## 7. 停止点与下一步

- 不继续执行依赖本场成功的候选审核、正式记忆或问答回查。
- 不清理或补写当前 blocked workflow，不重放业务 POST。
- 不部署、不 commit/push。
- 下一轮应先针对 `existingUseCaseNotSafelyResumable` 的真实创建/归属路径建立本地反例，并核查为何新 Live 在停止前已处于 syncPaused，以及为何关闭坐标只有 localOnly、未形成可冷启动核实的 checkpoint。

最终状态：`LOCAL_PASS / DEVICE_FAIL`。
