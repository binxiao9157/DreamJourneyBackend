# 完整回归中的失败与隔离核查

以下 `.xcresult` 均保存在 `evidence/`，失败没有被删除或改写。`xcodebuild` 退出码与具体断言分开判读；单项重跑通过不覆盖全量失败。

| 修前产物 | 精确失败 | 局部处理 | 相同断言/范围的后续证据 |
|---|---|---|---|
| `ios-final-v2.xcresult`、`ios-final-v4.xcresult` | 共享认证测试中第一次 401 后出现 attempt 1 的 `taskCreated/taskResumed` 通知；v4 的脱敏阶段序列证明没有 attempt 1 的新授权或新请求 | 仅校正旧测试的异步通知时序假设；401 后仍禁止旧 attempt 的授权、请求创建、响应处理，仍要求新 attempt 的 trace、401/200、请求数和授权头 | `mic-shared-auth-order-green.xcresult`；最终全量另核 |
| `ios-final-v5.xcresult` | MIC-11 的 0.1 秒真实计时器在全量运行时遇到账号租约失效，不能将该结果冒充 SDK 无回调超时 | 改为注入单调时钟和显式 watchdog：先确认真实 SDK start 与租约有效，再推进到截止 | `mic11-deterministic-green.xcresult`；最终全量另核 |
| `ios-final-v6.xcresult` | MIC-08：503 回执已收到，迟到 `taskResumed` 将落盘曝光从 `responseReceived` 倒退；这是产品诊断缺陷 | `EchoVoiceLaunchAttempt.note` 对曝光实行单调阶段，并防止迟到 resume 倒退已收到响应的阶段；保留 1 POST、503、首次失败阶段断言 | `mic08-exposure-order-green.xcresult`、`mic08-recovery-isolation-green.xcresult`；最终全量另核 |
| `ios-final-v6.xcresult` | 旧 B6 测试的 follow-up store 只传唯一 `UserDefaults`，磁盘默认目录按对象地址选择，长套件可能读到旧测试文件 | 测试 follow-up store 明确使用本条用例的唯一临时根目录；未改产品恢复规则 | `live-recovery-targeted-recheck.xcresult`、`mic08-recovery-isolation-green.xcresult`；最终全量另核 |
| `ios-final-v8.xcresult` | 三条 0 秒用例由 XCTest 进程 `signal kill`，没有业务断言结果 | 仅重启本地模拟器，不抹数据、不改业务断言；三条原用例单独重跑 | `ios-signal-kill-targeted-recheck.xcresult` 退出 0；全量环境稳定性另核 |
| `ios-final-v9.xcresult` | 旧 B6 UI 测试断言只读 GET 为 2，却让 coordinator 的后续 poll 用真实计时器自行触发，整套时出现第 3 次 GET | 测试注入已有可控 poll 调度，保留 2 GET、旧结果隔离与 pending-review 页面断言 | `b6-uikit-poll-isolation-green.xcresult`；最终全量另核 |

这些修正不构成 MIC-01–19 的完整验收。MIC-15/16/18 仍因隔离 PostgreSQL 不可用而未运行；历史现场慢启动/单次失败原因未由本地合成用例证实。
