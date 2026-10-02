# DJ-LIVE-START-20260924：真机开麦迟缓与一次发前失败

## 结论与边界

- 设备：已配对并解锁的 iPhone 14 Pro Max；安装 `com.gaominge.dreamjourney.app` 1.0.0 (1)。Xcode/CoreDevice 可识别并截图。故障不属于 USB 未连接。
- 本次有两个不同观察：一次从“正在准备麦克风”最终进入“我在听”；用户观察等待接近 1 分钟，此前通常 3 秒内。随后一次受控重试显示“语音暂不可用，请使用文字回响”，未进入聆听。
- 这不能写成“始终无法打开麦克风”，也不能写成“现在已经正常”。本轮没有代码修改、重新安装、部署或操作生产记忆数据。不要与同日多页读取超时事件直接合并根因。
- 真实 Provider、iOS 音频权限、蓝牙/热点或后端快照的单一根因均**未确定**。服务端整体 readiness 检查通过，但不代表 Live ticket 及客户端授权路径正常。

## 原始现场

| 观察 | 证据 | 可得结论 |
| --- | --- | --- |
| 22:57 页面“正在准备麦克风”，按钮灰色 | `evidence/preparing-microphone.png` | 尚未进入聆听；不能凭此图判定最终失败。 |
| 22:58 页面“我在听，您慢慢说”，停止方块及系统麦克风指示点可见 | `evidence/eventually-listening.png` | 该次后来确实开麦；两张按分钟显示的截图不能量出精确秒数。 |
| 用户对照：这次接近 1 分钟，以前 3 秒内 | 用户现场陈述 | 明显的体验回退；接近 1 分钟是估计值，不是仪器计时。 |
| 用户停止后做一次受控重试，报告“语音暂不可用，请使用文字回响” | `evidence/controlled-failure.png` | 这一次是明确失败；截图与本机诊断均为 23:05 左右。 |

设备不支持 CoreDevice `screen-record`（`Screen Recording` capability unavailable）。后备定时截图于 15:05:09Z 开始时页面已是失败状态，因此**没有得到本次从点击到失败的精确耗时**；不得拿截图取样间隔冒充启动时间。复测只做一次，失败后未继续点击。

## 阶段时间线（北京时间，脱敏）

这些服务端日志按时间邻近关联；缺少贯穿客户端的同一 trace，不能视为已证明同一请求链。

- 22:56:58：一次 `/auth/refresh` 完成 HTTP 200。
- 22:56:58–59：三次 `/v2/release-policy`、两次 `/config/runtime` 完成 HTTP 200。
- 22:57:49：第一次 `/voice/realtime-token` 完成 HTTP 200；未记录该请求的**发起时间或处理耗时**。
- 22:57:50：本机 NativeLive 脱敏诊断开始出现 SDK provider callback，说明该次 ticket 完成到 SDK 回调约 1 秒，但不证明麦克风可用的精确时点。
- 22:58：界面可见“我在听”；22:58–23:00 的持续对话不属于启动问题证据，未复制正文。
- 23:00:11：服务端另有一次 `/voice/realtime-token` HTTP 200；尚不能判定是重连还是另一次用户操作。
- 23:04:55：受控失败时本机 `EchoRuntimeDiagnosticsSnapshot` 记录 `fallbackReason=backendVoiceRuntimeRequestFailed`，没有 SDK/audio-route 失败码。相同快照中 `echoTextInput` request decision 为 `allowed=false, reason=capturedPolicyExpired`，到期时间 23:04:36；这是关联线索，不能单凭快照证明它就是 `fetchRealtimeVoiceConfig` 的直接拒绝值。
- 23:05：页面显示“语音暂不可用，请使用文字回响”。这次失败的服务端窗口没有观察到新的 `/voice/realtime-token`、认证、策略或运行配置访问日志；这支持“客户端发前失败或请求未到达后端”，但还需客户端原始错误/请求曝光状态确认。

## 当前代码定位

- `EchoViewController.configureVoiceRuntimeThenStart` 在启动 SDK 前先验证账号租约与后端配置，然后调用 `DreamJourneyBackendClient.fetchRealtimeVoiceConfig`。`fetch` 失败会走 `handleBlockedRealtimeVoice(reason: "backendVoiceRuntimeRequestFailed")`，UI 显示上述通用错误。
- `fetchRealtimeVoiceConfig` 通过 `requestJSON` 对 `/voice/realtime-token` 发 user-required POST；客户端路由映射将该路径归为 `echoTextInput` FeatureGate。受控失败快照的 `capturedPolicyExpired` 因而值得重点核对，但不能借此放宽权限。
- 后端 `/voice/realtime-token` 在有授权的前提下生成绑定正式记忆快照的运行配置。第一次 ticket 200 与随后 SDK 回调证明该次成功进入后续阶段；第二次没有 ticket 日志，不能将第二次失败归咎为火山 SDK 断连或服务端返回 5xx。
- App 只向页面展示通用失败文案，原始 `EchoRealtimeVoiceRuntimeFailureDiagnostic` 的 stage/businessCode 只通过脱敏日志输出，当前持久快照没有保存该单次请求的原始错误分类、曝光状态和阶段耗时。后续定位需要补这条诊断链，而不是反复试错。

## 请 Astra 分析并给出局部方案

1. 以当前 iOS 的真实 `FeatureGate → requestJSON → fetchRealtimeVoiceConfig → Echo` 连接复现“旧 decision 已过期，本次 policy 可用但 Live ticket 发前失败”；核对是否需要针对合法的只读/授权准备重新捕获 decision。不能固定 gate 为 true，也不能让文字权限代替本不允许的功能权限。
2. 对第一次约 50 秒窗口，分别采集点击、租约校验、策略/认证恢复、ticket 发起/首字节/完成、快照构造、SDK configure/start、音频会话和进入 listening 的同 trace 耗时。当前只有相邻请求的**完成时间**，不能预判瓶颈在后端或火山。
3. 为 `backendVoiceRuntimeRequestFailed` 持久保存白名单化的原始错误 domain/code、HTTP 状态、策略 reason、请求是否曝光、trace 与阶段耗时；不保存 token、正文、完整响应、原始业务 hash。区分用户可重试的临时失败与真实策略 deny。
4. 对照同日 API 进程曾全局无响应、受控重启后恢复的独立事件，检查是否共享认证/策略刷新问题；不要在无 trace 证明前合并根因。参见 `../../2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md`（以实际绝对路径为准）。
5. 提供不触碰既有会话/记忆数据的最小真机复测：一次静音开麦、明确结束、记录阶段时间；不通过连续点麦克风、重置账号或重复生成历史任务来碰运气。

## 交付状态

- 真机连接与截图：PASS。
- 后端整体 readiness：PASS；第一次 Live ticket HTTP 200、SDK 进入聆听：OBSERVED。
- 后一次受控开麦：FAIL，客户端 `backendVoiceRuntimeRequestFailed`；精确底层错误及根因：UNKNOWN。
- “以前 3 秒 / 现在接近 1 分钟”：用户观察，未获得仪器计时；需针对性再测。
- 代码修复、部署及修复版真机验收：NOT_RUN。
