# ISSUE-B6-01：App 重启后已完成会后任务显示“无法继续整理”

## 1. 问题状态

- 严重度：P1，阻断 B6 完整通过。
- 现场结果：FAIL。
- 发现环境：iPhone 14 Pro Max，iOS 26.4.1，生产配置，USB 日志连接。
- 发现时间：2026-09-14 01:20-01:22（Asia/Shanghai）。
- 数据边界：仅使用用户主动口述的合成信息；未审核候选，未修改正式记忆。

## 2. 用户可见现象

用户在 Live 中输入一条合成信息，收到有声回复后手动停止。在页面显示“正在整理”时彻底关闭 App。重新启动后：

- 回响页显示“当前无法继续整理，原对话已保留”。
- 没有自动开启麦克风或进入聆听。
- 页面没有额外错误弹窗。

稍后通过只读核对确认：本场后台任务已经完成，并生成了 1 条待审核候选。

## 3. 稳定复现步骤

1. 在合法登录态打开回响，开始一场新的原生 Live。
2. 口述一条唯一、可识别的合成新信息。
3. 等待一次有声回复。
4. 手动停止 Live。
5. 页面进入“正在整理”时立即将 App 切到后台，并从任务切换器彻底关闭。
6. 等待几十秒，让服务端任务有机会继续完成。
7. 重新启动 App，不点击麦克风。
8. 观察回响页的会后状态。
9. 通过只读接口和数据库核对原会后任务状态。

实际结果：步骤 8 显示“当前无法继续整理，原对话已保留”，但步骤 9 证明服务端已经完成原任务并生成候选。

期望结果：重启后发现原检查点，通过原账号、vault、authority 和批次进行只读状态恢复；若后台已完成，应收敛到“已进入待确认记忆”或等价完成态，不应显示不可恢复。

## 4. 客户端证据

关闭前的脱敏状态序列：

```text
conversationSummaryPersisted
captureStateChanged live -> saving
captureStateChanged saving -> queued
endedReceiptDuplicateIgnored organizationAlreadyOwned
applicationDidEnterBackground
processExit=0
```

重启后的脱敏状态序列：

```text
ownerScopedMemoryMounted
captureStateChanged live -> queued
captureStateChanged queued -> unavailable
```

启动早期还观察到账号租约/发布策略尚未完全恢复的窗口。现有证据只能证明该时序与错误状态相邻，不能直接认定它是唯一根因。

## 5. 服务端只读证据

同一场会后流程具有以下事实：

- `/end`：201。
- `/candidate-proposal/admit`：201。
- `/candidate-proposal/status`：200。
- Candidate Extraction Worker：`status=completed`。
- 业务结果：`candidateOutcome=pendingReview`。
- 本场生成候选数：1。
- 同一来源候选数：1。

因此，本次不是服务端整理失败，也不是候选未生成；错误发生在客户端重启后的检查点发现、恢复门禁、状态查询或终态映射链路。

## 6. 与本问题直接相关的排除证据

- 重启后没有自动开启麦克风，未把新表达接入旧关闭场次。
- 原会后任务的服务端 end、admit、status 和 Worker 均已成功。
- 原任务只生成 1 条候选，不存在同一工作流的重复写入证据。

这些证据只用于确定故障位于客户端重启恢复与终态展示，不扩展为其他问题分析。

## 7. 待 Astra 核查的真实调用路径

请独立核对以下路径，不要仅根据日志顺序猜测：

1. App 冷启动时会后 checkpoint、outbox、follow-up 的发现与合并顺序。
2. `EchoViewController` 的恢复入口是否在账号租约与发布策略可用前执行一次后，就把检查点终止为 unavailable。
3. 启动期 `leaseUnavailable`、`expiredPolicyCache`、账号挂载尚未完成时，是否应保持可恢复状态并在合法作用域恢复后重试只读查询。
4. 后台已完成时，状态接口结果如何映射为 completed、completedNoChange、pendingReview 或 failed。
5. 旧的启动回调是否可能在新的状态查询完成后覆盖页面终态。
6. 恢复协调器是否错误依赖旧 Controller、内存中的 productSessionID 或旧闭包，而不是磁盘检查点。

## 8. 修复要求

1. 建立真实冷启动反例：queued/organizing 时杀掉 App，后台在 App 关闭期间完成。
2. 重建页面和恢复协调器，不由测试直接注入旧 productSessionID。
3. 仅凭磁盘检查点和合法账号作用域发现原工作流。
4. 启动期暂时缺少账号租约或发布策略时，不得提前终止为 unavailable；应有界等待或在合法状态恢复后重新触发只读核实。
5. 后台已完成时，页面必须收敛到准确终态。
6. 不重新发送 end、ack、admit，不新建 session、batch、source 或 candidate。
7. 不自动启动麦克风，不把新表达接入旧场次。
8. 迟到回调不得覆盖新的完成状态。
9. 失败和超时必须保留检查点、准确显示失败层，并允许后续只读核实。

## 9. 必须补充的回归场景

- end 接受前后退出。
- ack 接受前后退出。
- admit 接受前后退出。
- status 为 queued/running 时退出，关闭期间转 completed。
- 启动时账号租约晚于恢复扫描就绪。
- 启动时策略缓存过期，策略恢复后继续原状态读取。
- 状态查询 401、离线、超时和迟到 200。
- 重复启动、前后台切换和连续进入回响页保持单飞。
- 恢复期间新 Live 身份独立，且不得复用旧关闭场次。
- 同一工作流最终只有一个 source、batch 和候选集合。

测试需贯穿真实页面生命周期、UseCase、BackendClient、磁盘存储及受控网络；服务端合同使用隔离 PostgreSQL 和合成数据验证。编译成功或单个接口 200 不能替代冷启动恢复验收。

## 10. 不得改变的边界

- 保留火山原生 Live 的声音、持续聆听、低延迟和打断。
- 不恢复 ASR -> DeepSeek -> TTS 串行链路。
- 正式记忆仍是唯一事实来源；本场未审核候选不得进入正式记忆。
- 不放宽账号、authority、FeatureGate、CAS、哈希或 Binding。
- 不通过延时、提前显示成功、删除检查点或自动重放写请求掩盖问题。
- 不处理生产候选、正式记忆、历史或 Dead Letter。

## 11. 本问题证据索引

- `/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b5-b6-device-test/run-2026-09-14-01/evidence/device/environment.txt`
- `/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b5-b6-device-test/run-2026-09-14-01/evidence/device/b6-restart-recovery.txt`

原始控制台仅实时观察，未保存包含业务标识或私人内容的原始日志；交付证据均为脱敏摘要。
