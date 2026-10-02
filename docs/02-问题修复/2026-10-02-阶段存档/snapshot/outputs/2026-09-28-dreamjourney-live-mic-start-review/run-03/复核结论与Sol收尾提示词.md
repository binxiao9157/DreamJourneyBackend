# Live 开麦 run-03 独立复核

日期：2026-09-28。编号：DJ-LIVE-START-20260924。

**结论：尚未满足全部本地要求，维持 LOCAL_INCOMPLETE 正确。** 这次有多项实际修复与有效证据；仍发现一处生产调用没有遵守自身校验结果的代码缺口，另有明确的组合验证未执行。不以真实 Provider/手机没测试作为本地不通过的理由。

本轮读取当前源码、原始 xcresult、日志及最终指纹，运行源码提取的离线 Swift 小探针；未改产品代码、未重跑完整 App 套件、未启动 PG/API、未连接手机、未调用 Provider、未部署、未操作生产/历史或 commit/push。

依据：[原设计](../../../02-问题修复/Live语音与开麦/2026-09-27-DreamJourney-Live开麦迟缓与发前失败-分析及开发指导.md)、[上轮复核](../run-02/复核结论与Sol收尾提示词.md)、[run-03 实现报告](../../2026-09-27-dreamjourney-live-mic-start-repair/run-03/implementation-report.md)、[MIC 矩阵](../../2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix.md)。

## 1. 本轮核实并认可的成果

| 项目 | 核实结论 |
|---|---|
| Controller 二次排队归属 | onDialogStarted/onError 现已冻结并重验 attempt/token/lease。相同业务断言修前2失败、修后2通过；不再把上轮缺口原样列为未修。 |
| runtime 旧响应 | fetchRuntimeConfig 在应用结果前核对发起时 lease；旧响应覆盖新账号 epoch/policy 的受控用例修前1失败、修后1通过。证明这个具体代码路径修复，不等于追溯证明历史某条响应就是首次MIC-11的写入者。 |
| 票据两次请求 | 真实 Controller/Client/Gate/transport 的 canonical写前401→第二POST未知结果用例通过，保留最多两次POST、零SDK启动。先前失败被明确归为未等taskResumed的装配时序，不冒充业务红。 |
| 默认日志与共享Handler | 当前stage logger有独立StreamHandler且propagate=false；默认Uvicorn日志配置+实际App/TestClient产生了票据拒绝阶段日志。受影响后端日志显示29测试通过，含共享Handler锁回归。属于本地受控验证，不是生产部署实测。 |
| MIC-14跨请求原因 | 实际测试先取得本请求deny，再用另一个allow覆盖latestDecision，Controller终态仍保留本请求policy reason、零ticket；单项产物1/1。这个子断言已补，不应重复列为未执行。 |
| iOS完整束 | 原始all-ios-final-03.xcresult：**770通过、0失败、3跳过**。 |
| 最终四场 | 四个独立xcresult各1/1。short-A→logical20→short-B→logical65顺序有证据；每个长场前短场两轮补充。20场110用户+110助手、4条长场候选、含短场5条正式记忆；65场150用户+150助手、17条长场候选、含短场18条正式记忆。候选可见、审核、正式记忆和API重建回查通过。 |
| 版本一致性 | 本轮独立重算1,297项依赖、312个构建产物，无不匹配；两配置和两仓库binary diff哈希匹配。源码指纹`749429978a1525ad7ccebee4c50f1c5d4d7bd7004239e0b2b1fca796d292d32c`；两端diff检查通过。 |
| 后端基线对照 | old HEAD/current两份日志均有同样3个259/260失败；保留的旧副本main、route ownership、route-auth测试、config和requirements内容均与HEAD相同。这三项可按已有对照作为有限基线例外，不等于全后端全绿。 |
| PG及D5进展 | invalid bearer 401、正例绑定字段逐项比较、二次消费拒绝、active409、issued替换、回滚及同trace阶段有记录。三种有限延迟均有阶段/线程栈/心跳；既有同步阻塞风险及历史首因继续单列。 |

原始复核产物：[xcresult汇总](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/xcresult-summary-review.json>)、[源码/构建重算](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/current-source-build-check.json>)、[四场/配置/旧快照核对](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/four-scene-config-and-baseline-check.json>)。全量三个SKIP的保存入口已另跑四场，不能因此认定保存链未测试；本轮未重新构建通用设备目标，设备构建成功仍以Sol保存的执行报告为来源。

## 2. 必须先修的具体代码缺口

### R1 / P1：markStartSubmitted拒绝后，生产Manager仍发送StartEngine

在[实际提交位置](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:4874>)：

```swift
pendingVoiceStartSubmitted = voiceLaunchControl.markStartSubmitted(dialogOperationId)
let startResult = engine.send(SEDirectiveStartEngine, data: configJSON)
```

`markStartSubmitted`会重新检查operation与截止，并可返回false。生产调用却只把false存入变量，下一行无条件发送。更早的4856行guard与这次检查之间还有raw ingress安装及诊断工作，单调时钟可以在此期间越过截止。

现有[Manager控制类测试](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:33228>)反而写成`if control.markStartSubmitted(...) { sdkStarts += 1 }`。测试因此执行了生产调用处没有的条件判断。它证明控制类返回值正确，不能证明Manager遵守返回值。

本轮[离线Swift探针](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/manager-submit-guard-probe.swift>)逐字提取生产控制类与上述两行，使用计数引擎边界，先允许、再推进到过期。其[实际输出](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/manager-submit-guard-probe-result.json>)为：`initialGuardAllowed=true`、`markStartSubmitted=false`、`engineBoundarySendCount=1`，应为0。该证据验证的是源码提交片段，**不是完整Manager/native SDK运行，也不是历史现场复现**。

**最小修改要求：**只有领取提交资格成功，才允许send；失败须按被冻结的launch/operation归属收尾，不得用“当前pending ID”误取消已替换的新场。清理由本次安装的ingress/音频资源负责，保留旧场合法drain。补实际生产启动编排路径的同断言红绿，令时钟在首次guard后、最终提交前跨截止，断言StartEngine=0、终态一次、没有错误移交或新场资源清理。

## 3. 剩余本地验证，按有限清单收尾

### R2：执行生产Manager启动编排，不能只测试control类

当前[`DialogVoiceLaunchExecutionControl`](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2241>)确实由生产Manager使用，抽取本身有价值。但测试未运行生产setup→提交→回调→cancel/complete的接线；R1已经说明只测类会漏什么。

通过最小SDK/clock/callback适配，使**生产实际使用的启动编排方法**在本地目标执行。替换硬件/供应商边界即可，不复制测试版Manager，不需要原生库支持Simulator。验证原设计限定的setup跨截止、StartEngine返回失败、SessionStarted缺失、pending取消后晚success/error、成功移交与截止竞速、旧ID不伤新场；逐项检查send/stop、greeting、ingress和音频lease副作用。不得把`if helper返回true`的测试自行编排算作生产接线通过。

### R3：MIC-19要让A真的在途，并证明B的正确结束

现有[MIC-19](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:33064>)已启用capture并使用临时磁盘，但A的`naturalInputPolicyAvailable`和`candidateReviewPolicyAvailable`均为false，没有实际正在完成的append/close/整理回调；测试主要比较预置A磁盘记录。B SDK失败分支仅验证新coordinator存在及A记录不变，尚未验证B的正规close。也未先断言A checkpoint是有效非空记录。

按真实产品的A/B生命周期关系建立有阶段屏障的在途A：A正在append/close或整理，B发前deny、B超时、B已有capture后SDK失败，按原三种场景分别核对。A正常业务自身推进可以改变水位，需通过**无B失败的同阶段对照/归属记录**证明B没有额外改变A，不能机械要求活动A所有字段永不变化。

保留A实际非空outbox/follow-up/checkpoint及原预算；检查B不替A发end/ACK/admit、不丢A坐标；检查B只关闭自己的capture且A继续正确drain/recover。不要把旁边造的静态A文件加“零A写请求”当作活动旧场保护。

### R4：原MIC矩阵剩余精确断言，优先复用已有证据

- MIC-02/09/10：各等待阶段和累计总截止，用可控clock、阶段屏障与取消回执；覆盖墙钟调整而单调截止不变。不要增大等待或只测ticket一种悬挂。
- MIC-04/06：共用刷新时取消一个waiter不影响正常waiter；有限的重复输入/不同失败竞速，验证一次终态和首错。把“所有通知时序/点击风暴”改为原设计能枚举的场景，不要求无限排列。
- MIC-03/07/08/12/13：在已有真实Controller用例上补精确终因/用户提示、fresh authority/剩余总预算、旧响应与当前请求结果归属，以及热路径请求数/绑定。已有正例不重写，已满足的断言链接原产物即可。
- MIC-14：跨请求latestDecision子项已经通过。磁盘不可写当前[测试](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:34860>)直接构造attempt并调用finish，只证明诊断失败不破坏该对象终态；尚未执行真实Controller/capture保存组合。可合并到R3注入诊断目录不可写，核对保存链继续正确，不必另造一套业务框架。因此矩阵整行PASS须注明子范围或补足组合。
- MIC-15：正例逐字段绑定、串行issued替换/active拒绝/单次消费已补；继续补原要求的并发屏障与身份失配保护，保留真实PG事务和默认入口。不要再次把这些已执行正例列成“完全没测”。

MIC-17中仅有真实SDK/设备声学未跑的部分，应单列外部NOT_RUN。MIC-16已补有限注入、心跳、线程栈和同trace阶段，可以承认限定本地机制验证；未拆分的同步内部耗时应明确边界，不能要求查明历史一分钟首因或修完旧全局中间件才允许本次LOCAL_PASS。其余适用本地保存/音频控制保护仍须成立。

## 4. 交付与状态校正

- 当前报告“Remaining assertions”仍列cross-request latestDecision缺证，与最新测试和矩阵相矛盾；改为该子项已通过、磁盘不可写组合未证明。保留旧报告作为历史，不抹去已做成果。
- 首轮MIC-11的近因为authorityEpochMismatch；本轮修复了能导致旧runtime结果跨账号应用的具体路径。未关联到首轮唯一写入者的部分继续未决，不把全量转绿称为历史首因已确认，也不因此无限阻塞已有局部修复交付。
- 当前四场及指纹已认可。R1及生产编排改动属于实际依赖变化，冻结最终源/构建/配置后重跑受影响专项与同版short-A→logical20→short-B→logical65；每个长场前独立短场两轮补充，候选→审核→正式记忆→重建保持。只改文档且依赖未变才可据指纹复用。
- 符合上述原范围本地断言可交付LOCAL_PASS并保留3项路由基线例外；真实Provider、手机、物理时长、部署、生产与历史处理各自NOT_RUN。历史慢启动/23:04/seq5首因继续未决，不能据本地通过关闭现场事件。

## 5. 给Sol的提示词

```text
继续DJ-LIVE-START-20260924本地收尾。先完整阅读：
/Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-03/复核结论与Sol收尾提示词.md

保留run-03已通过的回调归属、runtime旧响应防护、两次POST未知写保护、日志隔离、基线对照与四场保存链。先修第2节R1：生产Manager忽略markStartSubmitted=false仍send StartEngine；按冻结operation归属收尾，补首次校验后、最终提交前跨截止的同断言红绿。测试必须调用生产使用的启动编排，不能在helper测试里自行补if当成已验收。

按第3节R2—R4补原范围的有限本地断言：真实Manager编排与受控SDK边界；A实际在途时B失败及B正常close；累计截止/等待取消、共享刷新/有限竞速；现有Controller用例的原因、提示、预算和响应归属；MIC14诊断磁盘不可写不破坏保存；PG并发与身份保护。已证子项直接链接复用，不把已通过的latestDecision等继续写成未做，也不扩成无限组合或全局重构。

最终依赖变化后冻结并完成受影响回归和同版short-A→logical20→short-B→logical65，每个长场前独立短场两轮补充，验证候选、审核正式记忆和重建。交付具体改动、红绿、逐项证据、最终指纹、准确状态，更新登记册。历史首因及全局旧风险保持未决，不伪称解决，也不作为无限本地阻塞项。

本轮不调用真实Provider、不部署、不连接/检测/等待手机、不操作生产或历史、不commit/push。真机、原生SDK现场时序和声学由我后续主动发起；不能因没有手机停止本地任务。
```
