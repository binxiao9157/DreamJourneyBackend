# DreamJourney Live 20 分钟真机复测报告

## 结论

**FAIL。** 本轮证明长会话的本地持续采集可以超过 20 分钟，但没有证明完整持久化与关闭交接。第 49 段写入发生 401，认证恢复后的尝试返回 403；此后本地继续采集至 70 段，服务端确认停在 48 段。手动停止时仍有 22 段待同步，关闭执行器因 `existingUseCaseNotSafelyResumable` 有界退出为 `syncPaused`。

本轮不得标记 READY，也不得继续候选、冷启动或三标记来源关联验收。

## 安装基线

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 分支：`feature/prd-stitch-ui-adaptation`
- 已安装可执行文件 SHA-256：`9d29fa691ad6133ba6373f1b64baae6c4cb5af04846b630b2f383b13ff26f0c5`
- 本轮未重新安装、未修改代码、未清除 App 数据。

## 场景与结果

| 验收项 | 状态 | 证据 |
|---|---|---|
| 物理持续对话超过 20 分钟 | PASS | 用户从 22:34:17 开始持续多轮交流，并完成开场、中段、结束标记 |
| 原生声音及自动恢复聆听 | PASS | 结束标记得到有声回答并自动恢复聆听 |
| Canonical 本地持续采集 | PASS | 关闭时 `registeredMembers=70`、`deliveryCount=70`，无正文缺口 |
| 长会话持续上传 | FAIL | 服务端确认停在 48；第 49 段出现 401，恢复尝试为 403 |
| 多次 TTL/认证恢复后继续派送 | FAIL | 认证恢复未恢复合法写入，随后 22 段仅留本地 |
| 手动停止后的排空 | FAIL | `queuedTurnCount=22`，没有排空 |
| end/ACK/admit 关闭交接 | FAIL | `existingUseCaseNotSafelyResumable`，`coordinates=localOnly`，`outcome=notSent`；本轮未观察到最终关闭链成功证据 |
| 页面从 saving 收敛 | PARTIAL PASS | 未永久停留 saving，20 秒后进入本机保存/联网后同步提示，但业务关闭未完成 |
| 候选生成与三标记来源关联 | NOT_RUN | 依赖完整同步及关闭成功，按停止条件未继续 |
| 冷启动恢复 | NOT_RUN | 为保护失败现场未关闭 App |
| 隔离 PostgreSQL | NOT_RUN | 本轮为真机现场测试，不以真机结果替代数据库合同验证 |

## 已确认事实

1. Live 采集没有在 20 分钟内停止：本地登记与正文持久化持续到 70 段。
2. 网络写入在服务端确认 48 段后停止。
3. 转折请求首次收到 HTTP 401；认证恢复后的新尝试收到 HTTP 403。
4. 关闭时本地有 22 段队列，当前用例被判定为不可安全恢复。
5. 关闭执行器没有重放未知业务写，而是保留本地数据并有界退出为 `syncPaused`。这一安全边界符合预期，但完整保存目标未达到。

## 尚不能下结论

- 403 的服务端具体拒绝规则。客户端日志仅能证明认证恢复后仍被拒绝，不能单凭状态码认定是 FeatureGate、账号 authority、路由 decision 或后端版本合同中的哪一层。
- 末尾 22 段是否可在后续合法恢复后完整补齐。本轮为保护坐标未触发任何恢复动作。
- 三个标记是否最终生成正确且不重复的候选。

## 停止条件与现场保护

- 不点击“核实整理状态”。
- 不进入候选列表，不启动新 Live，不切换账号。
- 不重放 start/append/end/ACK/admit，不清理本地恢复记录。
- 保持当前页面和 App 进程，等待源码与服务端只读证据分析。

## 证据

- 完整脱敏日志：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/live-sanitized.log`
- 关键失败窗口：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/failure-window.log`
- 用户现场观察：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/user-observation.md`

## 下一步

先以本轮 trace 和时间点核对 403 对应的服务端只读日志，并在源码中定位认证恢复后 append 写入采用的账号租约、route decision 和 FeatureGate authority。修复前不得用新的 Live 覆盖本场恢复坐标。
