# DreamJourney Live 全链路历史复盘与根因证据报告

核查日期：2026-09-22。历史范围：2026-09-10 至本轮最新人工短场。本文是根因复盘，不是开发方案，也不授权部署或历史重放。

结论状态：**若干独立根因已证实；部分历史首发原因及最新第 5 条消息进入未知结果的初始触发仍未查明。不能宣布所有根因已经闭合。**

## 1. 先回答最关心的四件事

**短场以前确实成功，也确实被长场相关改造改坏过。** 至少两次有源码、补丁和前后证据：9/15 更换采集入口后，真实 ASR final 被降为 interim，等待现场没有出现的确认事件；9/16 新授权校验把两种不同身份格式直接比较，导致短场也无法发起保存。这不是用户印象错误。

**截至所查记录，没有找到物理 20 分钟长场完整走到本场候选、审核、正式记忆和重启读取的真机成功证据。** 9/19 有真实短场完整链成功；后期有逻辑 20/65 分钟、真实 HTTP、隔离 PostgreSQL 的局部或组合成功。它们有价值，但不能替代物理长场成功。

**反复失败不是一个从未修好的单一故障，而是不同位置接连断裂，加上验收和运行版本没有始终对齐。** 有的断在手机采集，有的断在认证/上传，有的断在 admission，有的已经完整提交但模型整理合同失败，还有独立的旧任务状态覆盖页面问题。只改“正在保存”的状态或补一条重试，并不能证明整个功能恢复。

**容量怀疑有依据，但已证实的瓶颈主要在我们自己的处理合同。** 旧版每次 organization“最多生成 8 条”与该处理块的事实完整性要求冲突；新分批还有后续关系请求重新带入过长原文的可复现边界。现有失败证据不支持“火山或 DeepSeek 到 20 分钟就停止保存”。最新人工短场更是尚未进入会后候选整理，不能归因于 DeepSeek。

我对过去几轮过宽的验收表述负有责任：局部红绿、组件通过、受控模型通过，曾被概括成范围更大的 LOCAL_PASS 或 READY；部分结论后来被独立复核撤回。即使文末写了 DEVICE_NOT_RUN，也不能弥补本地验收本身漏过生产输入或默认装配的问题。本文保留真实成功，同时撤回不受证据支持的外推。

## 2. 最新人工短场：手机与服务器已逐项对齐

本场使用同一个 productSessionID 关联，不用时间相近的历史任务冒充本场。对外只保留状态、数量、单向摘要和时间，不附对话正文或凭据。

| 检查点 | 本轮只读查到的事实 | 可下的结论 |
|---|---|---|
| 火山语音交互 | 人工记录三次有声回答并恢复聆听 | 本场语音交互正常；不能因此说记忆完成 |
| 手机 canonical / Outbox | 3 条用户输入 + 3 条助手回答，六条都有正文，complete、sealed，无 canonical issue | 第三轮没有在这份手机采集记录中丢失 |
| 前两轮上传 | 服务端 seq1–4 已保存；逐条 message ID 的摘要与手机相符 | 本机与服务器确属同场同消息，不是只比较数量 |
| 第三轮用户输入 seq5 | 手机保存冻结原命令，dispatchState=outcomeUnknown；服务端当前无该 message，也无该原 command 的已提交回执 | 当前未提交成功；仍不能据此判定原请求从未上网或精确网络失败原因 |
| 第三轮回答 seq6 | 手机 preparedNotExposed，排在 seq5 后 | 串行队列等待前项核实，尚未继续送达 |
| 核实接口 | 22:01:40 本场 live-delivery-status GET 返回 HTTP 500；异常为 UndefinedColumn：s.thread_id | 本应解除“结果未知”的只读恢复接口自身失败 |
| 数据库与运行代码 | 表列是 current_thread_id，运行代码 SELECT/JOIN 却使用 thread_id | 是可确定的内部 SQL/字段合同错误，不是模型拒绝 |
| 手动停止 | 22:02:09 手机 close intent 与水位 6 已保存；服务器仍 active、连续水位 4、关闭水位为空 | 停止意图在本机，但排空及服务端关闭没有完成 |
| 候选入口 | 当前场无 review batch；消息回执只有 start + 四个 append，没有 end | 本场会后候选整理尚未具备进入条件，不能解释为“候选生成了但没显示” |

时间为北京时间；证据中的 UTC 已加八小时。22:01:40 的恢复错误早于手动停止，不能把它说成停止按钮触发的首次错误。

这场目前能确定的因果链为：

```text
六条内容完整落在手机
  → 前四条提交成功
  → 第五条出现结果未知【初始原因未确定】
  → 第六条等待；只读核实第五条
  → 后端旧 SQL 查询不存在的列，GET 500【已确定】
  → 不能完成核实和排空
  → end / ACK / admit 未完成
  → 没有本场待确认候选
```

**不能把后端 SQL 错误倒推成 seq5 首次失败的唯一原因。** 本次读取的 SDK 诊断环只保存 providerCallback/silenceTimer 等事件；由本场 productSessionID 精确定位的采集诊断文件 events 为空、没有 firstCriticalFailure。两者均未保留这条业务请求首次失败的错误域、错误码和派发时序。部分 PrivacySafeDiagnostics 仅输出到控制台，不进入持久诊断环。重新插上手机可以读取现存状态，不能恢复当时没有保存的日志。

证据：[手机 Outbox 摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-outbox-summary.json)、[本场命令与数据库对齐](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-command-server-evidence.json)、[SDK 诊断摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-native-diagnostics.json)、[本场精确采集诊断](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-capture-diagnostics.json)、[原本场 HTTP 序列](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-session-http-summary.json)、[SQL 与表结构对照](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-status-query-code-schema-comparison.json)。

### 2.1 冷启动文案属于另一项待闭合问题

本场没有 completion checkpoint，follow-up 中也没有本场记录；该文件包含的是五条历史任务观察。手机显示的“尚未确认开始整理”对应恢复状态 `.actionRequired(.acknowledged)`，而本场服务器尚为 active、根本没走到 ACK。因此，不能拿这句文案证明本场已经完成服务端保存。

当前代码里，恢复服务将“只有关闭 Outbox、没有 checkpoint”的场标成 blocked；Controller 对 blocked 仅记日志。多个历史 recovery coordinator 仍可分别提交同一个状态区，并更新全局 active 指针；“核实整理状态”按钮优先调用这个 active coordinator。这是历史任务覆盖当前状态的明确结构性风险，9/16 指导已单独记录。

本轮没有捕获到实际渲染回调的 workflow ID，**不能确定当前那句文案究竟来自哪条历史任务或缓存**。该缺口不影响上面的队列/SQL 阻断结论，也不能反过来解释为什么本场候选为零。

代码：[恢复扫描与 outbox-only 分支](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:5037)、[历史协调器的共同渲染与 active 指针](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13600)、[按钮调用](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12278)、[恢复文案](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13920)；[follow-up 元数据](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-followup-summary.json)、[completion 扫描摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-completion-summary.json)。这些源码定位是当前工作树核查，不冒充对已安装二进制逐行反编译。

### 2.2 与自动短场的区别

自动 short-08 已经走过正文上传、end、ACK；随后 admission 因读取不存在的 `context.authority_epoch` 返回 500，没有本场 Source/job/候选。它与人工场的 SQL 500 是两个不同位置的错误。

自动场还出现 STREAM 静音注入后缺少 native 播放结束事件、未恢复下一轮的现象；人工麦克风场连续三轮正常，不能将该自动化音频现象推广为所有真人 Live 都失效，更不能让它替后端 admission 错误背锅。

证据：[自动短场实测报告](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)、[admission 类型错误](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/admission-typed-error.json)、[自动与人工对照](../2026-09-22-live-device-lab/comparison-sol-short/2026-09-22-Sol人工短场与自动短场对照分析.md)。

## 3. 9/10 至今的首个断点时间线

下表只保留能改变总体判断的节点，详细交付、红绿、复核撤回及阅读清单见三份分报告。

| 日期 | 已证实结果或程序缺陷 | 不能扩大成什么结论 |
|---|---|---|
| 9/10–11 | Live 正式记忆上下文多包一层 dialog；测试 Inspector 也认可同一个错误结构；修订后 B1 真机采用通过 | HTTP/token/StartSession 通过不等于模型使用正式记忆 |
| 9/11–14 | 短场/约十轮有真实本场候选；部分审核、更正、冷启恢复也真实通过 | 不是物理 20 分钟、更不是后来修改后的同版本保证 |
| 9/15 早晨 | 服务器前 16 消息，后续待送；失败与策略到期邻近；无完整关闭 | 授权/生命周期机制高度相容，但当时首次拒绝的精确层仍缺证据 |
| 9/15 晚间 | canonical 改造关闭旧 final 入口；新 ASRResponse 被固定 interim，仅 QueryConfirmed 才 complete；可定位的短场回归 | 不能归因于后来才修改的 B8，也不能说是第三方容量 |
| 9/16–17 | fresh authority 新消费者把 24 位账号代次摘要与 36 字符 lease UUID 直接比较；真实生产值构造失败，测试手造 UUID 通过 | 不是“用户没登录”，也不是模型不保存 |
| 9/17 非 ASR/封存 | 非 ASR 开场消息误登记 owner，无正文槽阻塞后续；另有停止/异步封存窗口 | 有确定红例，不代表已还原每次现场五个槽的组成 |
| 9/17 saving | 增加关闭恢复/有界等待后，页面能退出 saving，但真实输入仍未发送；后来修身份域才恢复短场 | 改状态和避免无限等待不等于完成持久化修复 |
| 9/17 短→长同包 | 短场确有新增候选并冷启保留；同一 App SHA 长场 70 本地、48 客户端确认、22 待送，401 后出现 403 | 48 是当时客户端确认水位；原生产 403 的具体 deny 原因仍未取证 |
| 9/18 长场与短场 | 正文/end/ACK/admit 成功；同场 Source→job 关联后来确认两场都首次处理 failed、0 候选，宽泛 responseContract.invalid | 原报告 pendingReview 归属错误，不能继续解释为只是轮询或候选不可见；精确 schema 检查点仍未知 |
| 9/19 短场 | 两候选→审核/更正→正式记忆→重启读取及检索，真实通过 | 不代表长场已通过 |
| 9/19 长场 | 39 用户轮/77 消息完整提交；首次 organization 8 条，support.factOmitted；第二次 schemaInvalid，最终 0 候选 | HTTP 均成功、耗时约数秒；没有该场容量硬拒绝/60 秒超时证据 |
| 9/20 分批 run01–04 | 多轮本地修复后，同一大 Source 的候选及正式记忆在隔离 PG 成功；主场注入受控 extractor | 不能与另一默认装配小场拼成真实模型大长场证据 |
| 9/20 实际发布后长场 | 确有发布和迁移；真人 33 用户轮，canonical/Outbox/服务器仅前 13 用户轮；无 close intent/end | “所有失败都因为没部署”错误；该场最早冲突/磁盘触发未保留，不能认定唯一首发原因 |
| 9/21–22 本地补强 | capture、真实双向 HTTP、隔离 PG、逐条身份、短场先行门禁逐步改进；run07 有限定 LOCAL_PASS | 逻辑 65 分钟约一分钟执行完成；没有变成物理时长/真实模型/运行版验收 |
| 9/22 两个短场 | 自动场 admission 500；人工场第五条未知、只读核实 SQL 500，三轮本机均保存 | 都不能归因于 DeepSeek 大容量；人工场初始未知原因仍未确定 |

历史详证：[9/10–17 iOS 与审核/恢复复盘](ios-history.md)、[9/18–22 验收发布复盘](validation-release.md)、[模型交互与容量复盘](provider-capacity.md)。

## 4. 为什么局部成功长期没有变成整体成功

### 4.1 真实回归与原有缺陷被新场景触发，应分开

9/15 finality 和 9/16 身份混比是有前后补丁链的回归；不能说只是“旧问题暴露”。9/17 同包短成功、长认证失败，则说明该包短路径可用，长运行触发另一边界。9/19 超过 8 条的整理失败，是旧限制与新增完整性要求不相容。9/22 则有本地修复未进入运行后端的直接指纹证据。

因此“每一次长场修复都把短场改坏”没有逐次证据，但“发生过明确回归，且原有短场成功没有得到持续保护”成立。长短共享采集、授权、关闭和候选入口，不能用只测长样本、组件测试数量增加来推导短场不受影响。

### 4.2 测试曾把真正要验证的边界提前替掉

已查到具体实例，而不是笼统指责 mock：

- final 适配有问题，但测试直接构造 complete，跳过真实 SDK 到 canonical 的转化。
- 生产账号代次是摘要，但授权测试注入 lease UUID，恰好满足错误比较。
- 逻辑长时间只推进 policy TTL，不推进真实 access token/session generation，未触发同类认证续期。
- checkpoint 单体通过，实际页面生命周期却发现 0 个恢复协调器；另一测试一直持有完整 Proposal，没走正常重入扫描。
- 模型支持器曾近乎一律答 supported；另有负例在 200 turn 输入校验就被挡住，根本没到声称验证的语义层。
- 某些大场的 admission、候选数量和正式记忆来自不同 Source，曾被拼成一个完整闭环。
- 后期真实 HTTP+PG 确有价值，但显式注入 extractor、手动连接 preorganizer 的大链，仍不等于生产默认 Worker 启动装配。

这解释了为什么几百条测试可以都绿，而真机第一场就失败。测试数量是真实计数；其证明范围曾被扩大。

短场先行也不是现在才想到：至少 9/18 修订的认证指导已写明新包短场失败不得继续长场；9/21 再收紧为每个最终版本、独立短场、实际候选及正式记忆链。早期要求、后来的增强和实际有没有执行，应分别核对。

### 4.3 当前运行版本确实缺两项已知修复

本轮只读核对 API 与候选 Worker：organization、long pipeline、candidate worker 三开关都为 true；四个关键文件在两容器中哈希一致，**不能称 API 与 Worker 混版**。但这四个文件都与当前本地新版不同。

| 已知错误 | 最早找到的本地修复记录 | 本轮运行状态 |
|---|---|---|
| admission 读取 context.authority_epoch | 9/21 capture run01 CAP-15；后续补合法/陈旧 epoch | 运行服务仍保留错误读取，自动短场 HTTP 500 |
| delivery-status 读取 s.thread_id | 9/22 round run05；run06 纠正报告曾误称“正式记忆重建 SQL” | 运行服务仍查询不存在列，人工短场 HTTP 500 |

9/20 的发布是真实发生过的；9/21–22 后续交付又明确 DEPLOY_NOT_RUN。9/21 切火山账号、重建 API 不代表自动发布后续全部业务修复。这一运行版差异需要在任何未来真机验收前被明确识别，但本次分析没有部署授权，也没有擅自发布。

另一个当前验收缺口：run07 的 25 文件 short receipt 清单仍遗漏 admission service/domain 两项实际依赖；它不能证明“任一影响保存的源码变化都会使旧收据失效”。这不证明 run07 那次测试混用版本，只证明门禁声明比依赖清单更强。

证据：[本轮运行开关及哈希](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-runtime-metadata.json)、[版本与发布详细核查](validation-release.md)。

### 4.4 阶段名和页面文字曾替代了业务完成证据

“本机有正文”“服务器有正文”“ACK/admit 已接受”“生成候选”“用户确认形成正式记忆”是五个不同事实。HTTP 200/201、pendingReview 文案、候选列表总数、模型说“记住了”，单独都不能证明本场候选与正式记忆正确。

恢复合同本身有过变化：9/11 设计允许重启后按原命令恢复 end/ACK/admit，9/14 B6 明确收紧为冷启动只读、零业务 POST。不能把后来要求倒推成早期违规；也不能为消除 unknown 随意恢复自动重发。

9/18 已发生把历史 pendingReview 误归给当前场；最新手机又出现本场未 ACK，却显示旧任务式“已保存”的现象。UI 仲裁是独立问题，既不能靠它宣告保存成功，也不能把所有后台零候选都说成 UI 没刷新。

## 5. 内外部容量与超时：已证实什么

### 5.1 旧版八条上限：内部合同有明确冲突

9/19 已授权的八次合成真实模型调用中，长输入相同，12 个独立事实：

| 参数 | 结果 | 能证明什么 |
|---|---|---|
| 最多 8 条，输出预算 4096 | 8 条，复核发现 4 项遗漏，生成 2221 tokens | 应用条数限制能造成漏事实 |
| 只改最多 24 条，输出仍 4096 | 12 条，复核无遗漏，生成 3400 tokens | 该样本无需靠扩大输出 token 才能完成 |
| 最多 24 条，输出 8192 | 12 条，无遗漏 | 不能据单次样本推出所有长输入容量已解决 |

全部 HTTP 200、finish_reason=stop；实际模型响应 alias 与请求 alias 不同，精确底层 revision 未保存。绑定的真实历史长场第二次 schemaInvalid 没有具体字段记录，所以不能编造“恰好第九条格式错误”的故事。

### 5.2 最新本地分批仍有一项可确定的容量缺口

本轮新做的是**零网络、零数据库、合成输入的真实函数边界探针**。真实关系分页函数根据候选 sourceTurnIndices 又取回原始整 turn，再交给真实 DeepSeek adapter 校验：

- 仅 1×1 关系页，但原 turn 4001 字符：HTTP 前报 `live conversation turn 0 is too long`。
- 合规 8×32 条数页，40 个原 turn 各 1000 字符：HTTP 前报 `live conversation transcript is too long`。

因此，初始拆分和按候选条数分页还不能保证后续每个请求都符合应用自己的 4000/30000 字符限制。旧链本来也会按输入长度分块；9/19 的 77 消息/4564 字符落在一个块，才体现为该场最多 8 条。新分批通过更多批次扩展此类场次，单次 8 条仍保留，但没有证明所有形状的长输入都可处理。这是当前真实代码缺陷；**没有证据说它造成了最新短场或某个历史私有场**。

证据：[探针代码](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/probe-relation-input-boundaries.py)、[执行结果，Provider 调用为 0](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/relation-input-boundaries.json)。

### 5.3 第三方的限制必须绑定实际 API 和型号

火山 Live 提供实时语音交互；DeepSeek 承担会中私有预整理及会后候选整理。前者的上下文窗口/历史轮数不等于应用能保存多少轮对话，后者允许更长输出也不会自动覆盖本应用设置的 4096。

9/19 留存的火山对应 API 官方快照列出 12K 上下文、默认 AppID 请求/吞吐配额、最近 20 轮历史和 10 分钟无交互错误；这些不能翻译成“一场只能 20 分钟”。本次官网重定向页面未成功刷新，当前账号实际配额、活跃连接硬时长及越过窗口后的策略仍未确定。

本次重新查到的 DeepSeek 官方页面存在型号 alias 更新；当前规格不可替代历史请求时的精确型号事实。JSON 模式只约束 JSON 输出，不能保证我们的字段和全部事实覆盖。`httpx.Client(timeout=60)` 作用于 connect/read/write/pool 各阶段，不是整个请求或任务绝对 60 秒完成保证。[DeepSeek JSON 官方说明](https://api-docs.deepseek.com/guides/json_mode/)、[官方更新](https://api-docs.deepseek.com/updates/)、[HTTPX 超时说明](https://www.python-httpx.org/advanced/timeouts/)。型号、配额、历史测量、内部参数和官方快照分开列在[容量分报告](provider-capacity.md)。

超时和总预算还有独立未证实项：原分批设计提出单请求整体期限、会后无进展及绝对期限；在本轮检查的 Live adapter/worker 模块中未定位等价实现。当前总请求和 token 预算相对原设计也有增大，尚未找到完整校准依据。这些是实现及证据差异，不是已证实的历史现场超时原因。

## 6. 架构、交互还是供应商：证据归类

| 类别 | 已证实问题 | 当前判断 |
|---|---|---|
| 内部采集与生命周期 | final 降级；非 ASR 假成员；单条错误与整场 capture 释放耦合的可复现链 | 存在边界设计与实现问题；第三方仍出声音时，本地记忆采集也可能已停 |
| 内部授权与传输 | 身份域混比；恢复准备过早消耗重试；当前只读 SQL 500 | 存在具体代码错误；未知写不自动重放的保护本身正确，恢复接口失效才使它无法继续 |
| 内部服务交接 | admission 字段合同；本地/运行版本不一致；开关不同的测试分支 | 与“模型内容太长”无关，也能让一轮短场失败 |
| 模型交互合同 | 8 条上限与全覆盖冲突；合法释义证据绑定；关系请求重新超限 | 内部合同和模型响应语义之间有真实缺口；HTTP 正常不代表可接受业务结果 |
| UI 状态归属 | 多历史任务共同渲染及按钮全局归属；当前场缺 checkpoint 时仅 blocked 日志 | 会误导观察和核实目标，不能当成后台生成成功 |
| 外部服务硬限/超时 | 当前材料未见能解释所有失败的共同外部错误；历史多次请求数秒 HTTP 200 | 不能排除未来/未观测场景，但不能拿假设替代已抓到的内部断点 |
| 验收与发布 | 理想夹具、同源错误、默认装配未覆盖、不同场证据拼接、已知修复未在运行版 | 是缺陷反复漏到真机的系统性原因，不能仅靠再加测试总数解决 |

没有证据要求推倒全部系统。也不能说架构无问题：采集/封存/派送/关闭的责任界限、全场语义完整性与分批合同、恢复状态与本场身份的绑定，确实暴露了结构性缺口。更准确的结论是：**多项内部实现和边界合同问题，加上验证与运行环境不一致，共同造成持续失败；不是已找到一个供应商限制就能解释全部。**

## 7. 仍未闭合的根因与需要的证据

| 未知项 | 当前缺什么 | 不能做的推断 |
|---|---|---|
| 最新 seq5 为何进入 outcomeUnknown | 首次 append 的错误域/码、派发边界、回调、网络与账号代次关联；现存持久 SDK 环没有该链 | 不能直接认定 15 秒超时、401、取消或 SQL 是首次原因 |
| 最新重启文案的精确历史归属 | UI 提交及按钮实际 workflow/page-owner ID | 不能指定某个历史任务就是唯一来源 |
| 9/15 早晨、9/17 401→403 首次拒绝 | 当时服务端具体 reason 及完整原请求链 | 不能把源码相容机制当现场唯一原因 |
| 9/18 responseContract.invalid | 原检查点/非法字段；当时未保存 | 不能补写成已经发生多次重试或必然容量超限 |
| 9/19 第二次 schemaInvalid | 精确 schema 字段与响应 revision | 不能声称扩容就能必然修好 |
| 9/20 第 13 轮后首次 capture 断点 | 最早错误未持久保存；环形日志也覆盖首段 | 本地能复现冲突链，不代表已抓到当场就是标点冲突；也未证明其他存储中后半段正文全丢 |
| 新分批在生产默认装配和真实模型下是否可靠 | 同版本、同配置、同场事实/Source/候选/正式记忆的真实证据 | 不能用逻辑时间和受控模型替代 |

本轮只读手机工作已经完成，没有重新对话、点击核实、启动业务恢复或写记忆。手机无需继续保持连接来等历史文档复盘。

若要唯一确定最新 seq5 的首发原因，需要另行明确启动一场**带请求级脱敏日志的短场复现**，在开始前记录同一安装包、运行后端、配置和数据库版本，并把第五条原命令的准备、派发、回调、核实连成一条证据。该复现目前未执行，也不保证一次即可触发；不应为了复现去重放当前结果未知的旧业务命令。先取得并讨论本次复盘结论，再确定下一步诊断及修复范围。

## 8. 阅读范围、操作边界与交付

本轮由三个独立分支并行核查历史 iOS、验收发布、模型容量；总控核对最新手机、精确服务端场次及运行代码指纹。检索目录覆盖 308 份候选 Markdown，其中 305 份全文阅读、2 份官方参考节选、1 份日期范围外背景排除；完整阅读/节选/背景与排除范围以 [阅读覆盖清单](阅读覆盖与证据边界.md) 为准，不能把命中文件名算全文阅读。各分报告保留实际阅读清单和原始证据链接。

新执行：手机已授权只读文件取证；限定本场的只读数据库/服务器元数据；公开官方文档查询；两项禁止网络的本地真实输入边界探针。

未执行：修改产品代码、部署、调用付费模型、候选审核、正式记忆写入、历史清理或重放、commit/push、新真机对话或长场测试。

交付文件：

- 本报告：事实与因果总表、当前确定原因和未决项。
- [iOS/审核/恢复历史分报告](ios-history.md)。
- [验收及发布分报告](validation-release.md)、[早期设计与验收补充复盘](validation-early-supplement.md)。
- [内外部模型与容量分报告](provider-capacity.md)。
- [本轮证据目录](document-inventory.md)及[阅读覆盖](阅读覆盖与证据边界.md)。

本报告不以“已经找到所有根因”收尾，也不把已经确认的局部错误无限搁置为猜测。下一份开发设计应以这些已证实断点和明确待补证项为输入，不能再从一个页面状态或一组测试计数推导整条链已经修复。
