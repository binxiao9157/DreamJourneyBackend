# Live 开麦修复 run-01 复核：尚未达到本地验收要求

日期：2026-09-28。问题：DJ-LIVE-START-20260924。范围：对现有交付、代码、测试产物和指纹进行核对；不修改产品、不启动真实 Provider/手机/生产，不部署或提交。

**结论：修复方向有实质进展，`LOCAL_INCOMPLETE` 标注准确；尚不符合完整交付要求。缺口不只是 PostgreSQL 没启动，还有真实 SDK、账号重绑、缺失缓存及诊断隔离的代码遗漏。继续本地修复，不能以596个测试通过代替短场/长场保存验收。**

原要求：[9/27主设计](../../../02-问题修复/Live语音与开麦/2026-09-27-DreamJourney-Live开麦迟缓与发前失败-分析及开发指导.md)。
本轮交付：[实现报告](../../2026-09-27-dreamjourney-live-mic-start-repair/run-01/implementation-report.md) · [MIC矩阵](../../2026-09-27-dreamjourney-live-mic-start-repair/run-01/mic-matrix.md) · [回归隔离](../../2026-09-27-dreamjourney-live-mic-start-repair/run-01/regression-isolation.md)。

## 1. 本次独立核实了什么

- 读取 `ios-final-v10.xcresult` 实际摘要：599 total，596 passed，0 failed，3 skipped，平台为 iOS Simulator。
- 三个跳过项分别为：短场候选审核正式闭环、110用户轮逻辑20分钟链、150用户轮逻辑65分钟链。它们正是需要隔离后端配合的关键链，不能算作通过。
- `mic01-red` 确有业务断言失败：当前允许策略未能授权新启动；`mic01-green-initial` 为1通过；完整Controller的修后产物亦为1通过。仍未提供**相同完整Controller链的修前红**。
- `mic04-rotation-red` 确实因预期0个ticket、实际1个ticket失败；修后对应产物3通过。它证明特定代次隔离，不证明所有真实账号通知重绑时序。
- 报告中可直接匹配的13份源码/配置文件 SHA-256 均与当前文件一致；本地无签名设备目标 dylib 存在且与报告指纹一致。未把这些指纹等同整个依赖闭包或线上版本。
- 两端 `git diff --check` 均通过。没有重跑完整业务套件或重建App；后端81通过是原报告结果，本轮未将其重新执行或升级为PG通过。
- MIC矩阵按完整原设计口径为15项PARTIAL、3项BLOCKED、1项NOT_RUN。这不否认已通过的子断言，但没有任何整行达到完整关闭条件。

## 2. 需要继续修改的具体代码

### R1／P1：截止与取消尚未进入真实 SDK 启动内部

当前 [Echo调用SDK并在返回后检查截止](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:16175>)，但 [Manager内部setup后直接performStartDialog](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:3712>)。Manager没有拿到本次launch截止，同步初始化跨过截止仍可能发出StartEngine。现有MIC-10替换的是Controller的SDK配置闭包，没有验证这条内部路径。

另一方面，[超时清理要求SDK已经active](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:16281>)，而[Manager.stopDialog同样要求isDialogActive](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:3809>)。该状态直到[SessionStarted才为true](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:5599>)；此回调还在通知Controller前发送greeting。因此，在等待SessionStarted时取消，不能仅靠当前stopDialog实现终止pending operation。

**续修要求：**将截止/取消与Manager的operation/generation绑定；在真实setup返回、定时补查、StartEngine副作用及SessionStarted处理前复核。提供只取消所属pending启动的能力，不能停新场，也不能屏蔽成功移交后的正常ASR/播放/停止保存。测试可以替换原生SDK边界，但必须运行真实Manager，不能用一个`startSDK { onDialogStarted() }`闭包代替本项验收。

必验：setup跨截止后StartEngine=0；StartEngine提交但尚未SessionStarted时超时/停止，迟到事件不激活、不greeting、不触碰新场；成功与截止竞速只有一个获胜。

### R2／P1：真实账号重绑没有即时结束旧 LaunchAttempt

[rebindEchoAccountScope](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:7328>)清理了原有生命周期和engine binding，但没有结束/解除`activeVoiceLaunchAttempt`。其[isActive](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:228>)仅看终态/截止；[新启动据此直接return](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:14973>)。权限回调之前尚未建立截止时，旧attempt还可能无限占位；已建立截止时，旧watchdog可能稍后作用于新账号页面。

**续修要求：**在真实账号/authority重绑路径立即撤销旧attempt及其专属waiter、timer/transport；旧回调不得改变新页面。测试真实通知路径，分别覆盖权限等待、ticket等待、新账号已经进入页面，不能只等短watchdog让旧attempt自然消失。

### R3／P2：合法缺失策略缓存的恢复分支遗漏

[恢复白名单](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:8213>)没有`missingPolicyCache`，而[真实无缓存返回该原因](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ReleasePolicyStore.swift:595>)。因此报告所说“缺失/过期可恢复”超出当前实现。

**续修要求：**按原设计补合法缺失缓存的一次刷新、重验账号和fresh capture；有效deny、本地关闭、损坏/错账号缓存分别处理，不能统一放行。以真实策略源造无缓存，验证Controller正常恢复及刷新后deny/换账号反例。

### R4／P2：曝光单调性应限定在同一次 HTTP 请求内

[EchoVoiceLaunchAttempt.note](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:286>)将不同POST的曝光取单调最大值。第一次401已到`responseReceived`，认证恢复后第二次POST丢响应仍会留下这个状态，无法正确表达最终票据发行未知。

**续修要求：**保留修好的“同一次503之后迟到resume不能倒退”，同时按HTTP请求序号分别记录曝光、状态和结果；最终发行结果必须来自当前ticket请求。补“规范写前401→第二次曝光→丢响应/超时”的产物：总POST保持2，第二次结果未知，不能由第一条401冒充有最终响应。此处确认的是诊断不准确，未据此断言当前已发生自动重复写。

### R5／P1：新增后端诊断未隔离失败，commit标签也有误标条件

`_log_voice_launch_stage`直接同步调用logger，broker在写票据前后直接执行`diagnostic_stage`。本轮对实际函数AST做纯函数受控注入，未导入App、未连接DB或网络：写前诊断抛错→异常外泄且fake store调用0次；写后诊断抛错→异常外泄且fake store调用1次；logger抛错也外泄。

这证明原设计D4要求的诊断失败隔离未落实，**不是声称生产logger必然抛错**。fake store不能证明PG提交/回滚；但PG事务内写后日志失败与commit后日志失败明显需要分开验证，后者不能被表述为“没有发行票据”。

**续修要求：**新增诊断应有界、非阻塞，并隔离日志异常；不能只加try/except处理同步无限等待。只修本次新增诊断，不趁机重写全部中间件。补日志异常/有限阻塞/队列满，以及commit前后分支，验证票据业务结果与无诊断对照一致，日志丢弃可辨识。

另一个纯函数反例发现：[记录ticketStoreCommitted的条件](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7820>)只检查正常退出、write flag、HTTP状态，未判断[UoW正常退出但rollback-only](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py:121>)。fake UoW已经回滚、HTTP200时仍记录committed。须绑定明确事务结果，覆盖rollback-only、commit失败、commit后归还连接失败。这里证明标签条件不足，不表示现场或正常成功路由必然触发了这个组合。

材料：[后端详细复核](backend-review.md) · [纯函数探针](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-01/backend-diagnostic-contract-probe.py>) · [探针原始输出](</Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-01/backend-diagnostic-contract-probe.json>)。均不替代隔离PG/完整ASGI验证。

## 3. 验证缺口不能全归给 PostgreSQL

### 3.1 不依赖PG的部分应先完成

- MIC-01完整Controller同断言修前红；上述R1–R5对应红绿及原MIC矩阵未完成的启动边界。
- MIC-05需在B仍starting时送A迟到回调，证明不会提前完成B，且SDK/音频无越权副作用。现有先让B listening再回放A的测试不足以覆盖此断言。
- MIC-19：A仍保存时B发前失败，以及B已建capture后SDK失败，检查A磁盘身份/水位/预算/命令不变。现有[Controller测试装配固定跳过memory capture](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:17192>)，不能拿这些用例证明旧场保护。完整保存验收另用真实采集与coordinator、磁盘。
- D4的policy/runtime/auth子请求序号、首次错误、不可写诊断磁盘、其他请求latestDecision干扰等按原矩阵补足；不要再加重复测试计数代替原断言。

### 3.2 恢复隔离PG后完成默认链

旧实例启动脚本位于先前交付 `run-03/closure-2026-09-24/C-capacity/commands.sh`，曾使用 `/private/tmp/dj-run03-pg/install/bin`。本轮只读核查发现旧安装和data目录均不在，常见路径也未发现当前可用PG/容器工具。因此不是简单向旧端口重连就能恢复。

**这是本地环境缺口，不是需要用户手机配合。** Sol应按原来的无生产访问边界重建专用PG运行环境，先检查安装/初始化/启动/监听，再执行测试。若下载、系统权限等确实阻止重建，保留具体命令和原始错误，继续完成3.1；不要以旧端口拒连直接停止全部工作，也不能改连生产数据库。

run-01交付包未找到原始runner/nc拒连日志，仅报告记载，续交需补环境取证。隔离必须在导入app前设定，防止全局recorder等仍绑定非测试store。

按原设计执行MIC-15/16、D5，随后冻结最终实际依赖与构建，完成：

**short-A → logical20 → short-B → logical65**。

每个长场前有同版独立短场；候选必须经真实客户端可见、审核进入正式记忆、重建回查，逐轮身份与事实集合核对。当前三个SKIP不能计入通过；改动了实际依赖不能继承9/24的final08结果。

## 4. 文档与状态

Sol已正确自报`LOCAL_INCOMPLETE`，保留了前序失败，这是符合预期的报告边界。主登记册却把详情状态改成自由文本，汇总仍算“待修复5”，导致现有校验器报状态数量不一致。本次复核只将登记册归类规范为“待修复”，保留详情`LOCAL_INCOMPLETE`及所有原始证据，不把产品状态升级。

历史近一分钟等待、23:04现场单次因果和seq5首次触发原因继续未决。当前代码遗漏是本地可确定的缺口，不能倒推它们就是每场历史事故首因。

## 5. 可直接发给 GPT‑6 Sol 的续修提示词

```text
继续 DJ-LIVE-START-20260924 的本地修复。先完整读取：
/Users/gaominge/Documents/liftora/outputs/2026-09-28-dreamjourney-live-mic-start-review/run-01/复核结论与Sol续修提示词.md

同时以其中链接的9/27主设计为验收依据，保留run-01所有已有修改和红绿证据。

先修R1–R5：将启动截止/取消落实到真实DialogEngineManager的setup、StartEngine和pending operation；真实账号重绑即时取消旧attempt；补合法missingPolicyCache恢复；按HTTP请求序号记录曝光与最终票据未知结果；让本次新增后端诊断异常/阻塞不影响票据业务，commit标签绑定真实事务结果。每项补对应受控反例，不调用真实Provider，不用替换整个Manager的闭包证明其内部逻辑正确。

补MIC-01完整Controller同断言修前红、MIC-05旧回调对仍starting新场的隔离、MIC-19旧场保存保护，并完成原MIC矩阵剩余适用断言。诊断和测试不得跳过真实Gate或memory capture来声称业务通过。

先完成不依赖PG的本地工作，同时重建专用隔离PG环境；旧端口拒连不等于需要手机。禁止加载真实.env或连接生产。保留安装/启动/预检原始证据；如有确切外部安装限制列明，不阻断其他本地部分。完成MIC-15/16、D5后，在最终同版按short-A→logical20→short-B→logical65验证待确认候选、审核正式记忆与重建回查，每个长场前独立短场必过。

一次性交付具体改动、同断言红绿、原矩阵闭合证据、最终指纹、四场与回归结果；更新登记册并跑登记校验。满足适用本地标准才LOCAL_PASS；否则逐条写清实际未满足断言。历史慢启动和seq5未决继续保持，不用本地机制证明代替历史首因。

本轮不部署、不调用真实Provider、不连接/检测/等待手机、不操作生产或历史、不commit/push。真机由我后续主动发起；不能以手机未连接暂停本地工作。
```
