# Live 开麦修复 run-02：独立复核与 Sol 收尾要求

日期：2026-09-28。问题编号：DJ-LIVE-START-20260924。

**结论：有实质进展，局部符合预期；尚未完成。截图继续标 `LOCAL_INCOMPLETE` 是正确的。** 本次检查了当前源码、原始 xcresult、日志、隔离 PG/D5 产物、四场结果和源/构建指纹，不仅是阅读截图。发现新的回调交接缺口和诊断日志共享锁风险；也区分了已修代码、缺少验证与历史首因未决。

本轮仅新增复核材料、运行只读证据检查及不联网的有界日志探针，更新登记册；未修改产品，未重跑整套 App 测试，未启动 PG/API，未连接手机、调用真实 Provider、部署、操作生产/历史或 commit/push。

依据：[原设计及 MIC-01–19](../../../02-问题修复/Live语音与开麦/2026-09-27-DreamJourney-Live开麦迟缓与发前失败-分析及开发指导.md)、[上轮 R1–R5 复核](../run-01/复核结论与Sol续修提示词.md)、[Sol run-02 报告](../../2026-09-27-dreamjourney-live-mic-start-repair/run-02/implementation-report.md)、[run-02 MIC 矩阵](../../2026-09-27-dreamjourney-live-mic-start-repair/run-02/mic-matrix.md)。

## 1. 已独立核实的进展

| 项目 | 核实结果与边界 |
|---|---|
| MIC-01 完整 Controller 链 | 逆转 D2 行为的隔离副本同断言 1 失败，现行代码 1 通过。保留“行为重建、非完整历史版本”的准确说明。 |
| 账号重绑、合法缺失缓存、跨 POST 曝光 | 当前代码已补；定向产物修前 3 失败、修后 3 通过。曝光用例直接测试 `attempt.note`，尚非两次真实 transport 的组合证明。 |
| MIC-05 已有反例 | A 取消后才到达的旧回调：修前 1 失败、修后 1 通过。不能覆盖下文事件已入队但未执行的窗口。 |
| Manager 截止/取消 | 真实分支已加入 setup 前后、StartEngine 前、SessionStarted 的有效性校验及按 ID 取消 pending；此前必须 active 才能停止的遗漏已修。运行证明仍有缺口。 |
| 后端诊断及事务标签 | 直接诊断异常隔离、有界队列、`uow.committed` 已落地；隔离 PG 有票据成功、401、提交/回滚及消费产物。共享日志 Handler 问题见下文。 |
| D5 | 完整 TestClient ASGI lifespan + 真实隔离 PG，有限 metrics/auth/pool 延迟及心跳、线程栈已留证；不是 Uvicorn/socket 或历史现场复现。原设计只要求本地有界机制诊断，不因此新增 socket 验收。 |
| 完整 iOS 束 | 首轮 **757 通过、1 失败、3 跳过**；同源重跑 **758 通过、0 失败、3 跳过**。MIC-11 定向日志确有 10 次通过。首轮失败不能删除或用重跑覆盖。 |
| 保存四场 | 四份 xcresult 各 1/1；顺序 short-A→logical20→short-B→logical65。两独立短场各生成 1 条候选。长场分别 110 用户+110 助手、150 用户+150 助手；4/17 条长场候选，连短场各 5/18 条正式记忆；客户端候选可见、审核及 API 重建回查有产物。 |
| 跳过项 | 完整 iOS 束中的三个 SKIP 是短场/逻辑20/逻辑65独立入口；本轮四场已另外执行，因此不能继续把它们解释为保存链完全没测。 |
| 最终版本一致性 | 独立重算 1,297 项受保护依赖、312 个构建产物，全部匹配；源码指纹 `7aae5172321cc96fdf79866b6214c09a8780de7a1034b364a02457dad60d4830`。两个配置、通用 iOS dylib、两端 tracked diff 指纹也匹配，`git diff --check` 均通过。 |

原始核验：[xcresult 汇总](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/xcresult-summary-review.json>)、[源码与构建重算](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/current-source-build-check.json>)、[四场及配置核对](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/four-scene-and-build-review.json>)。`reuseEligibleBySourceAndBuild=false` 比较的是旧 run-03 基线，不表示本次产物与当前源码不一致。

以上保存链运行的是受控模型 HTTP、隔离 PG 和模拟器。它证明当前版本这些本地场景通过，不能证明开麦所有失败分支通过，也不证明火山真实 SDK/DeepSeek/物理20或65分钟已通过。

## 2. 尚需完成的具体工作

### A. P1：修复 Controller 二次排队时丢失事件所属场次

[onDialogStarted](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:16619>)、[onError](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:17101>) 再次 `DispatchQueue.main.async`，只捕获 self；真正执行时检查、结束的是当前 attempt/token。Manager 的入口校验和新增 DEBUG callback guard 均发生在第二次排队之前。

静态可达窗口：**A 事件通过入口校验并排队 → 先执行的取消/重开动作切到 B → A 闭包执行，误将 B 变成 listening 或失败。** 这是源码上的具体身份隔离缺口；本轮未执行该窗口的 Controller 红绿，不称为已复现历史事故。

最小修复：在事件接收时冻结所属 attempt、生命周期/operation 身份；实际 UI、音频、终态副作用执行前重验。成功与错误均覆盖；不能仅在入口检查当前状态。使用可控队列/完成屏障，补 A-success/A-error 已入队后取消 A、开始 B 的同断言红绿；B 不得被提前成功、失败、stop 或释放 lease，B 自己的事件仍能完成。不要复制测试状态机绕开真实 Controller。

### B. P1：让生产实际使用的 Manager 控制逻辑可在本地执行，并补 MIC-19

Simulator 当前整段选择 UIQA Manager，通用 iOS 编译不能验证新加的真实 Manager 控制分支。通过最窄 SDK/clock/callback 边界，让**同一份生产控制逻辑**可在本地运行；不要求手机、真实 Provider，也不要求在 Simulator 装载不支持该平台的原生库。

按原 MIC-05/09/10/11/17 覆盖：累计截止不重置；setup 跨截止后零 StartEngine；StartEngine 失败；缺失 SessionStarted；pending 取消后晚 success/error；成功移交与截止竞速；旧 ID cancel 不影响新场。断言旧 ingress 不再新增 reserve/deliver，同时保留合法旧场 drain；当前 pending cancel 没有显式 close raw ingress，但 Controller 另有关闭保护，**尚不能据此声称发生旧场写坏**。

MIC-19 必须打开真实 capture/Coordinator/磁盘，不能使用现有测试装配默认 `skipVoiceLaunchMemoryCaptureForTesting=true` 来验收：

- A 仍在保存/整理时 B 发前 deny/超时；
- B 已建立自己的 capture 后 SDK 失败。

核对 A 的 outbox/follow-up/checkpoint 身份、水位、预算不变，B 不对 A 发 end/ACK/admit，B 只正规结束自身 capture，A 仍能 drain/recover。现有代码有保护，这是**关键验证缺口**，不是已证明 A 数据损坏。遇到反例才按最小范围补代码。

### C. P1：解释并收口 MIC-11 的授权代次污染

原始[失败片段](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/mic11-first-failure-excerpt.txt>)明确显示：ticket 200 后 `realtimeVoiceConfigResponse` 因 `authorityEpochMismatch` 被拒；`sdkStarts=0`，停在 `ticketResponse`；最终是 `accountLeaseInvalid`，不是预期的 SDK 等待超时。

所以近因已知为**授权代次失配**，尚未知道谁在何时更改了共享 runtime。不得仅称“SDK 偶发慢”，不得加大 XCTest/产品超时或去掉 lease 校验。局部记录 epoch 发布来源与前后值，检查 App 启动、Controller load/bind、恢复回调及 teardown 的异步生命周期。按能触发污染的顺序建立确定性反例；若为测试装配污染则只修隔离，若是生产回调越界则修对应身份检查。保留首轮失败及对照。

### D. P1：诊断在默认配置可能静默丢失，补 Handler 时还需防共享锁阻塞

**默认输出缺口：**仓库 Dockerfile 使用默认 Uvicorn 启动，没有指定 log config；本轮检索 app/scripts 未找到额外日志 Handler 配置。新增 logger 仅 `setLevel(INFO)`。以已安装 Uvicorn 的 `LOGGING_CONFIG`、当前队列和实际 helper 做零 App 导入、零 server 的有界配置探针，`app.main.voice_launch→app.main→root` 均无 Handler，INFO 未写到 stderr；队列已排空且 dropped=0。见[默认日志探针](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/backend-default-logging-probe.py>)及[结果](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/backend-default-logging-probe-result.json>)。已有绿色测试和 PG 样例主动加了 Handler，只能证明该测试配置能输出。此处验证的是仓库声明的默认日志配置；未读取线上实际启动配置，不声称线上日志一定丢失。

[voice_launch logger](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7920>) 默认向父 logger 传播，而票据的策略拒绝/observeDeny 路径有[同步 warning](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7229>)。标准 logging Handler 会持锁调用 emit。

本轮使用实际队列源码和 AST 提取的 `_log_voice_launch_stage`，仅注入标准 Handler 的有界等待：诊断 submit 能返回，但共用父 Handler 的 warning 在150ms观察窗不能完成；释放后两条日志交付、队列排空、子进程正常退出。见[探针](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/backend-shared-handler-probe.py>)及[输出](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/backend-shared-handler-probe-result.json>)。

这是**条件成立时已证实的共享锁机制**，不是生产必然阻塞或历史一分钟首因。两个配置条件要分别收口：默认部署配置确实有脱敏阶段输出；诊断 sink 变慢时不持有同步业务日志需要的共享 Handler 锁。修复范围只限本次新增诊断，保留异常隔离、有界丢弃。不要只 `propagate=False` 后把日志全部丢掉。补清洁默认启动和实际 logger/Handler、默认票据策略分支的有界对照，不再仅用测试手加 Handler 或没有 Handler 锁的 `_BlockedLogger` 证明可观测/非阻塞；不要扩展为重写全部旧中间件。

### E. 原矩阵尚未闭合的适用本地断言，按原范围补证

当前矩阵为3项有范围限定的 PASS、15项 PARTIAL、1项 NOT_RUN；这不等于有15个已证业务缺陷。已有产物能精确覆盖的先链接复用；只补未执行的原场景，不新增“所有可能组合”指标。

优先补：MIC-08 真实 Client→canonical写前401→认证恢复→第二POST曝光后丢响应/截止→第一请求晚通知，验证最多2次POST、第二次发行未知、不进SDK；当前直接 `attempt.note` 测试不足以证明组合链。MIC-15 的 PG 401 当前是无 Authorization 头，不替代合法入口的过期/无效 bearer、绑定、issued替代/active并发及消费保护。MIC-14 的诊断磁盘不可写、跨请求 latestDecision 干扰仍需真实组合。其余 MIC-02/03/04/06/07/09/12/13 的已列缺口按原设计具体断言逐项映射。

隔离 PG/D5 必须补可复核的导入前清洁配置、相关 sink 指向专用测试实例及拒外连装配；当前产物可以证明受控调用结果，不能自动证明未记录的全部隔离措施。D5 的日志 collector 在故障注入前已移除，原产物有线程栈与心跳，但缺各注入请求同 trace 的完整起止阶段；补原设计要求的关联取证，或明确未拆分的耗时区间，不能声称“新增阶段日志已经精确定位了三种延迟”。保留有界机制通过的结论，不要求本轮修完既有全局同步中间件。仅在隔离环境补运行证据，禁止改连生产。

后端 broad 产物实际是 **116测试、3失败**，形态均为259/260路由计数；本次未找到报告所称旧/当前执行对照。测试文件无 dirty diff 也不足以证明运行基线。提供现存同环境对照的确切路径，或在隔离旧快照/当前版补最小对照；未找到前写“疑似既存、未核实”，不宣称已证基线，也不反过来认定新增回归。不要混用其他历史套件的“7项”计数。

## 3. 文档校正与最终验收边界

1. Sol 报告 `4/220 user turns` 应改为短场2用户+2助手、长场110用户+110助手；65场150用户+150助手。不要把总发言数都写成用户回合数。
2. 后续真机清单中的短场仍须“两轮，第二轮补充第一轮”，不能降为一次事实输入；待确认→确认→正式记忆→重建回查不变。清单准备不启动真机。
3. 原生 SDK 实际秒数、火山/DeepSeek真实交互、声学和物理时长继续各自 `NOT_RUN`。本地能够执行的控制逻辑测试与这些外部项拆开，不能因外部未跑而永久保持本地不通过。
4. 保留当前已通过四场与指纹。修改实际依赖/配置/测试装配后冻结新版本，重跑受影响专项，并在同版执行 short-A→logical20→short-B→logical65；每个长场前独立短场。仅文档变化可按核验依据复用。不要反复无变化全量重跑代替定位。
5. 全部适用本地断言有证据才 `LOCAL_PASS`；仍有缺口则列具体断言。历史慢启动、23:04单次失败与seq5首因继续未决。不得因本地机制反例而认定它们的现场根因。

## 4. 直接发给 GPT‑6 Sol 的提示词

```text
继续 DJ-LIVE-START-20260924 的 run-02 本地收尾。先完整阅读：
/Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-02/复核结论与Sol收尾提示词.md
以及其中链接的 ios-review.md、backend-review.md、原9/27设计和MIC矩阵。保留当前dirty修改及所有失败/通过证据，不重写已通过的保存链。

先完成第2节A—D：修复成功/错误事件二次主队列跳转时的场次归属，补事件已入队后换场的真实Controller同断言红绿；使生产Manager控制逻辑经受控SDK边界可在本地执行，补截止、pending取消、成功移交与晚事件组合；关闭MIC-19真实capture/磁盘旧场保护缺口；依据首次MIC-11的authorityEpochMismatch和sdkStarts=0追踪epoch写入者，修确定的隔离或回调归属问题；补默认Uvicorn配置下阶段日志实际输出，并隔离新增诊断与同步业务日志共享Handler锁，保留有界丢弃，补清洁默认启动和真实logging/票据分支对照。

第2节E按原MIC断言收口。已有证据精确匹配则复用，不扩大到无限组合；MIC08不能只用attempt.note，MIC15不能只用无Authorization头401，MIC14不能跳过磁盘。补隔离PG的清洁环境和拒外连装配证据；后端116/3路由计数失败给出同环境旧/当前最小对照后再定基线，不能仅引用报告分类。

按第3节校正回合数、MIC11近因和真机短场两轮补充清单。实际依赖变化后冻结最终源码/构建/配置，在同版完成受影响回归以及short-A→logical20→short-B→logical65，短场不过不得运行对应长场。完整保存链要覆盖真实客户端候选、审核正式记忆及重建回查。不要用扩大等待、放松断言、跳过真实Gate/capture、扩重试预算或未知写重放来过测试。

持续完成不依赖外部的本地工作后一次性交付：具体改动、修前红/修后绿、MIC逐项精确证据、首次失败归因、PG与四场结果、最终指纹和复用/重跑依据，并更新问题登记册和校验链接。符合适用本地标准则LOCAL_PASS；不足则列确切缺失断言。历史近一分钟等待、23:04单次因果、seq5首因继续未决，不伪称找到。

本轮不调用真实Provider、不部署、不连接/检测/等待手机、不操作生产或历史、不commit/push。真实SDK现场时序、声学和物理时长单列NOT_RUN；真机由我后续主动发起，不能以手机未连接暂停本地任务。
```

细项定位：[iOS审查](ios-review.md)、[后端审查](backend-review.md)。
