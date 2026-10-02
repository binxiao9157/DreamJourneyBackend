# DreamJourney Live 真机复测记录

## 安装基线

- 日期：2026-09-17
- 设备：Minge的iPhone（iPhone15,3，iOS 27.0）
- Bundle ID：`com.gaominge.dreamjourney.app`
- 版本：`1.0.0 (1)`
- 配置：Debug 设备包，无 QA/合成数据/故障注入启动参数
- 后端地址：`https://www.mmdd10.tech/dreamjourney-api`
- 可执行文件 SHA-256：`3ec91819ca5ae488a013b5637deaae0cc2046bda166c01802ae8753263132b47`
- 安装：原位覆盖成功，未卸载，未清除 App 数据

## 步骤 1：候选列表基线

- 状态：PASS
- 刷新前：45 条
- 刷新后：45 条
- 页面错误：无
- 超过 10 秒的加载/刷新：无
- 证据：两次真实候选 GET 均完成账号租约、运行策略、FeatureGate、taskResumed、HTTP 200、JSON/typed decode 和 UI commit，均提交 `candidateCount=45`。
- trace：`d5960ee51eaa2751`、`9b7635d17bdacdde`（脱敏日志标识）

## 步骤 2：Live 关闭交接边界

### 2A 第一段

- 状态：PASS（Live 交互子项）
- 合成内容：第一段代号“松林十一号”
- 结果：正常进入聆听，有声音回答，回答后自动恢复聆听，无错误或长时等待。

### 2B 第二段说完立即停止

- 状态：FAIL
- 合成内容：第二段代号“银桥十二号”
- 操作：说完最后一字后立即手动停止。
- 声音：停止时已听到“了解了”，随后的语音被手动停止。
- 最终 UI：“已保存收到的内容，部分内容尚未完整记录”，15 秒后不变。
- 稳定性：无错误、无持续转圈、无长时等待。
- 关键证据：第二段 ASR final 到达；停止时日志为 `persistedOwnerTurnCount=0` / `queuedTurnCount=0`，随后 `coverageGapPresented unsealedTurnCount=5`。
- 候选核对：刷新前后均为 45 条，“松林十一号”和“银桥十二号”均为 0 条。
- 候选网络证据：两次真实 GET 均通过门禁并完成 `taskResumed -> HTTP 200 -> typedDecode -> uiCommitted`，均返回 `candidateCount=45`；不是缓存显示或刷新失败。
- 候选 trace：`bf4069f5427e4209`、`7349d316e9111910`（脱敏日志标识）。
- 判定：第一段已完成 Live 对答，第二段 ASR final 也到达，但关闭交接未封存任何一段，本场完整性验收失败。

## 后续状态

- Live 关闭交接边界：FAIL
- 同场候选完整性/去重：NOT_RUN
- 跨策略 TTL 持续 Live：NOT_RUN
- App 重启只读恢复：NOT_RUN
- 声音/长回答/打断保持性：NOT_RUN

## 步骤 4：当前进程的失败场次安全边界

- 状态：PASS（仅安全边界）
- 刚返回回响：“上次对话已保存，尚未确认开始整理，可再次核实”。
- 10 秒后：不变。
- 操作入口：“核实整理状态”。
- 自动开麦：否。
- 错误/卡住/旧场覆盖：无。
- 判定：当前进程未把失败场次假装成成功，也未自动开始新 Live。

## 步骤 5：冷启动只读恢复

- 状态：FAIL
- 重启前候选证据：本场两个代号均为 0 条。
- 冷启动刚进入回响：“上次对话已进入待确认记忆”。
- 10 秒后：不变。
- 操作入口：“核实整理状态”。
- 自动开麦：否。
- 错误/卡住：无。
- 冷启动日志：扫描 `blockedCount=8`；新增阻断工作流原因为 `closingOutboxMissingCheckpoint`，同时历史工作流返回 `pendingReview`。
- 判定：当前失败场次没有已进入候选的证据，但 UI 展示了历史成功状态；存在跨场状态归属/优先级错误。该问题与“当前场内容未封存”分开记录。

## 本轮停止判断

- 关闭交接主场景：FAIL。
- 冷启动当前场状态归属：FAIL。
- 为避免继续产生未封存任务，跨 TTL Live 和后续新 Live 场景不再执行，保持 NOT_RUN。
- 本轮不点击候选审核，不清理历史记录，不重放业务写请求。
