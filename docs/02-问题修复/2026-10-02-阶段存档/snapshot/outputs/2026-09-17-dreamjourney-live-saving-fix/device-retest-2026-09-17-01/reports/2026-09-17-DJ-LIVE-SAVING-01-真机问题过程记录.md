# DJ-LIVE-SAVING-01 真机问题过程记录

## 一、记录目的

本文件只记录 2026-09-17 修复版本的真机测试过程和可证实事实，供后续独立分析。它不把客户端汇总状态直接当作根因，也不把冷启动后的历史 pendingReview 当作本场成功。

## 二、测试环境

- 设备：iPhone 14 Pro Max，iOS 27.0
- 连接：USB，有线连接，设备已解锁和配对
- App：DreamJourney `1.0.0 (1)`
- 安装：原位覆盖安装，保留 App 数据
- 安装包 executable SHA-256：`acd0a63e7f7830cd15a302671782bedeed404880872a8463bb2331effb8e4eff`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 日志：操作前已启动真机 console 采集；没有断点或暂停式调试

## 三、测试前基线

1. App 保持登录，当前页面为“回响”，无报错或卡住。
2. 麦克风上方状态为：`上次对话已进入待确认记忆`。
3. 页面按钮为：`核实整理状态`。
4. 该状态属于安装前已有历史任务，不能预先归属本次测试。

## 四、本场 Live 操作

1. 用户点击麦克风，正常进入聆听。
2. 用户输入一条新的合成测试信息，使用唯一测试标记。
3. AI 有声音回答，内容正确复述测试标记。
4. 回答完成后自动恢复聆听。
5. 全程没有 UI 报错或长时间等待。

安全日志确认：

- owner ASR 最终结果进入 canonical ingress；
- owner turn 从 interim 收敛为 complete；
- assistant stream 最终收敛为 complete；
- 本地 coverage 在停止前为 `registeredMembers=2`、`partialTurns=0`、`deliveryCount=2`；
- 本记录不保存用户正文、转写或助手正文。

## 五、手动停止后的现场表现

用户在 AI 回答完整播放、页面已恢复聆听后点击麦克风停止。

| 时间点 | 页面状态 |
|---|---|
| 刚停止 | `正在保存本次对话` |
| 约 5 秒 | 仍为 `正在保存本次对话` |
| 约 20 秒 | `本次对话仍在本机保存，联网后会继续同步` |

其他表现：

- 没有永久停留在 saving；
- 没有报错、持续转圈或自动重新聆听；
- 页面显示“文字回响”；
- 麦克风按钮仍可用。

## 六、停止阶段诊断证据

真机 console 中观察到以下脱敏序列：

```text
captureStateChanged from=syncPaused to=saving ownerTurnCount=1 persistedOwnerTurnCount=0 queuedTurnCount=2
canonicalCoverageObserved stage=closeIntentPersisted ... deliveryCount=2 ... partialTurns=0 registeredMembers=2
closingProgressBlocked nextOwner=closingDeadline reason=existingUseCaseNotSafelyResumable
closingProgressDeadlineExceeded coordinates=localOnly lastProgressStage=existingUseCaseBlocked outcome=notSent handoffCount=0 pendingPersistenceCount=0 queueCount=2
captureStateChanged from=saving to=syncPaused ownerTurnCount=1 persistedOwnerTurnCount=0 queuedTurnCount=2
```

可以直接确认：

1. 本轮新增的 closing deadline 生效，解决了 saving 永久无主。
2. 手动停止前协调器已处于 `syncPaused`，停止只是将它转为 saving。
3. 现有 NaturalInput UseCase 被判定为不能安全恢复。
4. 两条 canonical turn 留在本地队列，服务端确认数为 0。
5. 关闭坐标仍是 `localOnly`，没有进入可继续完成 end/ACK/admit 的状态。

不能仅凭这些日志确认：

- 为什么当前新 Live 会在停止前处于 syncPaused；
- 现有 UseCase 对应当前场还是历史场；
- 最初导致 UseCase 非 ready 的具体 current GET、授权或绑定分支；
- 真机此前现场是否由完全相同的分支触发。

## 七、冷启动恢复过程

1. 用户彻底划掉 App。
2. 重新启动后不操作页面，等待 15 秒。
3. 页面显示：`上次对话已进入待确认记忆`。
4. 按钮显示：`核实整理状态`。
5. 没有自动开启麦克风、报错或持续加载。

冷启动日志：

- 测试前启动扫描：`blockedCount=11`；
- 本场测试后启动扫描：`blockedCount=12`；
- 一个新 workflow hash `sha256:70f0a16cb8f117d5` 首次出现，并被分类为 `closingOutboxMissingCheckpoint`；
- 新 workflow 没有进入 read-only recovery plan；
- 另有 4 个旧 workflow 进入读取计划；
- 旧 workflow 的 UI 提交结果包含 `pendingReview` 和 `actionRequired`。

因此，“上次对话已进入待确认记忆”只能证明某个历史 workflow 的读取结果，不能证明本场关闭成功。

## 八、候选列表只读核对

用户进入待确认记忆并刷新，没有执行确认、拒绝或更正。

| 项目 | 结果 |
|---|---|
| 刷新前候选数 | 45 |
| 刷新后候选数 | 45 |
| 本场唯一测试标记候选 | 0 |
| 页面错误/卡住 | 无 |

网络证据：两次候选 GET 均实际创建并恢复任务，收到 HTTP 200，完成 JSON 与 typed decode，最终 UI 提交 `candidateCount=45`。

## 九、问题拆分

### 问题 A：同进程关闭没有完成

- 状态：`FAIL`
- 已确认入口：`existingUseCaseNotSafelyResumable`
- 结果：正文未投递，end/ACK/admit 未形成同场完成闭环，最终 syncPaused。
- 注意：有界退出 saving 已 PASS，但不能替代关闭闭环。

### 问题 B：本场冷启动恢复坐标不完整

- 状态：`FAIL`
- 已确认表现：新增 workflow 为 `closingOutboxMissingCheckpoint`，没有进入 read-only recovery plan。
- 结果：本场只能被保留为 blocked，不能只读核实或继续合法恢复。

### 问题 C：历史 workflow 状态覆盖本场页面文案

- 状态：`FAIL`，但属于此前明确排除的历史 UI 仲裁范围。
- 已确认表现：本场 blocked，页面却由旧 workflow 提交为 pendingReview。
- 结果：用户看到“上次对话已进入待确认记忆”，实际候选列表没有本场内容。
- 本轮仅记录，不修改。

## 十、Astra 需要重点回答的问题

1. 新 Live 开始时为什么复用了或保留了一个非 ready 的 NaturalInput UseCase，使 capture state 在停止前已为 syncPaused？
2. `resumeCurrentSessionReadForClosingIfSafe()` 的安全条件是否过窄，还是创建新 Live 时本应替换/隔离旧 UseCase 却没有做到？
3. 在 owner/assistant 两条正文已完整落盘、start 尚未安全建立的情况下，应如何保存足够的原命令和坐标，使当前进程能够合法推进，或冷启动后只读核实？
4. 为什么 close intent 已持久化，但重启后仍归类为 `closingOutboxMissingCheckpoint`？缺少的是哪个 checkpoint、绑定或阶段转换？
5. 如何保持“未知写不得重放”边界，同时允许明确 notSent、同一主体、同一场次的原命令首次合法发送？
6. 历史 workflow 与本场 workflow 的页面仲裁应如何隔离，避免旧 pendingReview 覆盖本场 syncPaused/blocked？该项应单独设计，不要与问题 A/B 合并根因。

## 十一、停止边界

- 未审核、删除或更正任何候选。
- 未清理或补写 blocked workflow。
- 未重发历史 start/append/end/ACK/admit。
- 未部署、commit 或 push。
- 依赖本场完成的正式记忆和问答回查已停止。

最终现场状态：`LOCAL_PASS / DEVICE_FAIL`。
