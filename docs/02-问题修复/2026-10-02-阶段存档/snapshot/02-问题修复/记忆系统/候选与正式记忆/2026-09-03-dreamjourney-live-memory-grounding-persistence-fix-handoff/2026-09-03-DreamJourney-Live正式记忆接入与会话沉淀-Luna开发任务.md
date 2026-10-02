# DreamJourney Live 正式记忆接入与会话沉淀：Luna 开发任务

日期：2026-09-03\
执行模型：GPT-5.6 Luna\
任务性质：跨 iOS、后端、生产部署与真机验收的缺陷修复

## 1. 工作目录与当前基线

### 1.1 后端

```text
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
```

生成本交接包时：

```text
branch: main
status: clean, aligned with origin/main
HEAD: 6a6c6562d0c035226803b1015e812186a07b708e
```

Luna 开始时必须重新核验，不能假设该状态仍未变化。

### 1.2 iOS

```text
/Users/gaominge/Documents/Codex/Video/DreamJourney_dev
```

生成本交接包时：

```text
branch: feature/prd-stitch-ui-adaptation
relative to origin: ahead 1
HEAD: c474dcee264e9fd0d481e0c7f53a28e3632d98ec
```

存在未提交修改：

```text
DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourney/Sources/Modules/Echo/EchoViewModel.swift
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
DreamJourneyTests/AudioOwnerLeaseModelTests.swift
DreamJourneyTests/OwnerTruthContractsTests.swift
```

这些修改属于正在进行的 Live 架构实现。不得 reset、checkout、stash、覆盖或重做。必须先读 diff，再在其上做增量修复。

## 2. 执行原则

1. 先写能复现问题的测试，再做最小修复。
2. 后端 JSONB 500 是确定根因，直接修；Live grounding 是最后一跳未定责，先取证。
3. 不恢复 Live 逐轮 `/echo/answers`。
4. 不修改已经正常的 provider-owned 持续会话、barge-in 和音频 owner，除非测试证明必需。
5. 不新增正式记忆数据源，不读取本地 Archive 或旧 ConversationMemory 作为 Live 事实。
6. 不把 Candidate、Source 原文、Assistant 回答或未确认内容注入 Live。
7. 不记录完整记忆、完整转写、完整 StartEngine JSON、音频或凭据。
8. 每个改动必须能对应本任务中的一个验收项。
9. 不在未授权的情况下 push、merge 或部署。
10. 编译、安装和启动成功不等于业务修复完成。

## 3. 工作包顺序

```text
WP0 基线冻结与失败复现
 -> WP1 后端 JSONB 修复
 -> WP2 后端持久化端到端验证
 -> WP3 iOS 沉淀错误分层
 -> WP4 Live 上下文隐私安全诊断
 -> WP5 火山会话上下文 PoC
 -> WP6 条件式紧凑 prompt 修复
 -> WP7 Live/typed 整场整理回归
 -> WP8 编译、部署与真机验收
 -> WP9 清理与交付报告
```

WP5 的证据决定 WP6 的具体实现。不得跳过 WP5 直接猜测火山字段。

## 4. WP0：基线冻结与失败复现

### 目标

在编辑前保存当前行为和仓库状态，防止把已有改动或已经正常的 Live 体验误删。

### 操作

1. 分别执行 `git status --short --branch` 和 `git diff --stat`。
2. 阅读 iOS 七个未提交文件的完整 diff。
3. 记录后端和 iOS HEAD，不切换分支。
4. 在后端增加或先运行最小复现测试，证明 raw `dict` 传入 JSONB INSERT 会失败或参数没有经过 `_adapt_params`。
5. 记录当前 Live 真机基线：启动时间、首音时间、打断时间、回答自然结束后重新聆听状态。

### 通过标准

- 能指出已有未提交代码每一部分的目的。
- 有一个后端测试在修复前失败、修复后通过。
- 后续所有 Live 修改都能与基线比较。

## 5. WP1：修复 PostgreSQL JSONB 会话创建

### 目标

消除：

```text
psycopg.ProgrammingError: cannot adapt type 'dict'
```

### 代码落点

```text
app/services/owner_truth_conversation.py
tests/test_owner_truth_conversation.py
```

按仓库测试结构，可增加一个只操作临时数据库的 targeted smoke 及 runner：

```text
scripts/backend-owner-truth-live-session-postgres-smoke.py
scripts/run-backend-owner-truth-live-session-postgres-smoke.sh
```

文件名可以服从现有约定，但职责必须单一。

### 实现

修改 `PostgresOwnerTruthConversationRepository.start_interview_session` 的两次 INSERT：

```python
cursor.execute(SQL, self._adapt_params((..., metadata)))
```

不得：

- 把 dict 手工拼进 SQL；
- 把 JSON object 双重编码成普通字符串；
- 修改表结构；
- 放宽 JSONB CHECK；
- 绕过 receipt、锁或事务。

### 测试

至少覆盖：

1. `productSessionId="echo_live_test_001"`。
2. `productSessionId=None`。
3. 写入后 `metadata` 为 JSON object。
4. thread/session 的 productSessionId 一致。
5. 相同 command 重试不重复创建。
6. 任一后续 INSERT 失败时事务整体回滚。

### 验证命令

先运行聚焦测试，再运行全量 gate：

```bash
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python -m unittest tests.test_owner_truth_conversation
scripts/verify_backend.sh
git diff --check
```

若增加 PostgreSQL smoke，必须使用隔离临时数据库，不得写生产业务账户。

## 6. WP2：验证完整沉淀流水线

### 目标

证明修复不仅让 session start 返回成功，还能走完待确认记忆链路。

### 测试路径

```text
GET current session
 -> POST start session
 -> POST owner message
 -> POST assistant message
 -> POST end
 -> GET pending review batch
 -> POST acknowledgement
 -> POST candidate-proposal/admit
 -> GET candidate-proposal/status
```

### 必验场景

| 场景 | 期望 |
| --- | --- |
| 用户提供新事实 | `reviewReady`，且最多一个审核批次 |
| 用户只询问旧事实 | `noCandidates` 或无新增 Candidate |
| Assistant 提供新说法 | 不成为 Candidate 事实 |
| 重复 end/finalize | 返回幂等结果，不新建第二批次 |
| 乱序或版本冲突 | 返回稳定错误，客户端可用同一幂等键重试 |

### 完成条件

- 真实 PostgreSQL 全链路通过。
- Candidate extraction Worker 被真实触发或通过现有正式测试替身验证。
- 不以 InMemory repository 通过代替 PostgreSQL 证据。

## 7. WP3：iOS 沉淀错误分层

### 目标

避免所有失败都显示“本次对话暂未完成整理”，并为重试提供准确阶段。

### 代码落点

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
DreamJourneyTests/OwnerTruthContractsTests.swift
```

### 实现

1. 在 `EchoLiveMemoryCaptureCoordinator` 中保存失败阶段和稳定 reason code。
2. `OwnerTruthInterviewNaturalInputUseCase` 不再把所有 transport failure 丢弃为无信息 `.requestFailed`；只上传递稳定类别，不暴露原始响应正文。
3. 会话进行中持久化失败不得结束火山 Live 或抢占音频状态。
4. coordinator 的待发回合不能因为一次 start failure 立即清空。
5. 未获得 review-ready 证据前不能显示“已进入待确认记忆”。
6. 文字会话尚未主动结束时，显示“继续文字回响/结束并整理”，不要显示整理失败。

### 最小状态模型

```swift
enum EchoMemoryCaptureFailureStage {
    case sessionStart
    case turnPersist
    case sessionFinalize
    case reviewBatchAcknowledgement
    case candidateAdmission
    case candidateExtraction
    case candidateExtractionTimeout
}
```

命名可调整，但不能再只有一个不可解释的 `.unavailable`。

### 测试

- start 失败后 Live 音频继续。
- start 重试成功后按原顺序提交队列。
- typed 回答成功、沉淀失败时，回答仍显示且错误阶段准确。
- finalize 只调用一次。
- `reviewReady`、`noCandidates` 和 failure 文案互斥。

## 8. WP4：Live 上下文隐私安全诊断

### 目标

用证据回答“快照在 iOS 哪一步丢失或未被采用”。

### 代码落点

```text
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourney/Sources/Services/ConversationMemoryManager.swift
DreamJourneyTests/OwnerTruthContractsTests.swift
```

### 实现

1. token 解析后记录：合同版本、schema、事实数、字符数、字节数、checkpoint hash、context hash 是否匹配。
2. StartEngine 前记录：prompt format version、事实数、字符数、字节数、prompt hash、SDK 版本/资源配置代号。
3. StartEngine 后记录 directive 返回码。
4. provider 连接和首个回答只记录关联 hash、状态和时延。
5. 删除或替换：

```swift
print("[DialogEngine] 发送 StartEngine 指令, data: \(configJSON)")
```

6. `DialogPromptDebugRecorder` 如仍用于测试，只允许 Debug 内存持有且不得写普通日志；测试结束可显式清理。

### 禁止字段

```text
formal memory statement
full prompt
full StartEngine JSON
raw transcript
audio bytes
ticket/token/key
raw user/persona/vault ID
```

### 测试

- 诊断输出包含数量、hash 和状态。
- 用敏感 canary 字符串构造测试 payload，断言日志不包含 canary。
- StartEngine 原始配置不再出现在日志。

## 9. WP5：火山上下文字段真实 PoC

### 目标

判断根因属于：

```text
A. iOS 未正确传入会话级上下文
B. SDK/资源未采用当前字段
C. 字段生效，但原始 JSON 过长或表达不适合实时模型
```

### 执行

1. 确认 Pod 锁定版本：`SpeechEngineToB 0.0.14.6.1-bugfix`。
2. 通过当前 SDK 头文件、官方示例或可运行 Demo 确认 StartEngine JSON 层级。
3. 在隔离 QA 人物使用 5 条合成事实，不使用真实用户隐私作为调试 canary。
4. 第一次用极短 prompt 验证一个唯一事实能否被回答。
5. 第二次使用完整紧凑事实集合验证三种学校问法。
6. 第三次使用 500、2K、5K、10K、15K 字符测量命中率和首音时延。
7. 验证 Live final ASR 期间 `/echo/answers` 调用次数为 0。

### 决策

| PoC 结果 | 后续动作 |
| --- | --- |
| 短 prompt 也不生效 | 修正 SDK 正式字段或资源配置；不做长度优化掩盖问题 |
| 短 prompt 生效，长 JSON 失败 | 执行 WP6 紧凑格式和容量门禁 |
| 完整 JSON 生效，产品事实仍失败 | 核对安装包、目标 persona、checkpoint 和 prompt hash |
| 当前 SDK/资源不支持会话级上下文 | 停止，输出供应商阻塞和可选产品降级，不恢复逐轮 DeepSeek |

### 通过标准

- 三种学校问法命中同一事实。
- 未知球队问题明确不知道。
- StartEngine 字段和配置来自可验证资料或运行证据。
- 有事实命中、时延和上下文大小结果，不以“连接成功”代替。

## 10. WP6：条件式紧凑 prompt 修复

仅在 WP5 证明字段生效、但完整 JSON 表现不可靠时执行。

### 目标

把机器合同转换成模型易理解、确定性、无重复的只读事实文本。

### 建议落点

在 iOS 增加职责单一的 formatter，例如：

```text
DreamJourney/Sources/Services/FormalMemoryLivePromptFormatter.swift
DreamJourneyTests/FormalMemoryLivePromptFormatterTests.swift
```

如果代码库已有更合适的文件，服从现有边界，不为了文件名额外拆层。

### 输入

只读取：

```text
schemaVersion
projectionCheckpoint
persona
coreFacts[].ref
coreFacts[].dimension
coreFacts[].statement
coreFacts[].status
```

### 输出规则

- 只包含当前 ready 的 `coreFacts`。
- 不输出 `dimensionSummaries`，避免重复。
- 按 `ref` 稳定排序。
- statement 原样保留，不调用模型改写。
- 加入未知事实不得猜测和身份边界规则。
- 相同输入得到相同 prompt hash。
- 超预算明确失败，不静默删事实。

### 测试

- 输出不包含 `dimensionSummaries` 的重复正文。
- 学校、职业、家庭关系和时间事实全部存在。
- revoked/deleted/non-ready 不出现。
- 顺序和 hash 稳定。
- 超预算失败关闭。
- Prompt formatter 不读取 Archive、Candidate 或本地旧记忆。

## 11. WP7：Live 与 typed 整场整理回归

### Live

1. token 配置成功后创建一个 capture coordinator，并绑定同一 productSessionId。
2. 每个 final Owner turn 异步保存。
3. final Assistant text 只作为 context 保存。
4. partial 不保存。
5. 用户打断不会 finalize。
6. Assistant 播放结束不会 finalize。
7. 用户主动关闭或 listening 状态累计一分钟无输入时 finalize exactly once。
8. 整场最多一个 Source 和一个审核批次。

### Typed

1. 每次用户输入仍调用 `/echo/answers`。
2. 回答只显示文字，不调用火山、系统 TTS 或数字人朗读。
3. 用户新事实进入同一个会话沉淀协调器。
4. 单纯查询旧事实不制造 Candidate。
5. 用户选择“结束并整理”后才 finalize。
6. 若离开页面被定义为结束，必须有 exactly-once 测试；否则保留会话并明确提示。

### Entry mode

不要在 P0 中贸然扩展数据库枚举。优先保持当前可用合同，并用轻量元数据区分 `live` 与 `typedEcho`。如确需新增 entry mode，必须同步更新后端允许值、iOS 枚举、capture-mode 校验、数据库合同测试和迁移评估。

## 12. WP8：编译、部署与真机验收

### 12.1 后端本地 gate

```bash
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python -m unittest tests.test_owner_truth_conversation
.venv/bin/python -m unittest tests.test_formal_memory_conversation_snapshot
scripts/verify_backend.sh
git diff --check
```

### 12.2 iOS 测试与编译

先运行聚焦 XCTest，再运行仓库现有非真机 gate。至少执行：

```bash
cd /Users/gaominge/Documents/Codex/Video/DreamJourney_dev
xcodebuild test \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests \
  CODE_SIGNING_ALLOWED=NO

Scripts/QA/prd-stitch-ui/run-iphoneos-generic-build.sh
git diff --check
```

如果本机 simulator 名称不同，先查询可用 destination，不得伪造成功。

### 12.3 后端部署

只有用户明确授权部署后执行。权威 Runbook：

```text
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/docs/backend/2026-08-09-deployment-account-recovery-runbook.md
```

部署必须：

- 固定 `main` 的目标 commit；
- 运行 deployment preflight；
- 生成并验证迁移前备份；
- 本修复理论上无 migration，但仍执行 migrator dry-run/verify；
- 重建 API 和所有已启用 Worker；
- `/ready` 和 deployed readiness smoke 通过；
- 运行隔离的 session start/append/end smoke；
- 观察生产日志不再出现 dict adaptation 500。

### 12.4 真机

只有用户明确要求真机测试后执行。安装、启动与业务验收必须分别报告。

按照验收矩阵完成：

- 三种学校问法；
- 未知事实；
- 连续 20 轮；
- 10 次打断；
- 回答结束后立即说话；
- 提供新事实并关闭；
- 待确认只出现一个批次；
- 确认后新 Live 能使用新 checkpoint 回答。

## 13. WP9：清理与交付

### 清理

- 删除本次改动产生的临时调试正文、测试密钥和本地敏感样本。
- 保留隐私安全的结构化诊断。
- 不删除仍被 typed、腾讯数字人或其他路径使用的旧函数，除非调用图证明只属于已废弃 Live。
- 不顺手重构无关模块。

### Luna 每个工作包的报告格式

```text
工作包：WPx
状态：完成 / 阻塞

修改文件：
- <绝对路径>

行为变化：
- ...

保持不变：
- ...

测试：
- 命令：...
- 结果：...

证据：
- 日志事件/HTTP 状态/测试名称：...

未验证项：
- ...

下一步：
- ...
```

### 最终报告必须包含

- 后端和 iOS 最终 commit/branch/status；
- 所有修改文件；
- 根因与最终修法；
- 测试总数和失败项；
- 部署版本与 readiness；
- 真机安装、启动和业务验收分别是否完成；
- 性能基线与修复后对比；
- 未解决风险；
- 是否发生任何数据恢复或无法恢复的数据。

## 14. 停止条件

出现以下任一情况，Luna 必须停止相关工作包并向用户报告，不得自行绕过：

1. iOS 工作区出现无法归属的新修改，可能覆盖用户工作。
2. 生产部署目录不在 `main`、工作区不干净或目标提交不是 fast-forward。
3. JSONB 修复意外需要破坏性 schema 迁移。
4. 无法从当前 SDK 资料或真实 PoC 证明会话级上下文字段。
5. 火山当前资源无法同时支持持续 S2S、打断和正式记忆上下文。
6. 修复使首音、打断或回答后恢复聆听回归超过验收门槛。
7. 日志、测试产物或提交中出现用户正文、正式记忆、音频或密钥。
8. 后端 session 已成功但 Candidate Worker 仍不产出，且无法区分 `noCandidates` 与 extraction failure。
9. 需要把 Live 改回逐轮 `/echo/answers` 才能通过学校事实测试。
10. 用户未授权 push、merge、部署或真机安装，而任务执行到相应边界。
