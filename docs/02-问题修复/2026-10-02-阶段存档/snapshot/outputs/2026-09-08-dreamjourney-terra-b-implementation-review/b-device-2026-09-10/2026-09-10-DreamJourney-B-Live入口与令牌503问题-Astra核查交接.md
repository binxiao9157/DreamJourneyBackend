# DreamJourney B 阶段 Live 入口与令牌 503 问题 Astra 核查交接

## 1. 交接目的

请 Astra 先独立读取本目录中的原始日志并核查当前实际代码，再确认根因和提供修改。本文中的代码判断均为待验证假设，不应替代日志、接口响应和测试证据。

本次交接只覆盖两个问题：

1. 文字回响结束后，Live 按钮显示不可点击的勾。
2. 重启 App 绕过勾按钮后，`POST /voice/realtime-token` 持续返回 503，Live 无法启动。

不要让这两个问题阻塞其余不依赖 Live 的 B 阶段测试。未经另行授权，不清理历史数据、不重放 Dead Letter、不恢复切流、不推送 GitHub。

## 2. 测试环境与版本

- 设备：iPhone 14 Pro Max，iOS 26.4.1。
- App Bundle ID：`com.gaominge.dreamjourney.app`。
- iOS 分支：`feature/prd-stitch-ui-adaptation`。
- iOS 提交：`5fd061fd869edbe1fc13e8535a47880826581934`。
- 后端分支：`main`。
- 后端提交：`be9670b6ec05e73ab9562943f402e5a9e1346988`。
- 生产 API：`https://www.mmdd10.tech/dreamjourney-api`。
- 生产 API 容器：`dreamjourneybackend-api-1`。
- 时区说明：服务端日志为 UTC；`2026-09-09 17:21` 对应北京时间 `2026-09-10 01:21`。

## 3. 原始证据入口

请优先阅读以下文件，不要只阅读本文的摘要：

1. `device-console-group1.log`
   - 首次真机启动及三次文字回响完整日志。
   - 包含三次 `backendAnswerReceived`，引用数分别为 3、5、8。
   - 包含文字会话结束后 `captureStateChanged from=live to=organizing`，随后进入 `unavailable`。

2. `device-console-group1-live.log`
   - App 重启后的真机日志。
   - 包含多次 Live 启动尝试。
   - 核心事件为 `voiceRuntimeConfigUnavailable reason=backendFailure` 和 `providerCredentialBlocked reason=backendVoiceRuntimeRequestFailed`。

3. `server-realtime-token-503.log`
   - 从生产 API 容器只读提取的带时间戳访问日志。
   - 共记录六次 `/voice/realtime-token` HTTP 503。

4. `KNOWN-ISSUES.md`
   - B-DEV-001、B-DEV-002、B-DEV-003 的登记状态和数据安全边界。

如需核对生产容器中的完整原始上下文，可只读执行：

```bash
ssh dreamjourney-cloud "docker logs -t --since 2h dreamjourneybackend-api-1 2>&1 | grep -E 'liveSnapshotIssued|formalMemorySnapshot|realtimeVoice|/voice/realtime-token'"
```

禁止在诊断输出中打印火山凭证、会话票据、认证令牌或正式记忆正文。

## 4. 问题一：文字会话结束后 Live 按钮变成不可点击的勾

### 4.1 已观察事实

1. 三次文字问答均成功调用 `/echo/answers`。
2. 三次响应均为 `memoryGrounding=grounded`，Provider 为 DeepSeek。
3. 文字会话结束后，麦克风位置显示 `checkmark`，且按钮不可点击。
4. 结束并重新启动 App 后，麦克风入口恢复，因此不是永久权限或硬件故障。

### 4.2 待核查代码位置

- iOS：`DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
- `finishTypedEchoConversation()` 当前约在第 9302 行。
- `.replied` 的按钮渲染当前约在第 3781 行。
- `resetEchoViewModelToIdle()` 当前约在第 3258 行。

当前待验证假设：`finishTypedEchoConversation()` 结束记忆采集并清理最近对话后，没有完成 `.replied -> .idle` 状态转换；非 Live 的 `.replied` 分支会渲染禁用的 `checkmark`。

### 4.3 Astra 必须确认

1. `.replied` 是由哪一次文字回答回调保留，是否存在晚到回调再次覆盖 idle。
2. 结束文字会话时恢复 idle 是否会提前取消记忆整理或破坏整理状态提示。
3. 是否需要将“对话交互状态”和“记忆整理状态”拆开渲染。
4. 重复结束、页面切换、前后台切换是否保持幂等。

### 4.4 验收要求

- 文字问答结束后无需重启即可启动 Live。
- 麦克风恢复为可点击状态，记忆整理可在后台继续。
- 连续执行至少 10 次“文字问答 -> 结束 -> Live 入口恢复”。
- 旧生命周期或迟到异步回调不得重新锁定按钮。
- 不修改火山原生 Live 的录音、播放、打断和持续聆听实现。

## 5. 问题二：Live 运行配置接口持续返回 503

### 5.1 已观察事实

1. App 重启后可以点击麦克风。
2. 客户端在火山原生对话启动之前，请求后端 Live 运行配置失败。
3. 客户端只记录统一失败原因 `backendFailure`，没有保留后端 `detail.code`。
4. 生产 API 同一时间连续六次返回 `/voice/realtime-token` 503。
5. 当前日志中没有足够证据把故障归因于麦克风、音频播放、TTS 或打断实现。

### 5.2 待核查代码位置

- 后端路由：`app/main.py` 中 `realtime_token()`，当前约在第 17546 行。
- Live 快照构建：`app/main.py` 中 `_build_authorized_realtime_live_session()`，当前约在第 17034 行。
- 快照实现：`app/services/formal_memory_conversation_snapshot.py`。
- 票据签发与绑定：`app/services/realtime_voice_proxy.py` 中 `issue_runtime_config()`。
- iOS 请求失败处理：`EchoViewController.swift` 中 `voiceRuntimeConfigUnavailable`，当前约在第 10487 行。

### 5.3 Astra 首先必须取得的证据

在修改前捕获 `/voice/realtime-token` 的安全化响应状态和 `detail.code`。重点但不限于检查：

- `formalMemorySnapshotUnavailable`
- `formalMemorySnapshotTooLarge`
- `realtimeVoiceFormalMemoryBindingInvalid`
- `realtimeVoiceSubjectUnavailable`
- `realtimeVoiceUpstreamURLInvalid`
- `realtimeVoicePublicURLInvalid`

如果增加诊断日志，只允许记录错误码、阶段、请求关联 ID、快照事实数量和长度等非敏感元数据。不得记录正式记忆正文、票据或 Provider 密钥。

### 5.4 待验证假设

文字问答可正常检索正式记忆，但 Live 快照合同更严格，要求 Projection 同时满足：

- `state=ready`
- `rightsState=active`
- checkpoint 非空
- memoryRevision 为合法非负整数
- entries 与 eligibility 合同有效
- sessionContext 中的 checkpoint、contextHash、memoryRevision 与票据参数完全一致

因此“文字可以回答”不能证明 Live 快照一定可以签发。需要使用实际响应码和服务端阶段日志确认，禁止仅凭上述假设修改。

### 5.5 修复边界

- 不得跳过正式记忆绑定来让 Live 启动。
- 不得向移动端下发长期火山密钥。
- 不得以无正式记忆上下文的 Live 作为成功降级。
- 不得恢复逐轮 ASR -> DeepSeek -> TTS 链路。
- 保留火山原生 Live 的低延迟、持续聆听和用户打断能力。

### 5.6 验收要求

- `/voice/realtime-token` 返回 200，并记录安全化的 `liveSnapshotIssued` 证据。
- 会话票据保持短时、一次性、限定 audience/scope 且可撤销。
- Live 能回答已确认的学校、职业和饮食偏好，不改变正式记忆事实。
- 文字与 Live 使用同一份最新正式记忆事实权威。
- 失败路径能定位安全化业务错误码，不泄露用户正文和凭证。
- 原生 Live 的打断、恢复聆听和连续交流能力不回退。

## 6. 要求 Astra 输出的修改说明

Astra 完成核查后，请分别提供：

1. 每个问题的已证实根因以及对应原始日志行。
2. 实际修改文件、函数和状态合同。
3. 新增回归测试及其失败前、通过后证据。
4. 是否涉及后端迁移、配置变更或生产部署。
5. 对火山原生 Live 低延迟、打断、正式记忆绑定和数据安全的影响。
6. 尚未验证的风险，不得把未执行项目写成通过。

## 7. 本次交接动作边界

- 已保存日志并形成本文。
- 尚未为这两个问题修改代码。
- 尚未因这两个问题重新部署后端。
- 尚未推送 GitHub。
- B 阶段中不依赖 Live 的测试可以继续。
