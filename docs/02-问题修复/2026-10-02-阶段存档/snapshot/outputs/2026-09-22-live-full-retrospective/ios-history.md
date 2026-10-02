# 9/10–9/17 iOS / Live 采集、关闭与持久化历史复盘

复核日期：2026-09-22。只读审计历史文档、保存的源码/补丁、Git 对象、现有源码和已有结果包。未改业务代码，未运行 App 或业务测试，未操作手机、生产或 Provider；不提出新的修复方案。9/18 以后结果由总报告的后续章节承担，本文不据 9/17 结论判断今天的修复状态。

## 结论

**短场以前确实成功过，后来也确实被修复改动打断过；“以前从来没保存成功”不符合证据。** 9/11 两轮新事实及更正得到一条本场候选，9/12 约十轮有十个用户轮持久化并生成两条本场候选。9/15 的 canonical 主入口替换引入了可定位的 finality 回归；9/16 的长场授权修改又引入独立的身份域混比。后者不需要聊满二十分钟，一问一答也会在首次写入前失败。

**在本文截至 9/17 的范围内，没有找到“物理二十分钟、完整上传、完整关闭、首中尾材料及本场候选关联”全部真机通过的证据。** 已有约十轮成功不能换算成跨认证有效期的二十分钟成功；9/17 短场成功后，同一安装包的二十分钟测试明确失败。更准确的历史结论是“长场完整真机验收尚无通过证据”，而不是对未留存的所有使用经历断言“从未成功”。

**本地测试并非全是假通过，缺的是关键生产输入与组合路径。** 本轮直接读取现有结果包，确认 501/501、672/672 的确通过；但曾跳过真实 final 适配、真实开场 TTS、生产身份生成器、认证轮换，也出现逻辑时钟只驱动部分授权组件的情况。测试总数证明所选断言通过，无法替代这些缺失环节。

## 按首断点排列的历史链

| 时间 / 事件 | 首个已证实断点与不确定部分 | 当时实际修复 | 当时验证范围 | 后续同版本或新版真机结果 |
|---|---|---|---|---|
| 9/10 入口与 token 503 | 文字结束停在 replied；token 的实际业务码为 formalMemorySnapshotUnavailable，派生投影 rebuilding/缺 checkpoint。不是语音联网失败。 | 结束 interaction 回 idle；重建派生 Projection；增加安全错误分类。 | iOS 14 项、两次十轮文字 UIQA、后端相关合同与部署；报告明确真机待确认。 | 随后真实 Live 恢复有声、打断和结束；新暴露的是正式事实采用、候选 Worker 失败，不能把 token 200 当整个 Live 通过。见 E01。 |
| 9/10–11 B1 正式记忆未采用 | StartSession JSON 多套了一层 dialog；原 Inspector/测试也把错误字段当正例。真实 SDK 发出错误格式并不证明模型使用了记忆。 | 单层 asr/dialog/tts，严格字段及 hash 校验，正确 sessionSnapshot；修订实际 SDK 信封 Inspector。 | 289 项、后端测试、模拟器；T06 先失败后新字段真机通过。 | 9/11 B0/B1 真机通过；B2 约十轮、11 个用户轮已持久化；B3 两轮更正只出一条候选。B1 报告首段仍写 B2–B8 NOT_RUN，但后文已追加 B2/B3 PASS，须读全篇最终状态。见 E02。 |
| 9/12–14 保存成功但读取/恢复失败 | 9/12 同场十个用户轮已存、end/ack/admit 完成、候选新增；相邻缺陷是第二次审核假成功、首次候选 GET 发前过期拒绝。9/14 B6 后台已出候选，冷启 UI 却 unavailable。 | 候选/审核状态与读取恢复、B6 只读冷启恢复分轮实施。 | 局部 UIKit/FeatureGate/磁盘回归；不能把其通过称为 SDK 长场通过。 | 9/14 B6 新场保存及冷启只读恢复通过。B0–B6 总表明确保留部分 9/11 历史证据，并非所有项目都在 9/14 新包重跑。见 E03–E05。 |
| 9/15 早晨长场，发生于 canonical 改造前 | 服务器 seq1–16 连续、最后入库 08:10:42，捕获策略 08:10:47 到期；seq17 本地待送未入库；Live 继续到约 08:22；无本场 close 水位/end/Source/job。策略过期→capture 释放是源码与现场一致的高可信链，但缺首次请求级拒绝事件，不能确定首次拒绝具体层。 | 后续设计/实现将本机采集与派送分开，增加 canonical staging、Outbox V2、close intent、精确只读 delivery 状态等。 | 调查原有 7+7 项是现状证据；实施后 620 项通过，但真实 Manager→durable A16、完整逻辑/物理长场等明确未完成，整体 A_LOCAL_INCOMPLETE。 | 晚间新包真机反而出现零封存，属于下一行新回归；早晨故障不能归给尚未发生的改造。见 E06–E08。 |
| 9/15 晚间新回归：真实 final 不能封存 | 原生 provider 旧 final onASRResult 入口被关闭；新 ASRResponse 无条件标 interim，只有 QueryConfirmed 才 complete；现场十问均有 final、无可见 Confirmed；首个 interim 挡住完整后缀，owner/persisted/queued=0，unsealed=20。 | 9/16 改严格 final 解析，显式 final→complete；补身份冻结和停止前 ingress。 | 修前明确 final→interim 红，修后 2 项绿；但 9/16 十问整装、真实 evaluator/BackendClient 同时钟二十分钟未齐，仍 A_LOCAL_INCOMPLETE。 | 9/17 多轮补齐后，两问真机依旧未封存；该次至少已有 final 到达，另发现非 ASR 开场假成员。不能把后续全部失败仍归为同一 finality bug。见 E09–E13。 |
| 9/16 B8 / B8-S01 相邻共享链 | 文字 end/ack 后，admission 下层重取旧 route；admit 后即时 status 也使用旧 route；均在已封存/end 之后，不能解释 owner=0。 | fresh admission authority、201 错配归 unknown；本场私有有界只读 reader、poll 归属和持久终态确认。 | 469 项及后续 478 项；修复版文字真机当时 NOT_RUN。独立增量审计确认 14 个采集/封存函数不变。 | 9/17 成功短场经过共享 ACK/admit/status，能证明该新包该场共享链成功；不能追认 9/16 单独版本的文字专项已验收。见 E10、E14。 |
| 9/16–17 身份/close 补充反复复核 | 曾残留旧 ack Bool、unknown start 被 current 命中旁路、同对象原 start 丢失、旧 Q 回滚、freeze→deliver 换场、非空场 closeIntent 先于冻结成员登记；逻辑时钟未进入真实 evaluator。 | R1–R4 及最终两项逐一修补；reservation/handoff barrier；时钟窄注入。 | 10/492、9/500 的原 READY 曾被复核撤回；最终 10 项+501 项通过，设备仍待验。 | 真机包 3ec918… 两问后 unsealed=5、无本场候选，冷启又显示旧 pendingReview；局部红绿不等于实际入口所有事件都覆盖。见 E11–E13。 |
| 9/17 非 ASR 假 owner | 新 router 对 .other 也读 question_id 并登记 owner，parse(.other)=nil；独立开场 TTS 可在首位形成无正文槽，阻塞后续完整对答。源码反例已证实；现场有开场独立 ID、两问 final/两答终态，但没有逐槽 outbox，不能断言五槽组成或唯一现场根因。 | SDK event classifier 只允许 ASR 三类登记 owner；coverage 摘要分离本地正文/缺口/服务端确认；之后补摘要单独通知 UI。 | 2 项业务红，510 项最终回归；UI 精确变文案另有本地红绿。 | 真机普通完整回答后至少 45 秒 saving；另一 partial 场的文案正确，但精确无正文→partial 过渡没观察到。尚不是完整保存 PASS。见 E15。 |
| 9/17 saving 修复 | 已存在的失败 NaturalInputUseCase 非空，ensure 不重建，advance 又要求 ready，关闭无持续工作的执行者。是可复现机制，但未查到真实用例为何失败。 | 仅特定未发送 GET 失败可恢复一次；关闭无进展期限；重复 ended 不倒退。 | 真 Controller/Store/Client/FeatureGate 受控链，672/672；本地注入的身份仍掩盖下一行问题。 | 包 acd0a6…：约十五秒退出 saving，正文完整但 pending=2、服务端 owner 确认=0、无本场候选；核心关闭 DEVICE_FAIL。修好了无限等的表现，没有恢复发请求。见 E16。 |
| 9/16 引入、9/17 发现的授权回归 | NaturalInputRequestAuthority 强制 FeatureDecision.accountGeneration 等于 AccountLease UUID；生产前者是 24 位 SHA 摘要，后者 36 字符 UUID。新鲜允许策略仍构造失败，start POST 前停住；测试恰好注入 UUID 通过。 | 9/17 分开验证两种身份域，提取共享生产 generation 函数，保留 lease 与实际 requestJSON 重验。 | 原始补丁定位 9/16 11:35:24；源码反例生产值写边界0、注入UUID为1；真实组合红绿、509项+56项；隔离PG完整链当时 BLOCKED。 | 包 9d29fa…：短场 owner已确认1、queue0、ACK/admit/status、唯一本场新候选及冷启保持实际通过；旧 UI 仲裁独立失败。见 E17–E18。 |
| 9/17 同包物理二十分钟 | 70段本地完整，客户端确认48；第49段写401，另一trace先401后403；后22段未同步，关闭不排空。首次失败约13分15秒，不是20分钟或48条硬上限。 | 当天仅调查/设计 auth readiness、GET恢复授权、可证明认证拒绝恢复；后续实施见总报告9/18以后部分。 | 旧逻辑二十分钟只改变 policy TTL，固定 authSession，没有 access token / session generation 轮换。GET映射缺失及刷新后丢decision已离线证实；403的当次服务端 reason未取得。 | 同一9d29fa…包、无重装，长场明确 DEVICE_FAIL；候选三标记关联、冷启没继续。48是客户端已验收回执水位，不是本轮直接数据库盘点。见 E19。 |

## 两条可明确归因的回归

### 1. 9/15 canonical 切换

本轮重新计算 Git `11d0d005` 的 Manager SHA 为 `7bb0fd32bab45a46db23882e8c56a1675320ffafc62e80d967a6e1dcc7c90520`，与 9/15 开工指纹一致；保存的 9/16 B8 开工前 Manager 全文件 SHA 为 `f3ed12555b3222fa27b81edd6efed98c53d9723b1af56893bdb93a5064cfce5b`，与 9/15 交付一致。

- Git `11d0d005:EchoViewController.swift` 12031 先 `guard isFinal`，12056 无条件 `captureLiveOwnerTurn`，12057 之后才 provider return。
- [保存的改后 Manager](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/before/DialogEngineManager.swift:4557) 明确在解析出 result 后固定 `.interim`；[4648](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/before/DialogEngineManager.swift:4648) 才由 QueryConfirmed 输出 `.complete`。
- [保存的入口差异](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/live/probe/capture-entry.diff) 和 [同输入源码对照结果](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/live/probe/comparison-results.json) 分别证明旧入口停用与 final降级。9/15交付报告自身也明确记录旧原生入口关闭。

这是 9/15 未提交工作树改造的责任链，不能编造一个引入 commit，也不能归给 9/16 B8。该变化的源码归属已证实；每个历史安装二进制到每行源码的可复现构建绑定未重做。正式设计要求撤旧双写入口、只接纳经过验证的 final，但**没有要求 Confirmed-only**；设计把关键 SDK final 合同留待验证，实施自行收窄，A16未闭合就切默认入口，三个环节共同留下缺口。

### 2. 9/16 授权身份混比

[原始 apply_patch](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-guard-introduction.patch:11) 与 [调用时间](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-guard-introduction.txt:1) 精确定位新增比较；成功回执保存在同目录 `authority-guard-patch-result.json`。生产生成器原本就是摘要，出错的是新消费者，不是后来格式突然改变。

[当时源码 manifest](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/source-manifest.json) 定位到 Contracts 10344–10375、BackendClient 972–982、AccountLease 22–29；[源码反例结果](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-probe.stdout.json) 证明 allowed=true 时生产值仍 unavailable/qaOnlyDisabled，UUID fixture却能前进。本轮核对现有源码，当前 [authority](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:10433) 已无跨域比较，[生产generation](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:990) 仍是24位摘要，[lease](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/App/AccountLease.swift:22) 仍是UUID。此为修复后现状核对，不将现在代码冒充9/17失败源码。

现场缺少首次拒绝的完整原始日志，所以不能声称手机已直接抓到 qaOnlyDisabled；但源码阻断是确定的、与现场一致，删除错误比较后新短场又确实闭环。非ASR假成员、关闭竞态等是另外有确定反例的代码缺陷，未取得原始引入补丁时不扩张成精确个人/时刻归责。

## 过去的“通过”为什么没有覆盖实际问题

| 通过的证据 | 漏掉了什么 | 对结论的正确限制 |
|---|---|---|
| 9/10 Inspector和builder一致，旧T06真实出站 | 实现和Inspector共享错误双层字段期待 | 发出了不等于合同正确，更不等于模型采用正式事实；9/11另做正确字段与真实问答。 |
| 9/11、12真实保存，9/14 B0–B6汇总 | 汇总部分沿用旧日期证据；9/15后来切换了采集默认入口 | 历史PASS有效，但不保护之后更换入口的新包。 |
| 9/15 620项、gap/ACK测试、provider-free UIQA | 下游直接构造 complete；真实ASR final到canonical未验证；A16 NOT_RUN | 证明complete已到达后的存储保护，不证明complete能从真实SDK产生。 |
| 9/17 最初十问/逻辑二十分钟 | true gate / 手工decision；assistant DEBUG helper直接complete；无真实transport、stop/N或断网组合 | 复核撤回READY，后续补齐确有进展；不能保留被撤回的最高状态。 |
| R1–R4真实组合最初500项 | freeze-to-deliver测试从空场开始绕开非空closeIntent；fakeNow未驱动真实evaluator | 随后补成501项；这是测试前置避开故障路径的具体例子。 |
| 501项final/stop装配 | 从首ASR开始，没有真实开场非ASR TTS前缀 | 新router错误登记owner，首gap挡住全部后缀；后来新增D1红绿。 |
| 672项saving完整装配 | FeatureGate测试账号代次被注成lease UUID | 即使用真实Controller、磁盘、evaluator、BackendClient，也跳过了关键生产身份来源。 |
| 生产generation修正后的逻辑二十分钟 | 固定auth session，仅policyTTL变化；没有access-token认证续期 | 同包短场PASS与物理长场FAIL并不矛盾。 |
| 冷启“上次已进入待确认”、候选总数不变 | 没有绑定当前场batch/source；多个历史结果竞争页面 | 旧成功可以覆盖当前失败；候选不增加也可能是根本没送到Worker，不能作B7 noChange通过。 |

这些缺口不意味着应把全部已通过测试作废；它们说明原测试通过范围没有覆盖新改动真正改变的边界。特别是 9/15 长答约一分钟自然播放和真人打断通过，只属于音频交互；同包记忆采集失败也被报告明确保留。

## 本轮独立复核及证据限制

- Git实际历史在9/10–17范围只有 `11d0d005`（9/13 22:42）一条提交，前一个是9/9 `5fd061fd`；后续多轮实现留在dirty工作树，不能把同HEAD视为同安装包。
- 直接重算9/11 B2/B3、9/12筛选日志SHA，与历史白名单清单一致；安全解析确认最大owner/confirmed分别11/11、2/2、10/10。两份9/11日志可见QueryConfirmed均0；筛选过的9/12日志缺事件不能推断SDK未发送。
- 直接读取既有501和672结果包：均0 fail/0 skip。620包新式summary读取遇TestReport写缓存权限错误，改用只读legacy对象读取确认testsCount=620；没有运行新测试，620完整成功状态仍结合原报告/日志，不把读取错误算业务失败。
- 直接复读短场脱敏stage-sequence、长场failure-window；同包SHA完整相等。短场完整console未原样留存，只保留启动入口与白名单阶段摘要，所以证据比逐请求生产数据库核对弱；不抹去真实候选读取和用户核对事实。
- 9/15早晨的服务器16段是当时已授权查询留存结果；本轮没有重新查生产。9/17的48段是客户端确认计数，两者证据来源不同。
- `.green`文件夹/文件名不保证绿：历史有 green路径下失败包、原READY被后续复核撤回、清单编号错位后校正。本文采用实际结果/后续校正，不按标题取状态。
- 9/17长场403的服务端reason、原第49段是否入库、失败22段后来是否恢复，本文均不作超出记录的断言。

## 追加复核：B4 正式写入、恢复与早期 PASS 校正

这部分依据总目录补读主交付、独立复核和后续校正；补读没有改变前文两条 Live 回归的因果归属。B4 的失败必须与“Live 原文没有入库”分开：有些场次已经生成真实候选，后续坏在审核、只读恢复或提示。

| 历史节点 | 已证实首断点 / 实际改动 | 原通过为何未覆盖 | 后续结果与边界 |
|---|---|---|---|
| 9/10 A 阶段补齐 | 三组件发布后仍有旧 Worker 因 migrationHeadAhead 重启；连续 claim 后崩溃不经过 release_retryable，可突破最大尝试数。后来统一 API 与六个启用 Worker 镜像，并在 claim 回收过期租约时执行耗尽检查。 | 《Astra复核补齐证据索引》第8节明确撤回“自建 JSON 算 T06 PASS”“三组件部署算完整发布”“一次正常 retry 算连续崩溃上限”三种旧结论。 | 后台补齐有隔离 PG 和运行窗口证据；T06 当时仍 BLOCKED，晚间真实 SDK 证据又把错误双层路径当 PASS，直到 B1 修正。不能把不同层次的 PASS 合并。见 E20。 |
| 9/11 B4 审核展示 | 六处转义插值、V5 facets 未适配、复杂结构摘要代替完整变化、精度格式化后判等，使展示遗漏实际提交差异；增加不可变 Binding 和 typed diff。 | 首轮虽有295项，真实 builder temporalChange 被拒绝、正文不变的时间变化不可见；补至308项后仍有0.721→0.724格式化碰撞，真实 producer反例再补至316项。 | 后续9/11有用户真实确认、正式版本、投影和问答回查成功；这些是明确的一条正式写成功，不能证明所有更正合同。见 E21。 |
| 9/11 saving 与候选不可读 | 同场后端已经生成3条候选；重复 ended 使 queued→saving，随后 didBeginOrganization 阻止重启、旧观察回调又因状态被丢弃。候选页没有新 GET，但当时精确发前原因未取得。 | 9/12首轮332项虽证明 checkpoint 局部恢复，四项遗漏红测显示实际 lifecycle 六个阶段发现的协调器均为0；另外 DNS失败曾伪记responseReceived，task创建曾伪记已开始发送。 | 之后补接实际恢复入口、读取所有权和真实 Alamofire taskResumed，分别334/340项；9/12真机十轮新场成功生成2条本场候选。不能把9/11原候选读取失败直接改写为已抓到 expiredPolicyCache。见 E22。 |
| 9/12连续审核第二条假成功 | 第一条真实POST201/accepted/MemoryVersion成功；第二条仅本地gate后失败，无POST、无receipt、仍pending，但用户看到成功。9/13红测证实第一条后未刷新proposal，第二条仍用旧revision；修为持久成功后刷新、第二条重新预览、详情仅committed后关闭。 | 一次审核成功和列表可见不覆盖第二次独立意图；初始恢复测试直接替换policy快照，未经过真正policy HTTP/cache。后续 S1–S6 又补unknown互斥、传输曝光、UI intent收尾、版本预算、N+1回执、真实policy链。 | 9/13各轮仍把“连续第二条真实写入”列FAIL，不能用353/372项改写。9/14更正单次成功不是连续两条实测，本文没找到该特定连续操作当日新闭环。见 E23。 |
| 9/13关联组未知写及重新进入 | 首轮关联组发送后失败可再POST，缺持久组级核实；R1–R3增加零重发、组级GET与互斥。之后正常候选入口根本不扫描groupRecords，旧恢复还要求旧Proposal存活；再加磁盘发现与新页面恢复。 | R1–R3报告明确指出旧测试将第二次POST当正确行为；旧恢复测试只证明旧页面仍在。关联组run01通过后，又发现found删除pending后候选GET失败，再点核实只改refreshing、不发GET。 | run02补明确恢复分流，5项组合和397项通过；当时两个decision-result尚未部署，状态校为READY_FOR_DEPLOYMENT。9/14API确已发布，但成功写未自然触发unknown查询，不能宣称该现场恢复被真机通过。见 E24。 |
| 9/13更正HTTP200后失败 | 后端真实更正proposal hash为H1，iOS两处把它与原候选H0比较；合法正文更正必被挡在预览或最终Binding。独立审计用真实builder与Swift类型证明，生产相关domain文件同指纹，不能归给笼统镜像不同。 | 旧testV5CorrectionPreview用Spy直接把原inbox proposal当更正响应，H0/H1被人为设成相等。改用真实producer fixture后还发现合法createdAt:null被客户端拒绝。 | 双重Binding、冻结更正输入和null解码修正后，9/14匹配API/安装包由用户亲自更正，候选40→39、revision69→70、当前MemoryVersion、投影/向量、文字与新Live均有真实成功证据。见 E25。 |
| 9/14 FM只读恢复和B6冷启 | 正式版本已经写入，首次正式记忆GET却被FeatureGate发送前拒绝；精确deny子原因未留存，随后候选读取的expiredPolicyCache不能移接成正式读取原trace。正式记忆三入口增加统一只读恢复；B6改为只读coordinator、持久发现和有界核实。 | FM run02明确撤回run01若干依据接线/间接保持性标PASS的项，补首等待者取消、runtime期限、真Controller生命周期、防抖窗旧结果、旧编辑权。B6 run01跨进程用专用模拟reader，run02才走真实BackendClient/evaluator/URLProtocol。 | 9/14后续FM/B6真机总报告分别补通过；这不补9/17新采集/身份修改，也不使历史所有未知写恢复自动成为设备PASS。见 E26。 |

额外限制：9/15独立L1设计已把真实同账号token rotation列为NOT_RUN，且明确“现状7项保持测试不是新方案绿测”；其中已记录B7整场支持合同的长输入风险。9/17逻辑二十分钟后来虽逐步补齐，不能据此把这两类风险算成当时已有设备证据。9/17 saving过程记录还明确，完整正文2条、服务器确认0之后，新增 blocked workflow 未进入read-only plan，旧workflow却提交pendingReview；不能从该页面文字反推本场候选已成功。见 E27。

上述B4补读结论来自历史报告/保存的独立源码反例，未重跑其数百项测试或重新查生产。仅对前文明确列出的Live SHA与501/672结果包做了本轮独立再核验。

## 最后补读带来的历史校正

**冷启动写恢复在9/14发生明确合同变化。** 9/11会后保存设计第4.5节要求end/ack/admit丢响应或重启时重试原命令；9/14 B6设计第10.1节明确要求记录“新需求改为冷启动只读”，原`testLiveCompletionCheckpointReplaysOriginalEndAckAndAdmissionCommandsAfterCrash`的期望也因此改变。故“冷启仍有写路径”是新合同下必须调整的既有行为，不能说9/11实现从一开始违背同一零写要求。另一个独立缺陷仍成立：把临时访问失败当作任务不可恢复终态、缺少合法ready后重新查询，会导致后台已完成但页面失败；现场首次拒绝原因仍未确定。见 E28。

**身份域与真实门禁的问题并非到9/17才被描述。** 9/11同一设计第5.2节已经分开业务租约generation/generationId与认证sessionId派生的FeatureGate代次；第10.1节还指出`qaFeatureDecisionProvider`会绕过真实二次校验。9/16新增authority的错误域比较，及其UUID替身测试漏检，应归为新消费者没有遵守既有域语义和覆盖要求；不能把文档提出过约束当作后来实现已验证的证据。见 E28。

**FM现场reason与覆盖范围需要收窄。** 9/14 FM独立设计明确更正输入报告：正式列表只留存通用FeatureGate失败，`expiredPolicyCache`来自随后候选trace；留存服务端脱敏日志是0字节，不提供额外服务端证据。可确定缺少初始只读恢复，但不应追认原正式trace的精确deny。该修复合同只覆盖正式列表/详情/人物归纳三个入口；版本历史、审核历史及source-records仍列相邻缺口。后来的三个入口PASS不能扩展成所有记忆档案读取已通过。见 E29。

## 阅读完整度与总目录对照

机器可读的[完整阅读绝对路径数组](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/read-files-ios-history.json)仅列已完整读取的Markdown；[阅读覆盖清单](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/read-coverage-ios-history.json)分别列节选和仅目录比对。以路径首个日期落在9/10–17为口径，总目录为180份；当前本支完整阅读136份、节选1份、剩余43份仅目录比对。最后39份B4/B5/B6/FM设计、执行清单、README及支持记录已全部补读；这些类别在本支覆盖清单中无剩余全文缺口。其余设计/清单、草稿及音频/蓝牙材料由其他审计分支补读，总范围结论以合并清单为准，不能把本支“仅目录比对”写成全文已读。

E19认证设计只读历史事实/证据前四节与局限，未把后续实施方案当全文阅读；9/10第二组执行清单本次补读全文，现已列入完整数组。源文件、日志、JSON和xcresult核查另见前文，不混入Markdown全文计数。

## 阅读文件与证据索引

下列主文、修复报告和复核为本节实际复读依据。派生构建文件、Pods、DerivedData、完整私人转写不纳入阅读或输出；代码/结果包按上节范围核查。报告内的方案仅作为历史事实阅读，本文不复述成新的执行建议。

- E01：[9/10入口与503交付](../2026-09-10-dreamjourney-live-entry-token-fix/2026-09-10-DreamJourney-Live入口与令牌503修复交付报告.md)，[第二组真机缺陷](../2026-09-10-dreamjourney-live-entry-token-fix/2026-09-10-DreamJourney-Live第二组真机测试缺陷日志与Astra分析输入.md)，[A阶段报告](../2026-09-10-dreamjourney-live-second-round-fix/2026-09-10-Sol-Live第二组修复A阶段交付报告.md)。
- E02：[9/11 B1完整交付及后补B2/B3](/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/2026-09-11-DreamJourney-Live-B1修复交付报告.md:106)，[9/16旧成功证据复核](../2026-09-16-live-persistence-regression-audit/history/旧路径成功证据与09-15重构责任核验.md)。
- E03：[9/12真机复测](/Users/gaominge/Documents/liftora/outputs/2026-09-12-dreamjourney-b4-device-retest/2026-09-12-DreamJourney-B4真机复测报告.md:24)。
- E04：[9/14 B5/B6真机报告](../2026-09-14-dreamjourney-b5-b6-device-test/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B5-B6真机测试报告.md)。
- E05：[9/14 FM/B6及B0–B8总报告](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md:40)。
- E06：[9/15长场调查完整记录](../2026-09-15-live-long-session-investigation/2026-09-15-Live长对话与朗读中断-调查记录.md)。
- E07：[9/15持续采集设计](../../02-问题修复/记忆系统/采集与会后保存/2026-09-15-Astra-Live整场记忆持续采集与结束交接修复设计.md)，[长答设计](../../02-问题修复/Live语音与开麦/2026-09-15-Astra-Live长回答朗读中断修复设计.md)，[持续采集交付](../2026-09-15-dreamjourney-live-continuous-capture-playback-fix/run-2026-09-15-01/reports/2026-09-15-DreamJourney-Live整场持续采集与关闭交接-本地交付报告.md)，[长答交付](../2026-09-15-dreamjourney-live-continuous-capture-playback-fix/run-2026-09-15-01/reports/2026-09-15-DreamJourney-Live长回答朗读中断-本地交付报告.md)，[L1清单](../2026-09-15-dreamjourney-live-continuous-capture-playback-fix/run-2026-09-15-01/reports/L1-R01-R24-checklist.md)，[A清单](../2026-09-15-dreamjourney-live-continuous-capture-playback-fix/run-2026-09-15-01/reports/A01-A19-checklist.md)。
- E08：[9/15真机阶段汇总](../2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-B7-B8-Live真机复测阶段汇总.md)，[整场执行记录](../2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-Live整场持续采集与结束交接-真机执行记录.md)，[手机长答记录](../2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-Live长回答手机端真机验收记录.md)，及同目录issues内“整场未封存”“纯问题B7证据阻断”“Echo状态不一致”三份问题单。
- E09：[9/16回归引入点](../2026-09-16-live-persistence-regression-audit/live/回归引入点核查.md)，[9/16修订设计](../../02-问题修复/记忆系统/采集与会后保存/2026-09-16-Astra-02修订-Live采集回归与持续保存修复指导.md)，[9/16本地交付](../2026-09-16-dreamjourney-live-capture-continuous-save-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-Live采集回归与持续保存-本地修复报告.md)。
- E10：[B8增量独立审计](../2026-09-16-live-persistence-regression-audit/b8/最新B8修复对Live持久化路径的影响.md)。
- E11：[9/17补充交付](../2026-09-16-dreamjourney-live-capture-continuous-save-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live采集回归与持续保存-补充本地交付报告.md)，[撤回READY复核](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-Live补充交付复核-真机前置未通过.md)，[R1–R4交付](../2026-09-17-dreamjourney-live-preflight-r1-r4-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-R1-R4-真机前置补充修复报告.md)，[剩余两项复核](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-R1-R4复核-剩余两项.md)。
- E12：[最终两项交付](../2026-09-17-dreamjourney-live-preflight-final-two-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-R1-R4-最终两项本地修复报告.md)，同目录最终验收清单。
- E13：[9/17两问真机复测](../2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-真机复测记录.md)，同目录DJ-LIVE-DEVICE-01、02两份问题单。
- E14：[9/16 B8交付](../2026-09-16-dreamjourney-b8-ack-admission-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-DJ-B8-DEVICE-01-本地修复报告.md)，[B8-S01交付](../2026-09-16-dreamjourney-b8-submitted-status-read-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-B8-S01-本地修复报告.md)。
- E15：[非ASR设计/复核](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-02再修订-Live非ASR误登记与真机未封存修复指导.md)，[非ASR交付](../2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live非ASR误登记与真机未封存-本地修复报告.md)，[UI摘要交付及真机追加](../2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/reports/2026-09-17-DreamJourney-Live覆盖摘要UI刷新-补充修复报告.md)，两run D1清单，[最小真机观察](../2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/evidence/device/2026-09-17-minimal-ui-refresh-observation.md)。
- E16：[saving本地交付](../2026-09-17-dreamjourney-live-saving-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-DJ-LIVE-SAVING-01-本地修复报告.md)，[saving真机失败](../2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机复测报告.md)，[脱敏顺序](../2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/live-session-sanitized-observation.md)。
- E17：[持久化根因总设计/历史核查](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-Live持久化回归溯源与真实保存链修复设计.md)，[原补丁与probe目录](../2026-09-17-live-persistence-root-cause-audit/README.md)，[授权修复交付](../2026-09-17-dreamjourney-live-persistence-authority-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live持久化授权身份域局部修复报告.md)，同目录P01–P13清单。
- E18：[授权修复短场真机通过](../2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md)，[同场阶段摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/evidence/sanitized-stage-sequence.log)。
- E19：[同包20分钟真机失败](../2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-20分钟真机复测报告.md)，[失败窗口](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/failure-window.log)，[认证调查前四节及验证局限](../../02-问题修复/服务端与认证/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md)，[生产证据限制](../2026-09-17-live-20min-auth-sync-analysis/run-2026-09-17-01/evidence/limitations.md)。该设计已在9/18修订，本文仅使用其标明的9/17事实与代码证据，后续开发方案不作为9/17已完成事实。

- E20：[9/10 P1/P2补齐交付](../2026-09-10-dreamjourney-live-second-round-fix/2026-09-10-Astra-P1-P2补齐交付报告.md)，[复核撤回旧结论](../2026-09-10-dreamjourney-live-second-round-fix/2026-09-10-Astra复核补齐证据索引.md)，[错误字段仍被记为PASS的SDK原记录](../2026-09-10-dreamjourney-live-second-round-fix/evidence/t06-real-sdk-final-frame-20260910-2326.md)。
- E21：[B4初始交付](../2026-09-11-dreamjourney-b4-candidate-review-fix/2026-09-11-DreamJourney-B4-A修复交付报告.md)，[结构差异补齐](../2026-09-11-dreamjourney-b4-candidate-review-fix/structured-diff-supplement/2026-09-11-DreamJourney-B4结构化差异补充修复报告.md)，[原始判等/精度补齐](../2026-09-11-dreamjourney-b4-candidate-review-fix/raw-value-precision-fix/2026-09-11-DreamJourney-B4原始结构判等与显示精度修复报告.md)。
- E22：[9/11真实保存/候选读取故障](../2026-09-11-dreamjourney-b4-candidate-review-fix/device-followup-read-failure/2026-09-11-DreamJourney-B4-Live会后保存卡住与候选列表本地失败问题记录.md)，[9/12初始交付](../2026-09-12-dreamjourney-b4-post-session-candidate-read-fix/2026-09-12-DreamJourney-B4会后保存状态与候选列表发送前失败-本地交付报告.md)，[四项遗漏](../2026-09-12-dreamjourney-b4-post-session-candidate-read-fix/2026-09-12-DreamJourney-B4四项遗漏补充修复报告.md)，[诊断生命周期补齐](../2026-09-12-dreamjourney-b4-post-session-candidate-read-fix/diagnostic-attempt-lifecycle-2026-09-12/2026-09-12-DreamJourney-B4诊断尝试编号与网络生命周期补充修复报告.md)。
- E23：[9/12连续审核真实假成功](../2026-09-12-dreamjourney-b4-device-retest/2026-09-12-DreamJourney-B4真机新问题记录-Astra交接.md)，[连续审核与候选恢复](../2026-09-13-dreamjourney-b4-review-policy-recovery-fix/2026-09-13-DreamJourney-B4连续审核与候选读取恢复-本地交付报告.md)，[S1–S6边界补齐](../2026-09-13-dreamjourney-b4-review-boundary-supplement/2026-09-13-DreamJourney-B4审核未决结果与恢复边界-本地交付报告.md)。
- E24：[R1–R3与旧错误测试](../2026-09-13-dreamjourney-b4-final-boundary-fix/run-2026-09-13-01/reports/2026-09-13-DreamJourney-B4-R1-R3最终边界补充修复报告.md)，[关联组真正重入恢复](../2026-09-13-dreamjourney-b4-group-pending-reentry-fix/run-2026-09-13-01/reports/2026-09-13-DreamJourney-B4关联组未决结果重新进入恢复-本地交付报告.md)，[found之后刷新又停住的补齐](../2026-09-13-dreamjourney-b4-group-pending-reentry-fix/run-2026-09-13-02/reports/2026-09-13-DreamJourney-B4关联组结果已确定后候选刷新恢复-局部修复报告.md)。
- E25：[更正失败独立源码审计](../2026-09-13-dreamjourney-b4-correction-preview-audit/2026-09-13-Astra-独立核查证据.md)，[双重绑定交付](../2026-09-13-dreamjourney-b4-correction-preview-fix/run-2026-09-13-01/reports/2026-09-13-DreamJourney-B4更正预览双重绑定-本地交付报告.md)，[9/14真机正式写入及新缺陷](../2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md)。
- E26：[FM首轮](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-POLICY-01本地交付报告.md)，[FM五项遗漏/撤回](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/2026-09-14-DreamJourney-FM-POLICY-01五项遗漏补充修复报告.md)，[B6首轮](../2026-09-14-dreamjourney-b6-cold-start-read-recovery-fix/run-2026-09-14-01/2026-09-14-DreamJourney-B6会后任务冷启动只读恢复-本地交付报告.md)，[B6五项遗漏与真实reader](../2026-09-14-dreamjourney-b6-cold-start-read-recovery-fix/run-2026-09-14-02/2026-09-14-DreamJourney-B6五项遗漏补充修复-本地交付报告.md)。
- E27：[9/15独立L1设计完整阅读](../2026-09-15-live-fix-design-analysis/memory/2026-09-15-L1持续Live记忆中断-独立修复设计与验收.md)，[9/17saving完整分析设计](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-DJ-LIVE-SAVING-01-会后保存停滞问题分析与局部修复设计.md)，[9/17saving真机过程](../2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机问题过程记录.md)，[旧场覆盖独立设计](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-03补充-Echo冷启动旧场覆盖修复指导.md)。

- E28：[9/11会后保存与两种代次原设计](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-11-Sol-B4会后保存状态与候选列表发送前失败修复设计.md:190)，同文第5.2节（244行）和真实门禁测试约束（417行）；[9/14冷启动合同变更明确说明](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-14-Astra-B6会后任务冷启动只读恢复修复设计.md:356)。
- E29：[FM原正式trace原因校正与空服务端日志](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计.md:55)，同文第4.4节（128行）的三个入口边界；[FM最后修订执行清单](../2026-09-14-dreamjourney-formal-memory-policy-read-fix/run-2026-09-14-02/reports/M01-M28-W0-W6修订清单.md)。

记忆检索仅用于确定仓库位置和保留测试层级区分；历史结论全部回到本次读取的证据。相关记忆入口：MEMORY.md:101–103、150–160；未用记忆代替当前文档核验。
