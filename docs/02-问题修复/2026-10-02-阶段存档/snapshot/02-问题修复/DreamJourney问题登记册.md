# DreamJourney 问题登记册

更新日期：2026-10-02 · v1.39 · 维护入口：本文件。

**用途：统一管理问题及处理状态。已修问题保留在册；新的报告更新原条目，不再只散落在交付截图中。**

首版建档核对范围为 2026-09-10 至 2026-09-24 的 Live、会话保存、待确认/正式记忆及相关测试、发布事件。9/27增补开麦分析设计，9/28复核其run-01局部交付；其他条目仍以各自原核对证据为准，未重新验收全部模块或线上状态。更早设计和其他模块材料列入材料索引，尚未逐项裁定状态；这不是整个 App 的无遗漏缺陷审计。

共 **49 条记录**，包含产品问题、已修历史、代码风险、测试维护、观察和验收待办，不能把总数理解为仍有这么多个产品 bug。

待修复 **2**｜待核验 **6**｜首因未决 **1**｜基线待维护 **1**｜观察项 **1**｜本地已修待现场 **25**｜本地专项已关闭 **3**｜历史真机已通过 **8**｜验收待办 **2**

[维护规则与Sol交付要求](问题登记与验收维护规则.md) · [历史设计与材料索引](问题材料索引.md) · [9/22历史复盘](../outputs/2026-09-22-live-full-retrospective/2026-09-22-DreamJourney-Live全链路历史复盘与根因证据报告.md)

## 当前版本与证据边界

最新v1.39：本次发布修复已部署，真实DeepSeek8次合成检查通过，API及六Worker运行指纹/健康通过。新陶艺短场两轮→1主题/2底层候选→正式确认→新进程冷读通过；用户看到的短场异常提示尚未捕获，保留待核验。自然十分钟20轮40消息齐全，告别阶段0.576秒音频调度间隔触发首错；后台约7分半首次执行发布2主题36成员，但手机状态未知、工具缓存未跟随，完整链未通过。434次模型请求中374次关系比较，发布延迟及终态同步仍待修。状态数量不变，不关闭历史故障。[本轮部署与真机记录](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/2026-10-02发布修复部署与真机验收.md)。

历史v1.38：按用户新策略完成收尾发布局部修复，已验证新主题不因可选关联不确定、更正类型不兼容而整组丢弃；首层有限重组、上层失败保留子主题，证据和历史正式记忆保护不放宽。64项定向及102项相关回归通过，隔离PG短A→逻辑十分钟→短B发布确认回读通过。真实Provider诊断因本轮源码传输审批待确认尚未执行，部署/手机/物理十分钟NOT_RUN。该项移至本地已修待现场，现场首因未完全还原。[实施与验证](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/发布降级修复实施与验证.md)。

历史v1.37：授权恢复修复已安装到iPhone，后端原版一致性核对通过。新短场4条正文齐全，收尾attempt1更正类型不一致、attempt2无可靠主题，最终零发布；短场FAIL、自然十分钟及正式确认NOT_RUN。新增P0待核验，49条、待核验7，其余不变；授权修复尚未进入现场目标阶段。未修该发布规则或重试历史。[记录](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/问题记录与真机结果.md)。

历史v1.36：候选读取旧授权过期已完成产品 Client 局部修复，修前2项业务红、最终38项通过零失败零跳过；最终同版短A→逻辑20→短B→逻辑40全部确认及PG回读通过。主题写入仍禁止自动重发。该项转本地已修待现场；48条总数不变，待核验6、本地已修待现场24。本轮Provider/手机/部署NOT_RUN，上一场真机FAIL及其他历史未决不变。[实施记录](记忆系统/2026-10-02-长场后候选读取授权过期/实施与验证记录.md)。

历史v1.35：十分钟修复已部署并安装。真实DeepSeek4次分页专项通过；最终同版短场候选确认与正式冷读通过。物理场21轮、42消息，625秒完成当前轮后告别、636秒自动关麦，4主题36成员服务端发布；确认前Source读取被capturedPolicyExpired阻断，十分钟完整链FAIL/正式确认NOT_RUN。新增客户端与自动工具交互待核验P1，普通用户页面影响未证；48条，待核验7，其余计数不变。原历史失败不关闭。[本轮报告](记忆系统/2026-10-01-长场收尾关联审核失败/2026-10-02部署与十分钟真机验收.md)。

最新v1.34：用户授权后完成分页身份上下文v2、独立可靠子集重新归纳/安全审核、十分钟完成当前轮再告别关闭。后端153项、Controller5项、最终四场及音频6项、PG完整113/局部105事实确认与重建读取通过；本轮限定范围LOCAL_PASS，真实Provider/部署/手机NOT_RUN。原180秒观察与终态一致性等仍未修，原P0条目不关闭，47条及计数不变。[方案及验收](记忆系统/2026-10-01-长场收尾关联审核失败/分页与十分钟收尾实施验证.md)。

最新v1.33：本场244份请求/响应缓存精确重建，主要机制已证实：113事实单主题对5个旧主题计划140页；第110页不确定即整卡阻断，独立身份声明在未执行的111页。iOS180秒停止查询而后台327秒才终态，工具额外20分钟仅读缓存。首次绑定失配字段仍未决；P0条目保持待核验、计数不变。先讨论不改代码。[深层原因](记忆系统/2026-10-01-长场收尾关联审核失败/深层原因分析.md)。

历史v1.32：用户例外继续长场，目标改为70轮；第66轮音频停顿，最终132条正文齐全，前65轮130条文本哈希精确匹配。收尾任务第一次绑定校验失败，原job第二次以noReliableThemes终态失败，未生成待确认主题。新增P0待核验条目；手机原定1200秒观察已结束，仍持pending，工具记OBSERVATION_TIMEOUT；终态传播未完成。未改代码或重试历史任务。[本场全过程](记忆系统/2026-10-01-长场收尾关联审核失败/收尾全过程与失败记录.md)。

历史v1.31：用户完成手机信任后，短场2用户轮/4条正文完整保存，主题独立审核uncertain导致noReliableThemes，待确认发布FAIL。新增P0待核验条目；长场门禁未通过，20/40分钟NOT_RUN。分页分支未进入，语义首因未决。[记录](记忆系统/2026-10-01-短场主题审核不确定导致未发布/问题记录与本次真机结论.md)。

历史v1.30：2026-10-01容量修复部署通过；真机在开麦前被iOS系统拒绝启动，等待手机开发者信任核实。未新增业务失败或宣称现场通过。[记录](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/2026-10-01部署与真机测试记录.md)。

历史v1.29：长主题容量缺陷已按设计修复，LOCAL_PASS及真实DeepSeek合成专项30次通过；212项相关测试、97+16精确PG闭环、已有卡补充和最终同版短A→逻辑20→短B→逻辑40通过。条目转本地已修待现场、仍为P0；未部署、未操作手机或历史任务。音频P3未改。[实施与证据](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/实施与验证记录.md)。

最新v1.28：新短场完整真机闭环PASS；长场43分20秒、73用户轮保存，发布FAIL。只读重建26份哈希匹配模型结果确认97条主题事实超过内部关联阶段64条限制，发模型请求前失败，登记P0待修复。音频调度停顿按用户要求登记P3待核验。未改该产品缺陷，未重试历史任务。[本轮分析](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/问题记录与原因分析.md)。以下保留历史状态。

最新v1.27：用户接受本场星/新同音专名差异，不再将其计为验收失败。已执行保存至待确认链通过；第二轮、正式确认及冷读仍待补测，不能由人工接受差异推定未执行项PASS。原自动测试FAIL证据保留，状态计数不变。[验收口径补充](测试与验收/2026-09-30-真机自动验收中断与恢复核对/真机验收口径补充-同音专名.md)。

最新v1.26：本版工具真机短场完成。第一轮星/新专名断言FAIL；实际2条正文已收录，1条待确认主题/3候选事实发布，schema2主机来源核对PASS。自动确认因错误核心事实未执行，20/40分钟未启动；整体不升级PASS，状态计数不变。[本场完整记录](测试与验收/2026-09-30-真机自动验收中断与恢复核对/本版真机短场复测记录.md)。

最新v1.25：用户授权实施后，DJ-LIVE-LAB-RECOVERY-20260930完成工具本地修复与验收，移至本地已修待现场。真实封存消息/ASR账本及主机来源证明已替代completedTurns×2和预填夹具；实际PG同断言红绿、恢复确认/新进程冷读、两类零写入负例、最终同版短A→逻辑20→短B→逻辑40通过。工具LOCAL_PASS，真实Provider/手机/部署NOT_RUN，历史调度首因与真机FAIL不改写。总数43，待修复2，本地已修待现场22。[实施与验证](测试与验收/2026-09-30-真机自动验收中断与恢复核对/实施与验证记录.md)。以下为历史进展。

最新v1.24：按用户要求只记录不修改。新增DJ-LIVE-LAB-RECOVERY-20260930（待修复/测试工具缺陷）：来源核对错误使用completedTurns×2；恢复事实只在完整回合验收后收集，且原本地夹具绕过真实主机核对。数字格式已有归一化，本场专名差异是“星/新”；0.606秒调度耗时首因仍未决。已整理完整分析及15组验收方案，未追加代码修改、部署或测试。总数43，待修复3，其余计数不变。[文档入口](测试与验收/2026-09-30-真机自动验收中断与恢复核对/README.md)。

最新v1.23：更正意图版本已部署，API/六Worker指纹及健康通过。新真机短A仅第一轮：2条正文完整，收尾发布1条独立主题/2条事实，未关联旧记忆；专名识别不匹配后等待，0.606秒送帧间隔触发首错。恢复确认又被主机completedTurns计数0与实际保存2不一致拦住；正式确认、短B和20/40分钟NOT_RUN，整体DEVICE_SHORT_FAIL。新增证据补入DJ-LIVE-AUTOLAB-01、DJ-ASR-OBS-01及关联合同条目，42条及状态计数暂不变。不能以兜底成功关闭现场。[本轮报告](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/本版部署与真机短场复测.md)。

最新v1.22：更正意图原话绑定、独立复核及终态关联摘要绑定已实现；本地88项、PG11组、工具18项和最终短A→20→短B→40通过。真实模型15次分组保留失败，最终2次绑定路径通过；本轮未部署/真机，上一轮现场FAIL不覆盖。总数42及状态计数不变。[本轮实施验收](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/更正意图修复实施与验收.md)。

最新v1.21：关联合同修复本地、真实模型8次专项及部署校验通过。真机短A的4/4正文及1条pending主题已发布，但与旧已确认读书角关联为correction，自动确认保护拦截，短场整体FAIL，20/40分钟NOT_RUN。仍为本地已修待现场，总数42及状态计数不变；不以专项模型PASS代替真机。以下v1.19为修前记录。

最新v1.19：用户授权部署及真机后，真实模型前置8次检查未通过。none通过，duplicate/supplement因correctionsResolved=false拒绝，correction摘要保留冲突事实被正确拒绝。新版本已准备但未激活，真机NOT_RUN；原线上服务健康。新增待修复条目，总数42，待修复3。[本轮记录](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/问题分析与验证记录.md)。以下均为历史进展。

最新v1.18：独立主题误拦截专项完成本地修复与验证；本版真实Provider、部署、手机均NOT_RUN。待修复3→2、本地已修待现场19→20，总数41不变。9/30真实短场FAIL及其他历史未决仍保留。[实施记录](记忆系统/2026-09-30-独立主题误拦截/实施与验证记录.md)。以下v1.17等为历史进展。

最新v1.17：用户授权后已部署并安装；真实短场正文完整、候选失败，已定位独立主题误拦截并新增待修复条目。[最新证据](../outputs/2026-09-30-live-model-contract-device/run-01/真机短场失败与直接原因.md)。后续较早v1.15和9/29表格为历史快照。

最新9/30 v1.15：真机前真实模型关系检查失败，已暂停本轮安装与新场测试；详见文末v1.15和[预检报告](../outputs/2026-09-30-live-model-contract-device/run-01/README.md)。下述已部署和真机记录均为此前版本证据。

9/30最新现场进展：后端 `live-resilient-20260930/schema0128` 已部署，iPhone安装STAGE02。独立短场A真实2轮/4正文→1主题卡→3正式事实→新PID冷回查PASS。20分钟目标长场17分19秒/36轮后FAIL；后续自动补齐72条正文，但两次恢复快照均themeValidate.noReliableThemes失败、无主题发布。短B/40分钟NOT_RUN。详见[本次现场证据](../outputs/2026-09-30-live-resilient-device/run-01/现场问题与证据记录.md)。用户要求先分析讨论再修改，历史首因不关闭。

9/30增补：韧性保存与主题归纳已完成受控本地开发，短A→逻辑20→短B→逻辑40及真实隔离数据库闭环通过；真实Provider、手机、物理20/40分钟和本轮部署均NOT_RUN。[本轮开发及证据](../outputs/2026-09-29-live-resilient-memory-development/run-01/README.md)。以下9/29版本表为历史快照，不代表9/30新代码已安装或部署。

下表更新至9/29播放与确认修复run-01：本轮三次短场已生成本场候选；完整真机闭环仍失败在确认步骤。最终确认修复已完成本地验收但未重新安装。用户允许暂时拔掉手机，后续真机由用户主动发起。

| 层级 | 最新有依据的状态 | 依据 |
|---|---|---|
| 受控本地闭环 | LOCAL_PASS；iOS786/0/3，最终同版short-A→logical20→short-B→logical65通过；正式Live V5确认PG专项通过 | [最新交付](../outputs/2026-09-29-live-playback-repair/run-01/README.md) |
| 后端发布 | 沿用api-pool-isolation-20260929/schema0124；本轮无后端产品改动或新增部署 | [安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md) |
| iOS 安装/构建 | STAGE04曾安装并实际测试；最终STAGE05已编译和核对指纹，未安装 | [最新交付](../outputs/2026-09-29-live-playback-repair/run-01/README.md) |
| 服务可用性 | 前次修复后九次只读检查200；持续稳定性及历史首因未结案 | [安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md) |
| 最新真实验收 | 三场各2用户轮、4条正文和4/3/4条候选，实际PCM播放恢复通过；审核400导致完整短场FAIL，正式0。最新修复真机复测、物理20/65分钟NOT_RUN | [三场证据与边界](../outputs/2026-09-29-live-playback-repair/run-01/README.md) |


历史9/24源码与构建：final08受保护源码指纹 `45a6e1b97e2d31ab608658013fe4d27dd91c012c5c5f755a31edb5673de279fe`；现场签名可执行文件指纹 `07c4031d661a9ff3bfca296212a88d25bc1718f07bcdab9e98620f304abd3daf`。这些是报告锚点，后续变更必须重新核对。

**旧报告中的 NOT_RUN 是报告当时状态，不覆盖后来的部署证据；旧报告中的 PASS 同样不能覆盖后来的新故障。** 七项基线路由失败仍单列；46条PoolClosed按正确fixture已完成局部对照，不是46个未修产品问题。

## 登记总表

P0：用户当前最高优先级；P1：优先处理或验收；P2：专项保持/维护；P3：观察。优先级不代表已证明根因。点击编号跳到详情，点击详情材料进入原始文件。

### 待修复（2）

9/24读取事件拆分出的超时误分类、运维处理项仍待修复。API同步取池及统计阻塞机制已完成本地修复，移至“本地已修待现场”；历史事件与最新故障仍不合并首因。

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-READ-TIMEOUT-01](#dj-read-timeout-01) | 读取超时被误报为“发布策略拦截” | 产品缺陷 | P1 |
| [DJ-API-DIAG-01](#dj-api-diag-01) | 服务失活时缺少首因取证与有界运维恢复 | 运维缺口 | P1 |

### 待核验（6）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-LIVE-CLOSURE-REVIEW-20261001](#dj-live-closure-review-20261001) | 长场正文齐全，收尾关联审核失败，手机终态不同步待核验 | 现场故障，深层首因待核验 | P0 |
| [DJ-LIVE-THEME-UNCERTAIN-20261001](#dj-live-theme-uncertain-20261001) | 短场正文完整，主题审核不确定导致未发布 | 现场故障，语义首因待核验 | P0 |
| [DJ-LIVE-AUDIO-SCHEDULER-20260930](#dj-live-audio-scheduler-20260930) | 43分钟后送帧调度停顿 | 性能观察，首因待核验 | P3 |
| [DJ-MEM-READ-VERIFY-01](#dj-mem-read-verify-01) | 恢复后候选为零及记录页内容是否符合预期 | 数据展示核验 | P1 |
| [DJ-LIVE-AUTOLAB-01](#dj-live-autolab-01) | 静音 PCM 自动真机路径的播放完成与原生路径等价性 | 测试工具 | P2 |
| [DJ-LIVE-LONG-20260929](#dj-live-long-20260929) | 自动长场后段保存不齐、停止未收尾且候选未发布 | 现场故障，首因待核验 | P1 |

### 首因未决（1）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-LIVE-SEQ5-01](#dj-live-seq5-01) | 9/22 手工短场第 5 条提交结果未知的首发原因 | 历史首因 | P1 |

### 基线待维护（1）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-BACKEND-BASELINE-01](#dj-backend-baseline-01) | 后端七项路由数量基线失败 | 测试维护 | P2 |

### 观察项（1）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-ASR-OBS-01](#dj-asr-obs-01) | 个别词语识别歧义或偏差 | 观察 | P3 |

### 本地已修待现场（25）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-LIVE-THEME-CORRECTION-20261002](#dj-live-theme-correction-20261002) | 短场更正关系类型冲突，恢复后无可靠主题导致零发布 | 已部署，短场数据闭环通过；长场终态同步仍未过 | P0 |
| [DJ-LIVE-REVIEW-POLICY-20261002](#dj-live-review-policy-20261002) | 长场已发布，自动候选读取遭旧授权过期拦截 | 产品读取恢复缺口，已本地修复 | P1 |
| [DJ-LIVE-THEME-CAPACITY-20260930](#dj-live-theme-capacity-20260930) | 长场主题事实97条超过关联阶段64条限制，整场未发布 | 产品缺陷 | P0 |
| [DJ-LIVE-LAB-RECOVERY-20260930](#dj-live-lab-recovery-20260930) | 自动真机恢复用完成轮数误判已保存消息及事实集合 | 测试工具缺陷 | P1 |
| [DJ-API-READ-STALL-01](#dj-api-read-stall-01) | 登录后 API 整体读取停摆 | 产品故障 | P1 |
| [DJ-API-LIVENESS-01](#dj-api-liveness-01) | 健康检查返回前同步写诊断数据库 | 代码风险 | P1 |
| [DJ-LIVE-START-20260924](#dj-live-start-20260924) | Live 开麦迟缓、旧权限阻断新启动与请求级诊断缺口 | 产品故障/代码缺口 | P1 |
| [DJ-LIVE-CAPTURE-01](#dj-live-capture-01) | 单回合冲突导致整场采集提前结束或后段不入队 | 产品缺陷 | P1 |
| [DJ-LIVE-AUTH-01](#dj-live-auth-01) | 长场 401 后认证恢复、重试名额和队列排空 | 产品缺陷 | P1 |
| [DJ-LIVE-SAVING-01](#dj-live-saving-01) | 正常停止后长期停留 saving | 独立现场症状 | P1 |
| [DJ-ECHO-SCENE-01](#dj-echo-scene-01) | 历史任务覆盖本场文案或核实按钮查询错场 | 产品缺陷 | P1 |
| [DJ-LIVE-L20-01](#dj-live-l20-01) | 长场候选整理合同失败和单次输出容量不足 | 产品缺陷 | P1 |
| [DJ-LIVE-EVIDENCE-01](#dj-live-evidence-01) | 跨批重复、补充、纠正与原文证据身份误判 | 产品缺陷 | P1 |
| [DJ-MODEL-BOUNDS-01](#dj-model-bounds-01) | 关系页/长 turn 的容量边界与原文切片投影 | 产品缺陷 | P1 |
| [DJ-WORKER-BUDGET-01](#dj-worker-budget-01) | 恢复时预算/权限快照和失败 Run 边界不完整 | 产品缺陷 | P1 |
| [DJ-DELIVERY-SQL-01](#dj-delivery-sql-01) | 只读提交结果查询使用不存在的数据库列 | 产品缺陷 | P1 |
| [DJ-ADMISSION-EPOCH-01](#dj-admission-epoch-01) | admission 读取错误权限 epoch 导致500 | 产品缺陷 | P1 |
| [DJ-LIVE-READ-OBS-01](#dj-live-read-obs-01) | 状态读取期限、迟到结果和旧 envelope 处理 | 产品缺陷 | P1 |
| [DJ-LIVE-FIRST-ERROR-01](#dj-live-first-error-01) | 首次网络错误被包装遮蔽或诊断落盘不完整 | 产品缺陷 | P1 |
| [DJ-LIVE-WORKER-01](#dj-live-worker-01) | 会中预整理和 admission 到默认 Worker 的真实装配缺口 | 业务与验收缺口 | P1 |
| [DJ-B7-01](#dj-b7-01) | 纯问题或助手回答误成为待确认事实 | 产品缺陷 | P1 |
| [DJ-B8-01](#dj-b8-01) | 文字会话停止后的 ACK/admission 交接及状态读取 | 产品缺陷 | P1 |
| [DJ-LIVE-ENTRY-01](#dj-live-entry-01) | 结束文字会话后Live入口不可用及令牌503 | 产品修复 | P2 |

| [DJ-LIVE-INDEPENDENT-THEME-20260930](#dj-live-independent-theme-20260930) | 合法独立新主题被合并关系布尔门禁误拦截，候选无法发布 | 产品缺陷，真机及本地复现 | P1 |

| [DJ-LIVE-RELATION-REVIEW-20260930](#dj-live-relation-review-20260930) | 重复/补充复核不适用条件误拦截及更正摘要保留冲突旧事实 | 真实模型合同缺陷 | P1 |

### 本地专项已关闭（3）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-QA-CHAIN-01](#dj-qa-chain-01) | 模拟验收未覆盖真实默认链、身份和短场门禁 | 验收工具 | P1 |
| [DJ-QA-POOL-01](#dj-qa-pool-01) | 全量测试的未启动 PostgreSQL fixture 导致 PoolClosed | 测试装配 | P2 |
| [DJ-QA-ASYNC-01](#dj-qa-async-01) | 测试结束条件早于实际在途读取完成 | 测试装配 | P2 |

### 历史真机已通过（8）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-LIVE-CONFIRM-V5-01](#dj-live-confirm-v5-01) | Live专用确认缺V5绑定及命令编号不符合后端规则 | 产品合同缺陷 | P1 |
| [DJ-B1-01](#dj-b1-01) | Live 未采用已经确认的正式记忆 | 产品缺陷 | P1 |
| [DJ-B4-01](#dj-b4-01) | 候选审核、更正预览、未决恢复及正式写入链 | 产品缺陷 | P1 |
| [DJ-FM-POLICY-01](#dj-fm-policy-01) | 正式记忆只读策略过期后无法恢复 | 产品缺陷 | P1 |
| [DJ-B6-01](#dj-b6-01) | 整理中退出后冷启动无法只读恢复 | 产品缺陷 | P1 |
| [DJ-LIVE-AUDIO-01](#dj-live-audio-01) | 长回答被截断、插话后未恢复聆听 | 产品缺陷 | P1 |
| [DJ-LIVE-AUTH-DOMAIN-01](#dj-live-auth-domain-01) | 本地lease与服务端账号代次混比阻断保存 | 产品缺陷 | P1 |
| [DJ-LIVE-PARTIAL-01](#dj-live-partial-01) | 覆盖摘要刷新和停止于半句话时的partial分支 | 产品修复与正确分支 | P2 |

### 验收待办（2）

| 编号 | 问题/事项 | 类型 | 优先级 |
|---|---|---|---|
| [DJ-ACCEPT-PROVIDER-01](#dj-accept-provider-01) | 当前完整模型合同的真实Provider验收 | 外部验收 | P1 |
| [DJ-ACCEPT-DEVICE-01](#dj-accept-device-01) | 新版本短场及物理20/65分钟完整保存验收 | 现场验收 | P1 |

## 问题详情与证据链

以下每条状态以其最近一次证据及变更记录为准；本页更新日不代表所有条目都重新验收。“下一动作”是待办，不是本次已执行动作。责任分工：Astra维护/复核，Sol按具体授权实施；生产、真实Provider、手机及历史数据各按用户授权单独安排。

<a id="dj-live-start-20260924"></a>
### DJ-LIVE-START-20260924 · Live 开麦迟缓、旧权限阻断新启动与请求级诊断缺口

**状态：本地已修待现场｜类型：产品故障/代码缺口｜P1**\
阶段：指定有限本地修复与验收完成（LOCAL_PASS），现场事件未关闭。首次现场：2026-09-24；分析/设计：2026-09-27；最新复核：2026-09-29。沿用原事故编号。关联 DJ-LIVE-ENTRY-01、DJ-API-READ-STALL-01、DJ-API-LIVENESS-01，但没有合并根因。

- 已有事实：一次等待较久后进入聆听；另一次 `backendVoiceRuntimeRequestFailed`，全局快照有 `capturedPolicyExpired`。缺少本次请求 trace、原始错误及发起时间；不能把两次观察当同一失败。
- A／已修的局部代码缺口：新开麦复用旧 route capture；旧 capture 过期时，即使当前缓存新策略允许，ticket 仍可发前失败。run-02 在隔离逆转 D2 行为的副本中取得真实 Controller→Client→Gate→requestJSON 同断言业务红，并在现行代码转绿；合法缺失缓存刷新、账号重绑取消及跨 ticket 请求曝光归属亦有定向红绿。该副本不等于完整历史版本，也不能当历史单次因果证明。
- B／历史慢启动未决：没有分段计时，不能归因火山、DeepSeek、麦克风或某个60秒超时；统一启动截止缺失，需有界收尾与阶段证据。API同步DB等待风险另案管理。
- C／诊断进展：run-02以隔离PG证明ticket commit/rollback阶段；run-03已补默认日志输出、独立Handler及跨请求latestDecision不覆盖本次原因的Controller证据。closure-04已补诊断磁盘不可写时Controller/capture对静态旧场的保护；9/29接手又补活动旧场排空及新场失败隔离，见下方最新入口。全局latestDecision非本次请求证据；无ticket完成日志不等于无请求。
- 9/28 run-01复核（历史）：独立读取xcresult确认596通过、0失败、3跳过；三个跳过项恰为短场与两长场默认保存链。13份可匹配源码/配置及无签名dylib指纹一致。另定位真实SDK内部截止/取消、账号重绑、missingPolicyCache恢复、跨请求曝光归属及新增后端诊断异常隔离缺口，见[复核与续修清单](../outputs/2026-09-28-dreamjourney-live-mic-start-review/run-01/复核结论与Sol续修提示词.md)。
- 9/28 run-02独立复核：当前1,297项依赖及312构建产物与四场匹配；四份xcresult各1/1，完整束758/0/3与首次757/1/3均核实。发现Controller成功/错误事件二次排队后读取当前场次的静态竞态；真实Manager控制逻辑和MIC-19仍缺本地执行证据。首次MIC-11的直接原因已定位为ticket返回后authorityEpochMismatch、SDK启动0次，epoch写入来源未决。纯日志探针还证明仓库默认Uvicorn日志配置无阶段输出，以及加共用Handler后诊断慢输出会锁住同步warning的条件性机制；均非历史现场首因证明。详见[run-02复核与Sol收尾提示词](../outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/复核结论与Sol收尾提示词.md)。
- 9/28 run-03 本地续修：二次排队的 Controller 成功/错误事件同断言业务红2项、修后绿；旧账号 runtime 迟到响应覆盖新授权代次同断言业务红、修后绿。生产 Manager 共用控制逻辑、MIC-19 磁盘旧场保护、MIC-08 两次真实 ticket POST、默认日志与独立 Handler 均有局部执行证据；真实原生 Manager/SDK 时序不据此认定通过。另补当前/刷新后deny、401恢复中切账号、429/解码/快照分类与跨请求诊断串场的控制器/客户端测试。最后一次测试源码变化后重跑完整 iOS 770/0/3、通用 iOS 无签名构建及同版四场，1,297 项依赖与 312 项制品匹配。旧/今 HEAD 同环境路由计数各3项失败已留对照，为基线例外而非全后端PASS。见[run-03实现报告](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/implementation-report.md)与[逐项MIC矩阵](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix.md)。
- 9/28 run-03独立复核：原始770/0/3、四场各1/1、1,297项依赖及312产物均核实。发现生产Manager在markStartSubmitted=false后仍无条件send StartEngine；源码提取的离线Swift探针得到false但引擎边界调用1次，非完整SDK/历史现场复现。现有helper测试自行加if，未执行该生产提交接线。MIC-19活动A/B故障和MIC-14诊断磁盘不可写的保存组合仍缺；已通过的跨请求原因、runtime旧响应、日志隔离与路由基线对照不重复列为未做。见[run-03复核与收尾提示词](../outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/复核结论与Sol收尾提示词.md)。
- 9/28 run-03 续修实测：生产 Manager 的 StartEngine 提交已改为必须通过最终 operation/单调截止校验，拒绝只收尾原 operation；源码片段业务红（误发1次）、共用生产提交编排绿（误发0次）。真实 Controller/capture 诊断盘不可写隔离、策略等待跨截止、原因/可见提示、隔离 PG 并发和跨账号 403 均补证；最终 iOS 771/0/3、通用 iOS 构建及同版四场通过。但完整 Manager 受控 SDK 回调、活动 A 正在排空时 B 失败及 B 正常 close、部分累计截止/共享等待者组合仍未通过或未执行，整体继续 LOCAL_INCOMPLETE。此前活动 A 测试装配失败与一次完整回归 GET 计数失败均保留原始结果，不归为已解决产品故障。见[续修报告](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/closure-report-04.md)和[续修 MIC 矩阵](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix-closure-04.md)。
- 9/29独立复核closure-04：确认R1生产提交已使用最终守卫；原始完整束771/0/3、四场各1/1及1,297依赖/312制品和两配置均匹配。活动A测试原始失败未到达B SDK目标，恢复的静态A测试不能替代；final-06恢复入口2 GET/1 GET失败仍待归因。剩余完整Manager接线、活动A/B和原矩阵有限组合使状态继续LOCAL_INCOMPLETE。核对说明与Sol提示词见[独立复核](../outputs/2026-09-29-dreamjourney-live-mic-start-review/run-04/复核结论与Sol剩余任务.md)；本轮未改业务或连接外部。
- 9/29 Astra接手完成：实际生产Manager受控SDK完整链、活动A append在途+B deny/timeout/SDK失败及无B对照、真实共享auth刷新取消一人不影响另一人、累计单调截止和有限终态竞争均闭合。修复同步SDK失败后Controller二次超时收尾、待启动取消后原转写入口残留，隔离附带runtime查询和follow-up共享磁盘污染；不改GET/写重试预算。final-06旧称“2 GET/1 GET”已纠正：实际为2条恢复任务、1次GET，见[原证据与说明](../outputs/2026-09-29-dreamjourney-live-mic-start-takeover/run-01/implementation-report.md)。最新[交付入口](../outputs/2026-09-29-dreamjourney-live-mic-start-takeover/run-01/README.md)、[逐项矩阵](../outputs/2026-09-29-dreamjourney-live-mic-start-takeover/run-01/mic-matrix.md)、[最终指纹](../outputs/2026-09-29-dreamjourney-live-mic-start-takeover/run-01/fingerprints.md)。
- 下一动作：由用户主动发起真实Provider/真机验收，先短场完整落候选及正式记忆，再物理20分钟、未来65分钟；同trace核对启动各阶段。不能以本地模拟替代现场或宣布历史首因已知。
- 9/29授权部署与真机：发布、安装完成；短场先后在预检readTimeout、开麦ticket等待失败，0轮。新开麦同trace手机47ms发请求、15026ms timeout，服务端25055ms返回503；同期取得主线程统计sink取池阻塞栈。真实Provider对话、记忆完整链及20分钟未运行。局部启动截止生效，不等于现场事件关闭。见[9/29部署与真机失败记录](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-01/device-report.md)。
- 最新状态边界：9/29接手后受控生产Manager专项39/39；标准Simulator完整束774通过/0失败/3集成入口跳过，四场各1/1单独通过；通用iOS无签名构建通过。这是接手本地交付时的边界；随后部署/真机状态见上一条。历史近一分钟等待、23:04单次因果、seq5仍未决。
- 关闭条件：本地对应断言通过可转“本地已修待现场”；不能据本地通过直接关闭现场事件。后续按同 trace 开麦与短场保存验收，慢启动仍未定位则单独保留未决。
- 材料：[现场与截图](../outputs/2026-09-24-dreamjourney-live-mic-start-incident/run-01/incident-report.md) · [分析与开发指导](Live语音与开麦/2026-09-27-DreamJourney-Live开麦迟缓与发前失败-分析及开发指导.md) · [run-01](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-01/implementation-report.md) · [run-02交付](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-02/implementation-report.md) · [run-03交付](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/implementation-report.md) · [run-03 MIC矩阵](../outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix.md) · [iOS审查](../outputs/2026-09-27-dreamjourney-live-mic-start-analysis/ios-audit.md) · [后端审查](../outputs/2026-09-27-dreamjourney-live-mic-start-analysis/backend-audit.md)
- 9/29 run-02真实启动观察：修复版首个开麦ticket同trace 298ms返回200，手机约4.109秒进入聆听；调零后SHORT03真实ASR/回复文字与音频成功，但原生播放完成缺失，完整短场FAIL。停止后后台发布2条pending候选。局部启动成功不能关闭历史慢启动事件。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。


<a id="dj-api-read-stall-01"></a>
### DJ-API-READ-STALL-01 · 登录后 API 整体读取停摆

**状态：本地已修待现场｜类型：产品故障｜P1**\
历史编号/关联：9/24 postlogin-read-incident；父事件 A–E。

- 历史9/24证据：公网、服务器本机及容器内/live均无响应，同版本重启后恢复，当时没有Python栈。9/29新证据：真实短场期间本机/live超时，Python主线程停在统计中间件→evidence写入→PG池getconn；时间窗9次pool exhausted，同trace ticket 25秒503；未重启而自行恢复。池耗尽最初触发与历史事件同因关系仍未决。见[9/29部署与真机失败记录](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-01/device-report.md)。
- 证据边界：不能归因于手机网络、真实策略 deny 或模型容量；也不能仅因重启有效就关闭。与历史 seq5、候选整理失败分开登记。
- 9/29本地修复：请求checkout/finalize离开事件循环；统计有界后台投递；取消后的连接清理。真实PG同环境24并发旧版22次503、/live最慢17.812秒；最终版24/24成功、/live最慢0.007秒。134项回归及最终短A→逻辑20→短B→逻辑65通过。见[修复与验证报告](../outputs/2026-09-29-api-pool-stall-repair/run-01/implementation-report.md)。
- 下一动作/关闭要求：按已授权发布后，先核对线上运行指纹与只读健康，再执行真机短场门禁；通过后再20分钟。现场首个耗尽请求组合及历史同因关系仍未决。
- 材料：[独立现场记录](../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md) · [待修复记录](服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md) · [运行版本](../outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md)
- 9/29 run-02：池隔离版本已部署，本轮核对949个运行文件一致；测前九次只读检查及测后/live正常，真实ticket 298ms；最新短场失败点转为播放器完成，未复现同次API停摆。只完成局部现场观察，持续稳定性及历史池耗尽首因未闭合。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。


<a id="dj-api-liveness-01"></a>
### DJ-API-LIVENESS-01 · 健康检查返回前同步写诊断数据库

**状态：本地已修待现场｜类型：代码风险｜P1**\
历史编号/关联：DJ-API-READ-STALL-01 / B。

- 已有证据：/live 虽绕过请求 UoW，外层统计中间件仍可在返回前同步写 evidence_events；异常捕获没有给阻塞等待设置完成边界。
- 证据边界：9/29已在真实故障栈确认该统计路径同步取池阻塞主线程，后端与手机ticket同trace关联；仍未证明9/24停摆首发原因，也未完整查明池首先耗尽原因。基础路径在旧版存在，非本轮五文件新增。见[9/29部署与真机失败记录](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-01/device-report.md)。
- 9/29本地修复：HTTP统计改为单后台线程、128条有界队列，满时仅丢统计并计数；/live为纯内存异步入口。慢sink、队列满、取消、实际PG池满反例均通过；关闭等待有界，未扩大业务预算。见[修复与验证报告](../outputs/2026-09-29-api-pool-stall-repair/run-01/implementation-report.md)。
- 下一动作/关闭要求：部署后独立核对线上与真机，不以本地延时保证线上SLA；同期统计故障可计数，不再作为响应前置条件。
- 材料：[代码与待修要求](服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md) · [现场边界](../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md)
- 9/29 run-02：修复版运行身份已核实；测后/live 200、2.822ms。本轮未施加线上池耗尽故障，不能以该健康响应替代线上故障隔离压测。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。


<a id="dj-read-timeout-01"></a>
### DJ-READ-TIMEOUT-01 · 读取超时被误报为“发布策略拦截”

**状态：待修复｜类型：产品缺陷｜P1**\
历史编号/关联：DJ-API-READ-STALL-01 / C；readDeadlineExceeded → featurePolicyDenied。

- 已有证据：客户端把读取期限耗尽归入策略拒绝，通用提示和部分领域恢复分支随之误分类。
- 证据边界：不能只改字符串；此误分类无法解释服务器本机 /live 无响应。Echo 已有部分超时特判，不应全部替换。
- 下一动作/关闭要求：建立独立超时分类及红绿用例，保留真正 deny、请求预算、账号隔离和未知写不重发。
- 材料：[问题及代码定位](服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md) · [现场提示](../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md)

<a id="dj-api-diag-01"></a>
### DJ-API-DIAG-01 · 服务失活时缺少首因取证与有界运维恢复

**状态：待修复｜类型：运维缺口｜P1**\
历史编号/关联：DJ-API-READ-STALL-01 / D。

- 历史9/24仅有unhealthy、futex外围证据。9/29人工取证已取得故障期Python栈；有界健康观察的原生栈模式失败后改Python栈成功，未自动重启。持久化自动诊断/恢复机制仍未完成。见[9/29部署与真机失败记录](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-01/device-report.md)。
- 证据边界：日志没有 ERROR 不等于无阻塞；自动重启不是根因修复。
- 下一动作/关闭要求：设计先留证再有界恢复的流程，在隔离环境演练；不得用业务写重放或循环重启代替。
- 材料：[现场与恢复记录](../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md) · [后续要求](服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md)

<a id="dj-mem-read-verify-01"></a>
### DJ-MEM-READ-VERIFY-01 · 恢复后候选为零及记录页内容是否符合预期

**状态：待核验｜类型：数据展示核验｜P1**\
历史编号/关联：DJ-API-READ-STALL-01 / E。

- 已有证据：用户确认正式记忆重新可读，候选页显示零；同账号/过滤条件下数量、记录页及内容完整性尚未完成对应核对。
- 证据边界：9/21 有经授权清空全部待确认候选的记录，因此“零”本身不能判定丢失；也不能据页面可打开判定数据完整。
- 下一动作/关闭要求：后续只读比对账号、vault、过滤和清理基线，确认实际预期后关闭或拆出有证据的新缺陷。
- 材料：[现场记录](../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md) · [历史候选清理基线](../outputs/2026-09-21-dreamjourney-pending-memory-cleanup/清理完成记录.md)

<a id="dj-live-seq5-01"></a>
### DJ-LIVE-SEQ5-01 · 9/22 手工短场第 5 条提交结果未知的首发原因

**状态：首因未决｜类型：历史首因｜P1**\
历史编号/关联：SAVE-03；DIAG_SEQ5_INITIAL_TRIGGER_UNRESOLVED。

- 已有证据：手机保留 6 条，服务器收到 4 条；seq5 outcomeUnknown，seq6 尚未曝光。后续核实接口 SQL 错误已另行修复，首个 append 异常原因仍缺原始证据。
- 证据边界：合成 401、超时、断连和 SQL 故障验证恢复行为，不等于证明历史首因；修复 GET 500 不代表可以重发旧场 POST。
- 下一动作/关闭要求：保留历史未决；依赖已补首错诊断，在用户主动安排的新场取得首次异常证据。不得为结案重放旧任务。
- 材料：[原始真机失败](../outputs/2026-09-22-dreamjourney-live-device-retest/run-01/reports/2026-09-22-DreamJourney-Live短场门禁真机失败记录.md) · [完整复盘](../outputs/2026-09-22-live-full-retrospective/2026-09-22-DreamJourney-Live全链路历史复盘与根因证据报告.md) · [最新明确未决](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md) · [恢复设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md)

<a id="dj-live-autolab-01"></a>
### DJ-LIVE-AUTOLAB-01 · 静音 PCM 自动真机路径的播放完成与原生路径等价性

- 9/30确定性恢复核对缺陷已拆为[DJ-LIVE-LAB-RECOVERY-20260930](#dj-live-lab-recovery-20260930)，避免将工具失败计为产品保存失败；原生/声学等价性仍待核验。

- 9/30新增现场：自动输入时钟0.606秒间隔、专名不匹配和恢复计数缺口已记录。主机完成轮数0不能代表保存消息0，实际保存2且已发布；恢复确认因计数不一致超时。另有仅完整轮才收集ASR的静态缺口。工具尚未改，调度首因未决。 [本轮证据](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/本版部署与真机短场复测.md)。

**状态：待核验｜类型：测试工具｜P2**\
历史编号/关联：LAB-01；W7；LAB_COMPATIBILITY_DEVICE_PENDING；STREAM 播放完成回调。

- 已有证据：9/22 自动短场记录暴露 admission 错误及自动 STREAM 路径完成信号边界；手工麦克风交互正常不能自动证明该注入路径可稳定无人值守运行。
- 证据边界：admission 代码修复与自动化工具真实 SDK 顺序是两项证据。不得把受控本地回调当真实麦克风/扬声器通过。
- 下一动作/关闭要求：先按 W7 核对本地工具和超时退场；未来单独验证自动路径，再按用户要求补声学链。
- 材料：[自动短场记录](../outputs/2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md) · [工具指导](测试与验收/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md) · [当前边界](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md)
- 9/29 run-02再现：静音0、实际SDK STREAM首轮ASR/用户与助手显示/非静音解码均有证据，但3019/3020及播放完成计数均无记录，页面停在“回响正在抵达”；约105.6秒超时退出。停止后本场end/ACK/admission成功，服务器pending候选2条。尚不确定SDK契约、STREAM/decoder观察或产品回调归属哪层导致；不得把合成结束伪造为播放完成。完整无人值守工具仍未通过。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。

- 9/29最新：已找到锁定SDK播放器事件转发缺口；用真实player/decoder PCM精确核对补充完成证明，三次新短场各两轮已恢复聆听。工具另修V5纯add误拒绝。原生事件仍0，声学硬件与完整无人值守正式回查未通过，状态保持待核验。见[最新修复与原始证据](../outputs/2026-09-29-live-playback-repair/run-01/README.md)。

- 9/29后续：两次新短场已完成候选→审核→正式→冷启动回查；最终长场约17分钟失败，详见[本场报告](../outputs/2026-09-29-live-playback-device-retest/run-01/README.md)。工具等价性/声学边界未关闭，静音已由用户明确豁免。

<a id="dj-live-confirm-v5-01"></a>
### DJ-LIVE-CONFIRM-V5-01 · Live专用确认V5绑定与命令编号合同

**状态：历史真机已通过｜类型：产品合同缺陷｜P1**

- 现场：9/29 SHORT02/03正文和候选成功，正式单条确认HTTP400，正式记忆0；不再混同为“没有保存正文/没有生成候选”。
- 已复现：真实iOS UseCase漏V5绑定的两项同断言修前红、修后绿；默认裸UUID以数字开头时违反后端首字母规则。隔离PG正式路由证实坏编号400、缺绑定409且均无写入，完整绑定+前缀编号201，激活后1条正式记忆/1个版本，同命令重放不重复。
- 已修：单条确认携带显示方案绑定，默认单条/批量编号增加字母前缀；不改未知写重放规则。工具只允许本场纯新增并记录安全错误分类。
- 边界：未捕获现场400的完整请求，不能宣称唯一历史原因；该句为旧阶段记录；最新安装复测结果见下条。最终iOS786通过/0失败/3独立入口跳过及四场本地链全部通过。V5旧Live入口纠正仍需独立预览，未新增纠正功能。
- 下一步：用户主动安排新短场，审核→正式→冷启动回查全部成功后才能长场；不重试原失败场。材料：[本轮报告](../outputs/2026-09-29-live-playback-repair/run-01/README.md)。


- 9/29最新真机：SHORT与SHORT02分别3/4条本场候选确认成为正式记忆，冷启动同身份只读回查通过，0业务写。仅关闭本次纯新增绑定路径的现场缺口；长场未通过、旧400唯一首因未证明、旧记忆纠正仍不在范围。见[本轮证据](../outputs/2026-09-29-live-playback-device-retest/run-01/README.md)。

<a id="dj-backend-baseline-01"></a>
### DJ-BACKEND-BASELINE-01 · 后端七项路由数量基线失败

**状态：基线待维护｜类型：测试维护｜P2**\
历史编号/关联：259/260 route count；与 PoolClosed 分开。

- 已有证据：同环境旧 HEAD 已复现七项路由数量失败，属于明确基线例外；当前未完成这些断言的专项维护。
- 证据边界：不是本轮新增保存缺陷，但也不能宣称后端全量全绿。不能为了消红直接改数量预期。
- 下一动作/关闭要求：独立核对路由注册真值与合同清单，再维护断言；保持与 46 条 PoolClosed 装配问题分开。
- 材料：[逐项对照](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/per-method-matrix.md) · [归因报告](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/B-backend-closure-report.md)

<a id="dj-asr-obs-01"></a>
### DJ-ASR-OBS-01 · 个别词语识别歧义或偏差

- 9/30短场合成“星桥八五三四一八”被识别为“新桥 853418”，导致专名断言不匹配，数字转写和同音字偏差分别记录。 [本轮证据](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/本版部署与真机短场复测.md)。

**状态：观察项｜类型：观察｜P3**\
历史编号/关联：9/15 测试短语歧义；9/17 单词偏差。

- 已有证据：已有零星识别偏差记录，缺少稳定声学复现及端到端转写对照。
- 证据边界：不据此认定火山容量、长场保存或候选缺失的根因；不是待开发的已确认产品缺陷。
- 下一动作/关闭要求：未来声学验收保留输入/转写对照和脱敏 SDK 阶段；出现稳定反例再升级为缺陷。
- 材料：[原始观察](../outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/issues/2026-09-15-B7测试短语语音识别歧义-观察记录.md) · [后续观察](../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md)

<a id="dj-live-capture-01"></a>
### DJ-LIVE-CAPTURE-01 · 单回合冲突导致整场采集提前结束或后段不入队

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：CAP-01–15；R01–R08；9/20 第 13 轮后停送。

- 已有证据：已分离转写观察、回合封存和不可变投递，保护后续回合、停止意图及 Controller/Coordinator 生命周期；本地逐轮链通过。
- 证据边界：前序物理 20 分钟持续采集 PASS 不等于候选完整闭环 PASS；不能认定已查明每次历史冲突的首发触发。
- 下一动作/关闭要求：保持当前实现；在新版本短场先行后验证真实 SDK 顺序、后半段/停止尾段和场次身份。
- 材料：[现场失败](../outputs/2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md) · [开发设计](记忆系统/采集与会后保存/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md) · [本地修复](../outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md) · [最新统一验收](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-auth-01"></a>
### DJ-LIVE-AUTH-01 · 长场 401 后认证恢复、重试名额和队列排空

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：LIVE-L20-AUTH-01；70/48/22；401→403；existingUseCaseNotSafelyResumable。

- 已有证据：本地修复覆盖 fresh authority、唯一重试名额、已曝光 notSent 只读核实、明确 deny 有界停止和跨账号迟到隔离。
- 证据边界：受控认证测试 PASS；后续场次自然 401 未出现只能 NOT_OBSERVED。原现场 403 的所有触发细节不能凭本地用例补写。
- 下一动作/关闭要求：维持认证与未知写保护；后续现场遇到自然轮换再取证，正常闭环及受控故障分别验收。
- 材料：[原始长场失败](../outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-20分钟真机复测报告.md) · [设计](服务端与认证/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md) · [修复与迟到补测](../outputs/2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告.md) · [最终保护矩阵](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-saving-01"></a>
### DJ-LIVE-SAVING-01 · 正常停止后长期停留 saving

**状态：本地已修待现场｜类型：独立现场症状｜P1**\
历史编号/关联：9/17 saving≥45秒；不得与 partial 合并。

- 已有证据：原场持久化正文存在、close intent 已落盘，重启后读到 pendingReview。后来同日短场真实保存链通过；最新本地补齐当前进程观察和迟到状态边界。
- 证据边界：原证据不足以将 admission、轮询或 UI 任一阶段定为该场唯一根因。仅增加超时文案不算保存修复。
- 下一动作/关闭要求：新场同时核对 end/ACK/admit、候选实际可见及页面状态；保留原事件首因边界。
- 材料：[原始观察](../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/evidence/device/2026-09-17-minimal-ui-refresh-observation.md) · [独立设计](记忆系统/采集与会后保存/2026-09-17-Astra-DJ-LIVE-SAVING-01-会后保存停滞问题分析与局部修复设计.md) · [历史短场通过](../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md) · [最新收尾](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-echo-scene-01"></a>
### DJ-ECHO-SCENE-01 · 历史任务覆盖本场文案或核实按钮查询错场

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：Astra-03；SAVE-04；N3/W3/U03。

- 已有证据：最新本地以当前场身份选择恢复对象，覆盖旧任务、无 checkpoint、本场 blocked、冷启动错 command/thread/session 和账号变化。
- 证据边界：保护历史 B6 恢复，但不让旧场占用本场页面。已知竞争风险不等于证明某次历史截图必由哪条任务覆盖。
- 下一动作/关闭要求：后续验收新场/离开重进/冷启动的显示和按钮都绑定同一场，旧数据不被删除。
- 材料：[历史现场](../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md) · [早期设计](记忆系统/采集与会后保存/2026-09-17-Astra-03补充-Echo冷启动旧场覆盖修复指导.md) · [当前设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [同版负例](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-l20-01"></a>
### DJ-LIVE-L20-01 · 长场候选整理合同失败和单次输出容量不足

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：supportValidate.factOmitted；organizationValidate.schemaInvalid；早期最多8条。

- 已有证据：历史长场正文/end/ACK/admit 已通过，整理先遗漏事实后 schemaInvalid 耗尽原预算。现已分批预整理、跨批合并、会后校验统一发布。
- 证据边界：“8条”是旧生成合同的候选输出限制，不是用户只能说8轮；未证明供应商存在20分钟保存上限。该私人现场是否确有超过8个不可合并事实尚未证实；第二次schemaInvalid的具体字段未留存，无法还原。真实长场候选至正式记忆闭环仍缺最终通过证据。
- 下一动作/关闭要求：先验证当前真实模型合同，再短场先行安排物理20分钟；未来65分钟另行验收，不再只扩大固定条数。
- 材料：[独立失败记录](../outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md) · [容量核查](记忆系统/长对话整理/2026-09-19-Astra-Live长场失败-供应商限制与内部容量核查.md) · [分批设计](../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md) · [最终本地证据](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-live-evidence-01"></a>
### DJ-LIVE-EVIDENCE-01 · 跨批重复、补充、纠正与原文证据身份误判

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：R02-B-PARA；事实/问题混合；分页遗漏；C1。

- 已有证据：已将原文证据身份与模型表达分离，以 ID/原文范围/hash 绑定；本地覆盖合法重复、补充、纠正、撤回，缺失或错误证据拒绝。
- 证据边界：多批提到同件事不应自动产生多条候选。验收工具错误引用拦截与产品语义修复分别留证，不能混算。
- 下一动作/关闭要求：受影响变更继续测精确事实集合、证据范围、跨批合并及 B7；真实 Provider 仍需独立语义检验。
- 材料：[语义缺口设计](记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md) · [修复报告](../outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md) · [当前统一边界](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [300事实精确对照](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-model-bounds-01"></a>
### DJ-MODEL-BOUNDS-01 · 关系页/长 turn 的容量边界与原文切片投影

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：SAVE-05；VERIFY-09；N6；4001单turn/40000总字符。

- 已有证据：统一修复补齐全阶段有界投影、切片原文范围及默认 Worker 300事实落 PG 精确校验；七类负例均被拦截。
- 证据边界：本地容量证据使用受控模型 HTTP，不证明真实 DeepSeek 的输出一致性、费用或供应商超时表现。300事实不等于300用户轮。
- 下一动作/关闭要求：后续真实 Provider 按实际部署模型与请求预算复核；原文保存容量、输入窗口和输出预算分开记录。
- 材料：[设计与真实缺口](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [本地精确验收](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md) · [外部历史核查](记忆系统/长对话整理/2026-09-19-Astra-Live长场无法生成待确认记忆-深度根因核查.md)

<a id="dj-worker-budget-01"></a>
### DJ-WORKER-BUDGET-01 · 恢复时预算/权限快照和失败 Run 边界不完整

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：SAVE-08；N4；Prepared冻结；原job恢复。

- 已有证据：本地已冻结完整策略与 Prepared 请求；已终止、隔离或预算耗尽的 Run 不额外调用模型，原 Run/原 WorkUnit/原预算内合法 transient 恢复保留。并发、事务回滚、租约、epoch、HTTP503后原WorkUnit原预算验证通过。
- 证据边界：不能创建替代任务或重置预算来使测试通过；323次受控HTTP应对应323次持久尝试，不能把不同统计相加。
- 下一动作/关闭要求：保留默认 Worker/隔离 PG 的同断言证据；新生产运行单独核对持久预算和失败分类。
- 材料：[最初候选修复设计](记忆系统/长对话整理/2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md) · [完整冻结要求](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [最终事务和预算矩阵](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-delivery-sql-01"></a>
### DJ-DELIVERY-SQL-01 · 只读提交结果查询使用不存在的数据库列

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：SAVE-01；s.thread_id / current_thread_id。

- 已有证据：历史运行版本 delivery-status 查询 SQL 错列导致 GET500；本地真实路由/PG修复已验收，9/24修复版本已部署。
- 证据边界：代码已发布不等于历史结果未知写已恢复；不能据此重发未知 POST。它不自动解释 seq5 首次失败。
- 下一动作/关闭要求：在新场验证只读查询和响应丢失恢复；历史任务仍保持独立处置边界。
- 材料：[历史链路复盘](../outputs/2026-09-22-live-full-retrospective/2026-09-22-DreamJourney-Live全链路历史复盘与根因证据报告.md) · [W1设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [本地验收](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md) · [已发布版本](../outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md)

<a id="dj-admission-epoch-01"></a>
### DJ-ADMISSION-EPOCH-01 · admission 读取错误权限 epoch 导致500

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：SAVE-02；context.authority_epoch；CAP-15。

- 已有证据：自动短场 end/ACK 后 admission 的权限字段访问错误已修，保留真实 prepared authority 绑定与事务；本地默认链通过并已随9/24版本部署。
- 证据边界：不能填默认epoch或绕过校验修复；旧场无候选不直接证明最新源码仍有此错误。
- 下一动作/关闭要求：新场同版本核对 admission回执、默认Worker接手和候选实际可见，不单看HTTP201。
- 材料：[自动短场证据](../outputs/2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md) · [W1设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [本地验收](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md) · [部署锚点](../outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md)

<a id="dj-live-read-obs-01"></a>
### DJ-LIVE-READ-OBS-01 · 状态读取期限、迟到结果和旧 envelope 处理

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：N1a/N1b；W2/I10/U04。

- 已有证据：本地同断言逆N1行为副本红×3/当前绿×3，覆盖179→181秒在途响应和匹配item但旧envelope继续观察；final08再次通过。
- 证据边界：红测是局部旧行为重建，不是完整历史版本；没有延长GET预算或提前显示保存成功。
- 下一动作/关闭要求：保持持久化回调至UI完成屏障；当前进程、迟到状态、离开重进和冷启同场验收。
- 材料：[统一设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [红绿与最终证据](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md) · [断言矩阵](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-first-error-01"></a>
### DJ-LIVE-FIRST-ERROR-01 · 首次网络错误被包装遮蔽或诊断落盘不完整

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：N3 / D01–D04；AFError -1001/-1005。

- 已有证据：真实客户端组合已保留首个HTTP/系统错误、曝光状态和原command；网络失败与诊断磁盘不可写组合也已本地验收。
- 证据边界：补诊断不会倒推出缺失的历史首因；诊断失败不允许丢正文或重置业务状态。
- 下一动作/关闭要求：后续现场优先读取持久首错；对新故障保留第一次证据，而非被最后一次错误覆盖。
- 材料：[设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [组合验收](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-worker-01"></a>
### DJ-LIVE-WORKER-01 · 会中预整理和 admission 到默认 Worker 的真实装配缺口

**状态：本地已修待现场｜类型：业务与验收缺口｜P1**\
历史编号/关联：N2/N5；default lifespan；preorganization时基。

- 已有证据：已以独立正常 API lifespan、官方 Worker CLI、真实Store及隔离PG运行；final08两长场在转发end前分别观察到13/18个私有WorkUnit完成。
- 证据边界：不是所有页都在用户停止前完成的证明，不新增此指标；手动调用预整理器或注入成品extractor不能替代此证据。
- 下一动作/关闭要求：保护默认配置/启动链，后续核对真实部署会中处理与会后统一发布，失败不越预算。
- 材料：[统一架构设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [最终真实装配证据](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-b7-01"></a>
### DJ-B7-01 · 纯问题或助手回答误成为待确认事实

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：B7-QUESTION-AS-CANDIDATE-01；B7语义过滤；未审核素材隔离。

- 已有证据：后续本地语义回归覆盖纯问题不生成、混合表达只保留事实、助手诱导不升级；最近找到的专项真机仍为未完整封存导致 BLOCKED。
- 证据边界：不能把“没生成候选”当noChange通过，也不能把旧FAIL写成当前仍确定误生成。
- 下一动作/关闭要求：专项真机以完整封存/Worker执行/明确noChange及事实对照结案；长场修复不得回退此过滤。
- 材料：[问题设计](记忆系统/候选与正式记忆/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md) · [专项真机边界](../outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-B7-B8-Live真机复测阶段汇总.md) · [后续本地回归](../outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/checklists/R01-R08-CAP-KEEP执行清单.md)

<a id="dj-b8-01"></a>
### DJ-B8-01 · 文字会话停止后的 ACK/admission 交接及状态读取

**状态：本地已修待现场｜类型：产品缺陷｜P1**\
历史编号/关联：B8；DJ-B8-DEVICE-01；B8-S01；notObserved。

- 已有证据：文字入口交接和提交后状态读取本地已修；9/16修复报告明确新版本iPhone专项未跑，后续共享Live链不能自动代替文字入口验收。
- 证据边界：文字入口的专项真机闭环证据缺口与当前Live保存是关联但独立的验收范围。
- 下一动作/关闭要求：后续单独做键盘两轮补充→停止→候选→审核→正式读取，保护共享链的同场身份。
- 材料：[交接设计](记忆系统/候选与正式记忆/2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导.md) · [状态读取设计](记忆系统/候选与正式记忆/2026-09-16-Astra-01补充-B8提交后状态读取修复指导.md) · [本地报告](../outputs/2026-09-16-dreamjourney-b8-submitted-status-read-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-B8-S01-本地修复报告.md)

<a id="dj-qa-chain-01"></a>
### DJ-QA-CHAIN-01 · 模拟验收未覆盖真实默认链、身份和短场门禁

**状态：本地专项已关闭｜类型：验收工具｜P1**\
历史编号/关联：SIM/GATE/IDEMP；A3/B1/B3/C1/C2；N2/N6。

- 已有证据：最新final08使用真实客户端/磁盘/HTTP/默认API+Worker/隔离PG，按short-A→logical20→short-B→logical65，验证候选、审核、正式记忆及重建；精确证据负例与短场凭证失效检查有对应证据。
- 证据边界：关闭的是已列本地验收缺口，不把逻辑时钟、受控模型和模拟SDK当真实20/65分钟或第三方通过。
- 下一动作/关闭要求：持续作为受影响修改的门禁；实际依赖变更后旧short receipt失效，重新冻结版本并跑受影响场景。
- 材料：[逐轮模拟设计](测试与验收/2026-09-21-DreamJourney-Live逐轮模拟与待确认记忆全链验收补充设计.md) · [最新强化设计](记忆系统/统一保存修复/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md) · [最终验收](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-qa-pool-01"></a>
### DJ-QA-POOL-01 · 全量测试的未启动 PostgreSQL fixture 导致 PoolClosed

**状态：本地专项已关闭｜类型：测试装配｜P2**\
历史编号/关联：46条记录 / 44个方法；不是46个产品bug。

- 已有证据：旧/当前源码在错误未开启PG fixture下都复现；按仓库要求用memory合同fixture后，两者44方法及相关180项均通过。
- 证据边界：这是装配归因与正确环境对照，不是证明PG事务，也未宣称无筛选2675项全绿；原错误运行保留。
- 下一动作/关闭要求：后续明确memory合同套件与真实PG套件的启动/清理入口，防止把错误环境重复计为新保存回归。
- 材料：[逐项方法对照](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/per-method-matrix.md) · [修正装配结果](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/B-backend-closure-report.md)

<a id="dj-qa-async-01"></a>
### DJ-QA-ASYNC-01 · 测试结束条件早于实际在途读取完成

**状态：本地专项已关闭｜类型：测试装配｜P2**\
历史编号/关联：final07 short-B 3 GET；DEBUG hasActiveReadForTesting。

- 已有证据：final07原失败保留；final08增加实际在途读取完成观察，detach后等请求完成再计数；错command/thread/session各1 GET、0 POST通过。
- 证据边界：未放宽原<=2 GET断言，未改业务恢复策略；测试隔离失败不伪造成产品红绿。
- 下一动作/关闭要求：后续异步验收继续等真实持久化/请求完成信号，不用固定延时或单看UI状态。
- 材料：[原失败与修后证据](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)

<a id="dj-b1-01"></a>
### DJ-B1-01 · Live 未采用已经确认的正式记忆

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：B1；dialog.system_role上下文合同。

- 已有证据：9/11正式事实专项通过；9/14和9/15新Live保持采用正式事实，后续本地回归持续保护。
- 证据边界：历史通过不能自动等于9/24新版本现场通过；但不能继续把该旧缺陷列成当前确定未修。
- 下一动作/关闭要求：新版本保存闭环验收后，用新正式事实做后续Live回查，作为保持性验收。
- 材料：[设计](记忆系统/候选与正式记忆/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md) · [历史正式验收](../outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md)

<a id="dj-b4-01"></a>
### DJ-B4-01 · 候选审核、更正预览、未决恢复及正式写入链

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：B4/B4-8；更正双重绑定；连续审核；原始判等与显示精度。

- 已有证据：9/14后续总报告确认更正预览、二次确认、正式写入、投影、向量、文字和新Live回查及会后候选联合验收通过；覆盖早先B4整体FAIL。
- 证据边界：这些子项保留各自设计文档，不把多轮报告当多个相同bug；当前新增读取停摆另立问题。
- 下一动作/关闭要求：在新场候选审核时保持预览/确认绑定、未决写只读恢复和正式记忆准确回查。
- 材料：[更正预览设计](记忆系统/候选与正式记忆/2026-09-13-Astra-B4更正预览200后失败与双重绑定修复设计.md) · [恢复设计](记忆系统/候选与正式记忆/2026-09-13-Sol-B4关联组未决结果重新进入恢复修复手册.md) · [纠正旧FAIL的总验收](../outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md) · [其他B4材料](问题材料索引.md)

<a id="dj-fm-policy-01"></a>
### DJ-FM-POLICY-01 · 正式记忆只读策略过期后无法恢复

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：FM-POLICY-01。

- 已有证据：9/14真实策略过期后的同读取意图有界恢复、列表/搜索/详情及UI提交通过。
- 证据边界：9/24 readDeadlineExceeded误分类与API停摆是新事件，不回写为本项已再次证实回归。
- 下一动作/关闭要求：修超时分类时回归真正策略过期、明确deny、账号切换及只读零业务写。
- 材料：[设计](记忆系统/候选与正式记忆/2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计.md) · [历史验收](../outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md)

<a id="dj-b6-01"></a>
### DJ-B6-01 · 整理中退出后冷启动无法只读恢复

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：ISSUE-B6-01；B6；checkpoint/follow-up。

- 已有证据：9/14新进程只读恢复pendingReview、唯一候选、零end/ACK/admit重放和新Live隔离通过；后续本地增加错绑定和晚回调覆盖。
- 证据边界：磁盘恢复坐标可读不证明当前进程即时交接完好；当前页面仲裁另见DJ-ECHO-SCENE-01。
- 下一动作/关闭要求：后续短场闭环继续冷启核对同场、去重与零未知业务写。
- 材料：[设计](记忆系统/候选与正式记忆/2026-09-14-Astra-B6会后任务冷启动只读恢复修复设计.md) · [历史验收](../outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md) · [最新保持性](../outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md)

<a id="dj-live-audio-01"></a>
### DJ-LIVE-AUDIO-01 · 长回答被截断、插话后未恢复聆听

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：9/15 长回答自然结束与主动打断。

- 已有证据：9/15约一分钟自然朗读完整、主动插话即时停音、回答新问题并恢复聆听均通过；后续音频保持回归继续通过。
- 证据边界：一次长回答不是物理20/65分钟稳定性证据，也不替代静音自动STREAM路径专项验证。
- 下一动作/关闭要求：保留音频路径，涉及SDK/生命周期依赖才针对性回归；最后用户主动安排麦克风和扬声器效果验收。
- 材料：[修复设计](Live语音与开麦/2026-09-15-Astra-Live长回答朗读中断修复设计.md) · [真机证据](../outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-Live长回答手机端真机验收记录.md)

<a id="dj-live-auth-domain-01"></a>
### DJ-LIVE-AUTH-DOMAIN-01 · 本地lease与服务端账号代次混比阻断保存

**状态：历史真机已通过｜类型：产品缺陷｜P1**\
历史编号/关联：9/17 持久化授权身份域；与401恢复分开。

- 已有证据：9/17同场正文→end→ACK→admit→pendingReview及唯一候选、冷启无重复取得DEVICE_PASS；确认身份域误比较已修。
- 证据边界：此短场通过不能证明后续长场认证续期、模型整理或9/24发布完整通过。
- 下一动作/关闭要求：保持身份域分离，新版短场门禁验证真实授权绑定，避免与另一类401恢复混改。
- 材料：[设计](记忆系统/采集与会后保存/2026-09-17-Astra-Live持久化回归溯源与真实保存链修复设计.md) · [本地报告](../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live持久化授权身份域局部修复报告.md) · [真机通过](../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md)

<a id="dj-live-partial-01"></a>
### DJ-LIVE-PARTIAL-01 · 覆盖摘要刷新和停止于半句话时的partial分支

**状态：历史真机已通过｜类型：产品修复与正确分支｜P2**\
历史编号/关联：nonASR coverage；D1-01–D1-06。

- 已有证据：9/17半句话停止现场partial文案稳定、正文已落盘、无报错，应记PASS；此前真实Controller红绿保留。
- 证据边界：肉眼未见“无正文→partial”瞬时切换为NOT_OBSERVED，不是新缺陷。正常完整场saving异常单独登记。
- 下一动作/关闭要求：保护覆盖摘要及partial语义；若要验瞬时刷新用受控Controller，不要求现场必须看见极短过渡。
- 材料：[本地补充报告](../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/reports/2026-09-17-DreamJourney-Live覆盖摘要UI刷新-补充修复报告.md) · [正确partial现场](../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/evidence/device/2026-09-17-minimal-ui-refresh-observation.md)

<a id="dj-live-entry-01"></a>
### DJ-LIVE-ENTRY-01 · 结束文字会话后Live入口不可用及令牌503

**状态：本地已修待现场｜类型：产品修复｜P2**\
历史编号/关联：9/10入口生命周期；formalMemorySnapshotUnavailable。

- 已有证据：9/10分别修复入口idle交接、快照Projection不可用；当时本地/部署检查通过并明确专项真机待确认。后续Live能启动是保持性旁证。
- 证据边界：尚未将后续普通Live启动等同于原“文字结束后立即转Live”的逐项专项验收；不宣称旧503当前仍存在。
- 下一动作/关闭要求：未来安排文字入口专项时顺带核对文字停止后麦克风可用和正式记忆快照，不重做已完成维护。
- 材料：[设计](Live语音与开麦/2026-09-10-Sol-Live入口与令牌503修复指导.md) · [原修复报告](../outputs/2026-09-10-dreamjourney-live-entry-token-fix/2026-09-10-DreamJourney-Live入口与令牌503修复交付报告.md)

<a id="dj-accept-provider-01"></a>
### DJ-ACCEPT-PROVIDER-01 · 当前完整模型合同的真实Provider验收

**状态：验收待办｜类型：外部验收｜P1**\
历史编号/关联：当前版本 REAL_PROVIDER_NOT_RUN；非缺陷结论。

- 已有证据：历史有供应商诊断和新豆包账号直连通过；当前最终整套保存/分批/语义合同仍没有对应真实模型完成证据。
- 证据边界：供应商直连、iOS SDK/票据代理、DeepSeek整理及正式记忆入库是不同层；不能据直连PASS把全部打勾，也不预设供应商有故障。
- 下一动作/关闭要求：用户安排下一阶段时按当前版本、合成数据和明确费用预算检验请求大小、用时、finish_reason、语义和重试台账。
- 材料：[历史火山直连](../outputs/2026-09-21-volc-live-new-account-provider-test/2026-09-21-新豆包账号Live接口实测报告.md) · [供应商与内部边界](记忆系统/长对话整理/2026-09-19-Astra-Live长场失败-供应商限制与内部容量核查.md) · [当前发布边界](../outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md)
- 9/29 run-02有实际模型交互：iPhone SDK完成一轮真实ASR及回复；本场整理run published、provider_request_count=3、recovery=0，pending候选2条。不是整轮Provider NOT_RUN，但完整语义合同、长场容量和正式回查仍未验收。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。


<a id="dj-accept-device-01"></a>
### DJ-ACCEPT-DEVICE-01 · 新版本短场及物理20/65分钟完整保存验收

**状态：验收待办｜类型：现场验收｜P1**\
历史编号/关联：短场→20分钟→独立短场→65分钟；每次长场前短场门禁。

- 已有证据：当前本地final08已通过，后端8141ff2/schema0124已发布，手机已安装启动；新的短场候选审核正式闭环及物理20/65分钟尚未完成验收。
- 证据边界：历史短场确有DEVICE_PASS；本次核对范围内未找到物理长场“候选→审核→正式→回查”完整PASS。不把逻辑20/65分钟当现场时长。
- 下一动作/关闭要求：由用户主动发起；先确认读取稳定，再新短场两轮补充→候选可见→审核→正式回查；短场失败即停止长场，不因无手机阻断本地任务。
- 材料：[本地同版四场](../outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md) · [当前部署和安装](../outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md) · [后续操作顺序](../outputs/2026-09-23-live-save-unified-repair/run-03/release-and-retest-runbook.md)
- 9/29 run-02最新：签名包安装及后端修复版本核对完成；首轮真实输入后短场因原生播放完成缺失FAIL。停止后同Source两条pending候选落库；候选列表实际可见性、审核、正式记忆和冷启动读取均未验收；物理20/65分钟NOT_RUN。未发第二轮，未产生短场通行凭据。见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md)。


- 9/29最新现场验收：SHORT/SHORT02均PASS；同版20分钟目标长场FAIL（实际16分57.7秒、候选0）；65分钟NOT_RUN。见[最新报告](../outputs/2026-09-29-live-playback-device-retest/run-01/README.md)。不把预整理atom算待确认记忆。

<a id="dj-live-long-20260929"></a>
### DJ-LIVE-LONG-20260929 · 长场尾部保存与停止收尾未完成

**状态：待核验｜类型：现场故障，首因未定｜P1**

- 9/29最终同版独立短场已通过；长场16分57.7秒，第33次输入期间因送帧间隔0.523595秒触发audioSchedulerStalled，32次完整语音往返。不是物理20分钟通过。
- 只读核对：服务器保存31轮/62正文；前24轮已预整理成16个内部atom，6次DeepSeek响应全部接受且stop；后7轮未分配单元。无结束批次、Source、最终水位或发布清单，本场候选0，未进入正式记忆。
- 前31轮用户显示哈希与对应正文相符，助手哈希逐轮唯一匹配；第32轮助手无匹配。重复用户文本不能代替回合身份核对。
- 失配在测试停止前已出现；停止后UI关闭但closeIntent/end未持久化。诊断全文件逐事件写入与同步snapshot存在潜在阻塞风险，尚无线程栈/时延证据，不能定为根因。
- 用户要求先讨论再修改。保留手机和服务器本场数据，结束测试进程；未重放、补发或新开场。不得为了验证“能生成”强行写入候选。
- [分析报告](../outputs/2026-09-29-live-playback-device-retest/run-01/README.md) · [逐轮证据](/Users/gaominge/Documents/liftora/outputs/2026-09-29-live-playback-device-retest/run-01/long-turn-to-memory-audit.json) · [服务器只读快照](/Users/gaominge/Documents/liftora/outputs/2026-09-29-live-playback-device-retest/run-01/long-candidate-audit.json)。

- 9/29讨论后已形成[韧性保存与主题归纳设计记录](../02-设计文档/02-架构调整/记忆系统/2026-09-29-Live记忆韧性保存与主题归纳/README.md)：用户指定后续由当前助手实施，当前仅文档；20/40分钟替代本轮65分钟，主题归纳、部分发布与编辑草稿保护明确。尚未修改/测试，现场状态不升级。

- 9/30实施补充：新协议已实现会中私有事实/主题草稿、有限补缺、无end部分发布、主题归纳及编辑保护，受控本地LOCAL_PASS；最终四场为短A→逻辑20→短B→逻辑40。新能力默认关闭，未部署/调用真实Provider/连接手机。历史第32轮失配与停止意图未保存的唯一原因仍未定，条目继续待核验，未关闭现场故障。[实施报告与证据](../outputs/2026-09-29-live-resilient-memory-development/run-01/README.md)。

- 9/30真机补充：同版短场正式记忆冷回查PASS；长场36轮后送帧调度超限，随后72条手机/服务器消息按ID、角色和正文哈希全部匹配，停止意图也已落盘。但恢复快照两次noReliableThemes失败，仍无主题发布。正文保存改善不能记为记忆链修复完成。保留手机Outbox、SDK环形诊断、逐轮proof与服务器任务错误；具体根因待分析讨论。[本场记录](../outputs/2026-09-30-live-resilient-device/run-01/现场问题与证据记录.md)。

- 9/30原因分析补充：本场关系模型输出8项自引用，生产批内解析离线复现forwardBatchTarget，但对应unit此前已completed；继而3次themeSafety请求被拒绝。精确请求重建哈希一致，确认启用json_object却未在提示词包含json，违反DeepSeek公开要求；原始HTTP状态/拒绝正文缺失，不能伪称已观测特定400。最终noReliableThemes是上层结果。手机整文件写入/诊断与主线程调度仍为性能嫌疑，未定唯一首因。[完整分析](../outputs/2026-09-30-live-resilient-device/run-01/analysis/原因分析.md)。用户要求讨论后再设计修改，状态不升级为已修。

## 建档与状态变更记录

9/30 v1.12设计补充：DJ-LIVE-LONG-20260929已关联[模型合同与异常收尾补充修复方案](记忆系统/2026-09-30-长场模型合同与异常收尾/2026-09-30-长场模型合同与异常收尾补充修复方案.md)，包含完整验证前置、非法缓存拒用、Provider合同、保存性能取证，以及用户新增的“出错后停止新增输入但继续验证收尾”。仅设计归档，未修改产品或新增外部操作；40条登记及状态数量不变，现场问题未关闭。

| 日期 | 变更 | 依据/边界 |
|---|---|---|
| 2026-09-28 | 复核开麦run-01仍为LOCAL_INCOMPLETE；登记归类统一为“待修复”，修正详情与统计不一致 | 独立读取xcresult/指纹；定位R1–R5及保存链跳过证据；仅新增复核文档和纯函数fake依赖探针，无产品修改/外部验收 |
| 2026-09-28 | DJ-LIVE-START run-02 仍为待修复/LOCAL_INCOMPLETE | R1–R5局部代码和MIC-01完整Controller红绿；隔离PG/D5、同版四场通过，完整iOS束一次偶发失败后重跑全绿；原生Manager运行时与MIC-19等未闭环，真实Provider/部署/真机NOT_RUN，详见run-02交付 |
| 2026-09-28 | 独立核验DJ-LIVE-START run-02，保留待修复/LOCAL_INCOMPLETE | 核实四场与1,297依赖/312产物；定位二次排队回调归属缺口、MIC-11授权代次失配近因、默认诊断输出和共享Handler风险；新增复核/纯日志探针，未改产品或连接外部 |
| 2026-09-28 | 独立核验DJ-LIVE-START run-03，保留待修复/LOCAL_INCOMPLETE | 认可新红绿、770/0/3、同版四场与基线对照；发现生产提交忽略markStartSubmitted=false的具体遗漏，以离线源码片段探针验证；更新精确剩余断言，未改产品或连接外部 |
| 2026-09-24 | 首次建立统一登记册；迁入已修、待修、未决、观察及验收记录 | 不是重新执行业务验收；本次仅核对文档与必要代码证据 |
| 2026-09-24 | 将run03旧LOCAL_INCOMPLETE更新为适用本地门槛LOCAL_PASS | final08收尾证据；保留基线例外、未过滤套件非全绿及历史seq5未决 |
| 2026-09-24 | 更新为后端已发布、手机已安装；新增独立读取停摆及子问题 | 不沿用旧“未部署”，不把重启恢复记作根因修复 |
| 2026-09-24 | 分离历史DEVICE_PASS、当前本地通过与当前现场待验收 | 未再次运行测试、调用Provider、连接手机或访问生产 |
| 2026-09-27 | 增补 DJ-LIVE-START-20260924：待修复，登记册37→38条；关联A/B/C分析与Sol设计 | 只读核查代码和历史报告；未改产品或运行测试。旧权限发前失败路径已定位，历史两次观察的请求级因果仍未闭合；其他条目未重新现场验收 |
| 2026-09-27 | DJ-LIVE-START-20260924 待修复→本地局部修复、验收未完成 | 本轮 run-01：当前策略、15秒启动预算、取消与诊断局部实现；MIC-04/06/13 同断言红绿及最终回归。隔离 PG 与四场未通过门禁，真实 Provider/部署/真机未运行，历史两次现场因果仍未闭合 |

| 2026-09-29 | DJ-LIVE-START-20260924 本地验收完成→本地已修待现场；待修复5→4、本地已修待现场16→17，总数38不变 | Astra接手R2—R4；39专项、774/0/3及最终四场通过，登记册已链接唯一最新入口；历史首因与外部阶段未关闭 |

后续每次阶段交付都在此追加“旧状态→新状态、版本/构建、证据链接、仍未关闭的断言”；正文详情同步更新。若相同表现对应新根因，新增关联编号，不抹掉旧修复或篡改原报告。


| 2026-09-29 | API池阻塞专项修复：READ-STALL/LIVENESS转本地已修待现场，待修复4→2、本地已修17→19，总数38不变 | 默认API+真实PG同断言红绿、134回归及最终四场通过；取消checkout反例修复；见[报告](../outputs/2026-09-29-api-pool-stall-repair/run-01/implementation-report.md)。历史seq5与9/24首因未合并关闭；真实Provider/手机仍独立验收 |

| 2026-09-29 | API池阻塞修复已部署，仍保留本地已修待现场 | 发布 api-pool-isolation-20260929，949运行文件匹配，六Worker对齐，内网/公网只读检查通过；实际部署镜像20项隔离测试通过；手机未连接，真机与真实Provider NOT_RUN，未关闭现场验收 |

| 2026-09-29 | v1.6追加实际安装与短场run-02；各登记状态及38条总数不变 | 修复后API健康/票据正常，真实首轮保存及2条候选落库；播放完成门禁FAIL，候选UI/审核/正式回查与20/65分钟未执行。未把部分进展升级为整体现场通过；见[9/29安装与真机run-02](../outputs/2026-09-29-dreamjourney-live-device-acceptance/run-02/device-report.md) |

- 9/29本轮补充：实际三场候选4/3/4条，确认HTTP400尚未完成正式闭环；本地修复完成，最终四场逻辑链通过，但最新包仍待现场。用户允许手机断开，后续先独立短场，绝不继承旧短场凭证。见[最新交付](../outputs/2026-09-29-live-playback-repair/run-01/README.md)。

| 2026-09-29 | v1.8：V5确认转历史真机已通过；新增DJ-LIVE-LONG-20260929待核验，总数40 | 两短场正式冷回查通过，长场31轮保存/24轮预整理/候选0；先分析讨论、不再修改或重测。[报告](../outputs/2026-09-29-live-playback-device-retest/run-01/README.md) |

| 2026-09-29 | v1.9关联新设计；40条登记及状态数量不变 | [设计记录](../02-设计文档/02-架构调整/记忆系统/2026-09-29-Live记忆韧性保存与主题归纳/README.md)仅归档讨论，未实施/未验证，不把设计完成当问题关闭 |

| 2026-09-30 | v1.10：追加韧性保存/主题归纳受控本地实现；40条及现场状态数量不变 | [开发交付](../outputs/2026-09-29-live-resilient-memory-development/run-01/README.md)；实际短A→20→短B→40本地链、无end扫描、预算期限、并发确认等通过，真实Provider/真机/本轮部署NOT_RUN；历史首因未决 |

## 2026-09-30 v1.13：模型合同与异常收尾本地开发

关联DJ-LIVE-LONG-20260929、DJ-LIVE-AUTOLAB-01、DJ-WORKER-BUDGET-01；40条问题及现场状态数量不变，未关闭历史首因。

- [实施及逐项证据](../outputs/2026-09-30-live-model-contract-recovery-repair/run-01/README.md)：非法关系完成前验证、非法缓存拒用、JSON合同及稳定序列化、原预算/并发/迟到尝试隔离、保存测量/诊断采样、工具错误后有界观察已实施。
- 后端受影响150/150、隔离PG矩阵及短A→逻辑20→短B→逻辑40通过；最终iOS628通过、1失败、3跳过。
- **LOCAL_INCOMPLETE**：密集5760事件队列增长断言失败，末深度17、最大等待1094.90ms；72正文和停止manifest保留、1261.58ms排空，不抵消实时性失败。另有C01/E01/E05/E06本地证据缺口。
- [下一步存储设计与测量](../outputs/2026-09-30-live-model-contract-recovery-repair/run-01/保存负载测量与下一步设计.md)尚未实施。未放宽门槛或增加预算，未调用真实Provider、部署、操作手机、生产或历史，未commit/push。历史seq5、0.542664秒停顿首因及旧拒绝确切HTTP状态仍未决。

## 2026-09-30 v1.14：有界批提交与异常后收尾续修

关联DJ-LIVE-LONG-20260929、DJ-LIVE-AUTOLAB-01；40条及现场状态数量不变，现场故障未关闭。

- [run-02交付与完整证据](../outputs/2026-09-30-live-model-contract-recovery-repair/run-02/README.md)：已实施最多32项有界批提交、同路径Store互斥、实际读回比对、原失败批恢复；保留128项/4MB和原重试/性能标准。
- 最终iOS 635通过、0失败、3旧协议入口跳过；同版独立short-A→logical20→short-B→logical40通过；工具实际失败→公开停止→候选详情确认→新进程正式回查通过，原CONVERSATION_FAIL保留。
- 相同5760事件/60ms专项：末深度3、最大排队87.69ms、停止203.45ms排空；此前17/1094.90ms/1261.58ms失败证据保留。整文件写放大仍存在，不解释历史真机停顿唯一首因。
- 严格总状态仍为LOCAL_INCOMPLETE：C01实际SDK分布与持续音频送帧/保存/上传ACK同场证据未闭合。已补短场共同时间线，但不拼接独立专项冒称全部通过。真实Provider、部署、手机及物理20/40分钟NOT_RUN。
- 保留批写回归旧计数失败、R202无界瞬时注入触发容量保护，以及工具窗口/生命周期装配失败；修正测试装配的理由和原断言保留范围见实施报告。历史seq5、旧拒绝确切HTTP、0.542664秒停顿原因仍未决。

## 2026-09-30 v1.15：真机前真实Provider关系合同失败

关联DJ-ACCEPT-PROVIDER-01及DJ-LIVE-LONG-20260929；40条与分类计数不变，未关闭任何历史问题。

- [本次预检及原始证据](../outputs/2026-09-30-live-model-contract-device/run-01/README.md)：新签名包已构建，设备已识别；真实DeepSeek合成请求共6次，组织/复核/安全通过，关系分支失败。补齐生产theme字段后仍返回replaces=[]，而校验要求对象{}。不是本次容量/超时证据，也不归咎火山或麦克风。
- 原始探针缺theme与产品合同失败分开保留；没有篡改失败或放宽校验。后续独立关系复核未执行。
- 新release/镜像仅准备；线上仍live-resilient-20260930且ready。未安装、未启动手机Live、未做物理20/40、未改产品代码或历史业务数据。当前PROVIDER_PREFLIGHT_FAIL / DEVICE_NOT_RUN；不把新构建称为真机修复通过。

## 2026-09-30 v1.16：关系输出合同局部修复

关联DJ-ACCEPT-PROVIDER-01及DJ-LIVE-LONG-20260929，40条与分类计数不变。

- [修复及证据](../outputs/2026-09-30-live-theme-relation-contract-fix/run-01/README.md)：仅强化关系输出类型与五种示例；严格校验、Worker、保存链及预算不变。63项相关回归PASS。
- 此前失败的同一完整合成material逐字段匹配；真实DeepSeek修后返回replaces={}，关系与独立复核PASS（2次请求）。不将单例推断为所有现场问题关闭。
- 新后端镜像已准备但未激活，未新安装。额外4次真实验证被自动审批按历史8次额度拒绝，已请求用户明确授权继续真实流程；不是新增产品FAIL。短场/20/40真机NOT_RUN，历史首因未决。

<a id="dj-live-independent-theme-20260930"></a>
### DJ-LIVE-INDEPENDENT-THEME-20260930 · 独立新主题被合并关系门禁误拦截

**状态：本地已修待现场｜类型：产品缺陷｜P1**

- 现场：9/30本次STAGE01短场两轮4正文全部持久化、上传确认及停止位置完整；候选主题0，作业terminalFailed，错误themeValidate.noReliableThemes。
- 已确定直接链：themeRelation=none；独立复核supported，sameSubjectEvent=false、correctionsResolved=false，当前validate_relation却对none同样要求四项true，返回None；relate_themes标记themeRelationUnresolved，唯一主题被拦。真实阶段记录及同语义本地业务红已保存。
- 覆盖缺口：本地四场controlled_relations对所有关系固定四项true，漏掉独立主题的合理false组合；不是新的容量或超时证据。
- 用户授权后已按none/关联关系区分语义，保留严格类型、绑定、证据、不确定保护和原预算。相同业务红修后绿，72项受影响回归、隔离PG跨场及短A→logical20→短B→logical40通过。新主题候选可见并确认成正式记忆，旧主题未覆盖。
- 下一步：本版本真实Provider对照及后续授权部署/真机复验；本轮REAL_PROVIDER/DEPLOY/DEVICE/HISTORICAL_REPROCESS均NOT_RUN。历史失败任务及长场门禁失败记录保留，不用本地结果覆盖。
- [修复方案](记忆系统/2026-09-30-独立主题误拦截/修复方案.md) · [实施与验证记录](记忆系统/2026-09-30-独立主题误拦截/实施与验证记录.md)。
- 材料：[现场与直接原因](../outputs/2026-09-30-live-model-contract-device/run-01/真机短场失败与直接原因.md)。

## 2026-09-30 v1.17：真实修复验证、部署与短场失败

- 用户明确允许继续模型验证及真机流程；格式专项真实重复/补充/更正判断和复核通过。后端live-contract-recovery-v2-20260930已部署，API及六Worker均965文件匹配，schema0128 ready；STAGE01实际安装。
- 短场4/4正文已保存，但新的独立主题语义缺陷导致候选发布FAIL；增加上方产品条目，总数40→41，待修复2→3。其余状态计数不变，历史首因未关闭。
- 已完成失败后产品状态核对、延迟只读复查与本地业务红，未改该新问题代码或重放业务，物理20/40及短场正式记忆确认NOT_RUN。[完整记录](../outputs/2026-09-30-live-model-contract-device/run-01/真机短场失败与直接原因.md)。

## 2026-09-30 v1.18：归类与独立主题本地修复

- 主登记册及77份问题管理/修复材料归入02-问题修复，8份架构材料归入设计/架构调整，旧路径保留兼容跳转，outputs证据原位。
- 独立主题误拦截由待修复转为本地已修待现场；同断言红绿、72项相关回归、隔离PG及最终四场通过。只有本专项LOCAL_PASS。
- 新提示词真实模型表现、部署与真机均未验；没有重放历史，没有关闭seq5首因或其他长期性能未决问题。

<a id="dj-live-relation-review-20260930"></a>
### DJ-LIVE-RELATION-REVIEW-20260930 · 关联复核适用条件与纠正摘要

- 9/30本版已部署，新短场第一轮收尾后发布1条独立主题/2条事实，无旧主题关联。本场尚未确认到正式记忆，整体因语音/工具验收失败，20/40分钟未跑；状态不升级。 [本轮证据](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/本版部署与真机短场复测.md)。

**状态：本地已修待现场｜类型：真实模型合同缺陷｜P1**

- 首次确认：2026-09-30，独立主题修复的发布前8次真实DeepSeek合成对照；关联DJ-LIVE-INDEPENDENT-THEME-20260930。不是新真机FAIL，手机未启动。
- 重复/补充关系均得到supported，但无纠正操作时correctionsResolved=false，当前门禁返回None；对重复的同一返回，旧/新校验器均拒绝，不能称为本次新校验回归或已完成提示词因果归因。
- 更正关系映射通过，但摘要同时保留被否定的旧事实，summarySupported=false/unsupported；拒绝应保留，不能放宽摘要支持保护。
- 8次均正常finish_reason=stop，没有本组容量/截断/超时证据。none单例PASS保留，不外推全部关系和真实记忆闭环。
- 下一步：讨论并明确不适用条件及有效纠正摘要的合同，补同材料反例、真实对照；未修改本轮产品代码或使用额外模型额度。
- 状态：PROVIDER_GATE_FAIL；DEPLOY_PREPARED_NOT_ACTIVATED；DEVICE短/20/40及历史处理NOT_RUN，未commit/push。
- [分析与证据](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/问题分析与验证记录.md)。

- 9/30用户授权修复并要求开发时调用真实模型后：本地78项、隔离PG9组、短A→logical20→短B→logical40通过；新一组真实DeepSeek四类关系8次全部通过，0重试。错误更正摘要拒绝保护保留。
- [修复方案](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/修复方案.md) · [实施与验证记录](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/实施与验证记录.md)。本轮部署完成；上述PROVIDER_GATE_FAIL为修前原始状态。本轮新真机短A在自动确认保护处FAIL：正文及主题均已落地，但模型将与旧合成读书角相似的输入视作correction；未自动修改旧正式记忆，20/40分钟NOT_RUN。详见[本次真机分析](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/真机短场关联保护记录.md)。

## 2026-09-30 v1.19：发布前真实模型门禁失败

保留本地专项PASS，但新关联合同问题未通过真实模型门禁。新发布构建已准备；原线上版本live-contract-recovery-v2-20260930及健康状态确认不变。未将准备状态计为DEPLOY_PASS，未安装或开启新Live。问题总数41→42，待修复2→3，其余计数不变。

## 2026-09-30 v1.22：更正意图与独立测试场景

DJ-LIVE-RELATION-REVIEW-20260930继续本地已修待现场。对上一轮真机失败明确区分：保存和待确认发布成功，自动确认因关联旧正式主题被保护拦截；没有回写历史。新增本场原话意图绑定和终态主题显示文本绑定，测试独立场所语义修正，相关验证通过。当前源码未发布，旧stage因工具指纹变化不可复用。见[方案](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/更正意图与独立测试场景修复方案.md)及[结果](记忆系统/2026-09-30-关联复核适用条件与纠正摘要/更正意图修复实施与验收.md)。

<a id="dj-live-lab-recovery-20260930"></a>
### DJ-LIVE-LAB-RECOVERY-20260930 · 真机自动验收中断与恢复核对

- 最新run-02：新短场两轮、四条正文、主题确认与新进程正式冷读全部PASS；随后长场正文保存146条，恢复发布因新产品容量缺陷FAIL，未进入确认。旧run-01结果不改写。[本轮入口](../outputs/2026-09-30-live-lab-recovery-device/run-02/README.md)。以下是先前状态。

- 最新人工裁定：本场星/新差异已被用户接受，不再作为失败项；已执行保存链PASS，完整短场仍待补测，原工具FAIL是旧口径下的历史结果。[验收口径补充](测试与验收/2026-09-30-真机自动验收中断与恢复核对/真机验收口径补充-同音专名.md)。

**状态：本地已修待现场｜类型：测试工具缺陷｜P1**

- 首次确认：2026-09-30，已发布更正意图版本的短场A。关联DJ-LIVE-AUTOLAB-01、DJ-ASR-OBS-01和DJ-LIVE-RELATION-REVIEW-20260930。
- 已证实：完成验收回合0不等于已保存消息0；实际2条正文完整收录并发布1主题/2事实，主机completedTurns×2却要求0，来源证明无法继续。没有进入业务确认POST。
- 静态后续缺口：已final且保存、但整轮未通过的ASR不进入acceptedASRByTurn；恢复事实筛选可能为空。本次现场未走到该断言，实施前须同断言复现。
- 本地测试装配绕过：预填accepted正文并直接写binding，没有覆盖真实主机核对；不能用旧本地PASS证明本次条件已覆盖。
- 其他现场因素：合成“星桥”识别成“新桥”；数字和空格已有归一化。输入时钟0.606秒间隔已观测，耗时首因未决，不归为DeepSeek容量问题。
- 下一步：用户主动安排本版工具的真实短场完整闭环，再按短场门禁进行物理20/40分钟；依据新增帧时间线核实历史调度瓶颈。不得以本地PASS替代现场。
- 当前：工具LOCAL_PASS；27项Python、7项实际xcresult（0失败0跳过）、同版四场及通用iOS无签名构建通过。后续真机短场FAIL（星/新）；2条正文、1主题/3候选发布及主机Source核对PASS，正式确认NOT_RUN。最大送帧间隔0.045751秒，历史调度首因继续未决。20/40分钟未启动。
- [最新真机复测记录](测试与验收/2026-09-30-真机自动验收中断与恢复核对/本版真机短场复测记录.md)。
- 关闭条件：真实主机核对的修前红修后绿、最终本地四场、获得后续授权后的独立短场完整闭环；不能用恢复发布成功替代整场PASS。历史调度首因另行保留。
- [完整记录与分析](测试与验收/2026-09-30-真机自动验收中断与恢复核对/问题记录与原因分析.md) · [修复方案与15组验收](测试与验收/2026-09-30-真机自动验收中断与恢复核对/修复方案与验收矩阵.md) · [入口及原始证据](测试与验收/2026-09-30-真机自动验收中断与恢复核对/README.md)。

- [本次实施、实际验收层级与剩余边界](测试与验收/2026-09-30-真机自动验收中断与恢复核对/实施与验证记录.md)。


<a id="dj-live-theme-capacity-20260930"></a>
### DJ-LIVE-THEME-CAPACITY-20260930 · 长场主题关联容量不一致

**状态：本地已修待现场｜类型：产品缺陷｜P0**

- 本场113条内部事实聚合为97、16两主题；关联输入限64且只拆目标，不拆新事实，97条在模型请求前被拒绝，整场发布失败。
- 26份请求/响应哈希匹配缓存只读重建与实际发前守卫复现；无新增模型调用。直接原因已确认，历史其他失败不归并。
- 用户明确列为当前最高优先级；原诊断时尚未修改，现已完成下述本地修复，未重放旧任务。[完整证据](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/问题记录与原因分析.md)。

- 历史短场结果（2026-10-01）：双向分页已部署，API及6个Worker指纹一致；LOCAL_PASS / PROVIDER_SYNTHETIC_PASS / DEPLOY_PASS。iPhone信任后新短场4条正文完整，但主题审核uncertain导致发布FAIL；尚未进入大主题分页，长场NOT_RUN，历史未重处理。现场缺陷尚未关闭。[部署与真机记录](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/2026-10-01部署与真机测试记录.md)。[实施记录](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/实施与验证记录.md)。

<a id="dj-live-audio-scheduler-20260930"></a>
### DJ-LIVE-AUDIO-SCHEDULER-20260930 · 43分钟后音频调度停顿

**状态：待核验｜类型：性能观察，首因待核验｜P3**

- 第73用户轮、43分20秒，最大送帧间隔0.517609秒。工具公开停止后继续观察收尾，正文146条保存，随后另因容量契约发布失败。
- 按用户要求低优先级记录；没有线程耗时栈，不确认具体性能首因，不阻塞优先处理发布缺陷。[完整证据](记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/问题记录与原因分析.md)。

<a id="dj-live-theme-uncertain-20261001"></a>
### DJ-LIVE-THEME-UNCERTAIN-20261001 · 短场主题审核不确定导致未发布

**状态：待核验｜类型：现场故障，语义首因待核验｜P0**

- 新短场2用户轮/4条消息完整，收尾快照创建；真实模型11次正常结束，主题审核返回uncertain/sameSubjectEvent=false，最终correctionsResolved=false，无可靠主题后任务终态失败。
- 两轮核心标记按用户已接受的同音及格式规则校验通过；无当前场超时/容量失败证据，主题关联分页未进入。不能断言模型误判或本次分页引入回归。
- 快照failure_code为空而任务有明确错误，run仍organizing；诊断一致性另需核查。后续已获用户授权并完成本场正文及请求哈希核对；“补充一下”未丢失，实体名称两种写法是线索，审核无理由字段，深层语义触发原因仍未完全证实。
- 本短场结束时未改代码或重试任务，长场当时NOT_RUN；后续用户例外长场结果见DJ-LIVE-CLOSURE-REVIEW-20261001。[完整记录及证据](记忆系统/2026-10-01-短场主题审核不确定导致未发布/问题记录与本次真机结论.md)。

2026-10-01 DJ-LIVE-THEME-UNCERTAIN-20261001 补充：[授权后正文与实际输入核对](记忆系统/2026-10-01-短场主题审核不确定导致未发布/授权后正文与模型输入核对.md)。仅分析记录，状态不变。

2026-10-01用户决定：DJ-LIVE-THEME-UNCERTAIN-20261001先作为审核策略问题登记讨论，不修改代码；本次明确例外继续80轮长场，短场FAIL保留，不伪造门禁PASS。

<a id="dj-live-closure-review-20261001"></a>
### DJ-LIVE-CLOSURE-REVIEW-20261001 · 长场收尾关联审核失败

**状态：待核验｜类型：现场故障，深层首因待核验｜P0**

- 第66轮停止后132条消息全部同步，113条active内部事实存在；主题关联第一次themeRelationReviewBindingMismatch，恢复后noReliableThemes，快照failed、manifest不存在、本场主题0。
- Provider尝试记录409条，406 responseAccepted、3 rejected；110个关联请求与111个关联支持请求，只有一份同stage/requestHash重复两次。具体绑定失配字段和逐主题阻断因果仍待核验，不能仅凭计数下根因结论。
- job=failed有错误码，snapshot.failure_code为空、run仍organizing；手机pending坐标未更新。手机1200秒观察结束仍未更新，工具记录OBSERVATION_TIMEOUT；终态传播与诊断导出capture为空另记证据边界。
- 本轮只读取证及登记，不修改代码、重放任务或确认其他Source。[完整记录](记忆系统/2026-10-01-长场收尾关联审核失败/收尾全过程与失败记录.md)。

2026-10-01 音频P3补充：本场在启动后39分45.361秒、第66轮出现0.518982秒送帧间隔，并非满40分钟后；未取得线程栈，不作首因判断。详见上述长场记录。

2026-10-01 DJ-LIVE-CLOSURE-REVIEW-20261001 深查补充：主题内容已获支持与旧主题关系能否确定被绑成同一发布门槛；分页语境缺失和整主题阻断已复现。仅只读、零新增模型调用，先讨论方案。[分析及证据](记忆系统/2026-10-01-长场收尾关联审核失败/深层原因分析.md)。

2026-10-02 DJ-LIVE-CLOSURE-REVIEW-20261001 实施补充：上述已证实分页机制完成局部修复与本地验收，并加入10分钟自然收尾；观察链/现场尚待核验，不改写原失败。最新v1.34：用户授权后完成分页身份上下文v2、独立可靠子集重新归纳/安全审核、十分钟完成当前轮再告别关闭。后端153项、Controller5项、最终四场及音频6项、PG完整113/局部105事实确认与重建读取通过；本轮限定范围LOCAL_PASS，真实Provider/部署/手机NOT_RUN。原180秒观察与终态一致性等仍未修，原P0条目不关闭，47条及计数不变。[方案及验收](记忆系统/2026-10-01-长场收尾关联审核失败/分页与十分钟收尾实施验证.md)。



<a id="dj-live-review-policy-20261002"></a>

### DJ-LIVE-REVIEW-POLICY-20261002 · 长场后候选读取授权过期

**状态：本地已修待现场｜类型：产品读取恢复缺口｜P1**
首次发现：2026-10-02物理十分钟场；关联DJ-LIVE-CLOSURE-REVIEW-20261001、DJ-FM-POLICY-01。

- 已证：42条正文、4主题36成员已发布；自动路径读取Source前被capturedPolicyExpired拒绝，无确认写。
- 未证：普通用户页面进入/刷新是否同样失败；不能直接归因供应商或整个产品链。
- 本地修复：Source两入口与主题列表接入既有有界读取恢复；主题写入独立capture当前授权，策略拒绝不再误报账号变化。修前业务红、38项回归及隔离四场确认回读通过。
- 下一动作：用户主动发起新版本安装和短场→自然十分钟现场闭环；本轮真实Provider、部署和手机均NOT_RUN。
- Provider/部署/自然关麦/服务端发布通过；本场正式记忆确认NOT_RUN，完整链FAIL；不改历史问题状态。
- [记录与证据](记忆系统/2026-10-02-长场后候选读取授权过期/问题记录与证据.md) · [完整报告](记忆系统/2026-10-01-长场收尾关联审核失败/2026-10-02部署与十分钟真机验收.md)。

- [原因与方案](记忆系统/2026-10-02-长场后候选读取授权过期/原因分析与修复方案.md) · [实施与验证](记忆系统/2026-10-02-长场后候选读取授权过期/实施与验证记录.md)。

2026-10-02 DJ-LIVE-CLOSURE-REVIEW-20261001现场补充：十分钟当前轮跨界、自动告别、42消息齐全及待确认服务端发布已证；整场正式确认受新授权读取问题阻断，仍保留待核验。见上述本轮报告，不抹去旧长场FAIL。

<a id="dj-live-theme-correction-20261002"></a>
### DJ-LIVE-THEME-CORRECTION-20261002 · 短场主题更正类型冲突导致发布失败

**状态：本地已修待现场｜类型：发布策略局部修复，历史语义首因及现场效果待核验｜P0**

- 10/2新短场两轮、4条正文齐全，Source已创建，主题发布失败。第一次attempt为themeCorrectionKindMismatch，系统恢复后noReliableThemes；没有确认写。
- 授权修复未进入现场目标阶段，仍本地已修待现场。短场FAIL，长场/正式确认/冷读NOT_RUN，未绕过门禁。
- 不凭最终unit残留failure_code断言错误结果被复用；需重建首次关系与恢复响应，核对两端事实类型及独立结果能否保留。本轮只记录，未修改产品或重试旧场。
- [问题与证据](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/问题记录与真机结果.md)。

- 10/2后续：已按讨论修改并完成定向本地、隔离PG闭环；真实Provider传输授权待确认，部署/真机未运行，不关闭现场故障。[本轮实施与验证](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/发布降级修复实施与验证.md)。

2026-10-02 v1.39现场补充：本条修复已部署并完成真实模型检查与新短场全闭环；新十分钟后台已发布，客户端未跟随，不升级为整体通过。用户短场提示异常待核对，仍保持本地已修待现场（整体现场关闭条件未满足）。关联DJ-LIVE-CLOSURE-REVIEW-20261001及DJ-LIVE-AUTOLAB-01；后续优先解决发布延迟/终态可见性，音频首因保留。[现场报告](记忆系统/2026-10-02-短场主题更正类型冲突导致发布失败/2026-10-02发布修复部署与真机验收.md)。

2026-10-02用户要求先记录、稍后讨论：本次长场已捕获“上次整理失败，原对话已保留”与服务器同场published的矛盾，补入DJ-LIVE-CLOSURE-REVIEW-20261001现场记录。后台发布耗时、客户端终态同步、音频调度首错分别保留，不立即改代码。
