# 9/10—9/18 旧问题状态核对备忘

核对日期：2026-09-24。用途：供主问题登记册引用；不是新一轮设备验收，也不是当前产品缺陷的全量清单。

本轮只读核对既有设计、修复报告和验收记录，只新增本备忘。没有运行产品、测试、服务、Provider 或手机，没有访问生产或修改业务数据。

## 口径

- 历史 `DEVICE_PASS` 表示该版本、该场景确曾通过，不保证当前新版本也已通过。
- 后续明确修复和验收优先于早期问题单的 `FAIL`。不得将全部历史 `FAIL` 直接列为当前未修缺陷。
- `LOCAL_PASS` 不能替代真实 Provider、SDK、麦克风/扬声器或真机验收。
- “当前记录尚未找到后续专项通过证据”是证据缺口，不是证明产品当前仍然有该缺陷。
- 当前新版本的短场、20/65 分钟、Provider 及 9/24 登录后读取事件，由主登记册按最新记录单独登记；不要与以下旧编号合并为同一根因。

## 汇总

| 历史编号/问题 | 本次准确分类 | 尚需注意 |
|---|---|---|
| B1：Live 正式记忆未采用 | 历史真机通过 | 当前版本保持性验收；未启用的逐轮 RAG 不能直接记成未修缺陷 |
| B4、B4-8：审核、更正与正式入库 | 历史真机通过 | 早期 B4 总 FAIL 已被后续专项及 FM-POLICY 验收更新 |
| FM-POLICY-01：正式记忆读取策略过期 | 历史真机通过 | 不能据此排除新的全局 API 读取事件，也不能把新事件直接归因旧策略问题 |
| ISSUE-B6-01：已完成会后任务冷启动误显失败 | 原专项历史真机通过；后续“新旧场次 UI 归属”另有本地修复 | 9/15、9/17 后续归属问题不是 9/14 原场景已经失效的证据；当前专项真机仍需验收 |
| B7-QUESTION-AS-CANDIDATE-01：纯问题误成候选 | 已有本地修复与后续回归通过；专项真机闭合证据未找到 | 必须证明完整 Source 进入 Worker 后 noChange，不能以“没有候选”代替 |
| B8 / DJ-B8-DEVICE-01 / B8-S01：文字会话结束交接与即时读取 | 本地通过，文字入口专项真机闭合证据未找到 | 后续 Live 共享链通过不能自动覆盖文字入口 |
| Live 长回答自然播放/主动打断 | 历史真机通过 | 一分钟回答不等于物理 20/65 分钟稳定性通过 |
| DJ-ASR-OBS-01 及 9/17 单词识别偏差 | 独立观察未关闭，尚不足以定性稳定缺陷 | 不作为候选保存失败根因；需要转写对照和重复性证据后再决定是否开发 |

## B1：正式记忆采用

- 原始记录：[9/10 B1 分析输入](../2026-09-10-dreamjourney-live-second-round-fix/2026-09-10-Live-B1正式记忆未采用-Astra分析输入.md)。
- 设计：[B1 正式记忆未采用修复指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md)。该设计确认启动 JSON 双层 `dialog.dialog.system_role` 与供应商合同不符，并要求独立校验实际 SDK 出站结构；不是要求恢复旧逐轮 RAG。
- 修复及真实证据：[9/11 修复交付报告](/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/2026-09-11-DreamJourney-Live-B1修复交付报告.md:99)。实际四个正式事实问答经用户确认正确；真实 SDK 包装合同另有证据。其日志并未保留四题每一轮完整转写，不能扩大证据范围。
- 后续现场保持：[9/15 阶段汇总 B1](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-B7-B8-Live真机复测阶段汇总.md:25)：打断后仍采用正式事实“晨星”。
- 分类：**历史 DEVICE_PASS**。不能把早期 B1 FAIL 或未启用的逐轮检索重新列为目前已证实未修问题。

## B4 / B4-8：审核、更正、正式入库

- 设计集合：[连续审核假成功与候选读取恢复](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-12-Astra-B4连续审核假成功与候选读取恢复修复设计.md)、[更正预览 200 后失败与双重绑定](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-13-Astra-B4更正预览200后失败与双重绑定修复设计.md)。
- 早期真机状态：[9/14 更正预览与闭环复测报告](../2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md)：更正本身通过，但当时 B4 总状态受正式记忆读取策略过期影响而 FAIL。
- 后续覆盖证据：[9/14 FM-B6 及 B0-B8 总报告](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md:7)。第 4、7 节明确：更正预览、二次确认、正式写入、投影/向量、文字及新 Live 读取通过；B4、B4-8 均 PASS。
- 分类：**历史 DEVICE_PASS**。9/15 仅做候选只读刷新，报告明确保留历史 B4 PASS，没有重新执行全审核验收；不得称 9/15 又完整实测 B4。
- 局限：历史三个候选重复、新查询语义和 F3 实际影响仍按原报告各自 NOT_RUN，不自动升级为当前已证实缺陷；也不能用本项 PASS 证明所有重复、未知写和审核异常组合已测完。

## FM-POLICY-01：正式记忆只读恢复

- 原始输入：[正式记忆读取发布策略过期失败](../2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-正式记忆读取发布策略过期失败-Astra分析输入.md)。
- 设计：[FM-POLICY-01 修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-FM-POLICY-01正式记忆只读策略恢复修复设计.md)：列表、详情、人物归纳初始 gate 恢复及读取所有权修复；不是正式记忆未写入。
- 真机证据：[9/14 总报告](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md:32)：69 条正式记忆稳定可读，搜索/详情成功，人物归纳现场策略过期后同意图有界恢复成功。
- 分类：**历史 DEVICE_PASS**。9/24 新的登录后多入口读取异常必须使用新事件证据诊断，不能从症状相似认定是本旧问题复发。

## B6：冷启动恢复与后续新旧场次归属

- 原编号：`ISSUE-B6-01`。
- 原始记录：[重启后已完成会后任务显示无法继续整理](../2026-09-14-dreamjourney-b5-b6-device-test/run-2026-09-14-01/issues/ISSUE-B6-01-App重启后已完成会后任务显示无法继续整理.md)。
- 设计：[B6 会后任务冷启动只读恢复](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B6会后任务冷启动只读恢复修复设计.md)。设计明确：后台已接受且完成的任务，应只读收敛；从未接受的 end/ack/admit 不得自动补发并伪装完成。
- 原专项后续真机：[9/14 总报告](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-FM-B6及B0-B8真机验收总报告.md:35)：冷启动显示待确认记忆、唯一候选稳定、新 Live 不采用未审核事实、零业务写重放，B6_DEVICE_PASS。
- 后续独立问题：[9/17 持久化授权真机报告](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md:47)：本场候选确已生成，但旧工作流覆盖最近场次页面，单列历史状态 UI 仲裁 FAIL。
- 后续本地覆盖：[9/23 run-03 实施报告](../2026-09-23-live-save-unified-repair/run-03/implementation-report.md)：N3 新 blocked 场次拥有页面、精确冷启动读取、错绑定和跨账号隔离；[9/24 本地收尾](../2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md)确认为适用受控本地门禁 LOCAL_PASS。
- 分类：**原 B6 专项历史真机已通过；后续场次归属修复本地通过，当前版本该专项真机仍未由本备忘证明。** 不能简单记成“B6 从未修好”，也不能用 9/14 PASS 关闭后来独立发现的归属问题。

## B7：纯问题误生成候选

- 原编号：`B7-QUESTION-AS-CANDIDATE-01`。
- 原始记录：[9/14 纯问题误生成候选](../2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B7纯问题误生成候选-真机问题记录.md)。
- 设计：[B7 局部修复设计](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B7纯问题误生成候选局部修复设计.md)。
- 最后找到的专项现场证据：[9/15 阶段汇总](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-B7-B8-Live真机复测阶段汇总.md:12)：候选未增，但该场 owner/persisted/queued 为 0 且有未封存回合，不能证明完整 Worker 处理后 noChange，保留 FAIL/BLOCKED。
- 后续明确本地证据：[9/19 BE-IR 矩阵 BE-09](/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/reports/2026-09-19-BE-IR执行矩阵.md:20)、[9/21 KEEP-04](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/checklists/R01-R08-CAP-KEEP执行清单.md:47)：纯问题、确认问法、反问、助手诱导不生成伪事实；混合问句保留真实事实，回归 PASS。
- 分类：**本地修复与后续回归通过，当前记录尚未找到后续专项真机通过证据。** 后续已经补过本地组合与数据库验证，不应把 9/15 初始报告的全部环境缺失重新列为现存阻塞。
- 待闭合证据：同场完整 Source 进入 Worker，返回合法 noChange，候选集合保持不变，同时事实+问题的对照场保留事实；不可只观察 UI 没新增候选。

## B8：文字会话结束及恢复

- 历史编号：`B8`、`DJ-B8-DEVICE-01`、补充 `B8-S01`。
- 原现场：[9/15 阶段汇总](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-B7-B8-Live真机复测阶段汇总.md:13)：文字会话 end/ack 获得 201，ack 后没有观察到 admission 网络生命周期，页面 unavailable。
- 设计：[B8 文字会话结束 notObserved](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-14-Astra-B8文字会话结束notObserved局部修复设计.md)、[确认后交接中断](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01-B8确认后交接中断-问题分析与修复指导.md)、[提交后状态读取](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-16-Astra-01补充-B8提交后状态读取修复指导.md)。
- 本地修复：[9/16 ACK/admission 报告](../2026-09-16-dreamjourney-b8-ack-admission-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-DJ-B8-DEVICE-01-本地修复报告.md)、[9/16 S01 报告](/Users/gaominge/Documents/liftora/outputs/2026-09-16-dreamjourney-b8-submitted-status-read-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-B8-S01-本地修复报告.md:203)。后者明确修复版 iPhone 和生产 admission 后即时状态 NOT_RUN；第 229 节后保留文字场专项验收要求。
- 分类：**本地已修，当前记录尚未找到后续文字入口专项真机通过证据。** 9/17 Live 的 ACK/admit/status 成功只支持共享路径，不能追认为文字场实测。

## 音频长回答与主动打断

- 独立验收场景名：Live 长回答自然播放、Live 长回答主动打断；本次未找到需要另造编号的专项缺陷号。
- 原/最新直接证据：[9/15 Live 长回答手机端真机验收记录](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/2026-09-15-Live长回答手机端真机验收记录.md:6)。自然回答约一分钟完整播放，停音后文字没有继续变化，自动恢复聆听；约二十秒时主动插话，原回答立即停止，新问题被回应，并使用正确正式事实。
- 相关设计边界：[B1 指导](../../02-问题修复/记忆系统/候选与正式记忆/2026-09-10-Sol-Live-B1正式记忆未采用修复指导.md)明确保留原生 S2S、低延迟、播放与打断；本次没有找到并引用另一份专属音频修复设计，不虚构设计来源。
- 分类：**历史 DEVICE_PASS**。同场“手动停止后的状态”是 NOT_OBSERVED，不能混成音频 FAIL；该验收也不能替代整场采集、关闭交接和物理 20/65 分钟测试。

## ASR 偏差观察

- 编号：`DJ-ASR-OBS-01`。
- 原始观察：[9/15 B7 测试短语语音识别歧义](../2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/issues/2026-09-15-B7测试短语语音识别歧义-观察记录.md)：测试代号与回答主题出现歧义，缺少完整转写、重复输入和环境对照，不足以确认稳定识别缺陷。
- 后续观察：[9/17 真机报告](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md:37)：单词“真机”被识别成“侦缉”，代号正确；报告明确不影响该场保存链通过结论。
- 分类：**观察未关闭**。没有足够证据要求现在添加别名或语义补丁；未找到专项修复设计/复测闭合，明确保留为空，不以通用音频 PASS 代替。

## 本备忘未扩展的范围

Live 入口/令牌 503 与 nonASR 覆盖摘要/partial 的早期文件未在本次收束前完成原始—设计—后续证据逐项核对，因此不新增判定，也不从历史截图推定当前状态。主登记册可使用其他已完成核查的证据补入。

本备忘不宣布所有旧项在当前已部署版本全部通过。当前缺口应分别登记为“已确认未修缺陷”“本地已修待现场验收”“独立观察”，避免将三类混成新的故障总数。
