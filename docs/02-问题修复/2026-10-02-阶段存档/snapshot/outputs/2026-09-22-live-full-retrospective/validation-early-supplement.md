# 验收与发布复盘：9/10–17 核心设计及正式记忆读取补充

本补充只记录已读历史设计与交付证据的含义，不提出新方案，不把设计要求写成已经实现。本轮追加全文阅读 **20 份 Markdown**，清单见文末及 `read-files-validation-early-supplement.json`；不与原 `validation-release.md` 的 80 份混记。大文件工具输出曾截断的段落已分别补读；代码链接只作为历史文档的定位，不表示本补充重新逐行审核全部当前源码。未读取原对话或密钥，未修改产品源码，未测试、部署或操作手机/生产。9/08 文档只是这些设计引用的背景，不计本次全文阅读。

## 1. 更正短场门禁时间：不是到 9/21 才第一次要求短场先通过

9/17 认证主设计的现存版本修订于 9/18，§9 第 283 行已明确规定：新包先完成短场，同场 end/ACK/admit、真实候选唯一和冷启动保持；“短场失败立即停止，不能继续用长场掩盖回归”。§7 也把短场真实 Controller 组合和新版短场真机保持性分开列出。由于文件经过 9/18 修订，不能仅由文件名断言这句在 9/17 最初版本就已存在；可证的是 **9/18 修订版已有明确短场先行门禁**。[认证主设计（9/18 修订）](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md:279>)

9/21 的新增要求更严格：每次修复后、每次长场前、同一最终源码与配置、独立短场完整候选和正式记忆链先通过，并逐步加入收据和版本指纹。这是门禁范围及执行机制的加强，不能改写成此前没有短场门禁。原 9/18–22 报告第 3 节已经补充这一时间边界。

## 2. 9/10 已经识别“自证测试”和分层验收，后续问题并非从零发现

### 2.1 B1 的真实问题是测试判据也错了，不是测试完全没跑

B1 指导明确保留 `T06 PASS / B0 PASS / B1 FAIL`：T06 真实 SDK 出站只是将合成正文发给受控接收端，没有证明字段正确或模型采用事实。历史实现把 `system_role` 包在 `dialog.dialog.system_role`，相关测试恰好接受该错误路径、拒绝正确单层结构；Inspector 也使用相同错误预期。实际锁定版本的 SDK 出站证据表明 SDK 没替应用修正层级。此时构造器、校验器和测试共用错误判据，绿测真实但意义局限。[B1 指导：历史 PASS 与错误测试判据](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md:7>)

这不是可由“SDK 无声”“模型忘记”笼统解释的事实。早一份第二组设计对双包装仍保留 SDK 合同是否允许的疑问；后来的 B1 指导借合同及出站证据进一步收窄。复盘应保留这种证据增长，不把早期疑问追写成一开始已确定。B1 指导另外区分无逐轮 RAG、旧 RAG 编码错误、角色正文大小、结构化事实被提取摘要遗漏等不同问题；未启用的 RAG 不能标通过，也不能仅因没有 RAG 断言它就是四项事实回答失败根因。[2026-09-10-Sol-Live-B1正式记忆未采用修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md)、[2026-09-10-Sol-Live第二组真机问题设计与修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-10-Sol-Live第二组真机问题设计与修复指导.md)

补读的 [B1 脱敏现场记录](../2026-09-10-dreamjourney-live-second-round-fix/evidence/live-b1-formal-memory-failure-sanitized-20260910.md) 自己注明普通控制台是终端观察、没有生成原始 tee 文件，不能称为可重新解析的完整日志。它保留四轮有声问答均未采用正式记忆的 FAIL，T06 只证明 192 字节合成角色出站；会后整理尚未见终态，不判成功或失败。退出后的 audio lifecycle 日志不能倒推为先前四轮知识回答失败的根因。

### 2.2 入口失效、503、候选失败是三条证据链

入口指导在静态路径中确认 `.replied` 状态下按钮禁用且缺少恢复入口；六次 503 发生在 SDK 启动之前，但当时没有取得原始服务端 reason。成功取到 snapshot 的前置日志不等于 ticket 已成功持久化或签发，已解析错误又在上层被丢弃。它没有足够证据把 503 定为密钥、snapshot 或候选后台任务造成。[2026-09-10-Sol-Live入口与令牌503修复指导](../../02-问题修复/Live语音与开麦/2026-09-10-Sol-Live入口与令牌503修复指导.md)

第二组设计记载 API 和 Candidate Worker 镜像 ID 不同，但核对的五个源文件哈希相同。因此当时不能仅凭镜像 ID 说候选失败源自未部署。生产 job 首次处理即失败、attempt=1/max=1、候选为零；宽泛的 retriesExhausted 标签没有保留首个错误，不能充当已重试多次或具体模型根因。另有受控 ConnectError 路径把纯问题 fallback 成一条候选，属于被真实探针捕获的假成功；到 9/14 B7 设计时 Live 路径已改为对 HTTP/ValueError 抛错，该历史 fallback 不能继续冒充当时 B7 现场的唯一原因。[2026-09-10-Sol-Live第二组真机问题设计与修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-10-Sol-Live第二组真机问题设计与修复指导.md)、[2026-09-14-Astra-B7纯问题误生成候选局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md)

### 2.3 验收标准很早就要求版本对齐和证据分层

9/10 总交接已明确：4,398 是 80 组后端执行次数相加，不是 4,398 个独立业务场景；模拟器 404 PASS 不证明真实 Provider；BGE 的 200 样本总体召回不代表 PG 混合检索及最终回答。该文还区分单条生产合成路径、三种入口全覆盖、约 10 秒短压测和 30–60 分钟负载；恢复校验 fail closed 不能写成恢复成功，导入耗时不能直接写成 RTO。[总交接：已通过证据的边界](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-10-Sol记忆系统补齐与验收交接.md:60>)

文档把不依赖设备的 A 阶段与最后真机 B 阶段分开，允许本地/服务端阶段完成时设备仍 NOT_RUN；但匹配 API/所有 Worker/客户端版本、发布后核验以及脏工作树文件指纹已有明确要求。早期手机诊断可以独立开展，却不自动等于正式验收或整体 READY。这意味着 9/22 的版本交接缺口不能解释成此前完全没有版本核对要求。[2026-09-10-Sol记忆系统补齐与验收交接](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol记忆系统补齐与验收交接.md)

## 3. 9/14 B7/B8：合法局部 PASS 不能抹平相邻功能 FAIL

### 3.1 B7：未审核事实隔离成功，与纯问题变候选失败同时成立

B7 现场先证明未审核候选没有被当成正式事实使用，随后纯问题却使候选从 43 变为 45，新增内容是“用户问过……”的知识候选。受控探针走真实 parser、extractor、CandidateProposal、ExtractionService 和内存 Worker，合法 user index 仍能承载错误候选；直接引用 assistant index 被拒，只能证明索引角色围栏，不能证明使用 user index 的内容就有用户事实证据。返回空数组的对照也只证明零候选路径，不能证明真实模型的语义过滤。[B7 现场与受控证据](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md:30>)

真实冻结窗口是首个 owner 到最后 owner，最后助手回答不在 Source 内，但文档明确没有证据说这一窗口就是纯问题变事实的根因。生成和验证的实际响应、天然 admission 形成的 Source、真实/隔离 PG 与直接构造 Source 的测试需要分别看待。9/14 给 Sol 的提示词仅授权本地实施与相应已配置 Provider 的合成验证，部署和手机仍不在授权内；因此本地交付停止并不等于擅自遗漏已授权部署。[2026-09-14-Astra-B7纯问题误生成候选局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md)、[2026-09-14-Sol-B8-B7两项独立修复执行提示词](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Sol-B8-B7两项独立修复执行提示词.md)

### 3.2 B8：客户端标签不是服务器状态，也不是数据库计数

B8 指导明确纠正 `notObserved`：这是客户端恢复协调器汇总原因，status-v3 没有同名业务枚举；readyForAdmission、pendingAcknowledgement 或错误路径都可能被概括成该值。`sourceCount=2` 是本地恢复记录来源数，不是后端 Source 条数。内存状态探针证明 acknowledged 批次可以合法返回 readyForAdmission/notAdmitted，但不能证明现场恰好就是这组元组。[B8 诊断标签的边界](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-14-Astra-B8文字会话结束notObserved局部修复设计.md:7>)

原始 checkpoint phase=acknowledged 可以收窄窗口，却还没有把源码、实际设备和第一条 admission 失败 notice 完整绑定；policy、lease、存储失败、已发送但响应未知、PG 分支当时仍需分别取证。后续 9/16 指导又将“HTTP 201 + 通用 JSON 解码”与业务 typed receipt、lease、精确 binding 验收分开：只有后者成功才允许 acknowledged/admitted 状态。首份报告的“201 且 typed 成功”表述不能当成已取得完整 receipt 验收证据。[2026-09-14-Astra-B8文字会话结束notObserved局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B8文字会话结束notObserved局部修复设计.md)、[2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导.md)、[2026-09-16-Astra-01补充-B8提交后状态读取修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01补充-B8提交后状态读取修复指导.md)

## 4. FM-POLICY：run01 有具体过度 PASS，run02 已用直接反例补齐

9/14 设计定位到正式记忆列表/详情/人物归纳在发资源 GET 前被 FeatureGate 阻断、旧 route decision 刷新后仍拒绝、loading guard 错走账号变化分支等缺口。但当次正式读取究竟是哪一个 deny reason 并未保存；后来的候选 trace 有 expiredPolicyCache 不能直接归给之前的正式读取，空 access log 也不是“现场绝无请求”的完备证明。正式审核写本身 revision 69→70、派生 ready 及文字/Live 查询已经通过，读取 FAIL 不推翻这些写入成果。[2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计.md)、[baseline](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/evidence/pre-fix/baseline.md)

最清楚的证据落差出现在两轮执行矩阵：run01 **M19 仅凭 Controller 接线、超时测试和模拟器 UIQA 标 PASS**，**M28 用间接保持性证据标 PASS**；run02 修订清单直接承认前者“仅凭接线标 PASS 的证据不足”，后者已由直接反例替换。补充红测实际暴露首个等待者取消影响其他等待者、页面返回不重读、旧详情编辑仍可进入、搜索防抖期间旧结果提交，以及 runtime 恢复越过整体期限后仍发 GET。不能说 run01 的 418 个绿测已覆盖这些场景。[run01 执行矩阵](</Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/reports/M01-M28-W0-W6执行清单.md:30>)、[run02 对 M19/M28 的证据纠正](</Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/M01-M28-W0-W6修订清单.md:32>)

run02 还保留测试本身被修正的历史：前两版期限测试先被 authority epoch 门禁挡住，未到要验证的期限分支；最终 `runtime-deadline-red-v2` 才是有效业务红证据。真实 UIQA 前两次搜索回调清空选择失败，第三次修复后通过。定向 11/11、受影响 434/434、本地后端 62/62、两目标构建是有效本地证据；PG 零写证据明确沿用 run01，未伪装成第二次重新执行。[test-build-gate-summary](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/evidence/post-fix/test-build-gate-summary.md)、[M01-M28-W0-W6修订清单](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/M01-M28-W0-W6修订清单.md)

这些补齐关闭了该范围的本地缺口，但 run02 的 READY 明确限定为准备 FM 真机复测：历史现场仍 FAIL、修复版设备 NOT_RUN，后端没有本轮业务改动。decision-result 之前已经有发布证据，其当前生产状态未重新访问；“接口已发布”与“unknown 写结果真机恢复已过”也被明确拆开。不能泛称接口仍没部署，亦不能据发布把恢复升级为 PASS。[README](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/README.md)、[非本地状态](</Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/M01-M28-W0-W6修订清单.md:55>)

## 5. 9/16：已确认的采集缺陷与未实施的页面归属设计不可混为一项

### 5.1 Live 回合未封存：final 确实到过，但完整现场磁盘仍未取得

恢复的历史执行留存表明十问每问均有一次 ASR final，且都在停止之前；没有可见 QueryConfirmed 入口。当前代码却将 ASRResponse final 适配为 canonical interim，并依赖 QueryConfirmed 唯一定稿；真实 Store probe 证明首个 interim 会阻断后续连续封存。故“十问都可能还没收到 final”已经不是与现有事实同等可信的解释，不能继续主要归因于太快停止。[现场 final 与代码适配](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-16-Astra-02-Live回合未封存-问题分析与修复指导.md:13>)

该文同时保留准确限制：当时安装二进制未与当前源码逐行绑定，没有读到该场原始 Outbox；“10 owner interim + 10 assistant complete”是与现场相符的合成机制，不是现场槽位清单。没有候选也不能自动证明 B7 过滤通过；另一条 Conversation 摘要 `transcriptTurnCount=2` 不是整场采集成功。正常识别、回复、播放与记忆采集通路有各自生命周期。[不能升级的现场推断](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-16-Astra-02-Live回合未封存-问题分析与修复指导.md:27>)

### 5.2 Echo 恢复归属：设计未实施；单任务 UIKit 测试未覆盖多任务竞争

Echo 指导明确识别：多个历史 workflow 的回调直接写同一个状态区，非终态又可覆盖全局 active 指针；文案和按钮动作可能属于不同场次，active 恢复还会接管文字入口。原测试 helper 每次安装一个恢复任务前会 detach 其他旧任务，不能用该单任务 UIKit PASS 证明多任务仲裁。[Echo 静态归属与测试盲区](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-16-Astra-03-Echo恢复状态归属-问题分析与修复指导.md:44>)

已恢复日志证明三个历史场次的事件交错，并见 actionRequired/pendingReview 两种相反次序，但 `uiCommitted` 没有 workflow、Controller 或页面代次。因此不能把相邻日志写成已证实的同一 workflow 状态倒退、后端状态回退或物理并发 GET；捕获窗口未见自动 POST 也不等于所有生命周期都零 POST。文档状态是设计未实施，并要求在 B8/Live 各自验收后独立处理；后续局部认证或采集 PASS 不能借此宣称 Echo 历史任务归属已经修好。[Echo 证据等级与未确认事项](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-16-Astra-03-Echo恢复状态归属-问题分析与修复指导.md:15>)

### 5.3 B8 提交成功后的读取也是独立链路

9/16 补充指导规定，只有 admission typed receipt 验收、admitted checkpoint 写入、该场 followup 建立都完成，才能进入后续状态读取；随后 GET 失败不能降级到 acknowledged/admissionPrepared，更不能借恢复再发 ACK/admit。其测试要求沿用真实 401 不重放、201 binding unknown、磁盘 prepare 失败、policy 超时迟到和账号隔离，前一轮 469 PASS 不自动覆盖新补充。这再次表明“提交”和“看到最终候选”之间仍有独立验收面。[提交成功到状态读取的条件](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-16-Astra-01补充-B8提交后状态读取修复指导.md:54>)

## 6. 9/17 认证：场景模型缺的是认证轮换，不是逻辑时长本身

该文把 70 段本地完整正文、48 段客户端已确认、第 49 段遇 401、另一 trace 401→403、队尾 22 段和未完成关闭列为事实；“48 段”不是独立数据库清点。缺 captured policy 被真实后端 gate 拒绝是本地可重复机制，但当次 403 的 reason 没有取得，具体 token 到期也可能是其他认证失效；第 49 段数据库中一定不存在同样没有证据。不能用时长、状态码或响应字节数替代合同化“未应用”证明。[现场事实与推断分层](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md:58>)

设计点名旧 `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark` 使用固定 authSession 和固定 generation，只推进 policy TTL。因此“逻辑 20 分钟 PASS”未覆盖 access token 轮换是具体装配缺口，不是时间模拟这个方法本身无价值。至少一项组合必须实际走生产 refreshAuthSession/CAS/single-flight，而仅在闭包替换 auth session 的测试仍不能覆盖它。[原逻辑测试具体未覆盖什么](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md:230>)

文档允许纯本地阶段在设备 NOT_RUN 时完成，明确手机由用户另行主动发起，旧 70/48 场次另行保护；同时本地组合未建立就不能凭 GET 200 宣告 READY。本地、服务端合同、真实 Provider、真机、新场 PASS、旧场恢复原本就被要求分开。流程问题在多次实现与验收是否实际遵守这些限定，不能归结为“设计没有想到认证/短场/版本”或“没擅自操作手机就是没完成”。[本地完成与设备状态](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md:291>)

## 7. 本补充的阅读清单

以下 20 份均已全文阅读；搜索或节选只用于再次定位行号，不代替全文计数。

- [2026-09-10-Sol-Live-B1正式记忆未采用修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md)
- [2026-09-10-Sol-Live入口与令牌503修复指导](../../02-问题修复/Live语音与开麦/2026-09-10-Sol-Live入口与令牌503修复指导.md)
- [2026-09-10-Sol-Live第二组真机问题设计与修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-10-Sol-Live第二组真机问题设计与修复指导.md)
- [2026-09-10-Sol记忆系统补齐与验收交接](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol记忆系统补齐与验收交接.md)
- [2026-09-14-Astra-B7纯问题误生成候选局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md)
- [2026-09-14-Astra-B8文字会话结束notObserved局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B8文字会话结束notObserved局部修复设计.md)
- [2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计.md)
- [2026-09-14-Sol-B8-B7两项独立修复执行提示词](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Sol-B8-B7两项独立修复执行提示词.md)
- [2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导.md)
- [2026-09-16-Astra-01补充-B8提交后状态读取修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01补充-B8提交后状态读取修复指导.md)
- [2026-09-16-Astra-02-Live回合未封存-问题分析与修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-16-Astra-02-Live回合未封存-问题分析与修复指导.md)
- [2026-09-16-Astra-03-Echo恢复状态归属-问题分析与修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-16-Astra-03-Echo恢复状态归属-问题分析与修复指导.md)
- [2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计](../../02-问题修复/服务端与认证/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md)
- [redaction-scan](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/evidence/post-fix/redaction-scan.md)
- [baseline](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/evidence/pre-fix/baseline.md)
- [M01-M28-W0-W6执行清单](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/reports/M01-M28-W0-W6执行清单.md)
- [README](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/README.md)
- [test-build-gate-summary](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/evidence/post-fix/test-build-gate-summary.md)
- [M01-M28-W0-W6修订清单](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/M01-M28-W0-W6修订清单.md)
- [live-b1-formal-memory-failure-sanitized-20260910](../2026-09-10-dreamjourney-live-second-round-fix/evidence/live-b1-formal-memory-failure-sanitized-20260910.md)
