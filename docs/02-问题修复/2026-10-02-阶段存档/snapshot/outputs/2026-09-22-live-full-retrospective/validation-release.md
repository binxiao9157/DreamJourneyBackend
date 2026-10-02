# 9 月 18–22 日验收与发布复盘

复盘日期：2026-09-22。范围是本地交付、Astra 复核、执行矩阵、已有真机记录、发布/回退记录，以及与两处线上 500 相关的当前本地源码。仅只读分析，未修产品代码，未执行测试、部署、连接手机或生产，未读取原始私人对话、认证材料或密钥。本文不提出改造方案。

**结论：范围内没有一条完成“物理长场 → 全部正文落盘 → end/ACK/admission → 本场候选内容核对 → 审核 → 正式记忆 → 重建读取”的真机 PASS。** 但有 9 月 19 日真实短场完整链 PASS、9 月 20 日真实生产发布、9 月 20 日与 22 日逐步完善并最终获得 Astra 有限认可的本地链路 PASS。它们都是真实进展，不能相互替代。

反复测试仍失败涉及三个不同层次：产品缺陷被过于局部或理想化的测试漏过；若干轮本地交付把未覆盖部分一并写成整体通过；本地修复积累后，9 月 22 日真机所连后端仍保留已知旧缺陷。不能把此前所有失败统称为“没部署”，也没有证据把未获授权的后续部署当作应当擅自执行的工作。

## 1. 实证时间线与完整 PASS 的边界

| 日期/阶段 | 实際证明 | 未完成或失败位置 | 可成立的结论 |
|---|---|---|---|
| 9/18 认证同步本地修复与最终集成 | 状态恢复、认证和有关本地回归有交付报告 | 本地不包含完整真实 SDK、真机长场候选与正式记忆链 | 本地局部证据，不能外推真机长场 |
| 9/18 真机长场 | 20 分钟以上；16 用户轮、32 消息均服务器确认；end/ACK/admit 各 201；有多次 queued→organizing 状态读取 | 轮询耗尽进入 statusUnknown；原报告的 pendingReview 归属后来被 session→Source→job 唯一关联纠正：22:52:14 首次 failed、0 候选，responseContract.invalid；5 项标记未出现；自然 401 未观察到 | DEVICE_FAIL；候选未生成，不能解释为只是列表不可见 |
| 9/18 当晚真机短场 | 两用户轮，4/4 接收记录确认，end/ACK/admit 成功 | 23:19:53 首次处理 failed、0 候选、responseContract.invalid；第 6 次 GET 已见失败终态，并非轮询预算耗尽；详细检查点未保留 | 短场 FAIL；与长场同一宽泛码不证明同一具体检查失败 |
| 9/19 真机短场 | 两条事实形成两候选，候选 46→48；陈旧 authority 在写入前被拦截，刷新后重新读取/确认成功；纠正文案后正式记忆正确；重启、正式读取、文本/Live 检索和纯提问保护通过 | 不证明同版本长场及更高输入形状 | 真实短场完整链 PASS，应保留 |
| 9/19 真机长场 | 约 20 分钟，39 用户轮、77 总消息完整保存；end/ACK/admit 201 | Worker 第一次 organization 返回 8 条后 support.factOmitted；第二次 schemaInvalid；最终 retriesExhausted，0 本场候选；后续正式步骤 NOT_RUN | 真实候选整理合同/处理失败，不是采集丢失，也不是一句“未部署”能解释 |
| 9/20 batching run01–03 | 本地单元、PG、较大输入等逐步补证 | 每轮 Astra 都找到确定性反例或证据拼接，详见第 2 节 | 各局部绿测有效，整体 LOCAL_PASS 多次先于证据 |
| 9/20 batching run04 | 同一个 F-65 Source：73,728 字、301 总消息、150 用户轮，149 候选；含其他场合计 190 正式记忆；真实解析/服务逻辑、受控模型 HTTP、隔离 PG | 主长链显式注入同类 extractor；默认装配由另一 PG 场景验证；真实模型、物理长场、SDK 均不由此证明 | Astra 认可有限 LOCAL_PASS |
| 9/20 run04 生产发布 | 明确授权；0122 迁移 apply/verify；API 和 6 个启用长期 Worker 对齐；健康检查通过 | 当时业务验收状态明确仍为 DEVICE_RETEST_PENDING；短场大于 8 事实 NOT_RUN | DEPLOYED / INFRASTRUCTURE_READY 成立，业务完整 PASS 不成立 |
| 9/20 发布后真机长场 | 约 20 分钟、人工数 33 用户轮，语音一直可继续 | 后续诊断仅前 13 用户轮/26 消息到 outbox 和服务器；没有 close intent/end；停止后 unavailable，0 候选。前置预整理曾成功不等于最终候选流程已运行 | 真机 FAIL；客户端采集所有权/关闭链有缺陷 |
| 9/21 capture run01–03 | 从单向桥/数据重绑，推进为真实 Controller/Coordinator/磁盘/FeatureGate/BackendClient 双向 HTTP 与隔离 PG；run03 两 Source 为 4/300 总消息，长场 150 用户+150 助手，1/16 候选、17 正式记忆 | run01/run02 多项真实缺陷；run03 尚有门禁、跨批断言及逻辑时间缺口；真实 SDK/部署/真机仍 NOT_RUN | 逐轮改进真实，但三轮整体结论仍须受对应 Astra 复核限制 |
| 9/21–22 round run04–06 | 实际双向客户端链、逻辑时钟、真实 HTTP 故障与内容绑定逐步强化 | run04 配置/二进制/身份验证不足；run05 长反例未到模型入口；run06 错证据引用仍被受控接口接受 | 正常链通过不等于全部验收完成 |
| 9/22 round run07 | 4 份 xcresult 各 1/1 PASS、0 fail/skip；独立 short→logical20→short→logical65；长场总消息 220/300，候选 4/17，含短场正式记忆 5/18；候选在 iOS 被读取；Store 重建一致；C1 正/反例实际到受控接口 | 逻辑 65 分钟的实际测试约 62 秒；真实模型、设备、部署仍 NOT_RUN；见第 4 节装配边界 | Astra 明确认可限定 LOCAL_PASS，未宣称生产或真机通过 |
| 9/22 自动 short-08 | 1 用户+1 助手的 ASR/文字/可解码非静音音频；停止后的正文、end、ACK 成功 | 播放完成事件未出现，未进入第二轮；随后 admission 500，无 admission/job/候选/正式记忆；物理 20/65 分钟 NOT_RUN | 短场门禁 FAIL；SDK 恢复聆听与后端 admission 是独立故障 |
| 9/22 人工短场对照 | 手机可见 3 轮，语音和返回聆听正常；新增 outbox 证据有 3 用户+3 助手，均 complete/sealed、无 canonical issue，关闭水位 6 已持久化 | 服务端只有 2 用户+2 助手、active、无 batch；seq5 outcomeUnknown、seq6 preparedNotExposed；delivery-status GET 500 阻断未知结果恢复；初始未知结果成因待定 | 本地完整采集成立，同步/关闭/候选完整成功不成立 |

来源：[D013 · 2026-09-18-DreamJourney-Live长会话认证同步-本地修复报告](../2026-09-18-dreamjourney-live-l20-auth-sync-completion/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步-本地修复报告.md)、[D016 · 2026-09-18-DreamJourney-Live长会话认证同步真机复测报告](../2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步真机复测报告.md)、[D018 · 2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告](../2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告.md)、[D023 · 2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md)、[D024 · 2026-09-19-DreamJourney-Live候选整理-真机阶段记录](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md)、[D044 · 2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md)、[D047 · 2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md)、[D048 · 2026-09-20-DreamJourney-Live长对话run04生产发布记录](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长对话run04生产发布记录.md)、[D059 · 2026-09-21-DreamJourney-Live采集中断修复-run03本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md)、[D073 · 2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告.md)、[D075 · 2026-09-22-DreamJourney-Live短场门禁真机失败记录](../2026-09-22-dreamjourney-live-device-retest/run-01/reports/2026-09-22-DreamJourney-Live短场门禁真机失败记录.md)、[D076 · 2026-09-22-Sol人工短场与自动短场对照分析](../2026-09-22-live-device-lab/comparison-sol-short/2026-09-22-Sol人工短场与自动短场对照分析.md)、[D077 · 2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)、[D099 · 2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)、[D104 · 2026-09-22-Astra-Live逐轮模拟-run07复核通过](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md)。run07 的测试计数、时间与指纹另有 [Astra current-verification.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/current-verification.json)。

9/18 长短场后续关联还补足一个边界：服务器接收记录为 32/4 条，但整理 Source 只取首末用户消息窗口，实际是 16 用户+15 助手、2 用户+1 助手，最后助手回合未在 Source 内；全部用户正文在内。没有证据证明这个末段助手窗口导致本场失败。两场各只有一次 failed attempt，`candidateExtractionRetriesExhausted` 当时是统一终结标签，不能据标签说它已重试多次。[D019 · 2026-09-18-Live长短对话候选缺失-原因核查](../2026-09-18-live-short-repro/run-01/2026-09-18-Live长短对话候选缺失-原因核查.md)、[D020 · observations](../2026-09-18-live-short-repro/run-01/observations.md)

9/20 真机失败的最初记录没有拿到全部内部链条，联合诊断后来确认采集停止边界，并用真实本地组合复现：canonical conflict 推入 unavailable 后，Controller 的观察逻辑关闭入口、移除活动采集对象，后面回合和 finish 无法继续传递。**这一程序缺陷与现场相容，但没有证据确定它就是当场首次触发**；滚动日志覆盖了首错时段，owner/assistant 冲突和局部磁盘异常等都未能唯一排除。不能把已复现结构性缺陷写成已取证的现场首发事件。[D092 · 2026-09-20-DreamJourney-Live-run04长场失败-内外部联合诊断](../../02-问题修复/记忆系统/长对话整理/2026-09-20-DreamJourney-Live-run04长场失败-内外部联合诊断.md)、[D099 · 2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)

## 2. 哪些“LOCAL_PASS / READY”超过了当时证据

9/18 的认证复核还展示了另一种误判：不能把测试失败数直接当作产品缺陷数。原 517 项中 9 个失败方法，后来在业务源码不变的隔离副本中，仅修正“恢复时已过期的新凭据”、deny 场景先等 start 后输入的错误顺序，以及 fresh decision/异步等待，9 个方法分别转绿。另一个真实缺陷则有独立同断言红绿：认证恢复准备失败时提前领取唯一重试名额，再把准备失败当成原 append 未发送，触发非法磁盘状态回退；局部原型最终从确认 48 排空到 70 并关闭，569 项回归通过。不能用先前夹具失败证明这个产品根因，也不能据原型反推原生产 401→403 的唯一 deny 原因已查清。[D079 · 2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导](../../02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md)

更早两份认证指导明确记录：名为 green 的结果包实际 1/2；BUILD_FAIL 的测试执行数为 0；35 问答沿用每轮 120 秒实际为 68 分钟，不能称逻辑 20 分钟；GET 替身无论是否有策略 headers 都答 200，不能证明真实授权合同；一个路由映射断言、手写 retry-exposed 枚举、直接换 successor 均不能替代真实刷新/HTTP/崩溃恢复组合。它们后来被更新文档纠正，不能把旧快照的“未实现”不加时间地套到最终代码上。[D080 · 2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案](../../02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md)、[D081 · 2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导](../../02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md)

下面按交付与紧接的独立复核成对列出。这里的判断不是把所有绿测作废，而是指出被绿测漏掉、又被总结覆盖的范围。

| 交付 | 本地报告的结论/主要绿证据 | Astra 找到的反例与覆盖不足 |
|---|---|---|
| batching run01 | LOCAL_PASS；122 个后端测试、530 个 iOS 测试等；LM/LI 清单通过 | 同一用户轮 12 个事实仍受 8 条上限；关系步骤伪造内容可被接纳；仅按 turn 集合校验会漏掉同轮 11/12 原子；失败 reason 处理可崩溃而无终态。PG 与容量场景不足；协调器测试不能证明真实 Controller；relay 字节计数不能证明音频播放。[D033 · 2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告.md) / [D089 · 2026-09-20-Astra-Live长对话分批整理-交付核查与剩余修复要求](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-交付核查与剩余修复要求.md) |
| batching run02 | LOCAL_PASS；大输入和关系/重复补证 | 正常复述的支持证据仍不正确；合法补充跨页被判 factOmitted；同轮混合事实/问题可被排除；failed 父任务仍可多发请求。PG 40+1 场采用注入 extractor，不能代替默认预整理装配；第 7 次状态 GET 未覆盖。[D036 · 2026-09-20-DreamJourney-Live长对话分批整理-run02本地交付报告](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/reports/2026-09-20-DreamJourney-Live长对话分批整理-run02本地交付报告.md) / [D086 · 2026-09-20-Astra-Live长对话分批整理-run02复核与剩余闭环](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run02复核与剩余闭环.md) |
| batching run03 | LOCAL_PASS；F-65 入场、大输入和正式记忆证据 | 正常 paraphrase 的 support 绑定仍回退成过大片段；73k F-65 的 admission 与另一个 Source 的 41 条正式记忆不能拼成同一全链；Debug stub 哈希不能代表业务 dylib。[D038 · 2026-09-20-DreamJourney-Live长对话分批整理-run03本地交付报告](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-DreamJourney-Live长对话分批整理-run03本地交付报告.md) / [D087 · 2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md) |
| batching run04 | LOCAL_PASS | 上述收尾得到有限认可；真实 F-65 同 Source 的 149 候选链成立。复核同时写清显式注入与默认 runtime 分场验证，不包含真实模型和真机。[D044 · 2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md) / [D088 · 2026-09-20-Astra-Live长对话分批整理-run04复核结论](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run04复核结论.md) |
| capture run01 | LOCAL_PASS；547 个 iOS 绿测等；CAP-15 桥接与较大 PG 链 | 部分测试断言固化错误行为：interim 可盖 final、QueryConfirmed 提前封口、单回复缓存丢交错消息；冲突禁写测例实际上关闭了网络策略；close intent 只在 drain 后写，而测试期待此前为 nil；overflow 仅内存。CAP-15 由 URLProtocol 导出再 Python 重绑，并非真实响应驱动；新 authority 分支未有效覆盖。[D053 · 2026-09-21-DreamJourney-Live采集中断修复-本地交付报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/reports/2026-09-21-DreamJourney-Live采集中断修复-本地交付报告.md) / [D095 · 2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求.md) |
| capture run02 | LOCAL_PASS；553 PASS+1 SKIP；真实 2+9 轮双向链 | 旧 generation 先污染缓存后校验；完成回复身份 `Set.prefix(32)` 无序淘汰导致旧片段重开；持久 manifest 两个竞争窗口；Controller 释放仍受 terminal guard 影响；关键诊断记录互相挤掉。单独大 PG 场不能与 2+9 双向场拼为 150 用户轮。[D056 · 2026-09-21-DreamJourney-Live采集中断修复-run02本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/reports/2026-09-21-DreamJourney-Live采集中断修复-run02本地报告.md) / [D096 · 2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md) |
| capture run03 | LOCAL_PASS；真实短场后 150 用户轮、候选审核、正式记忆与重建链 | 正常链确实推进；门禁依赖缓存指纹及仅 7 文件清单可绕过；重复验证未跨实际整理批；`requestClose` 默认值可能清空 resolved；150 轮约每轮 1 秒不能等于逻辑 65 分钟。[D059 · 2026-09-21-DreamJourney-Live采集中断修复-run03本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md) / [D097 · 2026-09-21-Astra-Live采集中断修复-run03复核与局部收尾](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run03复核与局部收尾.md) |
| round run04 | `LOCAL_PASS / READY_FOR_SEPARATE_DEVICE_AND_PROVIDER_RETEST`；SIM/GATE 清单 PASS | Astra 明确降为 `NORMAL_CHAIN_PASS / LOCAL_ACCEPTANCE_INCOMPLETE`。运行 BASE64 配置与磁盘哈希可能分离；遗漏 main/PG/support/runner 依赖；只验 test binary 未验业务 dylib；按位置或正文验证身份不等于完整 Source binding；受控模型按序号答、支持器近乎一律 supported；候选可见证据曾只在 Python；伪时间未实际驱动 FeatureGate；真实 HTTP 401/未知写/429/timeout 不完整。[D062 · 2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告.md) / [D094 · 2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾](../../02-问题修复/测试与验收/2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾.md) |
| round run05 | LOCAL_PASS；真 HTTP 401、刷新、已保存但响应丢失后的 GET 恢复补证 | 配置/制品/实际故障验证已有实质改进，但依赖漏 4 项，身份仍按 zip 位置配对且回执偏计数；正式内容偏数量/关键词；伪事实仍被支持/关系受控接口接受；长反例 220/300 消息先被 200 turn 上限拦截，接口调用 0，不能说反例已被目标语义护栏拒绝。[D065 · 2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md) / [D102 · 2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md) |
| round run06 | 有限收尾；25 依赖、身份/关系和长反例输入边界补齐 | C1 “事实正确、引用另一无关用户问题”仍被受控接口放行。Astra 定位为验收工具缺陷，拒绝扩大为全验收 PASS；不是因此否认真实正常链。[D070 · 2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告.md) / [D103 · 2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md) |
| round run07 | C1 最后收尾 LOCAL_PASS | 修复事实→允许证据绑定，保留修前红证据，2 正常/2 负面对照实际调用接口；Astra 认可约定的本地收尾，没有再宣称真实模型/真机/部署通过。[D073 · 2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告.md) / [D104 · 2026-09-22-Astra-Live逐轮模拟-run07复核通过](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md) |

这里有两类问题：一类是产品确实仍有错误；另一类是测试输入、替身或断言没有走到声称验证的路径。后者会造成“改了一轮、测试全绿、复核又找到确定性反例”的循环。独立复核在这些轮次发挥了实际作用，但交付报告的总状态多次早于独立复核的完整证据。

## 3. 短场回归门禁何时真正加入

短场先行要求至少已见于 9/17 认证主设计的 **9/18 修订版**：第 9 节要求新包先通过同场 end/ACK/admit、真实唯一候选与冷启动保持，短场失败立即停止，不继续长场。文件经过修订，不能仅凭文件名把这条精确到 9/17 最初版。[早期认证设计](</Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md:279>)

短场测试并非 9/21 才第一次出现：9/19 已有真实短场完整链 PASS；9/20 run04 后续清单也有短场→20 分钟→65 分钟安排。但 9/20 发布/真机记录中的大于 8 事实短场仍为 NOT_RUN，随后进行了长场并失败。不能把这段历史改写为早已在入口强制执行了最终门禁。[D024 · 2026-09-19-DreamJourney-Live候选整理-真机阶段记录](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md)、[D046 · 2026-09-20-后续真机验收清单-run04](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-后续真机验收清单-run04.md)、[D048 · 2026-09-20-DreamJourney-Live长对话run04生产发布记录](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长对话run04生产发布记录.md)

**明确的“每次修复后、每次长场前、同一最终源码与配置、独立短场候选+正式链先通过”的硬要求，出现在 9/21 主指导第 12 节及 capture run02 Astra 第 8 节；两份原文都将其标作用户新增要求。** 原 CAP-15 把短长放在同一测试，短场只等到 pendingReview 就接长场，没有在长场开始前验证候选实际正文与审核/正式记忆。Astra 当轮独立跑出 `LOCAL_SHORT_GATE_PASS`，但也明确只属于当时源码，继续修改后要重跑。[D099 · 2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)、[D096 · 2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md)

capture run03 把短场与长场做成独立执行，并补成 150 用户轮双向链；后续 round run04–07 再逐步加配置、源码、宿主 dylib、实际身份、逻辑时钟和故障证明。这是从“有短场测试”到“门禁本身可审计且难绕过”的过程，不能用最后的门禁质量倒推前面各轮已具备同等保护。

9/22 真机操作指导进一步要求短场收据 schema 2，绑定完整 App bundle、设备、账号、工具及源码，一次性且一小时有效。但原文也承认它**不会自动绑定实际后端镜像和配置**，实际发布版本核对仍是独立证据。因而本地源码/收据一致并不能单独保证手机连到同一后端版本。[D105 · 2026-09-22-Sol-iPhone-Live自动化真机测试操作指导](../../02-问题修复/测试与验收/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md)

## 4. 实际默认装配、SDK、受控模型和隔离 PG 到底覆盖了什么

**隔离 PostgreSQL是真实且有价值的证据，不能称为纯内存 mock。** 本地后期测试确实走真实 HTTP 路由、真实 Store/SQL、候选审核、正式记忆和 Store 重建。run07 的客户端候选读取也已补成 iOS 实际响应，而非只在 Python 中查看数组。其限制在于入口和装配是否等同运行环境，以及外部响应是否过于理想。

- 9/20 run04 F-65 主链使用生产同类 extractor，但由测试显式注入；默认 runtime 和预整理交接是另一 PG 场。分开测试可以证明组件各自行为，不能自行推出同一长场从默认启动装配到所有异步交接均已通过。Astra run04 原文已写出这一边界。[D088 · 2026-09-20-Astra-Live长对话分批整理-run04复核结论](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run04复核结论.md)
- 当前 run07 server 显式设 `main_module.settings = runtime` 且打开长管线 flag，使用真实 PG；之后在 Source 就绪后构造 `controlled_extractor`、传给 `DiagnosticWorkerRuntime(extractor=...)`，再手动赋 `worker._live_preorganizer = extractor._live_extractor`。它证明受控输入下这条双向业务链，不能称为完全未经替换的生产默认 CLI/factory 启动。也不能仅凭这场证明真实会话进行中、后台预整理连续并发的全部生命周期。[装配与手动注入](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/tools/cap15_round_simulation_server.py:2070)
- 早期受控输出先后存在按位置答题、支持一律成功、错误引用仍可成功的问题；Astra 用反例逐轮证实，run07 才关闭本轮最后的 C1 错证据引用缺口。即使它通过，也仅证明受控响应及解析/后置断言，不能等同真实模型会在所有自然转述、密集同轮事实或长原文形状下遵守合同。[D094 · 2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾](../../02-问题修复/测试与验收/2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾.md)、[D102 · 2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md)、[D103 · 2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md)、[D104 · 2026-09-22-Astra-Live逐轮模拟-run07复核通过](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md)
- 本地 Swift 事件注入、Coordinator/Controller 测试不等于真实 Speech SDK 回调、音频播放完成及 mic 恢复的端到端证据。9/22 short-08 正是文字和音频包已经有了，但 native player completion 缺失而未开始下一轮；人工短场能恢复聆听又说明不能把所有场景一概归到 SDK 同一故障。[D077 · 2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)、[D076 · 2026-09-22-Sol人工短场与自动短场对照分析](../2026-09-22-live-device-lab/comparison-sol-short/2026-09-22-Sol人工短场与自动短场对照分析.md)

本次还发现两项**当前源码可直接核对的覆盖边界**，不将它们写成已经复现的历史失败：

1. `backend-owner-truth-live-candidate-formal-postgres-smoke.py` 从 `Settings.from_env()` 取 base，为 TestClient 替换 Store/auth/policy，但没有显式替换 `main_module.settings`。它在 HTTP admission **之后**才为 Worker `replace(...owner_truth_live_long_memory_pipeline_enabled=True)`。所以最终摘要中的 `admissionUsedHTTPRoute=true` 加 `longPipelineEnabled=true`，不能证明 HTTP admission 当时走到了启用 flag 的 Live binding 分支。历史环境 flag 未有本次可确认的完整快照，不能进一步断言它当时一定是 false。该差异解释了为什么“大 PG 链 HTTP admission 成功”仍未排除后来 CAP-15 才暴露的 `context.authority_epoch` 分支崩溃。[smoke 装配入口](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:830)、[Worker 设置](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:1053)、[HTTP 路由实际读取的 settings](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:11866)
2. run07 `PROTECTED_SOURCE_PATHS` 的 25 文件清单仍没有 `app/services/owner_truth_interview_candidate_proposal.py` 与 `app/domain/owner_truth/interview_candidate_proposal.py`，恰是 `authority_epoch` 修复依赖；`current_source_fingerprint()` 仅散列列出的路径。因此“所有实际依赖变化都会使短场收据失效”仍不能由此清单证明。此处只证明清单遗漏，**不证明 run07 实际运行发生了版本混用，也不推翻当次 xcresult**。[25 文件清单与散列函数](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/tools/cap15_round_simulation_server.py:72)

## 5. 两处线上 500：最早修复证据及未上线的可证边界

### 5.1 `context.authority_epoch`

最早可核实的本地发现和修复在 **9/21 capture-lifecycle run01**，不是 9/22 线上失败后才发现。run01 CAP-15 红日志出现 `OwnerTruthCommandContext` 缺少 `authority_epoch`；交付报告写明改为 `OwnerTruthInterviewCandidateProposalPreparation` 携带并校验 epoch，从准备结果绑定 Source。run01 报告把它称为“已成功 admission 后阻断 Worker”，这个定位不准确：Astra run01 明确纠正，异常发生在 admission 事务内部、持久化 admission/source/effect 之前。run02 又补了非零、非法、陈旧 epoch 与幂等实际分支覆盖。[D053 · 2026-09-21-DreamJourney-Live采集中断修复-本地交付报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/reports/2026-09-21-DreamJourney-Live采集中断修复-本地交付报告.md)、[D095 · 2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求.md)、[D056 · 2026-09-21-DreamJourney-Live采集中断修复-run02本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/reports/2026-09-21-DreamJourney-Live采集中断修复-run02本地报告.md)；[run01 修前堆栈](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/red/cap15-client-bridge-authority-epoch-red.log:156)

9/22 short-08 的已有只读证据为：UTC 13:28:08.730 ACK 返回 201，13:28:08.788 admit 返回 500；没有 admission、job 或本场候选。运行文件仍在 line 369 读 `context.authority_epoch`。当前本地在 line 330 使用 `prepared.authority_epoch` 并做非 bool、非负整数校验。本次重新算本地哈希与既有对照一致。

| 文件 `app/services/owner_truth_interview_candidate_proposal.py` | SHA-256 |
|---|---|
| 9/22 已保存的运行中版本 | `f6a9c7c7033945a6555da241d978ffbfb8317564992ecbc9b5a5b44d0e3f30f9` |
| 当前本地已修版本 | `f89610ac523030ac85d6c85ea706e45ff3951de859255c0c0c213cd867573130` |

[运行中代码片段](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/deployed-admission-source-fragment.json)、[本地指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/local-admission-source-fingerprint.json)、[类型异常](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/admission-typed-error.json)。这是已知修复未进入运行版本的直接证据；本场没有进入模型整理，不能归因于模型容量或超时。

### 5.2 `s.thread_id`

最早可核实的本地修复在 **9/22 round-simulation run05**：真实 HTTP “已经保存、响应丢失”后的状态查询暴露 SQL 读取不存在列，改为 `s.current_thread_id AS thread_id`，JOIN 也改为 `s.current_thread_id`。run05 交付将其写作“正式记忆重建 SQL”不准确；Astra run05 与 Sol run06 明确纠正实际方法是 `PostgresOwnerTruthConversationRepository.read_live_delivery_status`。[D065 · 2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md)、[D102 · 2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md)、[D070 · 2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告.md)

9/22 人工短场在 21:58:35 左右建立，22:01:40 `live-delivery-status` GET 500，栈指向该方法；实际表有 `current_thread_id`，没有 `thread_id`。对照文件同时记录了运行中 SELECT/JOIN、表列和本地修复。服务器仅存两用户/两助手、active、无 review batch。新增只读 outbox 证据进一步确认：本地 3 用户+3 助手六条均 complete/sealed，canonicalIssueCount=0；seq5 用户消息是 outcomeUnknown，已冻结原命令，seq6 助手消息是 preparedNotExposed；关闭意图水位 6 于 UTC 14:02:09 持久化。因此当前不是第三轮漏采集，而是第三轮同步结果未知、后续派发停住，旧 SQL 使恢复查询失败。seq5 最初为什么进入未知结果尚未确定，不能把 SQL 当作这个初始故障的唯一成因。新增证据：[current-phone-outbox-summary.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-outbox-summary.json)。

| 文件 `app/services/owner_truth_conversation.py` | SHA-256 |
|---|---|
| 9/22 已保存的运行中版本 | `a5c9ab0a97165f96917247058ec89c886129470fbd0d5a11fa75cb198c3df5b0` |
| 当前本地已修版本 | `f408e886af979b895de1e2a7749f19f55f747eec932425540e809f8020a10429` |

[运行代码/表结构/本地对照](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-status-query-code-schema-comparison.json)、[类型错误](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-delivery-status-typed-error.json)、[服务器计数摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-session-server-summary.json)。

### 5.3 为什么到 9/22 仍是旧版本

现有证据足以证明的是：两处本地修复均晚于最后一份已读的 9/20 发布记录；capture run01–03 以及 round run04–07 均明确 `DEPLOY_NOT_RUN`，用户阶段要求也明确真实 Provider、部署、手机安装与真机由用户另行主动发起。run07 Astra 的结论原文是 `LOCAL_PASS / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN`。本次范围内没有找到后续部署授权与成功发布/回读记录。[D053 · 2026-09-21-DreamJourney-Live采集中断修复-本地交付报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/reports/2026-09-21-DreamJourney-Live采集中断修复-本地交付报告.md)、[D056 · 2026-09-21-DreamJourney-Live采集中断修复-run02本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/reports/2026-09-21-DreamJourney-Live采集中断修复-run02本地报告.md)、[D059 · 2026-09-21-DreamJourney-Live采集中断修复-run03本地报告](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md)、[D065 · 2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md)、[D096 · 2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md)、[D104 · 2026-09-22-Astra-Live逐轮模拟-run07复核通过](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md)

新增 [current-runtime-metadata.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-runtime-metadata.json) 显示 API 与候选 Worker 的 organization/long-pipeline/worker 三项 flag 均 true，且核对的四个文件 SHA-256 彼此相同。因此不能泛称“忘记启用长场开关”或“API 与 Worker 混版”；可直接证明的是**二者共同运行旧版，而本地已有新版修复**。

因此，不应指控本地任务“没有擅自部署”本身违规，也不能虚构用户拒绝部署。但可以明确记录过程事实：后续真机测试所用运行后端与通过本地验收的源码没有对齐，且存在早已被本地回归识别的阻断；本地 READY 不能充当运行版本已就绪的证明。这里的缺口是阶段交接证据与版本核对，不是把全部历史失败归因于未部署。

## 6. 发布和回退证据意味着什么

9/19 阶段记录证明曾发布 5 个 Worker 相关文件到 `live-candidate-run03-20260919-1745`，Worker 更新，而 API 没有重建/重启；记录了各自镜像及运行状态。该版本下真实短场通过、随后长场的模型合同失败，说明不能说这一阶段只是在测试完全没有发布过的旧代码。[D024 · 2026-09-19-DreamJourney-Live候选整理-真机阶段记录](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md)

9/20 发布明确获授权，发布目录为 `live-long-memory-run04-20260920-2230`，归档 SHA-256 为 `c642004cc14ea4a010d312ff37e05d263fbe5ad93afa15409c483a4984758768`。API 镜像为 `2a35518b2222b3247f0a8a50ed76b1bd9aecc8ea680a1a4dc055a4e7d1886b12`，候选 Worker 为 `9a8b7db6d5bea1bb8c6878c9f8563285d3dc1baa3af987bf944cdc657822bbba`；迁移到 0122，启用的 6 Worker 与 API 对齐，健康接口/备份校验通过。归档 AppleDouble 文件、Compose 项目名及三个旧 Worker 在迁移后 restart loop 的问题有记录且已在该次发布中处理。[D048 · 2026-09-20-DreamJourney-Live长对话run04生产发布记录](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长对话run04生产发布记录.md)

发布报告主动把后续真机失败和 NOT_RUN 写出，因此 `INFRASTRUCTURE_READY` 不是虚假声称业务完整通关。保留旧镜像/目录、说明 0122 后回退兼容条件，是**回退准备**；范围内没有执行回退的证据。不能把“有回退材料”写成“做过回退演练/回退成功”。9/21–22 本地报告中的回退说明同样不是部署记录。

仓库 HEAD 在这些交付间不足以标识业务版本：工作树有累积未提交变更。故本次采用具体文件哈希、归档、镜像及报告时间识别版本，不以相同 commit hash 推断运行源码相同。

## 7. 可归因的产品根因与过程原因

产品问题随阶段变化，并非单一根因持续未修：9/18 候选合同失败，具体检查点缺失且原 pendingReview 归属判断错误；9/19 完整保存后模型合同和内部处理失败；9/20 真机后半场停止采集、finish 未执行，另有采集状态与所有权耦合的相容缺陷复现，但现场首因未定；9/21 起继续暴露助手回调排序、身份缓存、持久关闭竞争、authority admission 等确定性缺陷；9/22 真机一场是播放完成/恢复聆听障碍加 admission 旧字段，另一场本地采集完整，但 seq5 同步结果未知、seq6 尚未暴露，delivery-status 旧 SQL 又阻断查询恢复。每一类只在其已有证据范围归因。

过程原因有四个可实证部分：

1. **覆盖单位经常小于结论单位。** 不同 Source、不同输入规模、不同装配的证据被合并为同一全链；待确认状态、条数、正文关键字、音频字节计数曾替代实际内容/身份/播放证明。Astra 多次明确拆开这些概念。
2. **反例有时没有触达目标入口。** 关闭网络策略的“冲突禁写”、超出 200 turn 后在模型调用之前退出的长反例、HTTP admission 与 Worker flag 分离，都使绿测无法排除实际路径缺陷。部分已在后续轮修复，部分当前只可标作覆盖未证明。
3. **受控替身曾过度配合答案。** 顺序响应、无条件支持、错证据仍成功降低了测试发现模型合同问题的能力。run07 修掉约定最后缺口，仍不构成真实模型稳定性证明。
4. **本地结论与运行版本没有自动衔接。** 后续本地 PASS 含修复，但部署继续明确 NOT_RUN；最终手机连到旧字段/旧 SQL。短场门禁虽逐渐严密，源码/宿主指纹也不自动证明后端镜像一致。

若干文档还存在会妨碍追溯的表述错误：capture run01 把事务内失败写成 admission 成功后的 Worker 错误；round run05 把 delivery-status 查询写成正式记忆重建；9/19阶段记录前部仍保留较早的 LONG_NOT_RUN/PAUSED 状态，后文追加长场 FAIL。对这些应以更晚复核和具体证据定位，不凭旧标题/总状态判断。

## 8. 本分报告的阅读清单与限制

本分报告全文阅读 **80 份 Markdown**（下列 D 编号），并全文读取 10 份已有脱敏结构化证据；另有源代码/日志的检索和局部阅读。索引中另 26 份文件仅被发现或列名，未计作本代理全文阅读；其中 9/18 candidate-contract 与 9/19候选独立复核由另一分工覆盖。没有把“搜索命中”或“文件存在”写成逐字阅读。机读清单记录每条 `full_text`、`selected_sections_or_search` 或 `inventory_only_not_read`：[reading-manifest.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/validation-release-reading-manifest.json)。

真实生产状态只引用截至 9/22 已有只读证据；本次没有重新连接服务器。源码检查是当前工作树的静态证据，不假定当前文件等同历史每一刻。异常首发时间以最早可核实报告/红日志为界，不把它当作一定最早写入代码的时间。没有找到范围外授权/发布记录，不等于断言它们在任何地方都不存在。

### 全文阅读的 Markdown（80 份）

- [D013 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-completion/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步-本地修复报告.md](../2026-09-18-dreamjourney-live-l20-auth-sync-completion/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步-本地修复报告.md)
- [D014 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-device-observations.md](../2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-device-observations.md)
- [D015 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-sanitized-console-sequence.md](../2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/evidence/2026-09-18-sanitized-console-sequence.md)
- [D016 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步真机复测报告.md](../2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步真机复测报告.md)
- [D017 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/README.md](../2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/README.md)
- [D018 · outputs/2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告.md](../2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告.md)
- [D021 · outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/evidence/2026-09-19-L20-服务端阶段证据摘要.md](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/evidence/2026-09-19-L20-服务端阶段证据摘要.md)
- [D022 · outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/evidence/2026-09-19-晚间续测检查点.md](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/evidence/2026-09-19-晚间续测检查点.md)
- [D023 · outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md)
- [D024 · outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md](../2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DreamJourney-Live候选整理-真机阶段记录.md)
- [D030 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/README.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/README.md)
- [D031 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-LM-LI执行清单.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-LM-LI执行清单.md)
- [D032 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-发布与回退准备.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-发布与回退准备.md)
- [D033 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告.md)
- [D034 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/README.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/README.md)
- [D035 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/reports/2026-09-20-DreamJourney-Live长对话分批整理-run02-LM-LI执行清单.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/reports/2026-09-20-DreamJourney-Live长对话分批整理-run02-LM-LI执行清单.md)
- [D036 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/reports/2026-09-20-DreamJourney-Live长对话分批整理-run02本地交付报告.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/reports/2026-09-20-DreamJourney-Live长对话分批整理-run02本地交付报告.md)
- [D037 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-BE-IR-LM-LI执行矩阵-run03.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-BE-IR-LM-LI执行矩阵-run03.md)
- [D038 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-DreamJourney-Live长对话分批整理-run03本地交付报告.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-DreamJourney-Live长对话分批整理-run03本地交付报告.md)
- [D039 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-后续真机验收清单-run03.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/reports/2026-09-20-后续真机验收清单-run03.md)
- [D040 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/README.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/README.md)
- [D041 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/log-redaction-scan.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/log-redaction-scan.md)
- [D042 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/relay-load-assumptions.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/relay-load-assumptions.md)
- [D043 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/test-summary.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/evidence/test-summary.md)
- [D044 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-DreamJourney-Live长对话分批整理-run04本地交付报告.md)
- [D045 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-R02-B-PARA与补证执行矩阵-run04.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-R02-B-PARA与补证执行矩阵-run04.md)
- [D046 · outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-后续真机验收清单-run04.md](../2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/reports/2026-09-20-后续真机验收清单-run04.md)
- [D047 · outputs/2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md)
- [D048 · outputs/2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长对话run04生产发布记录.md](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长对话run04生产发布记录.md)
- [D049 · outputs/2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-生产服务器磁盘安全清理记录.md](../2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-生产服务器磁盘安全清理记录.md)
- [D050 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/README.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/README.md)
- [D051 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/checklists/CAP-KEEP执行清单.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/checklists/CAP-KEEP执行清单.md)
- [D052 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/checklists/后续真机验收清单.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/checklists/后续真机验收清单.md)
- [D053 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/reports/2026-09-21-DreamJourney-Live采集中断修复-本地交付报告.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/reports/2026-09-21-DreamJourney-Live采集中断修复-本地交付报告.md)
- [D054 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/README.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/README.md)
- [D055 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/checklists/R01-R08-CAP-KEEP执行清单.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/checklists/R01-R08-CAP-KEEP执行清单.md)
- [D056 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/reports/2026-09-21-DreamJourney-Live采集中断修复-run02本地报告.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02/reports/2026-09-21-DreamJourney-Live采集中断修复-run02本地报告.md)
- [D057 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/README.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/README.md)
- [D058 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/checklists/R2-01-R2-06与短场强制门禁.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/checklists/R2-01-R2-06与短场强制门禁.md)
- [D059 · outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md](../2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md)
- [D060 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/README.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/README.md)
- [D061 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/checklists/SIM-GATE-IDEMP执行清单.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/checklists/SIM-GATE-IDEMP执行清单.md)
- [D062 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告.md)
- [D063 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/运行命令.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/运行命令.md)
- [D064 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/README.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/README.md)
- [D065 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md)
- [D066 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/A-D精确验收矩阵.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/A-D精确验收矩阵.md)
- [D067 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/修前红测与修后结果.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/修前红测与修后结果.md)
- [D068 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/运行命令.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/运行命令.md)
- [D069 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/README.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/README.md)
- [D070 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告.md)
- [D071 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/A3-B1-B3-C1-C2-执行矩阵.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/A3-B1-B3-C1-C2-执行矩阵.md)
- [D072 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/README.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/README.md)
- [D073 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告.md)
- [D074 · outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/C1-事实证据绑定-执行矩阵.md](../2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/C1-事实证据绑定-执行矩阵.md)
- [D075 · outputs/2026-09-22-dreamjourney-live-device-retest/run-01/reports/2026-09-22-DreamJourney-Live短场门禁真机失败记录.md](../2026-09-22-dreamjourney-live-device-retest/run-01/reports/2026-09-22-DreamJourney-Live短场门禁真机失败记录.md)
- [D076 · outputs/2026-09-22-live-device-lab/comparison-sol-short/2026-09-22-Sol人工短场与自动短场对照分析.md](../2026-09-22-live-device-lab/comparison-sol-short/2026-09-22-Sol人工短场与自动短场对照分析.md)
- [D077 · outputs/2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)
- [D086 · 02-设计文档/02-问题修改/记忆系统/2026-09-20-Astra-Live长对话分批整理-run02复核与剩余闭环.md](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run02复核与剩余闭环.md)
- [D087 · 02-设计文档/02-问题修改/记忆系统/2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md)
- [D088 · 02-设计文档/02-问题修改/记忆系统/2026-09-20-Astra-Live长对话分批整理-run04复核结论.md](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run04复核结论.md)
- [D089 · 02-设计文档/02-问题修改/记忆系统/2026-09-20-Astra-Live长对话分批整理-交付核查与剩余修复要求.md](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-交付核查与剩余修复要求.md)
- [D094 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾.md](../../02-问题修复/测试与验收/2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾.md)
- [D095 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求.md](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求.md)
- [D096 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md)
- [D097 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-Astra-Live采集中断修复-run03复核与局部收尾.md](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run03复核与局部收尾.md)
- [D098 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-DreamJourney-Live逐轮模拟与待确认记忆全链验收补充设计.md](../../02-问题修复/测试与验收/2026-09-21-DreamJourney-Live逐轮模拟与待确认记忆全链验收补充设计.md)
- [D099 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)
- [D100 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-发给Sol-Live逐轮模拟全链验收提示词.md](../../02-问题修复/测试与验收/2026-09-21-发给Sol-Live逐轮模拟全链验收提示词.md)
- [D101 · 02-设计文档/02-问题修改/记忆系统/2026-09-21-发给Sol-Live采集中断修复提示词.md](../../02-问题修复/记忆系统/采集与会后保存/2026-09-21-发给Sol-Live采集中断修复提示词.md)
- [D102 · 02-设计文档/02-问题修改/记忆系统/2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md)
- [D103 · 02-设计文档/02-问题修改/记忆系统/2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md)
- [D104 · 02-设计文档/02-问题修改/记忆系统/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md](../../02-问题修复/测试与验收/2026-09-22-Astra-Live逐轮模拟-run07复核通过.md)
- [D105 · 02-设计文档/02-问题修改/记忆系统/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md](../../02-问题修复/测试与验收/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md)

- [D019 · outputs/2026-09-18-live-short-repro/run-01/2026-09-18-Live长短对话候选缺失-原因核查.md](../2026-09-18-live-short-repro/run-01/2026-09-18-Live长短对话候选缺失-原因核查.md)
- [D020 · outputs/2026-09-18-live-short-repro/run-01/observations.md](../2026-09-18-live-short-repro/run-01/observations.md)
- [D079 · 02-设计文档/02-问题修改/记忆系统/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md](../../02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md)
- [D080 · 02-设计文档/02-问题修改/记忆系统/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md](../../02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md)
- [D081 · 02-设计文档/02-问题修改/记忆系统/2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md](../../02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md)
- [D092 · 02-设计文档/02-问题修改/记忆系统/2026-09-20-DreamJourney-Live-run04长场失败-内外部联合诊断.md](../../02-问题修复/记忆系统/长对话整理/2026-09-20-DreamJourney-Live-run04长场失败-内外部联合诊断.md)

### 全文读取的脱敏结构化证据（10 份）

- [outputs/2026-09-22-live-device-lab/short-08/admission-typed-error.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/admission-typed-error.json>)
- [outputs/2026-09-22-live-device-lab/short-08/local-admission-source-fingerprint.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/local-admission-source-fingerprint.json>)
- [outputs/2026-09-22-live-device-lab/short-08/deployed-admission-source-fragment.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/deployed-admission-source-fragment.json>)
- [outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-status-query-code-schema-comparison.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-status-query-code-schema-comparison.json>)
- [outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-delivery-status-typed-error.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-delivery-status-typed-error.json>)
- [outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-session-server-summary.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/comparison-sol-short/sol-session-server-summary.json>)
- [outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/current-verification.json](</Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/current-verification.json>)
- [outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/artifacts/runner-complete.json](</Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/artifacts/runner-complete.json>)

- [current-phone-outbox-summary.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-phone-outbox-summary.json)

- [current-runtime-metadata.json](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/current-runtime-metadata.json)

### 仅检索/选段阅读的源码与日志

- [outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/red/cap15-client-bridge-authority-epoch-red.log](</Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/red/cap15-client-bridge-authority-epoch-red.log>): 搜索并读取 AttributeError 堆栈邻近段；未全文阅读。
- [outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/tools/cap15_round_simulation_server.py](</Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/tools/cap15_round_simulation_server.py>): 分段阅读源码指纹、API装配、Worker注入、受控输出与验收逻辑；未全文阅读。
- [/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py>): 分段阅读输入播种、HTTP admission、main_module配置、Worker设置与注入；未全文阅读。
- [/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py>): 搜索并读取 admission 路由实例化及 flag 传递；未全文阅读。
- [/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py>): 读取相关 git diff、authority binding 实现段及当前文件hash；未全文阅读。
- [/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_conversation.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_conversation.py>): 读取相关 git diff、read_live_delivery_status 查询及当前文件hash；未全文阅读。
- [/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/domain/owner_truth/interview_candidate_proposal.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/domain/owner_truth/interview_candidate_proposal.py>): 读取相关 git diff与authority_epoch字段校验；未全文阅读。
- MEMORY.md 仅关键词定位项目及证据分层惯例，未以记忆替代本地文档。
- run04 PostgreSQL final 日志读取输出有截断，仅用于已呈现的最终结果字段核对；不计全文日志阅读。
