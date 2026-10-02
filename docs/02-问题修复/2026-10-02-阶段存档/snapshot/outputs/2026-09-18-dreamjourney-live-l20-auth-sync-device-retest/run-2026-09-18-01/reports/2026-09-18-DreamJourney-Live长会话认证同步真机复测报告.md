# DreamJourney Live 长会话认证同步真机复测报告

## 结论

**状态：DEVICE_FAIL / NEEDS_LOCAL_DIAGNOSIS**

本轮证明整场采集、正文同步、end、ACK 与 admission 可以在原场次内完成，但没有完成“同场终态确认 -> 本场候选合法可见”的闭环。不能标记 Live 长会话或相关 B4/B8 项通过。

## 验收矩阵

| 项目 | 状态 | 证据 |
|---|---|---|
| 原位安装及登录态保留 | PASS | 真机可正常进入并读取 46 条候选 |
| 20+ 分钟持续 Live | PASS | 多轮自然交流持续至手动停止 |
| 声音、长回答、持续聆听 | PASS | 全程有声、无非预期中断、自动恢复聆听 |
| 原场次采集与正文同步 | PASS | 16 个用户轮次；32/32 登记内容获服务端确认 |
| end 回执绑定及检查点 | PASS | POST 201，binding accepted，checkpoint committed |
| ACK 回执绑定及检查点 | PASS | POST 201，binding accepted，checkpoint committed |
| admission 回执绑定及检查点 | PASS | POST 201，typed decode、binding、checkpoint 均通过 |
| 未知写禁止自动重放 | PASS | 停止、页面重入及冷启动期间未观察到重复业务 POST |
| 同进程即时终态确认 | FAIL | 6 次 GET 后预算耗尽，`organizing -> statusUnknown` |
| 未知状态页面只读入口 | FAIL | 首次 `statusUnknown` 页面没有“核实整理状态”入口 |
| 冷启动本场只读恢复 | PASS | 原 workflow 只读核实为 `pendingReview`，零业务写 |
| 历史工作流 UI 归属 | FAIL | 冷启动先显示历史失败，之后才被本场 pendingReview 覆盖 |
| “核实整理状态”目标归属 | FAIL | 按钮 GET 查询了另一条历史 workflow，未查询本场 |
| 本场候选真实读取 | FAIL | 多次真实 GET 均返回 46；五个本场标记均为 0 |
| 自然 401/凭据 successor | NOT_OBSERVED | 本场日志未出现自然 401，不能以时长推定通过 |
| 后续候选审核闭环 | NOT_RUN | 候选不可见，停止依赖该结果的后续操作 |

## 已确认的问题

### DJ-LIVE-L20-DEVICE-01：即时状态预算早于服务端终态耗尽

admission 已合法提交，但客户端只读轮询在服务端仍为 `organizing` 时耗尽 6 次预算并进入 `statusUnknown`。网络请求均成功，因此页面提示“联网后继续查询”误导了实际失败层。

### DJ-LIVE-L20-DEVICE-02：未知状态缺少安全只读核实入口

同进程进入 `statusUnknown` 后，页面只提供“文字回响”与麦克风。用户无法核实原 workflow，只能离开页面或重启 App 才触发恢复。

### DJ-LIVE-L20-DEVICE-03：历史 workflow 状态覆盖当前场显示

冷启动扫描同时处理多个历史 workflow。当前场只读结果最终为 `pendingReview`，但页面先显示“上次整理失败，原对话已保留”，之后才自动变化。UI 未稳定绑定当前场次。

### DJ-LIVE-L20-DEVICE-04：pendingReview 与候选列表不可见不一致

当前场只读状态已验证为 `pendingReview`，但候选列表多次真实读取均为 46，五个合成标记均不可见。需要用服务端只读证据继续区分：候选 Worker 尚未生成、生成 noChange、状态语义映射错误、索引/账号作用域不一致，或候选读取未包含该批次。本轮证据不足以指定唯一根因。

### DJ-LIVE-L20-DEVICE-05：“核实整理状态”查询了错误的历史 workflow

用户在页面显示“尚未确认开始整理，可再次核实”时单击一次按钮。客户端确实执行了只读 GET，但请求绑定的是另一条历史 workflow，并返回 `readyForAdmission/actionRequired`；本场 workflow 没有被核实。页面因此没有任何变化。该问题不是按钮未响应，也不是网络失败，而是恢复入口的任务归属错误。

## 独立观察

- 开场标记“云杉六十一号”在 AI 复述时变为“云山六十一号”。屏幕识别与后续其他标记正常，该项单独记录，不作为本轮候选缺失的已证实根因。
- 候选读取曾成功完成 expired policy cache 的只读恢复。这只能证明候选 GET 的策略恢复，不证明 Live 期间自然凭据轮换已发生。

## 下一步建议

1. 先停止继续制造新 Live 场次，保留当前恢复坐标与服务端数据。
2. 以本场脱敏 workflow 为关联，执行经授权的服务端只读核查：admission 事务、Worker 执行结果、noChange/失败原因、候选批次和账号作用域。
3. 本地建立四个独立反例：轮询预算耗尽后的同页只读恢复、`statusUnknown` 按钮、多个历史 workflow 的 UI 仲裁、`pendingReview` 但候选 GET 不含本批次。
4. 增加按钮目标绑定反例：当前场 A 与历史场 B 同时存在时，点击 A 页面上的“核实整理状态”只能查询 A，不能查询 B。
5. 修复后再做一场最小真机闭环；不要用本轮 HTTP 201/200 或最终页面文案替代候选可见性验收。

## 证据路径

- 人工观察：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-device-observations.md`
- 脱敏阶段日志：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-sanitized-console-sequence.md`
- 本报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步真机复测报告.md`

## 边界

- 未部署后端、未操作生产业务数据、未审核候选、未清理历史任务。
- 未 commit/push。
- B4/B8 及其他专项状态不因本轮局部通过项而升级。
