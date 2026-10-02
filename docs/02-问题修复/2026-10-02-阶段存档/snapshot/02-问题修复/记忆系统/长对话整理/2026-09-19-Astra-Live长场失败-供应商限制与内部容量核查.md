# DreamJourney Live 长场失败：供应商限制与内部容量核查

> 后续已补齐服务器逐请求耗时、精确输入长度、火山官方正文和经用户批准的真实模型对照。最新结论见[深度根因核查](2026-09-19-Astra-Live长场无法生成待确认记忆-深度根因核查.md)。下文保留首轮调查时的证据边界，不应继续把火山12K等已补足信息视为未知。

日期：2026-09-19\
关联问题：DJ-LIVE-L20-01\
性质：只读调查、离线合成反例与后续设计依据；不是已完成修复或真机放行报告。

## 1. 结论与证据强度

本场原始对话保存、关闭交接已通过，失败位于会后候选整理。需要同时核对供应商硬限制、模型输出能力、我们实际发送的参数及内部校验，不能只因 HTTP 200 就排除供应商影响。

目前有三层结论：

1. **现场已确认**：77 个 turn（39 个 user turn）进入组织请求；第一次组织生成 8 条草案，复核报告事实遗漏；第二次组织返回后结构校验失败，最终候选为 0。
2. **源码及离线反例已确认**：组织器最多允许 8 条记忆，同时要求不同主题原子化、复核覆盖全部明确事实。这组要求在超过 8 个不能合并的独立事实时存在容量矛盾。现有分块不按事实密度或结构化输出预算拆分。
3. **尚未确认**：现场具体遗漏哪些事实、第二次哪个结构检查失败、是否受到 4096 输出 token 预算或模型版本变化影响。不能把合成反例直接写成历史现场唯一根因。

当前没有本场触发 DeepSeek 上下文超限、429、请求超时，或火山整场超时的对应证据。这不等于供应商没有限制，也不等于模型语义输出一定可靠。

## 2. 本场真实失败链

| 阶段 | 已记录结果 | 能说明什么 |
|---|---|---|
| Live 持续采集、正文同步、end/ACK/admit | PASS | 原有保存与任务交接链本场通过 |
| Attempt 1 organization | 输入 77/39；HTTP 200；decoded；validated；memoryCount=8 | 返回可解析且通过本地组织结构校验 |
| Attempt 1 support | HTTP 200；decoded；supportValidate.factOmitted | 复核返回合法、非空的遗漏用户 turn 索引列表 |
| Attempt 2 organization | HTTP 200；decoded；organizationValidate.schemaInvalid | 已取得响应，但组织结构未通过检查 |
| 最终结果 | candidateExtractionRetriesExhausted；候选 0 | 未通过整场整理合同，未提交候选 |
| 失败坐标、只读恢复、冷启动 | PASS | 失败可恢复观察，不等于候选已经生成 |

摘要可数到 3 次成功 HTTP 响应：2 次 organization、1 次 support。它不是逐请求原始审计，不能据此补写未记录的调用信息。

`factOmitted` 的精确含义是“复核模型声称有用户事实没有被草案覆盖”。它可能是真遗漏，也可能是复核误判，不能写成“support 漏掉了 organization 已经提出的合法事实”。

`schemaInvalid` 合并了多种校验，包括草案数量、类型、正文、引用和 facets；不能仅凭此码认定 JSON 被截断，或第二次必然输出了 9 条。

证据：

- [独立问题记录](../../../outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md)
- [服务端阶段摘要](../../../outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/evidence/2026-09-19-L20-服务端阶段证据摘要.md)
- [真机记录，采用最新长场结论](/Users/gaominge/Documents/liftora/outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md:233)

## 3. DeepSeek：官方能力与实际使用限制

当前源码使用 `deepseek-v4-flash`，默认地址为 `https://api.deepseek.com/v1/chat/completions`；地址可由配置覆盖。本轮未读取生产环境配置或本场响应的实际 model，默认值不能替代部署指纹。

| 维度 | 官方文档或当前实现 | 对本场的判断 |
|---|---|---|
| 官方模型窗口 | 当前 Flash 文档为 1M 上下文、最大 384K 输出 | 这是供应商能力上限，不是本应用实际请求预算；未记录实际 token 用量，不能计算本场占用率 |
| 模型名称映射 | 官方说明旧 `deepseek-v4-flash` 名称仍被接受，但转由 V4.1-Flash 服务 | 代码名称不变也可能发生模型版本变化；尚无本场实际响应 model 证据，不能指认为原因 |
| 实际输出预算 | organization 与 support 均显式 `max_tokens=4096` | 供应商支持更大输出不会自动扩大我们的请求；复杂 JSON 会消耗预算 |
| 实际模式 | JSON object；thinking disabled；组织温度 0.1、复核温度 0 | 非思考模式本身不是已证实缺陷；低温度也不能证明语义完整或结构必然合格 |
| 并发限制 | 官方当前 Flash 账号并发 2500，超额返回 429 | 本场已记录失败请求为 200，没有对应 429 证据；账号实际额度仍以配置为准 |
| 供应商等待超时 | 官方说明请求等待 10 分钟仍未开始推理时关闭连接 | 不能解释成最多处理 10 分钟语音或推理总时长 10 分钟；本场并非该错误路径 |
| 我们的网络超时 | `httpx.Client(timeout=60)` | HTTPX 分连接/读/写/连接池超时；不能等同整项任务最多 60 秒 |
| JSON 能力边界 | JSON mode 要求格式并提示合理设置输出预算，也提示可能出现空内容 | JSON 格式能力不能代替应用 schema、事实完整性、纠正关系校验 |

官方来源：

- [DeepSeek 模型能力与旧名称映射](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek Chat Completions 请求与响应](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek 并发与连接保活限制](https://api-docs.deepseek.com/quick_start/rate_limit/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [HTTPX timeout 的具体语义](https://www.python-httpx.org/advanced/timeouts/)

当前代码把 `finish_reason=length` 单独映射为 `outputTruncated`，发生在后续 schema 校验之前；本场记录不是这个分支。由于原始 `finish_reason` 与 usage 未保留，仍不能反向补写为 `stop`，也不能证明预算完全充足。

## 4. 火山 Live：适用产品与未核实边界

实际源码接入的是 SpeechEngineToB，StartSession 的 model 参数为 `1.2.1.1`；后端资源 `volc.speech.dialog`，地址 `wss://openspeech.bytedance.com/api/v3/realtime/dialogue`。不能套用 RTC StartVoiceChat、独立流式 ASR 或方舟文本模型的限制。

我们自己的代理默认值：单场 3600 秒，单帧 2 MiB，全场双向累计 512 MiB。连接票据 TTL 为 60 秒，用于建立连接，消费后建立会话 lease，不代表每 60 秒结束。生产配置可覆盖这些值，本轮未读取实际部署值。

火山官方 API 和 SDK 主页面本次获取遭遇重定向或动态页面，未取得可核实的当前单场时长、上下文 token、静默断开和单次输入硬上限。**这些项目应保持“待核实”，不能写成无限制或已排除。**

可取得的官方更新说明中，`end_smooth_window_ms` 为用户判停等待时间，默认 1500ms、范围 500ms–50s；这是切句参数，不能解释成整场时长上限。[官方更新说明](https://www.volcengine.com/docs/6561/162929?lang=en)

后续应按实际 app/resource/model 版本核对官方控制台或接口正文，不沿用项目旧文档的 O2.0 12K 等历史参数，除非确认与当前接入版本相同。

- [对应 API 文档，正文限制仍待核实](https://www.volcengine.com/docs/6561/1594356?lang=zh)
- [对应 iOS SDK 文档，正文限制仍待核实](https://www.volcengine.com/docs/6561/1597646?lang=zh)
- [StartSession 实参](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:4225)
- [代理默认配置](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:352)
- [票据转会话 lease](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:207)

火山实时模型上下文与应用独立累计的 Source 正文是两条链。即使实时模型忘记早期话题，也不能解释已经保存在 Source 中的事实为何未被会后整理提取。反过来，“全部已登记正文上传成功”不能证明全部原始语音均被正确识别；要排除上游遗漏，需要将测试事实对照 canonical Source，而不是仅比较条数。

## 5. 已确认的内部容量冲突

当前组织与复核参数：

- 组织单请求：200 turns、单 turn 4000 字符、合计 30000 字符；最多 8 条记忆，每条主要正文最多 1000 字符。
- 支持复核：再送整场 turns 与全部草案，仍沿用上述输入限制，草案最多 32 条。
- 组织分块按 turn 数、字符数及分段索引安排，不估算事实密度或输出 JSON 规模。
- 组织 prompt 要求少量原子化记忆、不同主题分开；复核 prompt 要求明确新事实、感受、观点和时间补充都有最终草案。
- 固定修复提示要求重新完整组织与复核，但不带具体遗漏坐标；后续请求仍是 8 条和 4096 tokens。

因此，**输入放得下，不等于符合完整性要求的输出也放得下**。增加等待时间或在相同限制下重试，不能消除这种冲突。39 个用户 turn 不等于 39 个独立事实；本场“恰好 8 条”是满额线索，不是唯一原因证明。

代码定位：

- [请求参数与上限](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:973)
- [支持复核完整性要求](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1334)
- [组织 prompt](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1402)
- [超过 8 条直接拒绝](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1455)
- [组织分块算法](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:785)
- [factOmitted 的具体触发条件](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_memory_support.py:135)

离线合成探针使用全新内容与受控 HTTP，不调用真实模型，不读取或重放现场正文：77 turns、39 user turns、538 字符，仅分 1 块。提供 8 条草案并让受控复核指出剩余事实遗漏，得到 `factOmitted`；提供 9 条结构合法草案，得到 `schemaInvalid`，直接解析错误为 `too many memories`。

这证明内部限额足以制造同形错误链；它不证明真实模型必然这样输出，也不证明现场第二次确实超过 8 条。

- [探针源码](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/synthetic_77_39_capacity_probe.py)
- [执行结果](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/synthetic_77_39_capacity_probe.txt)

另有独立容量风险：组织可以分块，但支持复核仍一次接收整场并受原始 turn/字符及 32 草案限制。仅拆组织请求不能完成长场容量修复；本场两个 inputBuilt 已通过，不应把这个更大输入的风险冒充本场触发点。

## 6. 下一步设计与验证要求

### 6.1 补足可归因诊断

每次调用只保留安全元数据：实际域名及请求/响应模型名、配置与 prompt 版本、输入字符数和 turn 数、分块数、输出条数、请求耗时、HTTP 状态、usage、finish_reason。缺失值明确 unknown。

将 `schemaInvalid` 的具体检查点记录为固定枚举和不含正文的结构路径；遗漏只记录数量及受控索引。不能把完整响应、对话正文、密钥或任意异常文本写入普通日志。

对现场精确触发点，现有证据不足即保留未决，不通过自动重放失败任务“补证”。

### 6.2 容量设计方向

后续修复重点应是输出容量可满足完整性要求：容量感知的分批原子提取、稳定证据索引、全场纠正/撤回/去重、可分批但不丢跨块关系的支持复核，最后在原 Source 与原场次边界内完整提交。

需同时设计每批输出 token、全场草案数、复核规模和总调用预算。不能简单把 8 改成任意大数，也不能删除事实复核、把多个无关主题硬塞成一条、增加无界重试或提前显示成功。

供应商别名映射、JSON 保证范围和超时语义必须进入接口设计；是否换模型或使用其他结构化输出方式，应由受控比较结果决定，不能先认定“换更大模型”即可修复。

### 6.3 分阶段验收

本地开发必须独立完成，不等待手机。先用真实 adapter/Worker/数据库组合验证：超过 8 个独立事实、77/39 事实密集场、单轮多事实、跨块纠正、后半场补充、预算边界、截断/429/超时/非法结构、租约丢失与幂等。不能仅用预设 5 个目标事实的绿测试代表整场完整性。

受控 Provider 验证与本地验证分开记录；后续在明确执行范围内使用合成短/长输入，记录实际模型与 usage，验证当前模型返回的真实结构和覆盖率。不得自动以私人现场正文替代合成样本。

真机由用户主动发起，短场与物理至少 20 分钟长场均验证：完整正文 → 待确认候选 → 用户审核 → 正式记忆 → 冷启动回查且不重复；长场包含首中尾事实、后半场补充和明确纠正。

保持已通过的音频、持续采集、正文上传、end/ACK/admit、短场闭环及失败坐标恢复。若修改共享代码不可避免，受影响模块必须列入回归，不能把旧 PASS 当作新版本免测依据。

## 7. 本轮范围

已执行：本地记录与源码核对、官方公开文档查询、离线合成容量探针、分析文档。

未执行：产品源码修改、真实模型请求、服务器访问、生产或历史数据修改、失败任务重放、手机操作、部署、commit/push。

当前判断：内部容量合同存在可复现缺陷；现场精确 schema 触发点和火山当前硬限制仍待核实；长场业务仍为 FAIL，不能标记已修复。
