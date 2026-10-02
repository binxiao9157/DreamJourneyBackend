# L1-R01 至 L1-R24 本地执行清单

状态口径：`PASS` 仅表示本轮完整执行了该条的本地确定性断言；`NOT_RUN` 表示设计中的完整组合未执行，即使相邻保护已有绿测；`BLOCKED` 表示本机缺少明确前置条件。

| ID | 状态 | 本轮证据与说明 |
| --- | --- | --- |
| L1-R01 | NOT_RUN | 已实现采集与派送解耦，但未执行 300 秒策略到期并持续到 20 分钟的完整链路。 |
| L1-R02 | NOT_RUN | 共享 FeatureGate 的 fresh capture 保护已有回归；Live 策略过期组合未以真实 evaluator 全链执行。 |
| L1-R03 | NOT_RUN | `testLiveCaptureDeliveryFailureDoesNotStopDurableCaptureOrCloseIntent` 已验证失败后继续采集两条并冻结水位；设计指定的 seq17 至 seq30 规模未执行。 |
| L1-R04 | PASS | `testLiveCaptureReadOnlyStatusConfirmsLostAppendWithoutSecondPost`：未知 append 只读命中，append POST 始终为 1。 |
| L1-R05 | NOT_RUN | 未完整注入 task 创建前取消与策略拒绝两类曝光证明。 |
| L1-R06 | NOT_RUN | 未逐项执行 401/403/409/429/5xx/解码失败完整矩阵。 |
| L1-R07 | NOT_RUN | 超时、迟到结果丢弃与后续有界核实已有绿测；连续多条加网络抖动与退避组合未完整执行。 |
| L1-R08 | NOT_RUN | 新策略 deny、账号切换、authority 变化的 Live 全链未执行。 |
| L1-R09 | NOT_RUN | 同账号 token rotation 的 Live 全链未执行。 |
| L1-R10 | PASS | 派送失败后先持久化 close intent，冻结唯一水位，未提前 end；新表达仍在磁盘。 |
| L1-R11 | NOT_RUN | 未注入设备保护、磁盘满与 close marker 写失败。 |
| L1-R12 | NOT_RUN | 迟到 final、coverage gap、seal 后水位稳定已有绿测；与下一场 final 交错的完整组合未执行。 |
| L1-R13 | NOT_RUN | 未覆盖每个业务写曝光窗口的双进程 kill；现有 UIQA 仅覆盖页面进程重建。 |
| L1-R14 | NOT_RUN | B6 旧恢复保护保持绿；V1/V2 新显式恢复完整组合未执行。 |
| L1-R15 | NOT_RUN | 本轮未新增 V2 损坏、未知 schema、作用域错误组合测试。 |
| L1-R16 | NOT_RUN | 两个旧 pendingReview 与当前未同步任务乱序组合未执行。 |
| L1-R17 | NOT_RUN | 后端长输入、纯问题及跨片纠正回归通过；20 分钟完整 Source 与候选关联未执行。 |
| L1-R18 | BLOCKED | 无隔离 PostgreSQL 地址，且本机无 psql/pg_isready/docker/podman；已补 PostgreSQL 仓储脚本化游标合同测试，不能替代真实事务。 |
| L1-R19 | NOT_RUN | 未安装或运行 iPhone，20 分钟真机场景未执行。 |
| L1-R20 | PASS | `testLiveCanonicalOutboxDoesNotSealCompleteSuffixAcrossPendingGap`：完整 assistant 后缀不能越过 owner interim；owner final 后按 1、2 顺序 seal。 |
| L1-R21 | PASS | `testLiveCaptureCloseWithOnlyInterimMaterialRemainsDiscoverableAfterRestart`：close intent 已落盘、无伪水位，重建 store 后仍可发现 gap。 |
| L1-R22 | PASS | `testLiveCanonicalOutboxRetainsDeduplicationAfterAcknowledgement`：ACK 清理正文后同 ID 同内容零新增，异内容明确冲突。 |
| L1-R23 | NOT_RUN | 未执行原写迟到与显式恢复 retry 并发；未知 end/ack/admit 禁止重放的既有 B8 回归保持绿。 |
| L1-R24 | NOT_RUN | 两端各自 typed hash 与精确匹配已覆盖；Swift/Python Unicode、空白、角色、kind、时间、captureMode 的同向量交叉测试未建立。 |

## 汇总

- PASS：L1-R04、R10、R20、R21、R22。
- BLOCKED：L1-R18。
- 其余完整场景：NOT_RUN。
- 当前不存在可据此声明现场长场缺陷已关闭的证据。
