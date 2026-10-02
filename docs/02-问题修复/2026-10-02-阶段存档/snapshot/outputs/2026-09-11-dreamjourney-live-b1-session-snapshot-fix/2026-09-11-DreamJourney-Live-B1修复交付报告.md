# DreamJourney Live B1 正式记忆未采用修复交付报告

日期：2026-09-11\
依据：`2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md`\
当前结论：**代码、自动化、模拟器和受影响后端部署已完成；修订后的新正确字段 T06、修复版 B0 和 B1 已在真机通过。B2-B8 保持 NOT_RUN。**

## 1. 根因与证据

1. iOS 把本应作为 StartSession 根对象的 `asr/dialog/tts` 再包进一层 `dialog`，最终成为 `dialog.dialog.system_role`、`dialog.dialog.extra.model`。这与火山公开 StartSession 合同不一致。
2. 原测试与 Inspector 把错误双层路径写成正向期待，导致错误实现可以通过自身测试。
3. 后端已有正式记忆快照，但角色正文遗漏部分结构化限定，且早期逐条 JSON 对长快照开销过大；100 条完整事实会超过 32768 字符门禁。
4. 旧逐轮 RAG 使用错误的 `content` 根字段，并没有可证明的同轮采用关系，不能直接恢复。
5. 历史 T06 只证明旧双层正文曾由 SDK 发出；它不证明字段符合模型消费合同，也不证明模型采用事实。该历史 PASS 保留，新单层字段必须另做 T06。

修复前反例：`pre-fix-ios-start-session-failing-tests.log` 中四个反向合同均失败，分别证明正确单层被旧判据拒绝、双层反而被接受、model 位于错误层级、旧 RAG 负载格式错误。

## 2. 代码修改

### iOS

- `DialogProviderLiveStartConfigBuilder`：StartSession 固定为一个根对象，`asr`、`dialog`、`tts` 同级；`system_role` 与 `extra.model` 只允许出现在根 `dialog` 内。
- `submittedRoleText` / `encodedStartConfig`：拒绝缺失、空值、类型错误、正文不一致、重复 `system_role` 和旧双层包装。
- `performStartDialog`：删除二次 `dialog` 包装；保留 `SpeechEngineToB 0.0.14.6.1-bugfix`、火山原生播放器、连续聆听、打断与手动停止；端点等待明确为 1500ms，没有切回旧串行链路。
- `DialogLiveGroundingPlan.sessionSnapshot`：默认在会话开始绑定完整已审核正式事实，由火山原生 Live 生成文本和声音。
- `DialogLiveAnswerDispatchPolicy`：Provider-owned Live 的 ASR 只进入会话持久化，不再发第二次 `/echo/answers`；自动与委托流程保持原行为。
- `DialogChatRAGTextPayloadEncoder`：隔离路径改为 `external_rag` 的 JSON 数组字符串合同，但默认 `turnRag=false`，没有恢复旧 `recordEchoContextPacketForUserTurn`。
- T06：受控 WebSocket 客户端挂在真实 SDK `setWsClient` 边界；只记录事件、字段路径、字节数、哈希和布尔结果，不记录正文、凭据、音频或转写。
- Provider 轮次关联：按 question/reply/generation 记录最小诊断，拒绝旧 reply 混入新轮。

### Backend

- `formal-memory-conversation-v3`：只从 `eligible_entries` 生成快照，补齐主体、否定、强度、当前适用、时间、地点、场景和来源模式。
- 正式事实数据与回答规则分区，statement 中的命令式文字只能作为事实数据，不得被执行。
- 使用“一次字段顺序 + 每条值数组”的紧凑无损表示，保留 null/unknown 的语义区别，不截断正式事实。
- 100 条合成正式事实由约 48880 字符降至 21506 字符、24854 UTF-8 字节，`omitted=0`；真正超限仍明确失败，不伪装成全量快照。

## 3. A01-A21 结果

| 用例 | 结果 | 证据 |
| --- | --- | --- |
| A01-A04 | PASS | 单层 StartSession、双层负例、role 校验、model/ASR/TTS 位置测试 |
| A05 | PASS（协议单测） | event 100、gzip、错误事件、边界解析；不替代新真机 T06 |
| A06 | PASS | 普通启动不启用 T06 合成覆盖 |
| A07-A11 | PASS | 65/100 条覆盖、限定字段、特殊字符/哈希、显式超限 |
| A12 | PASS | 撤权、旧 revision、跨人物回归包含于后端完整门禁 |
| A13 | PASS | `DialogLiveAnswerDispatchPolicy` 证明原生 Live 不发后端逐轮回答；Archive/KBLite 未进入该路径 |
| A14-A16 | PASS | 重复文本不同问题、ID-only ASR、旧 reply/重连隔离 |
| A17 | PASS | 模拟器冷启动与重启 UIQA，文字结束后麦克风恢复；文字路径不调用朗读 |
| A18 | PASS | 会后整理、候选与 noChange 合同包含于双端回归 |
| A19 | PASS（隔离合同） | `external_rag` 编码通过，旧 `content` 根字段为负例；turnRag 未启用 |
| A20 | PASS | 日志扫描未发现角色正文、正式事实、凭据或转写正文输出 |
| A21 | PASS | iOS 聚焦测试、完整模拟器构建、实际 UIQA 均成功 |

## 4. 测试证据

- 后端完整门禁：`backend-full-verify-after-compact-role.log`，退出码 0。
- 后端定向回归：`backend-session-snapshot-after-compact-tests.log`，32/32 PASS。
- 100 条角色预算：`backend-compact-role-regression.log`，11/11 PASS，21506 chars / 24854 bytes / omitted 0。
- iOS 聚焦回归：`ios-live-b1-focused.xcresult` 与日志，289/289 PASS。
- iOS 完整模拟器构建：`ios-live-b1-simulator-build.log`，BUILD SUCCEEDED。
- 实际模拟器 UIQA：`simulator-echo-continuous-current/run-current/`；冷启动和进程重启两次均完成两轮、停止回 idle、文字结束麦克风可用、旧 reply 被拒绝。
- `git diff --check`：iOS 与后端均 PASS。保留所有既有未提交修改，未 commit、未 push。

## 5. 生产部署

- 源码包 SHA-256：`eb6a58b6a5e8c472fab755e764adade4547e8d196264baf3bd2c7c7aa4e81b7f`；`.env`/`.runtime.env` 明确排除。
- Release：`/opt/services/dreamjourney/releases/live-b1-20260911-0200`。
- 新 API image：`sha256:7c355bafeb9a0807abcddfd69bc1d46cf7a64cb90ad81334bc2ed1317a636bea`。
- 回退 image：`dreamjourney-rollback-api:pre-live-b1-20260911-0200`，原 image `sha256:0ff8a3308e0d...`。
- 迁移：候选镜像 verify 为 `0121/0121`，无 pending；本次没有新迁移。
- 切换前：active Live、leased async job、active narrative、leased embedding、processing rebuild 均为 0。
- 切换后：API healthy/restart 0，运行时 snapshot schema 为 v3；公网 `/health`、`/ready` PASS。
- 稳定窗口：70 秒三次采样，API 与六个启用 Worker 全部 running、restart 0，近期 migration/readiness/traceback 错误匹配 0。
- 部署态合成验证：4 条合成事实全部进入角色正文，1502 chars / 1808 bytes，哈希生成成功；未读取真实正式记忆。

发布中发现 systemd 备份服务仍读取旧主目录的 0119 迁移集，因生产已是 0121 而 fail closed。已增加 release 跟随 drop-in，`/opt/services/dreamjourney/current` 指向本次 release；发布后备份 `dj-20260910T175728Z-84afccfd`、schema 0121 验证通过。

## 6. 未完成项与真机步骤

### 2026-09-11 真机 T06 首次执行

- 设备：iPhone 14 Pro Max，iOS 26.4.1，设备状态 `available (paired)`。
- 构建：Debug 真机构建成功，Bundle ID `com.gaominge.dreamjourney.app`，使用同 Bundle ID 覆盖安装，未卸载或清除 App 数据。证据：`t06-device-build.log`。
- 启动：使用 `-DreamJourneyT06SDKCapture` 启动，真实 SDK 版本 `SpeechEngineToB 0.0.14.6.1-bugfix`，受控 WebSocket 客户端已连接。
- SDK 出站：捕获到首帧 `frameBytes=984`、安全头部 `1115100000000001`，随后捕获三个 136 字节帧；首帧未被当前 Inspector 解析为 StartSession，因此没有产生 `upstreamStartSessionObserved` 或 `contractMatch=true`。
- 同轮辅助证据：正式快照解码为 schema v3、65 条事实；T06 使用 192 字节合成角色正文，未记录真实正式记忆正文。
- 结果：**T06 FAIL**。现有解析器对真实 SDK 帧信封/事件布局的假设仍不完整，后续须先解析锁定 SDK 的实际帧结构并补反例，再重新执行 T06。证据：`t06-device-console.log`。
- 暂停：按用户要求稍后继续。诊断控制台已停止，App 进程正常退出；未执行 B0-B8。

### 2026-09-11 真机 T06 修订后复测

- 修复前反例：补充锁定 SDK 的 `sequence + event + session ID + payload` 信封用例，旧 Inspector 无法解析并按预期失败。证据：`t06-sdk-envelope-pre-fix.log`。
- 修复：Inspector 按 SDK 实际客户端事件帧读取 sequence、event、StartSession session ID 和 payload；没有改变 StartSession 正文或普通 Live 运行链路。
- 自动化：event 100、gzip、错误事件、旧双层负例及真实信封共 4/4 PASS。证据：`t06-sdk-envelope-post-fix.log`。
- 真机构建：同 Bundle ID Debug 构建和覆盖安装成功，未卸载或清除 App 数据。证据：`t06-device-v2-build.log`。
- 真实 SDK 出站：`SpeechEngineToB 0.0.14.6.1-bugfix` 的 StartSession 控制帧被捕获；`event=100`、`fieldPath=dialog.system_role`、`roleBytes=192`、`modelFieldPresent=true`、`legacyDialogWrapper=false`，正文哈希与 `providerContextHash` 相同，所有合同布尔项为 true。日志不含正文、凭据、音频或真实记忆。证据：`t06-device-v2-console.log`。
- 结果：**T06 PASS**。该结果只证明合成 sessionSnapshot 角色正文按正确字段通过真实 SDK 发出，不替代 Provider 对真实正式事实的回答验收。

### 2026-09-11 真机 B0/B1

- B0：开场有声、进入持续聆听、可手动停止并恢复麦克风，用户侧四项均通过；日志记录会话状态结转和音频所有权释放，无语音服务异常。结果：**PASS**。
- B1：同一场 Live 对学校/学历、毕业时间、当前职业和饮食偏好的四项回答均由用户确认为正确，四次均有声音；简短复测再次正确回答学校问题。
- 技术证据：普通启动关闭 T06 合成覆盖；正式快照 schema v3、65 条正式事实、`contextHashMatch=true`；最终角色正文 18702 字符/23800 字节；Provider 会话以 `groundingMode=sessionSnapshot` 启动并观察到匹配上下文哈希的回复。证据：`b0-b1-device-console.log`、`b1-device-console.log`、`b1-evidence-rerun-console.log`。
- 结果：**B1 PASS**。控制台受 App 前后台生命周期影响，未完整保留四问各自的逐轮回调；因此报告只主张本次用户事实回答及会话级正式快照采用通过，不把日志描述成四条逐轮语义转写证据。

### 2026-09-11 真机 B2

- 用户侧：朗读中插话可立即停止，随后自动恢复聆听；连续约 10 轮交流无被动关麦、无明显长等待，手动停止后麦克风恢复。
- 技术侧：同一 Provider 会话使用固定 `sessionSnapshot` 上下文，观测到 12 个轮次和 11 个已持久化用户轮；问题/回复关联保持 matched，停止后音频所有权释放，会话摘要保存 23 个转写轮次。未恢复旧 ASR -> DeepSeek -> TTS 链路。
- 结果：**B2 PASS**。证据：`b2-device-console.log`。

### 2026-09-11 真机 B3

- 用户在同一场 Live 明确提交一条隔离合成事实，随后把测试代号从“蓝桥”更正为“青禾”并主动关闭。
- iOS：2 个用户轮全部进入耐久队列并提交，停止后进入保存、排队和整理状态；没有重复结束请求。
- 后端：同一会话只创建一个 review batch，Candidate Extraction attempt 1 成功，`candidateCount=1`、`candidateOutcome=pendingReview`；没有把原值和更正值生成两条并列候选。
- 结果：**B3 PASS**。证据：`b3-device-console.log` 及生产 API/候选 Worker 的脱敏状态核查。候选尚未由用户审核，未写入正式记忆。

### 状态

- 历史 T06：PASS，仅保留“旧双层正文曾发出”的限定含义。
- 新正确字段 T06：PASS；真实 SDK 最终 StartSession 控制帧的 event、字段路径、正文长度、同值哈希、model 和无旧双层包装均已核验。
- 修复版 B0：PASS；开场、持续聆听、停止和麦克风恢复均正常。
- B1：PASS；四项真实正式事实均正确且有声回答，普通会话绑定正式快照。
- B2：PASS；打断、恢复聆听、连续至少 10 轮和手动停止均通过。
- B3：PASS；新事实和更正进入同一整理批次，只生成 1 条待确认候选。
- B4-B8：NOT_RUN；继续逐项执行。
- turnRag：未启用，R01-R10 不标 PASS。

### 用户配合步骤

1. 连接、解锁并信任 iPhone，保持数据线连接。
2. 先安装诊断构建；打开回响后只点击一次 Live，无需说出真实信息。
3. 由研发读取 T06 安全日志，必须同时确认真实 event 100、`dialog.system_role`、正文 byte count/hash 与 providerContextHash 同值、model 存在、无旧双层包装。
4. T06 通过后退出诊断启动参数，覆盖安装同代码正常构建，保留 App 数据。
5. B0：确认开场有声、进入持续聆听、手动停止回麦克风页。
6. B1：依次询问学校与学历、毕业时间、当前职业、饮食偏好；每项必须与正式记忆一致且有声，不能答不知道或修改强度。

## 7. 回退方案

后端异常时，把 API override 指回 `dreamjourney-rollback-api:pre-live-b1-20260911-0200` 并只重建 API；数据库无新迁移，不执行 down migration，不改正式记忆。iOS 若出现播放、打断或持续聆听回归，停止 B 测试并覆盖安装保存的上一可运行构建，保留 App 数据与审核历史。任何回退都不启用旧 ASR -> DeepSeek -> TTS，不恢复旧逐轮 RAG，不清理或重放历史任务。
