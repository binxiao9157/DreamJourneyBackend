# DreamJourney Live run-03 复核与局部收尾

日期：2026-09-21。复核当前工作区及 Sol run-03。

**结论：R2-01至R2-05的原缺陷已修好；实际先短场、再150用户轮的双向保存链也已通过。认可这些成果。完整要求还差三个局部收尾项，列于下文，不需要重做整套方案。**

本文件承接[run-02复核](2026-09-21-Astra-Live采集中断修复-run02复核与短场强制门禁.md)与[主指导§12](2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)。三项收尾不能扩大为音频、历史UI、后端语义整理重写；真机和真实模型仍由用户另行发起。

## 1. 本轮确认通过的内容

- 当前报告列出的9项源码/测试/工具文件SHA-256均与交付一致。实际读取原始xcresult：六个原反例修前0/6、修后6/6；OwnerTruth 559 PASS、2 SKIP、0 FAIL；音频相关42/42；两个SKIP入口的独立短场、150轮测试各1/1通过。
- 旧generation准入现在发生在助手缓存修改之前；完成reply身份不再按32项截断。独立抽取当前生产组件重跑，150个已完成reply的旧片段重放没有重新开启，新的第151个回复正常完成。
- 原停止manifest两个窗口均修好。真实Store源码及临时磁盘重建探针确认：只登记未提交正文时保留义务；正文先提交、manifest后到时不制造缺口。
- 真实Controller在安全移交后释放retained/ingress与诊断pin；活动critical日志优先级及结束后配额回收已有对应代码和测试。迟到partial显示的保持性失败也已补修和回归。
- 短场候选经历、第二轮补充、Source绑定、审核、正式记忆及Store重建验证后才写receipt。最终短场测试完成与长场启动相隔约27秒，顺序正确。
- 当前150用户轮+150助手轮通过真实BackendClient、本地HTTP、Backend/Worker、隔离PostgreSQL链；不是旧版2+9轮，也不是Python重绑命令回放。短场1候选，长场16候选，审核并重建后17条正式记忆。
- 补充、纠正、撤回已放在后续批次和后半场；这些通过应保留。

证据入口：[Sol本地报告](../../../outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md)、[Astra Store探针](../../../outputs/2026-09-21-astra-live-capture-review/run-03/evidence/manifest-probe/README.md)、[Astra助手组件结果](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-03/evidence/assistant-probe/result150.log)。

本轮Astra读取现有真实Controller/双向链xcresult和实际源码，并另跑组件及Store探针；没有重新运行整套重型集成，没有修改产品代码。组件探针不冒充Controller或真机。后端192项与构建通过按交付汇总记录，不声称本轮全部重跑。

## 2. GATE-01：变更后旧短场凭证必须真正失效

### 当前缺口

[Swift测试:5658](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5658)比较receipt和config里保存的指纹字符串；[服务器工具:59](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/tools/cap15_gated_bidirectional_server.py:59)只在启动时计算7个文件，范围遗漏BackendClient、FeatureGate、后端整理实现、配置和迁移等受保护依赖。

只读内存对照：改变配置baseURL但保留原configFingerprint，其余receipt字段不变，现guard仍会通过。这说明“比较两份一致的旧字符串”不能保证当前实际配置没变。当前v4的实际指纹和顺序已重算确认正确，**不否定本次短长场PASS**；缺口在未来修复后旧结果可能仍被接受，与用户“每次长场前先验证当前版本短场”的要求不符。

### 最小收尾

1. 在长场启动器中、发出任何长场请求前，重新计算本次实际配置及受影响源码/构建来源摘要，核对本run短场receipt。必须把验证器接到实际长场入口，不能另写一个没被调用的工具。
2. 指纹清单覆盖真实保存链依赖：iOS采集/Controller/Store/BackendClient/FeatureGate及测试；后端实际整理/Worker/配置/相关迁移；合成模型响应和验收工具。不得记录密钥明文；环境参数摘要按既有脱敏规则处理。
3. 绑定实际执行的测试包/应用构建来源，防止磁盘源码已更新却沿用旧二进制，或相反。可在build-for-testing时形成机器可核对的manifest，由启动器校验，不要求App运行时扫描宿主源码。
4. receipt缺失、run不同、源码不同、实际配置不同、短场失败均应使long入口在网络前拒绝；相同最终版本的合法short receipt正常放行。修改后重新短场再长场。

测试应含上述负例并断言long未启动、无新长场Source/业务请求。可以用临时manifest/配置副本控制变化，不修改真实Provider凭证、不访问生产。

## 3. GATE-02：150轮同源链还缺跨批重复

当前[长场输入:5610](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5610)仅在第1、2轮重复同一交通偏好；[受控模型工具:161](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/tools/cap15_gated_bidirectional_server.py:161)把它们作为同一批记忆返回。这证明批内去重，不能证明原要求的“前半场已整理后，后半场再次提到同一事实，不生成第二条候选”。

最小收尾：在后半场加入/移动一条明确重复早期事实的输入，使两次发生在实际不同的整理unit中；最好选择不再被纠正/撤回的稳定事实，防止最终候选减少掩盖重复。记录两个unit及各自输入来源。受控响应也分批返回，交给生产跨批合并处理，不在fixture里预先消掉重复。

断言同一事实最终只一条候选和正式记忆，保留两处合法证据；候选/审核/重建回查都保持。原跨批补充、纠正、撤回、纯问题和助手排除断言不能削弱。先执行修订后的短场门禁，再跑该150轮链，不需要真实手机或Provider。

## 4. IDEMP-01：默认空参数重复关闭会回退已完成清单

[Store requestClose:16949](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16949)允许已有manifest后传入空参数，但16956–16969仍用本次空数组计算resolved，清空了既有完成集合。

独立真实Store探针：非空manifest完成、同参重复仍pending=0；随后默认空参requestClose并重建，pending变成1。**这是Store幂等合同缺口；当前Coordinator有closeIntentPersisted/inflight保护，未证明正常短场能触发，不能称本次短场保存又失败。**

最小修改：参数相容性检查后，以最终`envelope.closeManifest`作为effective manifest计算handled/resolved；默认空参不能撤销已经完成的持久交接。

只需补“非空关闭→同参重复→默认空参重复→完全重建，pending始终0”的红绿断言，及未处理handoff仍保留pending的对照。保留原R2-03两个窗口绿测。无需修改Controller状态机、音频或业务请求重试。

## 5. 收尾顺序和边界

1. 保留run-03原始证据及已通过实现，在独立目录完成上述三项。
2. IDEMP-01先红后绿；GATE-01负例证明实际long入口拒绝旧证据；GATE-02补跨批重复，不重写生产整理逻辑。
3. 在最终版本上先完整短场，再150轮同源链；短场失败即停止长场并继续本地修复。重跑实际受影响回归与必要构建，保留证据和指纹。
4. 最终表述区分：本地受控模型/模拟器/隔离PG通过；真实Provider、设备短场、物理普通20分钟、密集20分钟和未来65分钟、部署、历史处理分别保持未执行。
5. 当前150轮链证明轮次数量与数据闭环，其capturedAt近似每轮增加1秒，不能将它称为同链逻辑65分钟。已有逻辑时间专项可单列，物理时长必须后续真机证明。未来清单继续保留普通20分钟与密集20分钟100用户轮两个场景，不将其合并。

用户未启动真机，不检测或等待手机；不以未连接手机中断本地收尾，不访问生产、不重放历史任务、不commit/push。

## 6. 发给Sol的提示词

请继续当前DreamJourney Live任务，先完整阅读这份run-03复核。Astra已经认可R2-01至R2-05原缺陷修复，以及本次先短场、再150用户轮的真实本地双向闭环；保留所有这些成果，不重做整套方案。

本轮仅完成三项局部收尾：GATE-01让实际long入口重新核验当前配置、受影响源码和构建来源，不能只比较receipt/config缓存指纹；GATE-02在同一150轮链加入真正跨批、后半场重复前半场稳定事实，验证只一条候选并保留两处证据；IDEMP-01修复requestClose默认空参重复调用使resolved清空的Store幂等缺口。按文档补正向和反向断言，不改已通过的音频、后端语义整理或历史UI。

在最终代码上先实际跑两轮补充短场，验证本场候选正文、Source归属、无重复、审核到正式记忆及重建回查；通过后才启动150轮链。短场失败则继续本地修复，不能跳过它测长场。完成受影响回归、构建、源码指纹和交付。连续完成本地任务；真实模型、部署、手机及真机测试由我另行主动发起，不检测或等待手机，不访问生产、不处理历史失败场、不commit/push。报告保留所有NOT_RUN边界，不将150轮等同于物理65分钟。
