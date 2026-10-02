# DJ-LIVE-START MIC-01–19 实测矩阵

`PASS` 仅表示该行所写的具体断言；`PARTIAL` 不等于整项验收。原始 `.xcresult` 位于同目录 `evidence/`。最终冻结版 OwnerTruth/Audio 总回归为 [`ios-final-v10.xcresult`](evidence/ios-final-v10.xcresult)；此前失败及其隔离处理见 [记录](regression-isolation.md)。

| 编号 | 状态 | 本轮实际断言与证据 | 仍缺的设计断言 |
|---|---|---|---|
| MIC-01 | PARTIAL | 旧 capture、当前缓存允许：Client+Gate 修前红 `mic01-red`、修后绿 `mic01-green-initial`；真实 Controller 修后 ticket 1、SDK 1、listening `mic01-controller-render-barrier` | 同一完整 Controller 链修前业务红 |
| MIC-02 | PARTIAL | 当前缓存过期后刷新 1 次、ticket 1：`mic02-03-07` | 同 attempt 累计总期限、刷新迟到 |
| MIC-03 | PARTIAL | 当前有效 deny：ticket 0：`mic02-03-07` | 本地关闭、刷新后 deny、真实 Controller SDK 0 |
| MIC-04 | PARTIAL | 换代次时 ticket 0，同断言红绿：`mic04-rotation-red/green`；悬挂 ticket 时账号轮换收尾：`mic04-pending-ticket-rotation-green` | 多等待者共享刷新、正常同账号对照全过程 |
| MIC-05 | PARTIAL | 真实 Controller 第一场 SDK 回调迟到，第二场 listening 不受污染：`mic05-old-sdk` | ticket 晚到、离页、SessionStarted/截止同时竞争 |
| MIC-06 | PARTIAL | 重复 permission completion 从 ticket/SDK 各 2 修到各 1，同断言红绿：`mic06-duplicate-red/green` | 连点及多个不同失败竞速 |
| MIC-07 | PARTIAL | 规范写前 401 至多一次 auth 恢复/共 2 POST，非规范 401 不重发：`mic07-canonical`、`mic02-03-07`；恢复后 deny：`mic07-recovery-deny` | 账号轮换/写后 401 组合 |
| MIC-08 | PARTIAL | 503 已曝光 POST 总数 1，HTTP 503/曝光落盘；迟到 resume 不再倒退 `responseReceived`：`ios-final-v6` 修前红、`mic08-exposure-order-green` 和 `ios-final-v10` 修后绿 | 丢响应、连接中断、首次 401 后第二次曝光未知 |
| MIC-09 | PARTIAL | helper 截止：`mic09-14`；真实 Controller 稳定账号、ticket 响应悬挂时执行注入 watchdog，终态超时且迟到 200 不启动 SDK：`mic09-stable-timeout` | 策略/runtime/auth/SDK 等其他等待阶段与取消 transport 回执 |
| MIC-10 | PARTIAL | 真实 Controller 同步 SDK configure 将时钟从 100 推至 116，StartEngine 0：`mic10-clock` | 多阶段累计耗时及系统墙钟调整 |
| MIC-11 | PARTIAL | 真实 Controller 永不 `SessionStarted` 时注入时钟触发有界终止：`mic11-deterministic-green` | SDK 未 ready、StartEngine 失败两分支 |
| MIC-12 | PARTIAL | MIC-01 当前缓存直接 ticket 1，未额外 policy GET；配置中保留正式记忆绑定 | 快路径确定性耗时、真实启动秒数、全绑定回归 |
| MIC-13 | PARTIAL | DNS 错误系统域/码且 HTTP nil 同断言红绿：`mic13-network/green`；503 状态由 MIC-08 验证 | 429、解码、snapshot 分类和真实 UI 提示专项 |
| MIC-14 | PARTIAL | helper 首错不被迟到错误覆盖，磁盘重建可读、无原身份正文：`mic09-14` | 不可写磁盘、全局 latestDecision 串场、完整 Controller 诊断 |
| MIC-15 | BLOCKED | 后端 memory 单测 81 PASS，不冒充 PG | 默认 API lifespan + 隔离 PG ticket/commit/并发/消费链；本机 `127.0.0.1:55520` 拒绝连接 |
| MIC-16 | BLOCKED | 无合成 PG 进程，未做有限阻塞注入 | metrics/auth/pool 三点、并发 `/live`、心跳、进程栈和有界退出 |
| MIC-17 | PARTIAL | 最终 OwnerTruth + AudioOwnerLease 596 PASS、0 FAIL、3 SKIP | 实际启动后采集、长回复/打断/续听、停止保存组合 |
| MIC-18 | BLOCKED | 原四场 runner 在 PG 预检退出，四场零启动 | 同版 `short-A→logical20→short-B→logical65` 及两短场候选/审核/正式/重建 |
| MIC-19 | NOT_RUN | 代码仅限本次 capture 的保护已审查 | A 保存中 B 发前失败和 B 已建 capture 后失败的真实组合 |

## D1–D5

- D1：独立 attempt、权限允许后 15 秒单调总预算和同步副作用复核已实现；各阶段全部悬挂组合仍 PARTIAL。
- D2：当前策略新捕获、有界刷新、冻结租约和规范写前 401 边界已实现且有针对性绿测；恢复后全部 deny/换号组合 PARTIAL。
- D3：ticket 取消、迟到 SDK、一次性权限 completion 与本次 capture 归属已实现；旧 ticket/音频 lease/旧场同时保存组合 PARTIAL。
- D4：客户端首错落盘、请求曝光、后端 ticket trace 阶段已有测试；子请求独立序号、真实 commit/发送完成和丢日志路径 PARTIAL。
- D5：隔离 PG 缺失，有限阻塞诊断 NOT_RUN；不把旧全局 API 机制风险混入本轮修复。
