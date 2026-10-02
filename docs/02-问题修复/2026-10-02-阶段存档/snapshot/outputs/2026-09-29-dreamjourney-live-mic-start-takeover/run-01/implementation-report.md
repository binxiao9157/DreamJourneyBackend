# DJ-LIVE-START-20260924：Astra 接手本地收尾

日期：2026-09-29。**LOCAL_PASS**；当前汇总与外部边界见同目录 README.md。

## 范围与结论边界

按 [run-04 复核第2节 R2/R3/R4](../../2026-09-29-dreamjourney-live-mic-start-review/run-04/复核结论与Sol剩余任务.md) 接手实现。保留进入本轮时两个仓库的 dirty 工作区，不重写短长保存链、重试预算或音频协议。后端源码未变，与进入本轮的 diff 逐字一致。

本轮可以证明有限本地代码缺陷及测试装配问题，不能据此认定历史近一分钟等待、23:04 单次失败、seq5 首发原因已查明。真实 Provider、原生供应商二进制运行时、iPhone、声学、物理20/65分钟、部署、生产及历史处理均未运行。未 commit/push。

## 具体改动

仅本轮差异见 [takeover-only.diff](takeover-only.diff)，进入时源码见 entry-source，前后 SHA256 见 [takeover-changed-files.json](takeover-changed-files.json)。整个 git dirty diff 含既有工作，不归为本轮改动。

1. **阻止同一启动二次终态处理。**实际 Manager 的同步 StartEngine 失败会先经 onError 结束启动；返回 Controller 后，旧调用又把 inactive attempt 当作 timeout 再处理。`handleBlockedRealtimeVoice` 增加传入 attempt 与当前 attempt 的身份检查，已结束旧 attempt 不再重复关闭或改写新场。修前实测出现 `voiceSDKStartFailed`、`voiceLaunchTimeout` 两次终态，修后原“只终结一次”断言通过。
2. **关闭取消场次残留的转写入口。**最终审查补上实际 ingress binding 与 Stop 指令断言后，`missingStarted` 和 `lateAfterCancel` 均产生红测：停止引擎/清理身份并未关闭已安装的原场入口。在 `finishCancelledVoiceLaunch` 清空身份前，按原 operation ID 关闭该入口。`native-cleanup-red.xcresult` 保留两个活动的同断言失败，`native-mic-final-02.xcresult` 同断言绿；入口、Stop、greeting、音频 lease 均直接检查，不再仅推断。
3. **真正执行生产 Manager 编排。**新增仅 DEBUG Simulator 可用的 `LIVE_MANAGER_CONTROLLED_SDK` 编译入口，替换最外层 SpeechEngine 边界，执行原生分支真实 setup、StartEngine、delegate proxy、SessionStarted、取消、complete、ingress 清理及 AudioSessionCoordinator。仅 SDK 的返回码/消息可控；没有复制另一套启动状态机。误用于非 DEBUG 或非 Simulator 会编译报错。它不证明火山二进制、网络握手或音频效果。
4. **隔离单元测试的非目标后台活动。**测试 Controller 安装器在 loadView 前停止附带的音色能力查询；显式环境 `DJ_ISOLATED_UNIT_TEST_HOST=1` 仅在 DEBUG Simulator 跳过 App/Scene 全局启动。默认 App、四场集成和设备构建不启用该环境。原因是附带共享 Client runtime 查询可能改写测试自有 authority epoch，使活动 A/B 尚未到目标阶段就失效。
5. **修复恢复入口测试的共享磁盘污染。**`testLiveRecoveryEntryDiscoversEveryCompletionCheckpointWithoutKnownSessionID` 使用专属 follow-up 目录和恢复注册表。原“扫描一条恢复任务”与请求预算断言保留；没有扩大 GET 上限。
6. **补真实共享认证刷新和故障组合。**真实 auth coalescer 的只读等待者计数用于完成屏障；HTTP URLProtocol 记录实际取消回执；Manager delegate 队列用完成计数屏障。测试不靠延长固定 sleep 判断通过。

## R2：Manager 完整受控链

[native-mic-final-02.xcresult](native-mic-final-02.xcresult) 39/39 PASS，其中 `testMICProductionManagerControlledSDKLifecycle` 包含11个有名场景：

| 场景 | 核心断言 |
|---|---|
| setupDeadline | SDK init 跨总截止，StartEngine=0，一次超时，音频 lease 释放 |
| directiveFailure | StartEngine 返回错误，一次 SDK 失败；迟到 success/error 不产生第二终态/聆听/问候 |
| missingStarted | 已发送但缺 SessionStarted，有界超时，后续消息无效 |
| lateAfterCancel | 用户显式 stop，Controller idle，晚到截止与消息不重新启动或变成超时 |
| handoff | SessionStarted 后成功移交；旧启动计时器不关闭会话；重复 started 不重复问候 |
| replacement | 旧引擎 success/error 不影响替换场；新场只启动一次 |
| cumulative | policy + runtime + auth + SDK 累计超过15秒，墙钟倒退不重置单调预算，0 StartEngine |
| authDeadline | 等待认证时总截止；迟到认证成功不创建第二 ticket |
| authRotation | 认证等待中底层账号失效，由有界总截止检查结束为 accountLeaseInvalid；不二次发票据。页面重绑即时取消另有独立测试 |
| runtimeDeadline | runtime 检查边界跨截止，0 ticket、0 SDK |
| networkLoss | 第一次 ticket 连接丢失，1 POST、network 原因、无虚构 HTTP 状态、不重发 |

健康场通过真实 Manager 检查 SDK header 中合成 ticket 与 StartEngine payload 中 providerRoleText 的精确值；诊断阶段 elapsed 单调，permissionEnd=1000ms、sdkStart阶段入口=1000ms、listening=6000ms、请求1次。取消路径直接核对实际router入口关闭；missingStarted/显式取消的Stop为2次（启动前1次、取消1次），健康移交后仅保留启动前1次，替换场旧stop为2次。

sdkStart 是阶段入口，包含随后 Manager 初始化耗时，不能误解为 SDK 已启动时刻。

`testMICManagerOldOperationCloseKeepsNewAssistantIngressBuffer` 验证本轮进入时已存在的清理修复：旧 operation 不清新场助手缓冲。该修复保留，未重复归为本轮新增。SDK shim 字符串参数标识用于受控调用检查，不声称是供应商 wire 编码验收。

## R3：旧场 A 活动与新场 B 失败

`testMIC19ActiveAAppendContinuesAfterBSDKFailure` 现在包含 deny / timeout / SDK failure / 无B对照四种情况：

- A 经真实 Controller、canonical ingress、Coordinator 和磁盘达到 append 在途，保持其响应完成闭包。
- B 经 Client/FeatureGate；deny 不发 ticket、不创建 capture；timeout 与 SDK failure 到达各自实际启动阶段。
- B 失败不得提前 end A，不得替 A ACK/admit，不得清 A 的冻结文本、命令与身份。
- B 为空 capture 时正确进入 empty 并释放 Controller 持有；不会凭空生成 outbox/end。等待实际异步释放完成后断言。
- 释放 A 的原 append 回执后，A 正常完成一次 end，原用户正文只出现一次，账号 lease 仍有效。无B对照使用同样屏障。

这里的 transport 是可控端口；真实 PostgreSQL 的候选、审核、正式记忆与重建回查由最终四场独立提供，不混称这一用例已独自验证全部PG链。

## R4：共享等待、预算和归属

`testMIC04ActualSharedAuthRefreshSurvivesFirstLaunchCancellation` 使用真实 refreshAuthSession 分组/队列和 AccountSessionActor、CAS session store，只替换 HTTP transport。两个启动等待同一个 refresh，等待实际 coalescer waiter=2 后取消第一个；第二个成功，refresh POST=1，ticket POST=3（两个首401和一个存活者重试），第一个成功回调=0，等待者最终清空，账号与 successor session 精确匹配。不是用测试自己的 completion 列表模拟共享刷新。

保留并重跑首次/第二次 ticket 曝光序号、首401后第二次未知结果不重发、迟到第一次回执不能覆盖第二次曝光、策略deny不耗错误重试、错误分类与首错持久化、跨请求 latestDecision 隔离。票据在途截止的测试确认 URLProtocol.stopLoading 实际发生。39项均在受控原生 Manager 编译配置中执行；单元回归另用标准 Simulator 配置。

## 原失败保留与归因纠正

- `entry-focused.xcresult`：进入时活动A/B测试1失败、缓冲保护1通过。
- `mic19-fixture-green.xcresult` **实际失败**，文件名不作为通过证据。将 formalMemory 数字epoch7错误当作 accountRuntime epoch 的尝试被撤回。
- `mic19-trace.xcresult` 偶然通过只用于定位，不单独作为修复证据。最终装配隔离后才收口。
- `native-manager-first.xcresult` 保留实际同步失败两次终态红证据；后续 native-manager-second/third 与 native-r4-first 保留测试屏障和空capture期望调整过程。
- `native-edges.xcresult` 暴露测试在仅写底层账号状态后错误期待即时页面通知；改为原要求的有界总截止入口，页面即时重绑另测。
- `native-edges-final.xcresult` 的 sdkStart计时期望3000错误；实际字段记录阶段入口1000。按真实语义保留入口及总耗时断言，最终39项通过。
- **纠正此前我和 Sol 对 final-06 的表述：原失败不是“2 GET/1 GET”。**原始日志与源码断言显示 `result.coordinators.count` 为2：一个 acknowledgementPrepared checkpoint 加一个共享磁盘遗留 followUpOnly workflow；真正状态读取仅1次。见 [原始日志摘录](historical-final06-corrected-evidence.txt)。本轮隔离目录/注册表，不修改产品 GET预算。旧报告保留作历史证据，其此处归因由本报告替代。

补充清理红绿后源码变化，因此此前第一版39项、774/0/3和four-scene-final仍保留为中间证据；最终有效版本改为native-mic-final-02、all-ios-final-02、generic-ios-final-02.log和four-scene-final-02，不用旧绿覆盖新改动。

## 复用边界

后端 diff 与进入时逐字一致，29项相关 memory 回归另行通过（[日志](backend-regression.log)）。复用原run-03受控 PG 的 ticket commit/rollback/单消费、同账号并发200/200仅一票据可消费、跨账号403，以及D5有限阻塞诊断的命名证据。不扩大为后端全量全绿：历史七项路由基线异常仍在登记册；本轮未重跑后端全量或重新裁定每一项。

最终完整回归774/0/3、无签名构建、同版四场各1/1及指纹均满足，见 README 与 fingerprints，故标 LOCAL_PASS。该状态仅关闭本地有限缺口，现场事件仍待用户主动安排验证。
