# Live 09-17 补充交付复核：真机前置尚未通过

复核日期：2026-09-17。结论：保留本轮已完成的实现和局部测试成果，继续第二份指导范围内的本地工作；暂不能认可 `READY_FOR_LIVE_DEVICE_RETEST`。

本次只读核对源码、测试代码、交付报告和已有 `.xcresult`。未重跑业务测试，未修改业务源码，未安装或运行手机，未访问生产。以下反例是静态审查发现，尚未由本次复核运行红测；应由 Sol 补成确定性本地红绿。

## 1. 已核实的成果与证据边界

- 当前五个核心文件 SHA 均与 [09-17 补充报告](../../../outputs/2026-09-16-dreamjourney-live-capture-continuous-save-fix/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live采集回归与持续保存-补充本地交付报告.md) 一致。
- 实际读取结果包确认：定向 10/10、OwnerTruth 492/492、音频/Echo 52/52；模拟器和通用设备目标构建均成功。
- 生产 canonical 接收已前移到 SDK 入口；start 发前原命令/曝光落盘、精确 start operation 匹配、BackendClient fresh inbox/ack 重载均有真实实现。
- 上述测试确实通过，但不能把未经过的路径写成已验证。当前存在调用链残余及组合证据不足，不能以总数推导全部 LC/TTL 门禁通过。

本复核沿用[第二份修订指导](2026-09-16-Astra-02修订-Live采集回归与持续保存修复指导.md)的既定范围：原始入口身份/关闭交接、原 start 持久恢复、fresh inbox/ack 贯通及真实组合验收。没有增加车机专项、历史 UI 仲裁或后端改造要求。

## 2. R1：ack 进入 fresh 流程前仍可能被旧 gate 拒绝

当前调用链：

- [Echo:2350](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2350) 仍向 acknowledgement UseCase 注入 `naturalInputPolicyAvailable`。
- [Echo:12393](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12393) 的生产闭包仍读取已捕获的 `requestServerPolicyManagedDecision(.echoTextInput)`。
- [Contracts:11202](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:11202) 的 `beginRequestOrFail` 和 `canCommit` 无条件检查该 Bool，未按 `requiresFreshRequestAuthority` 区分合法 fresh 路径。
- [新测试:1707](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1707) 使用 `releasePolicyAvailable: { true }`，绕过了生产中的旧授权接点。

影响：真实长场结束时，旧 route 已过期、当前 policy 允许，仍可能在 fresh inbox/ack 之前 unavailable。下层已经消费新 authority，不代表上层接点已贯通。

修复与验收：

1. 在要求 fresh authority 的本场路径中，用本次合法 authority 完成相应发前/回执提交判定，不能再被旧 route Bool 覆盖；保留 scope、lease、generation、实际当前拒绝等边界，不把 gate 固定为 true，不改全局旧 route 语义。
2. 从真实生产 gate 装配进入 ack：旧 route 过期/denied，但当前有效 policy allow，应走 fresh inbox 和唯一 ack POST。
3. 当前真实 policy deny 时零相应业务写；回执曝光后仍保留 B8 unknown 分类和禁止重放。

## 3. R2：start 持久恢复仍有两个未闭合分支

### R2a：unknown start 可以被 current 命中旁路接管

[Contracts:14818](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14818) 的 `receiveCurrentSession` 在 current 返回 resumed/active、product 和 entryMode 匹配时直接进入 ready，未先要求未知原 start 的 command/session 与精确 start operation 核实。

新恢复测试只构造 current=nil，不能证明 current 命中时仍遵守原命令边界。第二份第 7.6 节已经明确：裸 current 命中不能替代未知原 start 的 command 绑定。

要求：对于已持久化 mayExpose/outcomeUnknown 的原 start，current 无论有无结果都不能授权旁路恢复或重发。只有原坐标的精确 start operation 证据允许确认。测试覆盖 current 命中同 product 的不同 session、命中看似正确 session 但无原 command 证据，以及精确 operation 真正匹配；未知原 start POST 数始终不增加。

### R2b：当前对象丢失 start 回执后，即使只读命中也可能无法继续

- [Contracts:14605](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14605) 的 `preparedStartCommand` 为初始化时的 `let`。
- 新场 start 只生成并使用局部 command；当前 UseCase 的该属性仍为 nil。
- [Echo:2205](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2205) 在精确只读命中后调用 `bindReadOnlyVerifiedStart`。
- [Contracts:15467](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:15467) 又要求该初始化属性非空，因而当前对象恢复会直接返回；重建测试通过传入 prepared command 避开了此情形。

要求：当前对象和重建对象都从同一持久原命令状态恢复，不增加另一个会漂移的命令来源。补“新场 start 已提交但丢回执→保持同一个 UseCase→精确 GET 命中→ready→队尾合法首次 append”的测试；同时断言原 start POST 仍为一次、原 command/session 不变。

## 4. R3：原始身份冻结与关闭交接仍有两个间隙

### R3a：迟到旧问题可回滚当前问题窗口

[Manager:1213](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1213) 对 ASRInfo、ASRResponse、QueryConfirmed 的显式 questionID 无条件赋值给 activeQuestionID。

反例：登记 Q1→登记 Q2→收到显式 Q1 的迟到确认→收到不带 ID 的 Q2 正文。当前窗口会被旧 Q1 回滚，最后正文可能归入 Q1。

要求：已登记旧 Q 的迟到事件只更新旧成员，不改变当前开放接收窗口；只允许有合同依据的新窗口登记改变当前归属。补上述完整序列，证明 Q2 不被污染、Q1 重复/冲突仍遵守已有规则。原包无法辨识的歧义继续 fail closed，不靠正文猜身份。

### R3b：router 已冻结到 Echo 接收之间仍可能停止或换场

[Manager:1252](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1252) 在 freeze 内先解锁，再调用 deliver；close 不等待已冻结、未完成交接的事件。生产 deliver 只捕获 delegate，[Echo:14458](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:14458) 再取 `currentCanonicalTranscriptIngress()`，而非被冻结的原场 sink。

反例：A 事件完成 freeze→在 deliver 前暂停→停止 A 或打开 B→恢复 A 的 deliver。事件仍可能丢失，或通过同一个 Echo delegate 进入当前 B ingress。

要求：关闭边界必须包含 router 已接受但尚未交接的成员/正文；将交付绑定原场 sink、operation/epoch 和作用域，不能在迟到执行时改取当前 ingress。采用明确的串行交接或可等待的 in-flight/barrier 协议，避免简单持锁调用外部 delegate 引入重入死锁。

补确定性调度钩子停在 freeze 与 deliver 之间，分别验证停止、换场、换账号：停止前已接受材料在合法原作用域下排空；B 零旧事件污染；账号失效零跨账号落盘；stop 后新成员拒绝。当前 LC06 在同步登记已经完成后才停止，未覆盖这一间隙。

## 5. R4：组合测试的实际覆盖低于报告和清单描述

| 测试 | 实际覆盖 | 尚未证明的声明 |
| --- | --- | --- |
| [逻辑 20 分钟:1741](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1741) | 真实 UseCase + Spy；手工设置 allow/expired decision，直接提交 delivery，推进 10×120 秒，断言三次刷新和序号 | 未经过 Manager/Echo/Outbox/真实 FeatureGate/BackendClient；没有短断网、最终 stop 或 N 冻结 |
| [10 问:1844](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1844) | owner 解析→Echo→真实磁盘→UseCase + Spy；一次受控过期/刷新，10 条 owner 派送 | gate 为 true、手工 decision；未经过真实 transport/evaluator；没有 interim、最终 stop/N 验证 |
| assistant 输入 | [DEBUG helper:1267](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1267) 直接生成 complete | 未验证真实 assistant 文本流终态、分段聚合与播放分离的整场接线 |

处理要求：

1. 保留局部单测 PASS；把完整 LC-08、TTL-L04 及对应连接门禁恢复为 PARTIAL/NOT_RUN，直到有实际证据。更正报告中“已执行短断网、最终 stop 固定 N”等目前未由该测试执行的描述。
2. 增加同一装配场景，走实际 Manager 生产接收路径的可控输入、Echo、磁盘 Outbox、UseCase、真实 FeatureGate evaluator/缓存和 BackendClient/requestJSON。网络可以用 URLProtocol，时间/磁盘可以受控；不能用 always-true gate 和手工业务 allow 代替被测权限决策。
3. owner 输入包含 identifier-only、interim、明确 final；assistant 走受支持消息处理/文本流路径，不能在入口直接构造 complete。验证单次定稿、重复/迟到隔离以及按登记内容计算的关闭 N，不用硬编码 20 代替 N。
4. 在同一有效装配中跨多个 TTL、并发唤醒、短断网后恢复；对明确 deny 和未知写故障可使用隔离子场景，但每条清单结论须指向真实断言，不能将各段 mock 成功拼成整链 PASS。最终停止贯通 end→inbox/ack→admit/status 的受控网络链，核对唯一命令与准确状态。
5. 以上都是本地确定性验证，不要求先连接真实 Provider 或真机；真实 SDK 顺序和物理 20 分钟留给后续真机验收。

## 6. 交给 Sol 的补充提示词

```text
请先按《2026-09-17-Astra-Live补充交付复核-真机前置未通过.md》继续完成第二份范围内的本地修复。

保留已完成的 B8/B8-S01/S01-08、start 持久字段、Backend fresh 重载和 canonical 前移。逐项修复 R1 的上层旧 gate、R2 的 current 旁路与同对象 start 恢复、R3 的旧 Q 窗口回滚和 freeze→deliver 关闭交接。每项先建立原输入/原业务断言的红测，再修复到绿，不通过把 gate 固定 true、重建对象或省略交错来绕过问题。

按 R4 补真正经过 evaluator、BackendClient 和原始消息处理的 10 问/逻辑 20 分钟组合；测试包含实际断网恢复、最终 stop 和 N/结束交接。先更正当前报告中超出证据的 PASS/READY 声明，再根据实际结果更新。保留已有通过结果，不用总数替代逐项验收。

只做本地工作，不安装或启动手机、不部署、不处理历史记录、不 commit/push，不开始第三项历史 UI 仲裁。交付每项代码位置、对应红绿结果和仍未执行事项后停止，再复核真机前置条件。
```

## 7. 复核基线

本次引用行号对应以下版本，后续修改须按函数名重新定位：

- Contracts：`948dd28dee5b93b01a47cecf6a98ca491cef03d8369fcb9f82462df2ccdbb305`
- Echo：`253cb4b2598622ab07ee184394609891701924ffe3693a2e59b94232d75b3cd3`
- BackendClient：`02a5a36f55f6e9ca829894332d6bcd5384810ba46b572005ca8721d0f5369ae8`
- Manager：`37b215d01e032a3f041dcb79f2dcf9c7e00870c2fb25970f4240217743a7ed76`
- Tests：`fe18f9c777f08a4440faae10ddc3efc7735ed71a7c962bd3239caca331cf6c46`

原交付报告和测试结果包均保持不变；本文是独立复核意见，不把静态反例声称为已运行测试或现场复现。
