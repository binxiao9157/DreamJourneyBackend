# B 阶段真机测试期间已登记问题

## B-DEV-001 文字回响结束后 Live 入口停在不可点击勾状态

- 状态：已复现，暂缓修复，不阻塞其余 B 阶段体验验证。
- 设备：iPhone 14 Pro Max，iOS 26.4.1。
- iOS 版本：`5fd061fd869edbe1fc13e8535a47880826581934`。
- 后端版本：`be9670b6ec05e73ab9562943f402e5a9e1346988`。
- 触发步骤：连续完成三次文字回响，结束文字会话并等待整理。
- 现象：麦克风按钮显示不可点击的 `checkmark`，无法直接启动 Live。
- 客户端证据：三次 `/echo/answers` 均成功且分别为 3、5、8 条引用；整理状态随后从 `organizing` 进入 `unavailable`。
- 初步代码定位：文字会话结束后仍保留 `.replied` 交互状态；非 Live 的 `.replied` 渲染会把麦克风配置为不可点击的 `checkmark`，结束整理路径未显式恢复 idle。
- 数据安全：未审核任何 Candidate，正式记忆未发生写入。

## B-DEV-002 同一文字会话的候选整理进入 Dead Letter

- 状态：已复现，暂缓修复，不执行历史恢复或 Dead Letter 重放。
- Review Batch：`a73e6af7-69fc-50b8-ba9f-fb1b3a42ff3e`。
- Source：`7555b71f-3c30-5601-a804-508226299aeb`。
- Operation：`00c7edec-5b5b-5da4-a3de-c6af6427c245`。
- Worker 结果：`candidateExtractionRetriesExhausted`，0 个 Candidate，Dead Letter 状态为 open。
- 只读数据库核查：Review Batch 已 acknowledged、Source active，但该 Source 没有生成 `memory_candidates`。
- 合成诊断：同一 DeepSeek 组织器对纯提问对话正确返回 0 条；对合成自述事实返回 3 条且通过 V5 enrich 合同。生产会话的具体失败分支仍待在不重放真实转写的前提下继续定位。
- 数据安全：没有自动落入正式记忆；未经授权不做重放、清理或恢复切流。

## B-DEV-003 Live 运行配置接口持续返回 503

- 状态：已复现，当前阻塞 Live 相关 B 阶段用例，非 Live 用例继续执行。
- 触发步骤：重启 App 绕过 B-DEV-001 后点击麦克风启动 Live。
- 客户端结果：连续出现 `voiceRuntimeConfigUnavailable reason=backendFailure` 和 `providerCredentialBlocked reason=backendVoiceRuntimeRequestFailed`。
- 服务端结果：生产 API 连续六次 `POST /voice/realtime-token` 返回 HTTP 503。
- 已确认边界：请求失败发生在火山原生对话启动之前；当前没有证据表明是麦克风、音频播放或打断链路故障。
- 未确认事项：现有访问日志没有输出安全化业务错误码，尚不能在 `formalMemorySnapshot*`、`realtimeVoiceFormalMemoryBindingInvalid`、subject 状态和代理 URL 校验之间定责。
- 数据安全：未签发可用 Live 会话，未修改正式记忆，未重放历史任务。
