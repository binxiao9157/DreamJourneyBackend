# DreamJourney B8 文字会话结束 notObserved 本地修复报告

## 1. 状态

`B8_A_LOCAL_INCOMPLETE`

iOS 局部实现、11 项定向测试、跨进程模拟器 UIQA、603 项完整回归和双目标编译通过；隔离 PostgreSQL T15-T17、admit 已提交但响应丢失的完整查询收敛反例，以及真机 T20 尚未完成。因此不标记 `READY_FOR_B8_DEVICE_RETEST`。现场 B8 继续为 `FAIL`。

## 2. 已确认根因与待验证假设

已确认代码缺口：

1. end/ack/admit 的旧写入口只有普通 `Result`，不能把发送前拒绝、已暴露后未知、HTTP 明确拒绝和合法提交分开。
2. admission 遇到策略未就绪时直接终止，缺少原正常关闭执行器内的一次有界只读策略恢复。
3. checkpoint 在 end/ack/admit 回执落盘窗口缺少逐阶段传输结果，冷启动可能把未知状态当成可重放准备态。
4. status 的 `readyForAdmission` 被并入 notObserved，导致 acknowledgement 后的真实阶段丢失。
5. typed 关闭写没有传安全 trace；诊断无法区分 request 创建、task 创建、task 恢复、HTTP 和解码。

修复前实测还确认：真实 BackendClient 收到已暴露 401 时，分类未落入明确 `serverRejected`，B8 定向测试失败。修复后同场景只发送一次 POST，记录 taskCreated/taskResumed/HTTP 401，且不做认证写重放。

仍待验证：现场第一次停止发生在 ack 后哪个具体阶段；`notObserved` 只是客户端汇总原因，未据此猜测数据库、网络或策略为唯一根因。

## 3. 修改

| 文件 | 函数/区域 | 局部修改 |
|---|---|---|
| `OwnerTruthContracts.swift` | end/ack/admit typed client、`OwnerTruthInterviewWriteTransportOutcome`、三个 UseCase | 引入 notSent/outcomeUnknown/serverRejected/committed；未知后禁止新写；原 command 先落盘；admission 一次策略恢复和 30 秒收尾；迟到回调按 generation 隔离 |
| `DreamJourneyBackendClient.swift` | 三个 `*Write`、`requestJSON` task lifecycle probe | 关闭业务写的认证/恢复自动重试；按真实 task resume 标记 exposure；HTTP 明确拒绝与无响应分离；三条写入使用随机安全 trace，不记录业务 ID |
| `EchoViewController.swift` | completion checkpoint store、restore、协调器 status 分类和 UI 文案 | 持久化 end/ack/admit 结果；冷启动只读核实、不重放；pendingAcknowledgement/readyForAdmission/409/410 分层；真实策略刷新接线 |
| `MemoryArchiveViewController.swift` | 新 phase/notice 渲染 | 补齐新状态展示，不放宽审核权 |
| `OwnerTruthContractsTests.swift` | 10 个 B8 + 1 个 B6 保持性测试 | 覆盖策略、存储、未知写、401、发送前取消、阶段显示和只读 discovery |

未改变火山原生 Live、声音、持续聆听、打断、正式记忆快照绑定或文字问答。

## 4. 红绿证据

### 红例

- `b8/green/b8-targeted-v2.xcresult`：10 项中 1 项失败。
- 失败断言：`testB8RealBackendAdmission401NeverReplaysBusinessPost` 未得到“已暴露 401 的明确拒绝”分类。
- 其余新增用例是在修复开发过程中建立，不能伪称全部具有独立修复前 result bundle。

### 绿例

- `b8/green/b8-targeted-final.xcresult`：11/11 PASS。
- `b8/green/b8-request-cancel-before-task-v6.xcresult`：PASS。锁定 Alamofire 5.10 的真实语义：`requestCreated` 后、URLSessionTask 创建前取消；无 taskCreated/taskResumed/HTTP/POST，结果为 notSent。
- `combined/ios-full-final.xcresult`：603/603 PASS。
- `final/b8-targeted-summary.json`、`final/ios-full-summary.json`：机器可读摘要。

## 5. 跨进程 UIQA

证据目录：`b8/uiqa/b7-b8-20260914-v2/`

- 第一进程 PID 16274：append/end/ack/admit 各 1 次，磁盘 follow-up 存在。
- 第二进程 PID 16299：只凭磁盘恢复，同 workflow 安全哈希；真实 `DreamJourneyBackendClient` 适配，status GET=1。
- 恢复业务写=0，未启动麦克风，未创建新 capture。
- 页面显示“上次对话已进入待确认记忆”。
- 截图：`02-recovered-pending-review.png`。

## 6. B8 矩阵

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| B8-R01 ack 后策略过期 | PASS | 原 command 先落盘，一次策略恢复；超时后迟到成功不发送 |
| B8-R02 admission 落盘失败 | PASS | 明确 storageFailed，admit POST=0 |
| B8-R03 admit 提交但响应丢失 | NOT_RUN | 未完成“服务端已提交 + 响应丢失 + 同 batch GET 收敛”的单一组合反例；未知后零新写子项已 PASS |
| B8-R04 readyForAdmission/409/410 | PASS | readyForAdmission 独立显示；既有 status 合同回归通过 |
| B8-T05 完整 watermark 前禁止关闭 | PASS | sequence/close watermark 保持测试通过 |
| B8-T06 end 前后崩溃 | PASS | endPrepared 通过严格只读 discovery 找到已接受 batch，end 重放=0 |
| B8-T07 ack 前后崩溃 | PASS | pendingAcknowledgement/readyForAdmission 分层，恢复写=0 |
| B8-T08 admit 曝光边界 | PASS | 发送前取消 notSent；暴露后失败 outcomeUnknown；未知后禁止新写 |
| B8-T09 解码/绑定/迟到回调 | PASS | typed 合同与 generation fencing 回归通过 |
| B8-T10 重复回执/生命周期 | PASS | 单调 phase、原 command 幂等保持测试通过 |
| B8-T11 账号/租约/authority | PASS | 相关 OwnerTruth 作用域和旧回调隔离测试通过 |
| B8-T12 checkpoint/outbox/follow-up | PASS | V1/V2、损坏和 B6 只读恢复保持测试通过 |
| B8-T13 status 元组/UI | PASS | readyForAdmission 新反例及完整 status 合同回归通过 |
| B8-T14 网络/HTTP/预算 | PASS | 发送前取消、401、策略超时及既有网络诊断回归通过；不以 HTTP 200 代替业务结果 |
| B8-T15 PG admission 故障点 | BLOCKED | 本机无隔离 PostgreSQL 工具，未访问生产替代 |
| B8-T16 PG 并发/幂等/CAS | BLOCKED | 同上 |
| B8-T17 PG end/ack/status 零写读取 | BLOCKED | 同上 |
| B8-T18 两进程真实恢复 | PASS | 两 PID、磁盘、BackendClient、status GET、UI、恢复写 0 |
| B8-T19 恢复与新 Live 隔离 | PASS | 恢复未开麦/未建 capture；相关完整回归通过 |
| B8-T20 文字结束→新 Live/重启→新 Live | NOT_RUN | 必须后续授权真机闭环，本地证据不替代 |

## 7. 编译、指纹与安全

- 通用模拟器：`combined/builds/simulator-final.xcresult`，PASS。
- 通用 iOS 设备目标：`combined/builds/generic-ios-device-final.xcresult`，PASS，未安装设备。
- `git diff --check`：PASS。
- 最终指纹：`final/ios-relevant-source-sha256.txt`。
- 安全日志只包含白名单阶段、attempt、时间、计数与随机 trace 哈希；扫描未发现凭据值、正文或完整响应。

## 8. 部署判断、风险与回退

- B8 是 iOS 局部修复，无本问题后端业务改动，不需要后端部署；本轮也未部署 App。
- R03 单一组合反例和隔离 PG 未完成，不能证明所有未知提交都能现场收敛。
- 真机前需安装本报告指纹对应构建，关闭 QA/故障注入，按设计步骤验证真实文字结束和新 Live。
- 局部回退仅撤销 typed 写分类、checkpoint 可选字段、admission 有界策略恢复、status 分层和对应 UI/测试块；必须保留 B6 只读 registry、磁盘扫描、round fencing，且绝不能恢复冷启动写重放。
