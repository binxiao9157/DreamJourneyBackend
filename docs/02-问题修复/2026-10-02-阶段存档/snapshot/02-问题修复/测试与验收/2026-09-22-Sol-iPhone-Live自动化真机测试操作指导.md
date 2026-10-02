# Sol：iPhone Live 自动化真机测试操作指导

> **9/29最新结论，优先于下方历史状态：** 新SHORT/SHORT02均完成候选→确认→正式→冷启动只读回查（3/4条）；同版LONG20在16分57.7秒失败，32次语音往返但服务器31轮，24轮预整理为16个内部atom，本场最终待确认候选0。65分钟未运行。用户已取消本次静音硬门槛；真实播放器保留。当前仅记录分析，须先讨论再修改，不重开或重放失败场。见[完整报告](../../outputs/2026-09-29-live-playback-device-retest/run-01/README.md)。


> **2026-09-29 更新，优先于下文历史 stage-12 说明。** 本轮三次真实短场均完成两轮语音、4条正文保存及候选发布，分别4/3/4条；完整正式记忆闭环仍未通过。原生播放完成事件仍为0，产品改用严格的实际player/decoder PCM一致性证明恢复聆听，不伪造SDK事件。工具已允许有绑定的V5纯新增方案，仍拒绝历史修改。Live单条确认已补V5绑定字段及字母前缀命令编号，本地正式路由+隔离PG对照通过；最新修复尚未安装复测。用户允许暂时拔掉手机，后续新场由用户主动发起。
>
> 最新入口：[9/29修复与测试报告](../../outputs/2026-09-29-live-playback-repair/run-01/README.md)。再次测试应新建stage并检查签名有效期，再独立短场→候选→审核→正式→冷启动回查，取得新短场凭证后才开始物理20分钟。不得复用失败SHORT/SHORT02/SHORT03的业务写，也不得沿用旧版本短场凭证。三场失败证据保留，20/65分钟均未启动。
>
> 当前每轮完成门禁区分原生事件与`verifiedPlayerPCMCompletions`；后者要求同reply/generation的非零样本数量及SHA256完全一致、合成结束后观测到200ms实际player静音样本。没有固定延时冒充完成，也没有关闭播放器。静音数字流仍不等于物理声学验收。


日期：2026-09-22。工具目录：`/Users/gaominge/Documents/liftora/tools/live_device_lab/`。

本文件用于复用本次自动化工具。请先完整阅读 [本轮实测与阻塞报告](../../outputs/2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)。测试结论以该报告和带 runID 的 JSON 证据为准；文档中的流程不代表全部已经通过。

**当前状态：工具本地保护测试 16/16 PASS；真实短场 FAIL；20 分钟 NOT_RUN。** 最终 short-08 发送了第一条用户语音并获得真实识别、页面文字与音频，但未恢复聆听，未发送第二条；停止后正文、end、ACK 成功，admission HTTP 500，本场候选与正式记忆为 0。工具完整自动审核/正式记忆成功路径尚未通过真机验收。不得因看到“工具已交付”直接开始长场。

### 收到文档后的首要工作

先做本地核对和修复，不要求用户接手机：

1. `DJ-LAB-ADM-01`：运行中后端错误读取 `context.authority_epoch`，但 `OwnerTruthCommandContext` 无该字段。本地已改为校验后的 `prepared.authority_epoch`。先验证现有修复，不再添加默认 epoch 或放宽权限。核对真实 Live admission 短/长场、过期 authority、幂等、unknown-write 和隔离数据库回归；形成 API/Worker 发布版本及迁移清单。
2. `DJ-LAB-SDK-01`：本轮实际 SDK 没有交付 App 等待的 3019/3020 原生播放回调。固定实际 SDK/最终链接指纹，核对其内部 2001/2002 分发及 App 的排空约定，局部修复或验证供应商兼容版本；不能用固定延时、伪造 playerFinished、提前恢复聆听或关闭播放器掩盖故障。保留分段音频、打断、迟到事件、reply/generation、停止和账号隔离回归。
3. 上述两项分别交付，不把“SDK 修好”当作“服务器 admission 已发布”。不把本次问题归为 DeepSeek 容量限制：本场没有创建整理任务。
4. 所有产品改动按本地反例→修复→同断言通过及受影响模块回归执行。真实 Provider、部署和真机结果单列。**没有手机也应完成本地开发交付**，不能以等待手机结束未完成的本地任务。
5. 发布和新真机运行由用户主动发起。本轮报告没有授权部署或重新处理 short-08。获发布授权后先核对实际 API/Worker 镜像或源码指纹、功能配置与迁移；不把本地版本当作实际后端版本。

## 1. 目标与不可改变的顺序

用户要求每次长场前先验证短场完整闭环：

真实 iPhone SDK 音频输入 → 火山 ASR → App 正常回复链 → 实际页面文字及 TTS/播放器完成 → 本地采集、落盘和同步 → 停止后的 end/ACK/admit → 本场待确认候选 → 用户已授权的本场审核 → 正式记忆 → 重启读取同一正式记忆。

按 `短场 → 物理至少 20 分钟` 执行。未来进行 65 分钟时重新执行独立短场。短场失败时，长场为 `NOT_RUN`，不伪造通过凭证，不跳过候选、审核或正式记忆检查。

本轮用户已授权使用当前测试/演示账号，并允许把本场合成候选确认到正式记忆。该授权不等于可以审核历史候选、修改旧正式记忆、恢复历史失败任务、清理数据或部署服务。

## 2. 工具的真实覆盖边界

1. Mac 将全新合成句子生成为 PCM 文件，不通过 Mac 扬声器播放。
2. 真实 iPhone 的实际 SpeechEngine SDK 使用 `STREAM` 输入；调用 `feedAudio` 的长度是 Int16 样本数，不是字节数。输入为 16 kHz、单声道、16 位 PCM，以真实 20 ms 帧率发送。
   输入只有一个发送任务；没有用户语音时也持续发送静音帧，不能在等待开场白或回复时停止音频时钟。停止按钮触发前先结束输入任务。
3. 不伪造 ASR、QueryConfirmed、final、canonical 事件；不把合成正文直接 POST 为对话，不使用 Mock Backend 或模拟 Provider 代替本次真机。
4. 当前源码的 `DialogLiveGroundingPlan.sessionSnapshot` 指定 `.provider`：Live 由火山直答并使用 SDK 内置播放器，正式记忆快照在会话开始时绑定。必须按这个真实路径测试，不得强改成 `/echo/answers` 逐轮代答。最终 stage-12 保留原 player 回调配置，只额外启用 **decoder PCM** 作只读观察，保持原播放器和回答模式。解码数据非静音不等于原生播放已经完成。
5. 使用真实 Echo 控制器的按钮处理函数和真实候选确认控制器，继续经过 UseCase、FeatureGate、BackendClient。它不是独立的 XCUITest 点击命中验证。
6. 关闭系统媒体音量不会关闭真实播放器。硬件麦克风拾音、实际扬声器听感、回声/环境噪音效果必须单独标 `NOT_RUN`，以后再做有声验收。
7. 当前脚本验证 Live 产生的文字与记忆档案持久化。**独立“文字回响”键盘输入入口、独立档案上传/录入入口没有被该 Live 脚本覆盖**，不得把 Live 中的文字刷新写成这些入口通过。

## 3. 准备与构建

保持手机有线连接、解锁、已信任电脑，开发者模式可用。测试版首次安装可能需要用户在系统内完成开发者验证；不能用修改系统信任或绕过签名的方式解决。

每次都检查签名与 provisioning profile 有效期，须覆盖计划短场、长场和收尾时间；不能复用本轮短期签名而假设次日或长场结束时仍有效。后端发布核对与工具本地验证先完成，再请用户主动安排真机。

用户将 iPhone 控制中心的**媒体音量**拉到底。响铃静音键不替代这一步。本次工具尝试自动设置媒体音量没有在该手机生效，已通过用户手动调零解决。不能禁用 App 的播放器来满足静音条件。

从 `/Users/gaominge/Documents/liftora` 执行，下面的目录名必须换成新目录：

```sh
python3 -m unittest discover -s tools/live_device_lab -p 'test_*.py' -v
python3 tools/live_device_lab/lab.py stage outputs/live-device-lab/STAGE
python3 tools/live_device_lab/lab.py build outputs/live-device-lab/STAGE --signed
python3 tools/live_device_lab/lab.py prepare outputs/live-device-lab/SHORT --profile short
```

工具在隔离副本中插入测试专用桥接；不修改或重置 `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。不要改用 `.xcodeproj` 绕过 Pods，构建入口是 workspace。Mac 沙箱中 `say` 可能返回空音频、Xcode/CoreDevice 可能无法访问系统服务；应使用正常主机权限执行，不能将其报告为产品失败。

测试 hook 必须同时满足 `DEBUG && LIVE_DEVICE_AUTOMATION`。普通 Debug 和 Release 不得启用。使用 `run` 启动环境才将 recorder 改为 STREAM；普通用户启动不应改变原麦克风输入模式。

## 4. 执行短场与长场

获取并核对当前设备 ID；不得使用模拟器 ID。以下 `DEVICE_ID` 为占位符：

```sh
python3 tools/live_device_lab/lab.py run outputs/live-device-lab/SHORT --stage outputs/live-device-lab/STAGE --device DEVICE_ID --current-test-account
```

短场包含两次输入，第二次补充第一次事实。既检查每轮真实语音和页面文字，也检查服务端 Source 包含每次实际 ASR 内容。候选必须在真实待确认读取结果中可见，且归属于当前场次的 Source。

只有生成 `SHORT/short-receipt.json` 且冷启动读取也成功时，才能执行：

```sh
python3 tools/live_device_lab/lab.py prepare outputs/live-device-lab/LONG20 --profile 20m
python3 tools/live_device_lab/lab.py run outputs/live-device-lab/LONG20 --stage outputs/live-device-lab/STAGE --device DEVICE_ID --current-test-account --short-receipt outputs/live-device-lab/SHORT/short-receipt.json
```

短场凭证绑定原源码、隔离测试源码、工具、完整签名 App bundle、设备、账号，一小时内有效，只能消费一次。完整 bundle 包括 `DreamJourney.debug.dylib`、framework、资源和签名材料，不能只哈希很小的 Debug 启动文件。修改任何相关代码或更换构建后重新测短场。不要把之前本地逻辑短场凭证当作真机凭证。

当前 schema 2 会在安装/复制/启动手机前检查门禁；实际账号仍由手机在开始 Live 前复核。此主机端补强在 short-08 之后完成，本地篡改和拒绝测试已通过，尚未重新运行真机。旧 schema 1 的 stage-01 至 stage-12 保留作证据，后续必须新建 stage，不得改写旧指纹伪装新版本通过。

后端部署、功能开关或模型配置在短场后发生变化，也必须重新进行短场。当前凭证不自动读取并绑定服务端镜像版本；Sol 需保留短场与长场前的只读发布版本核对记录，不能夸大主机校验的覆盖范围。

20 分钟脚本有 110 次用户语音输入，真实历时至少 1200 秒，最长预算 3600 秒；65 分钟脚本有 150 次输入，至少 3900 秒。较慢的真实回复会延长总时长，不能通过压缩时钟、截短输入或跳过回复硬凑 20 分钟。

当前长场脚本有重复事实与末尾补充，主要验证持续性、去重及保存闭环。它不等价于 110 个不同事实的语义容量测试，不代替全部维度、纠正/撤回、权限轮换和断网专项。

## 5. 证据必须如何验收

本工具只启动一次，在同一进程内完成预检后开始 Live，避免工具额外触发两次冷启动恢复。长场仍在设备端校验短场凭证对应的账号，失败时在 Live 开始前结束。

### 每轮真实交互

- 合成 PCM 哈希和序号。
- 实际 ASR final 与 Provider question 的哈希；不能由期望文本冒充实际识别结果。
- 真实页面用户文字、助手文字出现，记录各自哈希。
- 非静音回复 PCM 字节数大于零；实际播放器有完成回调并恢复聆听。
- 输入开始、语音输入完成、ASR final、页面文字、首段 PCM 和播放完成时间。
- 每轮采集已登记、已落盘、排队和已确认数量。最终 Source 逐轮匹配，不只检查总数。

时间是测量值；未预先约定延迟阈值时，不能笼统声称“实时性全部达标”，应给出实测范围与异常。

开场白单独判断：本次静音 STREAM 环境中，SayHello 有真实 TTS 结束事件和非静音解码 PCM，但没有观察到原生播放器开始/结束事件。最终 stage-12 仅用真实合成结束、非静音解码 PCM 和页面可输入状态结束开场白准备等待；short-08 的 `silentTailBytes` 和原生完成数均为 0，因此**开场白播放排空未获证实**。不得把它写成播放 PASS，也不得伪造产品播放完成回调。正式用户问答仍要求真实完成并恢复聆听，该断言本次正确失败。

### 保存和审核闭环

- 停止时完整覆盖，无无正文成员或 partial；合法 partial 应单独测试，不能伪装完整保存。
- 当前进程真正到达 `pendingReview`，并获得本场 reviewBatchID。
- 真实待确认入口读取能找到本场 batch，候选内容与 Source 绑定正确。
- 审核只能针对本场 candidateID；包含旧记忆变更的 proposedChangeSet 自动停止，不扩展权限。
- 审核记录为 accepted，正式 memoryID/versionID 存在，正式内容包含已约定事实。
- 冷启动后的新 PID 能读取相同 Source/candidate/memory/version 关系；不靠重新写入证明恢复成功。

## 6. 失败处置

预检失败：先检查 mode、HTTP 状态/错误类别、FeatureGate reason、媒体音量、系统签名。未开始 Live 的预检可以在保留原证据后纠正并重做；不要把它算作长短场业务失败。

开始 Live 后失败：保留 `result.json`、本场诊断、runID 和保存坐标，停止长场。工具最多通过真实停止按钮结束一次当前场，不自行重发 end/ACK/admit/review，不自动创建替代命令，不清理 outbox。出现未知写结果时优先只读核实。

当前工具失败快照可能早于产品异步关闭链完成：`liveOpenAfterFailure=false` 只证明已停止 Live。随后在不重启业务写、不点击核实按钮的前提下，只读采集**同场** outbox、completion 和服务器阶段/数量摘要，单独保存采集时刻，不覆盖最初 `result.json`。本次 short-08 的补充证据证明 end/ACK 已提交、admission 500，不能因早期快照 `closeIntentPersisted=false` 再开一个“未落 close intent”缺陷。只导出脱敏状态/数量/绑定，临时原始文件用完删除，不读取整个账号历史。

`launched.json` 和手机 `device-run-started.json` 防止重复启动同一业务场。不能删除这两个文件以重跑。若确认是工具本身的断言/接入问题，先说明证据和修正，再使用新 runID；保留原场记录。

不要因为设备不在场而中断后续**本地开发**。本地开发交付与真机测试分开；只有用户主动授权真机时才执行本文件。

## 7. 交付格式

至少分别报告：工具本地检查、签名构建、真实 Provider、真机短场、物理 20 分钟、候选确认、正式记忆、冷启动读取、独立文字/档案入口、麦克风/扬声器、部署、历史数据操作。

每项标 `PASS / FAIL / NOT_RUN / NOT_OBSERVED` 并链接证据。不得用模型回答成功代替保存成功，用页面文案代替数据库/读接口证据，或用上一个版本的短场代替当前版本门禁。

证据输出在指定 run 目录中，工具字段和更多操作说明见 `/Users/gaominge/Documents/liftora/tools/live_device_lab/README.md`。未经用户指示不清理本次写入的合成候选或正式记忆。

本次未产生短场通过凭证、未审核候选、未创建本场正式记忆，`long20-01` 只有准备好的音频，没有启动。交付时必须明确这几点；修复后新短场通过，再执行物理 20 分钟及其候选→正式记忆闭环。
