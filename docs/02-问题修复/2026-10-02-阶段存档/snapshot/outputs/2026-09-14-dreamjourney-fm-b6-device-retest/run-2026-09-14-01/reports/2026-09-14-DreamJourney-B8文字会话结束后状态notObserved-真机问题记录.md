# DreamJourney B8 文字会话结束后状态 `notObserved` 真机问题记录

## 1. 文档边界

- 问题编号：`B8-TEXT-END-NOT-OBSERVED-01`
- 状态：`FAIL`
- 记录日期：2026-09-14
- 测试设备：iPhone 14 Pro Max，iOS 26.4.1
- App Bundle ID：`com.gaominge.dreamjourney.app`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- App 可执行文件 SHA-256：`f9b809bbc6ed10b5ed8e4481e58acb1ac29373eea0bda1136afd22db633a3d8b`
- 生产地址：`https://www.mmdd10.tech/dreamjourney-api`
- 本文只记录文字会话结束后的会后任务可发现性问题，不讨论 Live 纯问题误生成候选。

## 2. 前置条件

1. 用户处于合法登录状态。
2. 使用真机诊断版本，未启用 QA 旁路、合成候选或故障注入。
3. 设备日志在操作前已开始采集。
4. 本次仅使用合成测试文字，不操作或清理历史候选、正式记忆和 Dead Letter。

## 3. 复现步骤

1. 在“回响”页面进入“文字回响”。
2. 用户发送一次合成测试文字：`本次B7隔离测试的未审核代号是云帆九号。`
3. 文字问答成功返回。
4. 用户结束文字会话。
5. 观察会后整理状态，不执行人工重试、审核或数据修补。

## 4. 预期结果

1. 会话内容被持久保存。
2. 原会后命令和批次能够被后续只读状态查询唯一发现。
3. 页面进入明确的 `noChange`、整理中、待确认或确定失败状态。
4. 若状态暂时未知，应保留原坐标并提供有界只读恢复，不应将服务端未观察到的任务伪装成成功。
5. 恢复阶段不得重放 `end/ack/admit`。

## 5. 实际结果

- 文字消息发送和回答均成功，无明显等待。
- 结束后页面最终显示：`当前无法继续整理，原对话已保留`。
- 新工作流在本地进入 `acknowledged`，但会后状态只读查询返回 `notObserved`。
- 页面进入 `actionRequired`，未形成可审核候选，B8 的“结束文字会话，再开 Live”前置闭环失败。
- 恢复阶段只观察到只读状态查询，未观察到额外的 `end/ack/admit` 重放。

现场截图：

- `../evidence/screenshots/B8-text-session-not-observed.jpg`

## 6. 脱敏日志时间线

关联工作流：`sha256:cd1cf012d8b13324`

```text
captureStateChanged from=live to=saving ownerTurnCount=1
captureStateChanged from=saving to=queued ownerTurnCount=1
endedReceiptDuplicateIgnored stage=organizationAlreadyOwned duplicateIgnored=1
captureStateChanged from=queued to=unavailable ownerTurnCount=1
planMerged phase=acknowledged sourceCount=2 workflow=sha256:cd1cf012d8b13324
readAttemptStarted readMode=readOnly trace=sha256:16bab952af86223a
responseReceived resource=liveMemoryRecoveryStatus httpStatus=200 responseBytes=632
resourceContractDecoded resource=liveMemoryRecoveryStatus
resultValidated workflow=sha256:cd1cf012d8b13324
roundFinished reason=notObserved workflow=sha256:cd1cf012d8b13324
uiCommitted state=actionRequired
```

后续重新扫描同一工作流仍得到 `notObserved`，未把结果改写为成功。

完整脱敏摘要：

- `../evidence/sanitized-logs/2026-09-14-B7-B8-device-evidence-summary.log`

## 7. 已证实与未证实

### 已证实

1. 文字回答链路成功。
2. 本地会后状态经过 `saving -> queued -> unavailable`。
3. 磁盘恢复入口发现新工作流，阶段为 `acknowledged`。
4. 状态接口真实返回 HTTP 200 并成功解码。
5. 解码后的业务结果是 `notObserved`，不是网络失败或 JSON 失败。
6. 页面准确显示需要处理的状态，没有假装整理成功。

### 尚未证实

1. `end`、`ack`、`admit` 中哪一阶段首次未在服务端形成可查询坐标。
2. 是服务端事务未提交、状态查询索引/绑定不一致、客户端持久坐标不完整，还是时序窗口导致。
3. HTTP 200 只证明查询成功，不证明原写命令已成功或确定未发送。
4. 本问题是否只影响文字会话，Live 与档案 Source 的成功不能替代该入口验收。

## 8. 影响与停止点

- B8“结束文字会话”分支：`FAIL`。
- 依赖该会话生成候选的验证：`BLOCKED`。
- 原对话保留：已由 UI 和本地状态确认，但不等于服务端整理成功。
- 不自动补发写请求，不删除本地恢复记录，不操作生产历史。

## 9. 给设计分析的核查要求

1. 逐段核对文字会话的 `end -> ack -> admit -> status GET` 真实命令、持久坐标和事务边界。
2. 建立真实 BackendClient、真实协调器、磁盘恢复和受控网络的确定性失败反例。
3. 区分 `notSent`、`outcomeUnknown`、`notObserved`、绑定冲突与合法终态。
4. 不得把 `notObserved` 当作确定未写入，也不得用重发 POST 充当查询。
5. 保留当前只读恢复、幂等命令、账号作用域、FeatureGate 和旧回调隔离。
6. 输出局部修复设计、红绿门禁、部署依赖、真机复测步骤和回退方案。
