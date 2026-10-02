# Live LC / TTL 补充本地执行清单

判定口径：`PASS_LOCAL` 只表示本地真实装配或受控依赖下的业务断言通过；`NOT_RUN` 表示未执行。逻辑时钟、模拟器和通用设备编译均不能替代真实 iPhone 或真实 Provider/SDK 验收。

## LC-01 至 LC-15

| 编号 | 状态 | 本地证据 | 真机/真实 SDK 边界 |
|---|---|---|---|
| LC-01 | PASS_LOCAL | explicit final 修前红、修后 strict mapping 通过；沿用上一 run 证据并纳入 492 项回归。 | 真实 Provider 序列 NOT_RUN。 |
| LC-02 | PASS_LOCAL | Bool/缺失/null/字符串/数字/legacy definite 严格解析保持通过。 | 真实 Provider 全变体 NOT_RUN。 |
| LC-03 | PASS_LOCAL | complete 后相同 final/Confirmed 幂等，零新 canonical/delivery。 | 真机 NOT_RUN。 |
| LC-04 | PASS_LOCAL | complete/ACK 后迟到 interim 不覆盖内容、finality、sequence 或 command。 | 真机乱序 NOT_RUN。 |
| LC-05 | PASS_LOCAL | 不同 final 明确冲突且不阻断后续问题。 | 真机 NOT_RUN。 |
| LC-06 | PASS_LOCAL | `testManagerIngressFreezesIdentifierWindowBeforeEchoQueueAndStop`：原始入口先冻结 Q1 身份和接收序，再登记 Q2，迟到执行 Q1 不污染 Q2。 | 真实 SDK 原始消息顺序 NOT_RUN。 |
| LC-07 | PASS_LOCAL | identifier-only、缺 ID、账号租约变化和拒绝路径 fail closed；无 sequence 猜测 fallback。 | provider epoch/双窗口真实歧义 NOT_RUN。 |
| LC-08 | PARTIAL | 既有测试覆盖 owner 解析、Echo、磁盘与 UseCase，但使用 Spy、手工 decision，assistant 也未走真实文本流处理；待 R4 生产装配补证。 | 真机 10 问 NOT_RUN。 |
| LC-09 | PASS_LOCAL | 首个 gap 阻断后缀，不跨 gap seal。 | 真机 NOT_RUN。 |
| LC-10 | PASS_LOCAL | member 在 SDK 原始入口登记；事件入队但 Manager/Echo 尚未处理即 stop，barrier 排空已登记成员后才冻结 N/end。 | 真机调度竞态 NOT_RUN。 |
| LC-11 | PASS_LOCAL | identifier-only 首次成员参与 close drain；stop 后新问题拒绝，不因 Store.closeIntent 顺序丢失。 | 真实 SDK identifier-only NOT_RUN。 |
| LC-12 | PASS_LOCAL | interim-only、排空截止和磁盘重建保留 partial/gap，不强制 complete、不自动开麦或重放写。 | 进程被系统终止的真机场景 NOT_RUN。 |
| LC-13 | PASS_LOCAL | 真实 Echo controller 的 finish→gap→idle 状态保持可见。 | 真机 UI NOT_RUN。 |
| LC-14 | PASS_LOCAL | assistant 文本 complete 与音频听完分离；音频/Echo 保持性 52/52。 | 真实 SDK 播放与蓝牙均 NOT_RUN，本轮不设车机专项门槛。 |
| LC-15 | PASS_LOCAL | B8/B8-S01/S01-08、unknown write、跨轮 poll 和 B6 no-replay 均在定向及 492 项回归中通过。 | 修复版真机 B8 闭环 NOT_RUN。 |

## TTL-L01 至 TTL-L12

| 编号 | 状态 | 本地证据 | 真机边界 |
|---|---|---|---|
| TTL-L01 | PASS_LOCAL | 注入 clock 推过 TTL；旧 route 拒绝，新 request authority 贯通 UseCase 与 BackendClient，原 scene/product 不变。 | 设备自然 TTL NOT_RUN。 |
| TTL-L02 | PASS_LOCAL | 10 问并发唤醒共享一次有界 policy refresh 和派送单飞；预算不随等待者放大。 | 真机并发 NOT_RUN。 |
| TTL-L03 | PASS_LOCAL | `testLiveExpiredPolicyExplicitDenyDoesNotExposeOrPostAppend`：fresh deny 后零 POST、文件保留、无循环刷新。 | 真机 NOT_RUN。 |
| TTL-L04 | PARTIAL | 既有测试证明注入时钟、多次 TTL 与顺序，但未经过完整 Manager/Echo/真实 FeatureGate/BackendClient，也未实际覆盖短断网、最终 stop 和 N 冻结。 | 物理 20 分钟真机 NOT_RUN。 |
| TTL-L05 | PASS_LOCAL | start 原 command 在 POST 前以 preparedNotExposed 落盘；mayExpose 单向持久，可靠未曝光仅原 command 首次发送。 | 进程杀死窗口真机 NOT_RUN。 |
| TTL-L06 | PASS_LOCAL | append 曝光丢回执后按原坐标 status 精确命中，原 POST 次数为 1，队尾按序推进。 | 真机网络中断 NOT_RUN。 |
| TTL-L07 | PASS_LOCAL | missing/404/timeout/部分响应保持 unknown、零重发、不跳序；迟到旧结果受身份/代次隔离。 | 真机 NOT_RUN。 |
| TTL-L08 | PASS_LOCAL | `testLiveStartCommandAndExposureSurviveStoreReconstruction` 与 `testLiveUnknownStartReconstructionUsesExactReadOnlyStatusWithoutSecondPost`：start command/session/product/曝光状态跨 store 重建，精确只读命中才绑定，POST 不增加。 | 冷启动真机 NOT_RUN。 |
| TTL-L09 | PASS_LOCAL | start/append 可绑定成功回执不被旧 route 推翻；scope 变化仍 fail closed。 | 真机自然过期 NOT_RUN。 |
| TTL-L10 | PASS_LOCAL | inbox/ack fresh authority 通过真实 BackendClient 受控网络；close/end/ack/admit 保持原 command 与 unknown 零重放。 | 真机跨 TTL 关闭 NOT_RUN。 |
| TTL-L11 | PASS_LOCAL | 401、断网、5xx、解码失败维持 typed exposure；业务 POST transport 不递归认证重发。 | 真实网络故障组合 NOT_RUN。 |
| TTL-L12 | PASS_LOCAL | 账号/vault/authority 变化、迟到 policy/write 回调和旧记录重建不跨 lease、不自动恢复历史 POST。 | 真机账号自然轮换 NOT_RUN。 |

## 总结

- LC-08 及 R3 相关交接：`PARTIAL`；其余既有局部 PASS 保留，等待 R1-R4 重审。
- TTL-L04 及 R1/R2/R4 相关连接门禁：`PARTIAL`；不得由测试总数推导整链 PASS。
- 真实 iPhone、真实 Provider/SDK、物理 20 分钟与修复版 B8 现场闭环：`NOT_RUN`。
- 本地状态：`A_LOCAL_INCOMPLETE`。
