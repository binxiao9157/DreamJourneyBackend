# Live 长场无法生成待确认记忆：深度根因核查

关联现场：2026-09-19，DJ-LIVE-L20-01。\
范围：只读现场核查、代码历史与官方接口核对、经用户明确授权的最多 8 次合成模型诊断。\
本轮只查根因，不修改业务、不提出已定稿开发方案、不进行真机测试。

## 1. 核心结论

**已通过当前部署代码、历史提交及真实 DeepSeek 对照，证实一项会导致长场候选生成失败的内部容量缺陷：组织器最多输出 8 条原子记忆，但后续复核要求覆盖整场事实。事实超过单次输出条数容量时，组织器受限省略，复核拒绝整批，结果候选为 0。**

现有分块主要看输入字符与 turn 数，没有为事实数量及输出条数安排足够容量。因此“20 分钟文本仍能放入一次请求”不能证明一次请求能产出所有合格记忆。

对本次真机现场，已确定首轮失败是复核报告遗漏，第二轮结构校验失败，预算终结。真实模型诊断复现了首轮同码失败，并在仅改变条数上限后通过；这把内部容量冲突从推测推进为真实请求可触发的缺陷。它与首轮现场相符，但现存证据尚未证明该私人场次确有超过 8 个不能合并的独立事实，不能定为全部历史失败的唯一根因。

**仍须保留一个边界：历史第二轮 `schemaInvalid` 的具体字段原因已被当时程序丢弃，无法从现存记录恢复；历史首轮遗漏索引也未保留。不能声称已经还原那一轮全部模型输出，或第二轮必然输出了超过 8 条。**

最新现场有完整响应时间证据，可以排除“该场这些整理请求因等待达到 60 秒而失败”。真实对照也显示：在这组长场样本上，4096 token 预算足以容纳 12 条结果，失败首先受制于内部条数上限，而不是供应商 token 用尽。

## 2. 本次新增的生产只读证据

仅在服务器内关联两场已定位 job，数据库事务为只读，返回安全数量、时间、配置及代码指纹；未向本地传回正文、账号标识、模型响应或密钥。

### 2.1 输入与运行配置

| 指标 | 短场成功 | 长场失败 |
|---|---:|---:|
| Source 中 user turn | 2 | 39 |
| Source 中 assistant turn | 1 | 38 |
| Source 中总 turn | 3 | 77 |
| 全部 conversationTurns 正文字符 | 78 | 4564 |
| 最长单 turn 字符 | 29 | 130 |
| content_payload.text 字符 | 51 | 1595 |
| 原始组织请求完整 prompt 字符，含模板与结构 | 4014 | 11417 |
| 原始组织请求完整 prompt UTF-8 字节 | 5750 | 22013 |
| 显式 max_tokens | 4096 | 4096 |
| 组织最大记忆条数 | 8 | 8 |

请求长度是在服务器内用现存 Source 和当前部署 adapter **只构造、不发送**得到，不能称为捕获到的历史 HTTP 请求字节。修复尝试的固定提示加入后，长场 prompt 为 11508 字符、22124 UTF-8 字节。

当前部署输入上限仍是 200 turns、单 turn 4000 字符、合计 30000 字符；本场均未接近。字符不等于 token，不用字符数伪装精确 token 使用率。

整理实际配置域名为 `api.deepseek.com`，请求模型名为 `deepseek-v4-flash`。部署 Worker 中 adapter、support validator 和 Worker 三个文件的 SHA-256 均与本次审查的本地源码一致。

### 2.2 逐次真实 HTTP 耗时

耗时按服务器现存 stage 日志的 RequestStarted 至 ResponseReceived 计算，包含网络与服务等待，不等于纯推理时长。

| 场次 | 请求 | 耗时 | HTTP | 随后结果 |
|---|---|---:|---:|---|
| 短场 | 组织 | 1.614 秒 | 200 | 2 条草案通过 |
| 短场 | 复核 | 0.767 秒 | 200 | 通过，提交 2 条候选 |
| 长场 attempt 1 | 组织 | 7.505 秒 | 200 | 8 条草案通过 |
| 长场 attempt 1 | 复核 | 2.189 秒 | 200 | factOmitted |
| 长场 attempt 2 | 组织 | 8.154 秒 | 200 | schemaInvalid |

长场第一 attempt 约 9.711 秒；下一 attempt 在约 8 秒等待后开始。从首个组织请求开始到最终失败约 25.95 秒。页面等待超过 20 秒不代表一个模型 HTTP 请求超时。

这些记录表明本场失败发生于收到响应后的应用校验，不是输入超限、429、HTTP timeout，也没有进入当前代码专门处理的 `finish_reason=length → outputTruncated` 分支。历史 finish_reason 原值未保留，不能补写为 stop。

## 3. 经用户批准的真实模型对照

用户批准最多 8 次使用现有 DeepSeek 账号的合成诊断。本轮实际执行 **8 次**，未额外重试。

所有输入是新构造的合成内容，没有读取私人现场正文。长样本固定为 77 turns / 39 user turns，包含 12 个不同主题的明确原子事实，其余 user turns 为问题。三个长样本的输入相同，诊断变量是条数上限与输出预算。

全部通过同一实际 adapter 路径调用 organization/support；仅在独立 Python 进程的子类内改变参数，不修改源文件、环境配置或现有 Worker，不创建 Source、job、候选或记忆。

| 组别 | 事实数 | 条数上限 | 输出预算 | 模型组织结果 | 复核 | 结果 |
|---|---:|---:|---:|---|---|---|
| 短场，现有参数 | 2 | 8 | 4096 | 2 条；622 completion tokens | 遗漏 0 | 通过 |
| 长场，现有参数 | 12 | 8 | 4096 | 8 条；2221 completion tokens | 遗漏 4 | factOmitted |
| 同一长场，仅改条数 | 12 | 24 | 4096 | 12 条；3400 completion tokens | 遗漏 0 | 通过 |
| 同一长场，另扩大输出预算 | 12 | 24 | 8192 | 12 条；3507 completion tokens | 遗漏 0 | 通过 |

8 次响应均 HTTP 200、`finish_reason=stop`，响应声明 `model=deepseek-flash`；单次耗时 0.938–9.254 秒。

三个长场组织请求的 prompt token 都为 **2671**。现有参数组只消耗 2221 completion tokens 就正常结束并返回 8 条，说明该组不是输出达到 4096 后被截断；仅扩大条数的组在仍为 4096 的预算下完成 12 条，证明这份输入不需要先扩大 token 预算才能通过。

这组结果验证了“条数容量限制导致遗漏，再被全覆盖复核整批拒绝”的机制。表中遗漏数量来自 support 模型判断；12 条通过代表通过当前支持合同，脚本没有保存逐条事实与独立真值的比对，不能写成人工证明每个事实百分之百准确。它不是历史私人对话重放，不能替代历史字段级错误证据，也不能证明把产品上限改成 24 就能支持所有长场。真实模型每组仅执行一次，不宣称长期稳定率或全面验收通过。

## 4. 为什么以前能产出候选，后来容易整场失败

仓库历史定位如下；提交记录证明代码变化，不单独证明上线时间或所有历史现场发生原因。

| 提交 | 时间 | 行为变化 |
|---|---|---|
| `f7c5d9a40110d22e51af491efa2cb9be98d6a8e1` | 2026-08-13 | 引入 Live 持久化整理，最多 8 条；按 turn/字符分块 |
| `51f100d05b57ce98871db1017eec0e70d29495fe` | 2026-08-24 | 输出预算由 2048 调为 4096，明确关闭 thinking；保留 8 条 |
| `f3ebc8a4937a5be8bc27762369d4193ea683d0ac` | 2026-09-15 | 增加整场独立事实支持复核；遗漏非空即整批拒绝；未配套调整 8 条容量与分块 |

这是已确认的规则变化：旧路径可能保存结构合格但不完整的 8 条；新路径要求完整性，同样的遗漏会阻止任何候选落地。用户看到“以前有记录，现在一条也没有”，与这种变化相符。

问题在于提取容量与完整性门槛没有同时适配。完整性与防止助手回答被当作用户事实的保护本身不能据此删除。本轮没有证据把这次失败归因于此前长回答播放或车载蓝牙修改。

## 5. 外部供应商边界：查到什么，是否与本场相符

### 5.1 DeepSeek

官方当前 Flash 文档列 1M 上下文、最大 384K 输出，并说明旧 `deepseek-v4-flash` 名称转由 V4.1-Flash 服务；本轮真实合成响应声明 `deepseek-flash`，与旧名称映射需区分。历史响应实际 model 未保留，不能事后认定发生了哪次底层版本切换。[官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)

官方并发超额会返回 429；长时间排队未开始推理有连接关闭规则。本场三次请求实际返回快且为 200，当前真实合成诊断也都正常结束，未观察到这些限制触发。[并发和保活说明](https://api-docs.deepseek.com/quick_start/rate_limit/)

JSON object 模式不等于完整满足业务字段和事实覆盖。此次真实诊断中模型按“最多 8 条”输出了合格 JSON，复核随后报告合成输入仍有 4 个事实未覆盖，触发整批拒绝：这暴露了跨阶段要求不匹配，HTTP 成功无法替代业务校验。[官方 JSON 文档](https://api-docs.deepseek.com/guides/json_mode/)

### 5.2 火山端到端实时语音

本次已取得对应官方 API 完整正文，补足上一轮“主页面无法读取”的缺口。API 文档更新时间为 2026-08-20。

| 已确认的官方限制 | 适用含义 | 本场判断 |
|---|---|---|
| model `1.2.1.1` = O2.0；最大上下文 12K | 实时模型上下文容量 | 不能把它当作应用 Source 的保存上限 |
| 10 分钟没有对话交互释放连接 | 空闲断开，错误45000003 | 活跃聊 20 分钟不等于空闲 10 分钟 |
| dialog_id 加载、默认 ConversationRetrieve 最近20轮QA | 上下文加载与查询范围 | 本应用逐条持久化；本场 Source 已有39个用户turn，没有仅用此接口取尾部20轮 |
| 默认60QPM、10万TPM | AppID每分钟 StartSession 次数及 token 流量 | 不是每场60轮或20分钟时长限制；本场未见相应限流证据 |

官方错误表还有时长相关错误名，但未列出活跃单场最长时长数值；12K 超出后的具体处理及音频 token 折算也未明确，本轮不自行推导。

源码核查未发现代理静默裁剪前半场文本的路径：它原样转发，超流量/失去授权会关闭连接；iOS 通过 canonical 事件独立累计 Outbox，未依赖 ConversationRetrieve 生成整场 Source。当前 Worker 环境中的代理配置为单场 3600 秒、单帧 2 MiB、全场双向 512 MiB；这不是对 API 容器实际环境的单独核验，也不是火山官方限额。

因此火山容量会影响实时上下文能力，应纳入产品边界，但**不能用火山上下文遗忘解释已经在本场 Source 中保存、随后进入 DeepSeek 整理的正文被整批拒绝**。原语音有无 ASR 漏识别另需事实与 Source 对照，本轮未宣称逐字音频完整性得到证明。

官方来源：[实时语音 API](https://www.volcengine.com/docs/6561/1594356?lang=zh)、[迁移后 API 页面](https://docs.volcengine.com/docs/DoubaoVoice/End-to-endreal-timespeechlargemodelAPIaccessdocument?lang=zh)、[iOS SDK](https://www.volcengine.com/docs/6561/1597646?lang=zh)。本次通过官方公开正文接口取得内容并保留了本地文本快照。

## 6. 为什么第二轮具体错误不能事后完全还原

adapter 将多种解析错误统一包装为 `schemaInvalid`；原始原因只在进程内 `__cause__` 中。Worker 捕获后不保留该链，也不输出 traceback，只把类型化失败码持久化。

本次读取的现存 Worker 日志没有原始原因。`job_attempts` 与 `extraction_results` 没有原始模型响应、usage 或字段级失败点；Live Worker 未接入其他响应审计存储。具体异常已丢失，继续读取相同记录无法恢复。

这限制了历史单次事件的精确归因，不能否定已经由真实模型对照证明的容量缺陷，也不能用对照结果虚构历史第二轮返回了什么。

## 7. 历史多次失败不能合并为同一原因

| 现场 | 失败位置 | 与最新问题关系 |
|---|---|---|
| 70 段本地、48 段服务端 | append认证恢复阻断，22段未排空，未完成end/ACK/admit | 之前的上传链问题；本次正文与关闭交接已通过 |
| 9/18长场与短场均无候选 | 整理合同统一错误码，字段原因未留存 | 当时短场也失败，不能一概归为时长或容量 |
| 9/19短场完整闭环成功、长场失败 | 首轮遗漏、第二轮结构不合格 | 本轮深查对象，容量冲突已由真实模型对照验证 |

本轮保留已通过的短场候选/审核/正式记忆、Live语音、正文同步、关闭交接和失败恢复结论；没有改变这些业务模块。

## 8. 证据与执行边界

- [服务器逐请求时间线](../../../outputs/2026-09-19-live-l20-capacity-analysis/evidence/current-request-timing.md)
- [服务器白名单阶段元数据](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/current-worker-metadata.json)
- [精确绑定的长短场容量与部署指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/bound-session-capacity.json)
- [真实 Provider 合成对照结果](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/evidence/provider-capacity-diagnostic.json)
- [对照脚本，默认不执行模型请求](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-l20-capacity-analysis/provider_capacity_diagnostic.py)
- [官方火山 API 文本快照](../../../outputs/2026-09-19-live-l20-capacity-analysis/evidence/volc-official-api-20260919.md)
- [官方火山 SDK 文本快照](../../../outputs/2026-09-19-live-l20-capacity-analysis/evidence/volc-official-sdk-20260919.md)

状态：`DIAGNOSIS_COMPLETED_WITH_HISTORICAL_FIELD_GAP / PRODUCT_FIX_NOT_STARTED`。

执行了经批准的 8 次真实合成模型调用及只读服务器核查；未修改产品源码、数据库或历史任务，未部署、未 commit/push、未操作手机。本次通过项是诊断对照，不是产品修复验收；真机仍由用户后续主动发起。
