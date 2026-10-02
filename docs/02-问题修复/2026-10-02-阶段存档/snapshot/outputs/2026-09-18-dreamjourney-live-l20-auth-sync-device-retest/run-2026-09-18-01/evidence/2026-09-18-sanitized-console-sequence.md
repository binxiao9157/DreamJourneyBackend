# 脱敏控制台阶段证据

本文件仅记录白名单阶段、计数和脱敏关联。未记录正文、转写、令牌、请求头、原始命令或原始身份标识。

## 本场关闭链

- 本场脱敏 workflow：`sha256:e716db2a62d198a3`
- 停止时采集状态：`ownerTurnCount=16`、`persistedOwnerTurnCount=16`、`queuedTurnCount=0`
- 正文同步：`deliveryCount=32`、`registeredMembers=32`、`serverConfirmedCount=32`
- end：POST HTTP 201，回执绑定通过，检查点提交成功
- ACK：新鲜授权通过，POST HTTP 201，回执绑定通过，检查点提交成功
- admission：新鲜授权通过，POST HTTP 201，typed decode 通过，回执绑定通过，检查点与 follow-up 坐标提交成功
- 未观察到自动重复发送 end、ACK 或 admission

## 同进程即时状态读取

- 同一状态读取 trace 内执行 attempt 1 至 attempt 6
- 每次 GET 均为 HTTP 200，typed decode 与结果校验通过
- 服务端状态从 `queued` 进入 `organizing`
- attempt 6 后以 `budgetExhausted` 结束
- 页面状态随后从 `organizing` 进入 `statusUnknown`
- 网络当时可用，因此“联网后继续查询”并不准确描述已观察到的直接失败层

## 页面重入与冷启动

- 页面重入及冷启动恢复仅观察到 `liveMemoryRecoveryStatus` GET，没有新增业务 POST
- 冷启动扫描重新发现本场 workflow，phase 为 `admitted`
- 本场只读结果通过校验并持久化 completion observation，最终提交 `pendingReview`
- 同一次扫描还包含多条历史 workflow，期间出现 `pendingReview`、`actionRequired`、`terminalFailure` 等交错 UI 提交
- 这与冷启动先显示“上次整理失败”、随后自动变为“已进入待确认记忆”的现象一致

## 候选列表

- 自动恢复后，候选 GET：HTTP 200
- JSON 与 typed contract 解码成功
- 服务端返回并提交 UI：`candidateCount=46`
- 用户再次手动刷新后，第二个独立 GET 同样 HTTP 200、typed decode 成功、`candidateCount=46`
- 五个本场合成标记均不可见

## “核实整理状态”按钮

- 用户单击按钮后，客户端确实发起一个 `liveMemoryRecoveryStatus` GET
- GET 为 HTTP 200，typed decode 与结果校验通过
- 该 GET 绑定的 workflow 为另一条历史记录，不是本场 `sha256:e716db2a62d198a3`
- 历史 workflow 返回 `readyForAdmission/actionRequired`
- 本场 workflow 没有因这次按钮操作获得新的只读核实
- 页面因此保持“尚未确认开始整理，可再次核实”，无可见反馈
- 点击期间未观察到业务写 POST

## 尚未观察到

- 本场 Live 期间未观察到自然 401 或凭据 successor，因此“长会话自然凭据轮换恢复”保持 NOT_OBSERVED。
- 候选列表曾出现 expired policy cache 的只读刷新成功，但这不能替代 Live 长会话认证恢复验收。
