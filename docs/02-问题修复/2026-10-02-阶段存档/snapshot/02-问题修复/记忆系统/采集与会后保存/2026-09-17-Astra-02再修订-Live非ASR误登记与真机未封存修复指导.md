# Live 真机未封存：非 ASR 事件误登记用户回合

日期：2026-09-17。对应 `DJ-LIVE-DEVICE-01`；属于原 `DJ-LIVE-CAPTURE-01` 尚未通过的真机闭环。阶段：只读调查与开发指导，未修改业务代码、未重新运行设备或访问生产。

## 1. 结论与状态

**当前 Live 保存验收为 FAIL，不再保持 READY_FOR_LIVE_DEVICE_RETEST。** 之前 501 项本地回归通过的历史事实保留，但测试缺少真实 SDK 在首个 ASR 前的开场 TTS 事件。

确认的源码缺陷是：原始入口把非 ASR 事件也交给 canonical router；router 会根据其中的新 question ID 登记一条 owner 成员，但 `.other` 解析永远不产生用户正文。若这条空成员位于首位，Store 的连续封存保护会挡住后面已经 complete 的正常对答。

本次真实日志确有独立 question ID 的开场 TTS，随后两问都出现 final、两答都出现结束事件。这与上述机制高度吻合。**尚未读取本场 outbox 的逐成员内容及接收序号，因此不能宣称已直接证明五个未封存成员的组成，或排除所有并存缺陷。**

本项先修这个已能复现的入口缺陷，并补从真实 SDK 事件分类到 Echo/磁盘的组合证据；不再把主要原因笼统归为用户停得太快、ASR final 没到、授权过期或车载蓝牙。

## 2. 现场证据与计数含义

现场报告：[关闭交接内容未封存](../../../outputs/2026-09-17-dreamjourney-live-device-retest/run-2026-09-17-01/reports/DJ-LIVE-DEVICE-01-关闭交接内容未封存.md)。本次找回 Sol 原测试任务的完整 CommandExecution 输出，未制造新场次；原报告目录两份 devicectl 文件只有启动和退出信息。

白名单输出：[live-console.safe.log](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-device-failure-analysis/live-console.safe.log)。下面的 L 编号是原 stdout 行号，不是导出文件物理行号。

| 原行 | 实际观察 | 能证明什么 |
|---|---|---|
| L0394、L0396、L0397 | 开场 TTS，question 别名 ID-42，turnSequence=0，staleQuestion | 首 ASR 前确有携带独立问题身份的非 ASR 事件 |
| L0398、L0592 | 第一问 ID-44、第二问 ID-46 | 两个用户问题与开场问题身份不同 |
| L0572、L0849 | 两次 liveASRResponseObserved，isFinal=true | 不能把本次失败解释成两个 final 都没到 |
| L0585/L0590、L0864/L0867 | 两问对应 chatEnded、ttsEnded | 原诊断序列中两轮均已出现回答结束事件；不能仅按第二轮停止时序解释第一轮失败 |
| L0874、L0877 | owner=0、persistedOwner=0、queued=0；unsealed=5 | 尚未形成可交接的完整封存前缀 |

必须纠正两种计数解释：

- `persistedOwnerTurnCount` 在服务端回执确认并完成 outbox 确认后增加（[Echo:2188](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2188)），不是本地正文条数。其为零不能证明本地一字未存。
- `unsealedTurnCount` 包含尚无 delivery 的登记成员，可能包含被前置缺口挡住的 complete 后缀（[Store:16684](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16684)）。其为五不能证明五条都缺 final。

来源、输出指纹与可重复提取脚本在 [分析目录](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-device-failure-analysis)。只导出结构字段及临时关联别名，不复制正文、原始账号/场次标识或凭据。

## 3. 源码机制与最小复现

| 接缝 | 当前行为 |
|---|---|
| [Manager:4853](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:4853) | ASRInfo、ASRResponse、QueryConfirmed 之外全部映射为 `.other`，仍进入 router.freeze |
| [Manager:1282](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1282) | questionID/canonicalID/member 登记没有按事件种类限制；成员角色固定为 owner |
| [Manager:1384](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1384) | `.other` 的 parse 返回 nil；可能只有登记，没有正文 |
| [Manager:1316](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1316) | 原场 reserve 会接受这个错误成员；之前的排空屏障保证交接，但不能判断登记资格是否正确 |
| [Store:16781](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16781) | 首成员没有正文或不是 complete 时停止连续封存；这是完整性保护，不应删除 |

已运行当前 router 源码提取 probe：[运行说明及输出](../../../outputs/2026-09-17-live-device-failure-analysis/router-probe/README.md)。输入 `.other(Q0)` 后再输入合法 ASR final(Q1)，结果是额外 owner 成员存在、该成员无正文，而 Q1 final 本身正确。它执行的是提取的当前 Swift router，不是完整 App、UIKit、Store 或真机测试；不能以此替代下一节组合红绿。

已有停止边界用例从首个 ASRInfo 开始（[测试:1312](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1312)），没有覆盖生产开场前缀。这解释了局部排空测试通过但现场仍失败的证据缺口。

## 4. 修复范围

### 4.1 在登记之前限定 owner 事件资格

- 只有明确的 ASRInfo、ASRResponse、QueryConfirmed 路径能登记或更新 owner 成员。
- `.other` 可以保留回调序号和元数据供原有 SDK handler 使用，但不得创建 owner、消耗 owner 登记集合、调用 owner reserve，或改变 owner 的 active question 窗口。
- 不要直接屏蔽所有非 ASR SDK 回调；开场语音、助手正文/终态、TTS、音频及状态事件继续走原来的处理链。
- ASR identifier-only 是合法登记信号，必须保留。没有正文的合法用户成员仍要等待 final，不能被无差别过滤。
- 保留显式旧 question 不回滚窗口、无 ID 的后续片段归属、账号/engine/operation 绑定、停止前原场交接屏障和停止后真正新成员拒绝。
- 助手内容仍按自己的 role/reply 身份形成 canonical 记录，不能借 owner 白名单修复把助手事实混入用户证据。

### 4.2 保留关闭和未知写保护

不得用跳过首 gap、把 partial 强制改 complete、删除空成员历史记录、放开 closeIntent 后所有新 ID、提前 end 或重放 start/append/end/ack/admit 来使测试变绿。

此前已完成的 durable start、fresh authority、B8/B8-S01、unknown 只读恢复、账号隔离保持不变。此次没有证据要求改后端合同或部署服务。

### 4.3 补齐首缺口诊断与真实文案

诊断区分：登记成员数、无正文成员数、partial 数、complete 但被前置缺口阻挡数、已形成 delivery 数、服务端已确认数。至少能定位 firstGapOrdinal、role、reason、原始事件类别、接收序号、是否正文/是否终态、登记及 upsert 的接受/拒绝结果。

仅输出白名单枚举/计数/现有安全关联，不输出正文、原始标识、正文哈希、token 或完整 payload。生产默认无额外远端采集。

当前 [coverageGap 文案:12815](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12815) 无条件声称“已保存收到的内容”。应依据实际本地持久化快照区分“确有正文已保存但未完整”和“尚无法确认完整保存”，不得用服务端 ACK 计数代替本地正文证据。本项只校正本场提示事实，不实施历史多场 UI 仲裁。

## 5. 固定本轮验收范围

| 编号 | 受控红绿用例 | 必要断言 |
|---|---|---|
| D1-01 | 真实 SDK 类型映射：独立 Q0 的开场 TTS → Q1 ASR | 修前稳定产生额外 owner；修后 `.other` 无 owner/member/reserve，元数据和原事件处理仍保留 |
| D1-02 | 生产 Manager → Echo → 临时磁盘 Store：开场 Q0 → Q1/Q2 两问两答均 complete → stop | owner=2；四条真实对答形成连续 delivery；unsealed=0；最终水位=4；没有 Q0 owner；同场 end/ack/admit 的身份、次数遵守既有合同 |
| D1-03 | 首 ASR 前、两问之间、旧问题迟到的 TTS/Chat/非内容事件；之后无 ID ASR final | 非 ASR 不登记用户、不劫持 active question；合法 final 仍属于正确问题，不串场 |
| D1-04 | 合法 ASR identifier-only，暂时没有 final；再补 final | 未补时真实缺口仍阻止提前关闭；补齐后按序封存，不能把所有无正文事件都忽略 |
| D1-05 | 复用已有 q1 落盘、q2 冻结延迟、stop、切换 B 场及账号失效用例 | 原场排空、新场零污染、停止后新 ID 拒绝、账号失效拒绝均不回归 |
| D1-06 | 仅登记与已有本地正文两种 coverageGap 状态 | 文案分别符合可核实事实；诊断指出首缺口，而非只输出总数 |

D1-02 必须从真实生产事件分类入口进入，不能只手工给 router 喂 `.asrInfo`，也不能只模拟一个 Python selector。助手用生产 assembler/接线；不直接向 Store 填好 complete 来绕开本案入口。

对第二轮在回答尚未完成时停止，应保留真实 partial/gap 语义；“四条 complete、水位四”只适用于本表明确规定的两答已 complete 组合，不强迫所有主动停止场景假装完整。

先保存 D1-01/D1-02 的修前失败，再修生产代码并用相同断言重跑。定向通过后运行受影响 OwnerTruth 回归、Echo/音频保持性及设备目标构建；不增加与本问题无关的新验收项目。

## 6. 真机顺序与独立交付

1. Sol 先单独交付本项代码、配对红绿、指纹、诊断说明及未运行项，标 `LOCAL_PASS / DEVICE_PENDING`；历史现场 FAIL 保留。
2. 在用户安排的真机阶段先做一场最小完整对答闭环，包含真实开场语音；同场核实 canonical 登记、正文持久化、封存水位、end/ack/admit、Worker 及候选 GET。只凭候选总数、HTTP 成功或“已进入”文案不能通过。
3. 首场仍出现首 gap、零交接或错误归属就停止，保存脱敏事件与本地快照摘要，不继续制造场次。跨 TTL、物理二十分钟和 B7 继续保持未执行或阻断，直到其前提成立。
4. 第三项 Echo 历史状态仲裁另行交付；本项不得同时修它，也不清理之前产生的失败 outbox、候选或历史恢复任务。

## 7. 可直接发给 Sol 的提示词

```text
只处理《2026-09-17-Astra-02再修订-Live非ASR误登记与真机未封存修复指导.md》，暂不处理 Echo 历史状态仲裁。

真机两问都有 explicit final，首 ASR 前还存在独立 question ID 的开场 TTS。当前 router 对 .other 也登记 owner，但 parse(.other)=nil；最早的空成员能阻塞后续完整对答。源码提取 probe 已证明这条缺陷；现场五槽组成尚未直接核实，不要把推断写成唯一现场根因。

先补真实 SDK 类型映射→Manager→Echo→磁盘的开场Q0+两问两答红测，再把 owner 登记限定到 ASRInfo/ASRResponse/QueryConfirmed。.other 保留原 SDK 元数据/序号和正常处理，不创建用户成员或改用户窗口。保留 identifier-only、显式final、原场reserve排空、停止边界、账号隔离、fresh authority 和 unknown只读恢复。

按D1-01至D1-06完成本地验证，补首缺口结构诊断；区分本地正文持久化、封存和服务端确认计数，校正coverageGap文案。不要跳过真正缺口，不删历史数据，不重放未知业务写，不改后端、音频或车机逻辑。交付后停在LOCAL_PASS/DEVICE_PENDING，先不自行安装或继续真机。
```

## 8. 分析源码指纹

- DialogEngineManager：`4c14caac71e41786f07804f1c821945bcafbb853dd9faba55ced1ff85401d700`
- EchoViewController：`303f4f5319c1d14355d6ad4ce33979543bd146cce9b1035eae36cd335b5f493d`
- OwnerTruthContracts：`f5e9d6970d6dec3cd4c2d85a0b5baccb89dd4af12be082f7013e574104772bd5`
- OwnerTruthContractsTests：`14f368c63f881cf12e17296d9d0169805eb3fe01c2670dd6a30c9b7673015ef8`

这些是本次读取的工作树指纹，与上一轮本地交付对应。真机安装来源及设备包指纹以原执行记录为准；本次未重新安装或读取设备数据。
