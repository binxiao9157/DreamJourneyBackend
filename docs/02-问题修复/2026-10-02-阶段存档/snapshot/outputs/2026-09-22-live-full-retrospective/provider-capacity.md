# Live 长场后端模型链与外部容量复盘

核查日期：2026-09-22。范围是历史材料、当前本地源码、官方公开文档和无网络的输入边界探针。本轮没有访问生产，没有调用火山或 DeepSeek，没有操作数据库、重放历史任务、修改产品代码或实施修复。本文不提供修复设计。

## 结论

1. **旧链路的 8 条是我们自行设定的单次 organization 上限，不是 DeepSeek 或火山的限制，也不是对所有形状整场一律最多 8 条。** 旧版已有按 200 turns、单 turn 4000/总 30000 字符的初始分块；9/19 的 77 turns/4564 字符长场只形成一块，单次最多 8 条与随后要求覆盖全场的 support 冲突。2026-09-19 的 8 次合成真实模型请求已经证明这个冲突能实际发生：同一份 12 事实长输入，8 条上限得到 8 条和 4 项遗漏；仅提高条数上限、保持输出预算 4096，即得到 12 条且复核无遗漏。
2. **9/19 绑定长场的已知失败发生在“HTTP 成功后的业务合同”层。** 第一次生成 8 条后 support 报 `factOmitted`，重试组织返回 `schemaInvalid`；没有该场输入超限、429 或网络超时证据。具体遗漏内容与第二次非法字段未留存，不能把合成对照写成所有历史失败的唯一原因。
3. **短场更容易成功，是事实量、JSON 复杂度、关联数及跨阶段机会更少，不是时长本身触发了一个 20 分钟供应商闸门。** 9/18 另有短场同样失败，因此“短场永远正常、长场必然是容量问题”不成立。
4. **新分批通过更细单元和必要时细化，不再让这类长场只依赖一次最多 8 条的 organization，也解除旧的整场 support 请求；但未证明所有长输入都可通过。** 分批仍有单请求 8 条、4000 字符/turn、30000 字符/请求等限制。当前跨批关系页会重新带入原始整 turn；本次无网络探针证明，即使关系页只有 1×1 或合规 8×32，也能在发 HTTP 前重新触发旧输入限制。不能把本地 149 条成功外推为真实 Provider 的任意长场保证。
5. **9/20 run04 的设备长场是另一条故障链。** 当时 canonical 捕获在约 6 分钟停止，尚未形成 end/ACK/admit/Source；私有预整理的两次 DeepSeek 调用成功。因此该场候选为零不能归因为会后大输入被 DeepSeek 拒绝。
6. **目前的证据不足以宣称“新分批完整真实链已验收”。** run01–04 的修复以受控输出和本地数据库为主，真实 Provider 明确未运行；F-65 主链注入了 extractor，HTTP admission 的长 pipeline 开关又没有与随后 worker 一起开启。默认装配、真实语义、生产开关和物理时长必须分别记账。

## 1. 历史现场与真实模型证据

### 1.1 9/18 的合同修复解决了什么

初始设计记录：长样本 16 个 user / 15 个 assistant、Source 2018 字符、最长 turn 212；短样本 2 个 user / 1 个 assistant、Source 162 字符、最长 turn 82。两者均已完成结束/ACK/admit，但候选为零，错误为 `candidateExtraction.responseContract.invalid`。这些输入不触及后来讨论的 200 turns、单 turn 4000、总 30000 上限；原始模型输出没有保留下来，精确非法字段仍未知。

run01–03 主要完成错误细分、有限重试与安全反馈、证据类型校验、数据库事务和恢复证据。它们没有真实调用 Provider，也没有证明“短场或任意 20 分钟已经可靠”。run02 PostgreSQL 的 6 条正式记忆来自短场 1 条加长场 5 条；这个长场事实数仍未超过旧 8 条瓶颈。

独立复核反复发现“绿色测试绕过真实默认组合”问题：run01 把 Live extractor 直接注入 Worker，而默认链经 `ModelAssistedOwnerTruthSourceExtractor` 包装，导致 `retry_context` 未正确传递；run02 仍有默认日志级别使诊断不可见、解析成功就提前记录 support 成功、异常 `finish_reason` 类型被错归 transient、后续重试历史未白名单校验等缺口。run03 关闭了这些指定缺口，但历史现场精确触发点和真实 Provider 仍未补证。

### 1.2 9/19 绑定短/长场：输入规模与时序

| 项目 | 短场 | 长场 |
|---|---:|---:|
| turns / user turns | 3 / 2 | 77 / 39 |
| 全对话字符数 / 最长 turn | 78 / 29 | 4564 / 130 |
| 用户 Source 字符数 | — | 1595 |
| organization 结果 | 1.614 秒，HTTP 200 | attempt 1：7.505 秒，HTTP 200，8 条 |
| support 结果 | 0.767 秒，HTTP 200，通过 | 2.189 秒，HTTP 200，`factOmitted` |
| 下一次 organization | — | 8.154 秒，HTTP 200，`schemaInvalid` |

长任务总体约 25.95 秒，含约 8 秒重试等待。该记录不支持“60 秒 timeout”或“DeepSeek 输入窗口耗尽”。错误路径也不是 `outputTruncated`。不过历史逐响应 `finish_reason`、精确 schema 字段、原始遗漏索引没有保存，不能反推它们的值。

服务器当时用相同参数重建的 prompt：短 4014 字符/5750 UTF-8 bytes，长 11417/22013，重试 11508/22124。它们是重建测量，不是原始出站请求抓包，也不是 token 数。历史报告核对了当时部署文件指纹、域名 `api.deepseek.com` 和请求模型 `deepseek-v4-flash`；本次未刷新生产状态。

### 1.3 八次合成真实模型调用能证明的范围

四组，每组 organization + support，共 8 次；全部 HTTP 200、`finish_reason=stop`、响应 `model=deepseek-flash`。长样本 77 turns / 39 user，含 12 个独立事实，其余含问题等非事实输入。

| 合成组 | 生成条数上限 / 输出预算 | 草案数 | organization completion tokens | support 遗漏 |
|---|---|---:|---:|---:|
| short_current | 8 / 4096 | 2 | 622 | 0 |
| long_current | 8 / 4096 | 8 | 2221 | 4 |
| long_memory_cap_only | 24 / 4096 | 12 | 3400 | 0 |
| long_memory_and_token_cap | 24 / 8192 | 12 | 3507 | 0 |

`long_current` organization/support 约 6.079/2.503 秒；仅改条数为 24 后约 8.952/2.351 秒；再把输出预算改 8192 后约 9.254/2.587 秒。长组 organization 输入都是 2671 tokens；support 输入随草案量增大，为 3368、4213、4291 tokens。

这个对照直接支持“8 条人为瓶颈与全量覆盖冲突”，并表明该样本没有必要依赖 8192 输出才能成功。它不证明所有长对话只需改为 24：每组只跑一次，事实更密、字段更多、关系更复杂时可能不同；复核遗漏也是模型判断，不能当成任意自然语言的形式证明；本次不是私有历史正文回放。

历史深查还给出代码演进：8 条限制先于完整性复核存在；后续加上“遗漏即拒绝”，却未同步扩大处理架构，因而原先可能部分发布的场景会变成零发布。报告列出的引入提交为 `f7c5d9a`（8 条及分片）、`51f100`（2048→4096、关闭 thinking）、`f3ebc8`（严格 support）；这些历史代码记录不等同实际部署日期证据。

证据：[真实调用结果](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/provider-capacity-diagnostic.json)、[诊断脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/provider_capacity_diagnostic.py)、[绑定输入统计](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/bound-session-capacity.json)、[逐请求时序](../2026-09-19-live-l20-capacity-analysis/evidence/current-request-timing.md)。

## 2. 外部边界与内部限制不能混用

| 项目 | 谁规定 / 适用边界 | 核查结论 |
|---|---|---|
| 8 条 memories | 本应用 prompt 和 parser | 人为单次生成上限，不是供应商容量 |
| 200 turns / 单 turn 4000 / 总 30000 字符 | 本应用 `normalize_conversation_turns` | 不是 tokens，不是 DeepSeek 官方 context；初始分片避开它不代表所有后续阶段都避开 |
| organization/support 4096 output tokens | 本应用显式请求参数 | 官方支持更大输出也不会自动提高此参数 |
| relation 2048 或 batch relation 4096 | 本应用请求参数 | 关系判断 JSON 同样可能到达输出限制 |
| `httpx.Client(timeout=60)` | 本应用 HTTP 超时 | connect/read/write/pool 分阶段；不是整个任务或整次响应的绝对 60 秒 deadline |
| Live 默认 3600 秒、512 MiB；长 profile 7200 秒、1 GiB；单帧 2 MiB | 本应用 relay/lease | 是否启用由服务端开关和用途决定；不是火山官方单场时长 |
| DeepSeek context / max output / concurrency | 官方模型与账号规格 | 须绑定对应型号、时点和实际账号；不能反推已失败历史请求 |
| 火山实时上下文 12K、历史 20 轮等 | 对应实时语音 API 模型/接口 | 不等同本应用累计 Source 容量，也不等同一场最多 20 分钟 |

DeepSeek 2026-09-22 可读到的官方英文资料标注当前 `deepseek-flash` 为 V4.1-Flash，context 1M、最大输出 384K；旧 `deepseek-v4-flash` 名称仍接受并转由 V4.1 服务。[官方模型页](https://api-docs.deepseek.com/quick_start/pricing/)与[2026-09-10 更新说明](https://api-docs.deepseek.com/updates/)。9/19 合成结果的响应型号与这个映射相容；但没有精确底层模型 revision/指纹，不能声称复现了更早私有现场的相同模型。当前新型号的上限不能用来覆盖旧型号事实。

官方 `max_tokens` 是输出限制，输入与生成合计还受 context 限制。`json_object` 约束 JSON 格式，不承诺本应用的字段、条数、证据、语义覆盖合同；官方还说明可能有空 content、输出被截断。[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。因此 HTTP 200/合法 JSON/`stop` 都不等于可以发布候选。

官方当前 Flash 并发为账号级 2500，超过会 429；等待尚未开始推理达 10 分钟才关闭连接，等待时可能发空行保活。这不是“最多处理十分钟语音”。[DeepSeek 限速与保活](https://api-docs.deepseek.com/quick_start/rate_limit/)。HTTPX read timeout 看的是接收下一数据块的等待时间，持续保活不能被当作独立整体 deadline。[HTTPX 官方 timeout 说明](https://www.python-httpx.org/advanced/timeouts/)。

火山本项目接的是 SpeechEngineToB / `volc.speech.dialog` / `wss://openspeech.bytedance.com/api/v3/realtime/dialogue`，iOS 当前 StartSession model 为 `1.2.1.1`（[DialogEngineManager.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:4615)）。2026-09-19 保存的官方 API 正文将它映射为 O2.0，另列 SC2.0 `2.2.0.0`；列出最大 context 12K、默认每 AppID 60 QPM / 100k TPM、`dialog_id` 带入最近 20 轮、ConversationRetrieve 默认最近 20 轮、45000003 是 10 分钟无交互。不能套用 RTC StartVoiceChat、独立 ASR 或方舟文本型号的规格。

火山证据是[9/19 官方正文快照](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/volc-official-api-20260919.md:23)，重点行 23、202、496、519、692、734、1009、1209。原[官方 API 链接](https://www.volcengine.com/docs/6561/1594356?lang=zh)和[SDK 链接](https://www.volcengine.com/docs/6561/1597646?lang=zh)本次重定向后的页面无法成功抓取；这些数字不能冒充 9/22 再次核实的当前账号额度。12K 超出后的具体策略、音频 token 换算、活跃连接最长时长，现有材料未证明。

实时模型上下文与应用逐条保存的 canonical Source 是两条链。火山忘记早期话题不能直接解释“早期正文已完整入 Source，DeepSeek 却未提取”；反过来，上传的 turn 数一致也不能证明每句实际说过的话均已识别并进入 canonical Source。

## 3. 当前默认调用链、参数与取值来源

当前 backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，工作树存在未提交改动。以下定位以本次读取的本地文件为准，不代表正在运行的生产实例。

默认 Worker 不注入 extractor 时，创建共享的 `ModelAssistedOwnerTruthLiveConversationExtractor`，其 repository 使用 store-backed 实现，再把它交给外层 `ModelAssistedOwnerTruthSourceExtractor`；`run_once` 先尝试 private preorganization，再处理 extraction job。代码：[默认装配](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:3037)、[外层路由](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2889)、[heartbeat 转发](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:3428)。注入 extractor 的测试不会自然得到这个默认组合。

Live extractor 默认创建 `DeepSeekLiveMemoryOrganizationProxy` 作为 organization/support/relation 能力提供者：[初始化](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:618)。模型名写在 adapter：`deepseek-v4-flash`；`thinking=disabled`，organization 温度 0.1，复核温度 0，JSON object，明确的 max_tokens。代码：[模型和上限](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:965)、[HTTP 60 秒](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1018)、[organization 请求](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1024)、[support 请求](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1227)、[关系请求](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1265)、[批关系请求](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1347)。

配置从 `Settings.from_env()` / `os.getenv` 读取；代码自身不是自动 dotenv 加载器。Compose Worker 用 `env_file: .env` 注入后执行 worker 模块。相关定位：[默认开关](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:145)、[DeepSeek 配置](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:347)、[relay 配置](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:361)、[环境读取](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:689)、[Compose](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/docker-compose.yml:55)。

本次只白名单检查本地配置键，没有输出 API key 或整个 `.env`。本地 `.env` 的 DeepSeek host 为 `api.deepseek.com`，没有发现长 pipeline / long profile 等覆盖键；审计进程 `Settings.from_env()` 的 organization、long pipeline、long profile 均 false，relay 为默认值。**这只证明本地文件/本审计进程值，不能证明生产开关关闭。** 9/20 历史现场报告反而记录当时部署 long pipeline/profile 已开、7200 秒/1 GiB；两个时点不可合并。

adapter 收到 `finish_reason=length` 会报 `outputTruncated`；其他异常原因也有校验。但 `None` 仍允许，观察记录后续有默认 `stop` 的做法；`_response_observation` 只保留 finish_reason 和 usage，没有读取 response envelope 的 `model`，持久化主要写请求 adapter 的 model 名。因而现有常规审计不能像 9/19 专门诊断那样可靠区分请求 alias 与实际响应型号。代码：[响应处理](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1635)、[安全观察字段](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1665)、[Provider attempt 写入](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1737)。

## 4. 分批真正解除的限制，以及仍可到达的边界

### 已实现的结构变化

初始 chunk 依据 Provider 200 turns、4000/turn、30000 总字符，并在新模式加最多 8 个 user finals；长 turn 先切句/片。组织输出截断或 support 遗漏等特定错误会递归细分，深度上限 8，预算沿同一 run 累积。[分片](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1301)、[细化](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1564)、[组织与验证](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1664)。并非所有 `schemaInvalid` 都会自动再分片。

跨批关系有 incoming 8 × existing 32 页，验证 scannedExistingCount 和每个 incoming 的结果完整性；关系改变后做 4 候选一页的复核。run04 引入稳定 evidence ID/源范围/hash 和 responsibilityAtomIds，使合法释义无需依赖生成句子的字面 `.find`，每页只负责其拥有的 atoms；全局 manifest 再核对 required/excluded、唯一归属、替代/撤回及 proof。[关系页](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2469)、[最终复核](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2259)、[support 合同](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_memory_support.py:101)、[manifest](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:1541)。

这些变化让同场超过 8、32、200 turns 或 30000 字符在特定分布下可处理；run04 同一个 F-65 Source 为 73728 字符、301 turns、150 user，得到 149 候选和 149 正式记忆，加另两场 41 条共 190。数据库重建与幂等也有本地证明。它依赖用户审核，候选不是自动变成正式记忆。

### 当前反例：按候选条数分页仍可能带回过长原文

`_relation_batch_page` 从 incoming/existing 的 `sourceTurnIndices` 选取原始 `turns`，没有在这里改为已经细分的 evidence 片段：[worker 第 2613 行](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2613)。adapter 随后仍执行统一输入限制：[batch normalization](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1366)、[normalize](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1769)。

本次使用真实 `_relation_batch_page` + 真实 DeepSeek adapter 输入校验、内存 run repository 和一旦创建网络 client 即报错的 adapter 子类，做了两个新合成边界探针。没有网络 client 创建、没有 HTTP、没有数据库：

| 输入 | incoming / existing | 结果 | network client attempts |
|---|---:|---|---:|
| 1 个原始 user turn，4001 字符 | 1 / 1 | `ValueError: live conversation turn 0 is too long` | 0 |
| 40 个 user turns，每个 1000 字符，总 40000 | 8 / 32 | `ValueError: live conversation transcript is too long` | 0 |

这是当前代码可达的内部前置失败，不是模拟“模型通过”，也不是重现了某个历史私有场。它直接否定“8×32 条数分页已经保证所有请求符合字符限制”。因此“新分批是否真的解除”应回答：**单次 8 条仍在；新实现通过多批及细化扩展整场处理能力，并移除单次全场 support，尚未全面解除所有后续请求的输入边界。** 旧版也有字符/turn 分块，不能把这次改造描述为首次引入任何分块；关键变化是降低初始单元密度、对遗漏等情形细化，以及将完整性责任从单次全场请求转到分片证明和全场台账。代码上 `extract` 第 976 行始终调用 `_organization_chunks`，长 pipeline 才额外传 `maximum_user_finals=8`；legacy 分支第 1098 行仍逐 chunk 请求，最后第 1208 行对整场 support。

可复跑入口：[probe-relation-input-boundaries.py](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/probe-relation-input-boundaries.py)，实际输出：[relation-input-boundaries.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/relation-input-boundaries.json)。执行使用 backend `.venv/bin/python`；完整入口为 `ModelAssistedOwnerTruthLiveConversationExtractor._relation_batch_page` → `DeepSeekLiveMemoryOrganizationProxy.request_relation_batch_review` → `normalize_turns`。脚本的 `_client` 无条件禁止联网；保存脚本后重跑得到同样两项结果。它只检查关系页输入前置校验，不宣称完整业务链通过。

### 预算与 deadline 的实现证据边界

| 项目 | 9/20 开发设计 | 当前代码 |
|---|---|---|
| work units | 2048 | 2048 |
| 总 provider requests | 512 | 2048 |
| 累计 input/output 预留 | 4M / 2M | 32M / 8M |
| recovery / unit extra / concurrency | 32 / 1 / 2 | 32 / 1 / 2 |
| 输入度量 | 完整实际 prompt 估计 | `conservative_token_estimate` 为 canonical JSON UTF-8 byte count；organization 有请求 builder；批关系用 turns+candidate payload，不含实际整份 prompt |
| 单请求整体 deadline | 独立整体 90 秒 | 检查的 Live adapter/worker/long-memory 模块未找到实现；HTTP timeout=60、preorganization lease=90 都不等价 |
| 会后无进展/绝对期限 | 600 / 3600 秒 | 在上述 Live 链检查范围未定位相应时钟约束 |

预算存在原子累计检查，[Policy](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:63)、[预留检查](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:1158)。但 byte estimate 不是真实 tokenizer 计数；数量上限也不是花费或耗时的真实模型测量。本次所读 run01–04 材料未找到当前 2048/32M/8M 相对于原设计的完整校准说明。这里记录差异及证据缺口，不推断是谁批准、何时部署。

## 5. 为什么多轮 LOCAL_PASS 仍不能回答真实长场是否成功

| 复核阶段 | 独立复核实际发现的关键反例 | 后续本地关闭边界 |
|---|---|---|
| 分批 run01 | 88 字符单 turn 12 事实仍卡 8；合法 JSON relation 可加入无依据“房子”；12 atoms 可只发 1 条；错误状态引用不存在 `failure.reason` | run02 处理，但仍存在组合缺口 |
| run02 | duplicate 清 proof 不重验；4 条补充页复核收到整 turn 12 事实；事实与问题同 turn 引起 required/excluded 冲突；失败私有 run 被主 extraction 再打第三次调用 | run03 处理 |
| run03 | 正常释义 `.find` 失败后退到整 turn，使 4 条页收到 6 事实；F-65 当时只证明 admission，41 正式记忆来自另一组；F-DENSE 直接 fake Provider 绕过 adapter 限制 | run04 引入稳定 evidence 和 page responsibility，并补同一 F-65 链 |
| run04 | 12 atoms 经合法 supplement 得 11 候选，正反例通过；同一 F-65 Source 149 正式记忆 | 真实 adapter builder/parser + MockTransport + 注入 extractor + 隔离 PostgreSQL；真实 Provider、真机仍未运行 |

run04 Astra 复核已经明确纠正“真实默认装配”的过强表述：主 F-65 流把生产同类 extractor 注入 Runtime，另有小场景验证无注入 default/preorganization；它们不能自动合成一个“F-65 完整默认链”证据。

当前 formal smoke 还有一个可定位的分支差别：`base=Settings.from_env()`，随后只替换 main_module 的 store/auth/policy，构造 TestClient；先 HTTP admit，**之后**才 `replace(base, owner_truth_live_long_memory_pipeline_enabled=True)` 供注入的 worker。HTTP route 使用的是 `main_module.settings`。因此既有 F-65 HTTP 成功不能证明长 pipeline 开启时 admission 的 Live run binding 分支经过测试。定位：[smoke base/HTTP](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:830)、[admit](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:1011)、[随后 worker settings](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:1053)、[route 开关](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:11868)、[service 分支](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:318)。当前 service 的 epoch 已取 `prepared.authority_epoch`；历史 `context.authority_epoch` 的代码时点由另一份 release 审计独立核对，不能用当前文件倒推历史。

## 6. 9/20 run04 长场为何不同

历史联合诊断记录：约 6 分钟后 canonical 只留下 13 user / 13 assistant，但 SDK 音频回调仍继续到更晚；outbox 26 完整、pending 0，却没有 close intent、end/ACK/admit，也没有最终 Source/review batch。私有 run 处于 collecting，只有一段完成，2 次 Provider 调用均成功：atom extraction 3.915 秒、prompt 2574/output 1171；support 1.446 秒、prompt 2187/output 237，均 `stop`，远未耗尽 4096。

本地已复现相同 ID 的 final 文本发生标点差异 → StoreConflict → Coordinator unavailable → ingress 关闭/释放 capture 的链，但现场环形日志丢弃 3095 条，最早可见日志已晚于断点；不能确定现场首次冲突的精确字段。火山 `ChatTextQueryConfirmed` 官方定义是 text query 的确认事件，并非普遍保证“第二份语音 final 应按同 ID 永远字面相同”的依据。它属于外部事件语义与本地不可变正文合同之间的相互作用，不能归并到 DeepSeek 长文本容量。

## 7. 仍缺的证据

- 9/18 精确结构错误；9/19 私有长场的遗漏明细、第二次 schema 字段、响应模型 revision 和原始 finish_reason。
- 火山当前账号/型号的最新有效额度、12K 后上下文处理策略、活跃连接硬时长，以及语音到 canonical Source 的逐事实覆盖。
- 新分批默认装配、长开关 admission、真实模型自然释义/纠正/撤回/密集事实的同一次端到端证据；本地 mock 输出不能代替它。
- 当前生产 worker/API 的同版本、配置、实际生效 profile、迁移与租约状态。本次明确未访问生产。
- 当前跨批关系整 turn 请求的边界在真实全场中的发生频率；新探针只证明代码路径可失败，不给出现实发生概率。
- 当前预算扩张、整体 deadline/会后期限与原设计要求之间的可追溯实现及校准证据。
- 物理 20 分钟、100+ 用户轮密集场、物理 65 分钟分别走完同场 Source→候选→用户审核→正式记忆→冷启动的真实验证。逻辑 3900 秒元数据和模拟音频流量不能等同真实持续运行。

## 8. 完整阅读清单与检索边界

以下“全文”表示读完整文档；代码按调用链和相关函数完整段落阅读，没有宣称读完整 backend 仓库。

机器可读逐文件清单：[read-files-provider.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/read-files-provider.json)。全文、节选与非 Markdown 证据分列，不把输出目录中所有附件或日志都算作已全文阅读。

### 设计/独立复核：全文

目录 `/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/`：

- `2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md`
- `2026-09-19-Astra-Live候选整理修复-交付复核与剩余修改清单.md`
- `2026-09-19-Astra-Live候选整理修复-run02放行复核与收尾清单.md`
- `2026-09-19-Astra-Live长场失败-供应商限制与内部容量核查.md`
- `2026-09-19-Astra-Live长场无法生成待确认记忆-深度根因核查.md`
- `2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md`
- `2026-09-20-Astra-Live长对话分批整理-本地与真机验收清单.md`
- `2026-09-20-发给Sol-Live长对话开发提示词.md`
- `2026-09-20-Astra-Live长对话分批整理-交付核查与剩余修复要求.md`
- `2026-09-20-Astra-Live长对话分批整理-run02复核与剩余闭环.md`
- `2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md`
- `2026-09-20-Astra-Live长对话分批整理-run04复核结论.md`
- `2026-09-20-DreamJourney-Live-run04长场失败-内外部联合诊断.md`

### 9/18 candidate-contract-fix：全文

目录 `/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/`：

- run-01：README；`2026-09-19-DreamJourney-Live记忆候选整理失败-本地修复报告.md`；`2026-09-19-BE-IR执行矩阵.md`；`evidence/blockers/postgresql-environment.md`；`evidence/independent-review/backend-head-baseline-route-review.md`；`evidence/green/ios-result-summary.md`。
- run-02：README；`2026-09-19-DreamJourney-Live候选整理修复-本地交付报告.md`；`2026-09-19-BE-IR执行矩阵.md`；`2026-09-19-真机复测清单.md`。
- run-03：README；`2026-09-19-DreamJourney-Live候选整理run02收尾-本地交付报告.md`；`2026-09-19-BE-IR执行矩阵-run03.md`。

### 9/19 真实模型容量分析

目录 `/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/`：

- 全文：`provider_capacity_diagnostic.py`、`evidence/provider-capacity-diagnostic.json`、`evidence/bound-session-capacity.json`、`evidence/current-request-timing.md`。
- 定向阅读：`evidence/volc-official-api-20260919.md` 的型号/context、配额、历史轮次、事件定义和错误码段，行号见第 2 节。未声称全文读完 1315 行官方快照；SDK 快照仅定位，不作为全文验收证据。

### 9/20 long-memory-batching：全文

目录 `/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/`：

- run-2026-09-20-01：README；`2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告.md`；`2026-09-20-DreamJourney-Live长对话分批整理-LM-LI执行清单.md`；`2026-09-20-DreamJourney-Live长对话分批整理-发布与回退准备.md`。
- run-2026-09-20-02：README；`2026-09-20-DreamJourney-Live长对话分批整理-run02本地交付报告.md`；`2026-09-20-DreamJourney-Live长对话分批整理-run02-LM-LI执行清单.md`。
- run-2026-09-20-03：`2026-09-20-DreamJourney-Live长对话分批整理-run03本地交付报告.md`；`2026-09-20-BE-IR-LM-LI执行矩阵-run03.md`；`2026-09-20-后续真机验收清单-run03.md`。
- run-2026-09-20-04：README；`2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md`；`2026-09-20-R02-B-PARA与补证执行矩阵-run04.md`；`2026-09-20-后续真机验收清单-run04.md`；evidence 下 `test-summary.md`、`relay-load-assumptions.md`、`log-redaction-scan.md`。

### 当前源码：相关完整函数/装配段

- `app/async_effects/owner_truth_candidate_extraction_worker.py`：Live extractor 构造/预整理/提取；初始分片、细化、证据绑定、组织与 support；单项和批量跨页关系；最终复核/manifest；外层 Source extractor、默认 Runtime、heartbeat；相关 retry/budget/stage 处理。
- `app/services/deepseek.py`：Live proxy、全部 Live 请求构造与响应提取、输入 normalization、organization/support/relation prompt/parser、usage/finish_reason；Text adapter 仅用于区分 timeout。
- `app/services/owner_truth_live_memory_support.py`：稳定 evidence catalog、scoped 与 legacy support 完整性判定。
- `app/services/owner_truth_live_long_memory.py`：identity/预算、provider reservation、unit/run 状态及 final completeness manifest 相关段。
- `app/core/config.py`、`docker-compose.yml`、`env.example`：对应环境键、默认值和 worker 注入路径；本地 `.env` 仅白名单键值/host，未读取输出秘密。
- `app/services/owner_truth_interview_candidate_proposal.py:290` 起 admission 分支、`app/main.py:11868` route 装配、`app/domain/owner_truth/source_commands.py` Source 容量入口。
- `scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`：controlled extractor 及 HTTP admit/settings/worker/scenario/restart 段；未在本轮运行 PG smoke。
- iOS `DialogEngineManager.swift`：StartSession model 定位；端到端 iOS 生命周期由并行专项审计负责。

### 当前源码指纹

| 文件（backend 相对路径） | SHA-256 |
|---|---|
| `app/async_effects/owner_truth_candidate_extraction_worker.py` | `975fa8739d8beb0b205db7e05565c5d50c8d5b014948b0ca8f908d83bb83d8c4` |
| `app/services/deepseek.py` | `316f9eeb26e83da4a677df296f57069bca6fa0a083daf6743f8d932772650460` |
| `app/services/owner_truth_live_long_memory.py` | `b43c62049f3a4803648a50cb2fffd95e64114713bcaee767ac57c0c1c873e862` |
| `app/services/owner_truth_live_memory_support.py` | `5bf7d1e9bb5c823199d4fbae02f0dae7b569eff111c4609108638578fb012239` |
| `app/core/config.py` | `f6f7b9a0cea8b7409c2a9cecdf65009c8551265b2ee5c40caa22a51ae7a4829d` |
| `app/services/owner_truth_interview_candidate_proposal.py` | `f89610ac523030ac85d6c85ea706e45ff3951de859255c0c0c213cd867573130` |

本文新执行验证仅为两个不出网边界探针；历史真实 Provider 与本地 mock/PG 的结果均注明来源和边界。状态：`READ_ONLY_ANALYSIS_COMPLETE / NEW_PROVIDER_NOT_RUN / PRODUCTION_NOT_ACCESSED / PRODUCT_CODE_UNCHANGED`。

## 9. 补读 9/18–22 辅助证据后的校正与补充

本轮随后完整补读 `remaining-inventory.json` 中 9/18–22 的 23 份辅助 Markdown，共 93252 bytes。全文清单从 44 份更新为 **67 份**；新增路径在 [read-files-provider.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/read-files-provider.json) 的 `supplemental_read_pass.files` 完整列出。火山两份官方快照仍明确列为节选/定位，没有算成全文。没有根据旧文档内的开发提示重新执行修复或外部操作。

### 数据库旧门禁确实闭合，不应反复抹去已通过部分

9/19 run02 PostgreSQL 独立复核认可真实 Worker→候选→审核→Memory/Version→Store/TestClient 重建和幂等，限定短 1 条/长 5 条；同时确实缺少提交中途回滚、两个连接竞争、模型等待期间 epoch 变化及长期事务观测。run03 独立复核逐条确认这些有限补证已经闭合：HTTP barrier 下活动 lease 唯一、真实 consume 后注入故障全部回滚、原 job attempt2 单份恢复、另一连接提升 epoch 后禁止旧候选、等待点 `pg_stat_activity` 无 idle-in-transaction。后续白名单历史资格也有实测。它们不是仅凭手填 true 判断。

因此这部分历史 `LOCAL_PASS` 可以保留，不能将后续发现的长输入或默认装配缺口反过来描述成“以前所有测试都没有价值”。run03 独立总论本身明确：超长 support 输入是另一个未关闭事项，历史精确 trigger 仍未知，Provider/设备/部署未运行。依据：[run02 PG 复核](../2026-09-19-live-candidate-delivery-review/run-02/evidence/postgres-review.md)、[run03 PG 复核](../2026-09-19-live-candidate-delivery-review/run-03/evidence/postgres-review.md)、[run03 独立结论](../2026-09-19-live-candidate-delivery-review/run-03/reports/2026-09-19-run03独立复核结论.md)。

### 9/20 首因不可恢复，除了 ring 覆盖还有观测路径缺失

辅助排除文档进一步明确：`canonicalTurnUpsert` / `captureStateChanged` 的 `PrivacySafeDiagnostics.log` 当时仅 `print`，不进入 NativeLiveDiagnosticsRingStore。因此不能把缺少首个采集失败证据仅解释为“ring 丢了 3095 条”；关键归因事件本就不在该持久 ring。

重复 ASR final（不依赖 3021）和重复 assistant ended 都在共享生产组件＋真实 Controller/Coordinator/磁盘/受控 HTTP 组合里复现冲突→整场采集退役；但 Simulator 的 `UI_QA_SIMULATOR` 不编译原生 SDK `handleProviderMessage` 完整 switch。ChatEnded 后 TTS 中间句再追加的风险有代码依据，没有真实 SDK 顺序执行证据。瞬时文件写入或 envelope 校验失败也仍是首触发次选解释。

另外，旧 `ConversationMemoryManager` 可能在 stop 把其接受的最后 20 条另存 `memory.json`；该路径受 UI turnIntent 门控，不是完整 canonical 备份，也不会自动补回当前 Source。文档没有读取该场文件，所以应说“canonical 主链只证实前 13 个 user/26 条”，不能说“后半场全部正文在所有存储中都已丢失”。依据：[canonical 控制器配对](../2026-09-20-live-run04-device-failure-analysis/evidence/canonical-controller-pair-analysis.md)、[排除与增量探针](../2026-09-20-live-run04-device-failure-analysis/evidence/ios-capture-path-exclusion-and-increment-probes.md)。

### 9/21 新火山账号确有真实成功，但只证明短冒烟

新账号实测沿用 `1.2.1.1`、相同 WebSocket 地址和后端上游鉴权构造，真实跑了两轮文字、两轮语音及生成中插入新语音，回答符合合成算术预期，五份音频解码且非静音，两场会话和连接正常结束。成功脚本约 26.55 秒；第一次验证器失败来自 Python wave 不支持 WAVE_FORMAT_EXTENSIBLE，不能写成火山失败。

这个结果说明不是只连上 HTTP 101；但长时稳定、配额上限、弱网、iPhone SDK、backend ticket/proxy 与完整记忆链全部明确 NOT_RUN，不能据此关闭长场容量验收。随后另一份切换记录显示，仅把六项 Live 环境配置接入当时活动 release `live-long-memory-run04-20260920-2230` 并重建 API；Worker、数据库、源码没有随之更新。凭证成功和代码修复是否部署是不同事实。依据：[真实新账号接口报告](../2026-09-21-volc-live-new-account-provider-test/2026-09-21-新豆包账号Live接口实测报告.md)、[切换与前置记录](../2026-09-21-volc-live-new-account-provider-test/2026-09-21-DreamJourney-火山Live凭证切换与真机前置记录.md)。

### 9/22 run05 的通过与漏项须同时保留

gate-ledger review 认可了配置和宿主/测试/xctestrun 绑定、门禁一次性消费、部分实际磁盘→HTTP→Source 正文账本、最终 manifest 的双证据保留。独立反例同时指出：21 项源码清单遗漏真实候选读取实现及 `owner_truth_conversation.py`；raw→disk 身份并非来自实际 canonical→delivery 映射；receipt 身份、Source 身份及请求 vault/session 错置仍能通过；正式 memory 的目标关联尚未逐条核对。

特别是该份 9/22 文档已定位本地 `live-delivery-status` SQL 使用 `s.current_thread_id AS thread_id`，但这个文件未进入当时门禁散列。它证明源码依赖绑定漏项，不证明那时生产也已更新。当前设备场和实际生产版本应以主审计的独立现场证据为准，本分报告未访问生产。

C/D 复核又发现：长场输入替换负例把 220/300 turns 直接交给最多 200 turns 的 adapter，HTTP 前就被 inputInvalid 拦住，不能证明假 Provider 在 HTTP 边界识别了篡改；support 删掉实际所需 turn、relation 换掉证据和命题，原受控应答/后置校验仍能通过。另一方面，独立新注入的 429/ReadTimeout worker 预算四项确实通过。不能把前者当业务已造假，也不能把后者冒充 Sol 原测试已包含。依据：[A/B gate-ledger 复核](../2026-09-22-astra-live-round-simulation-review/run-05/evidence/gate-ledger/review.md)、[C/D 复核](../2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/README.md)。

其余补读包括 9/18 认证恢复隔离原型、9/21 capture/manifest 正反探针、真实短场 HTTP/PG 门禁以及授权清理记录。它们补齐历史证据层级；未改变本报告关于 8 条冲突、跨批关系输入边界、真实 Provider 验收不足的结论。9/21 清理是明确授权将 46 条待确认标拒绝，正式记忆 72 条和 Source 101 条保持不变；不能将该次待确认归零误判成模型未生成。
