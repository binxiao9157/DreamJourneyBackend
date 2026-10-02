# DreamJourney Live 保存闭环：验收矩阵与证据合同

日期：2026-09-23。配套[统一修复开发设计](2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md)。以下是要求，不是已通过结果。

## 1. 如何使用这份矩阵

每行是一个必须覆盖的业务断言集合，不要求机械新增同数量测试函数。复用现有 CAP/KEEP、BE/IR、LM/LI、SIM/GATE/IDEMP 用例时填写真实测试名和覆盖差异。不要做所有故障与所有时长的笛卡尔积；健康短、20、65 全链与针对性故障组合分别运行。

必需本地项未验证就是本地缺口，不能用手机、Provider 尚未运行遮掩。当前已经修好的旧缺陷用隔离旧快照/最小反向补丁保留红测来源，不回滚共享工作区。新发现的可疑源码先做反例确认，不把静态风险直接写成已复现现场原因。

### 1.1 每行交付字段

`caseID / requirement / fixtureHash / finalArtifactHash / configHash / schemaVersion / command / actualStage / replacedBoundary / assertions / POST-GET counts / result / evidencePath / remainingLimits`。

红绿项另加 `preFixSource / reversePatchHash / redAssertion / greenSameAssertion`。红必须到达目标业务阶段；编译错误、0 tests、前置容量拒绝、skip 不算该阶段业务红。保持性测试无需人为制造产品缺陷，验收工具防伪负例也不能冒充历史生产缺陷。

### 1.2 各层替换边界

| 层 | 必须真实 | 可受控替换 | 不可扩大解释 |
|---|---|---|---|
| iOS | SDK 事件解析/Manager、Controller、Coordinator、磁盘、FeatureGate、BackendClient、候选展示逻辑 | SDK 原始事件来源、网络故障、时钟、磁盘故障入口 | 不等于真实 SDK/硬件 |
| 后端 | 正式路由、权限消费者、PG Store、默认 Worker/runtime/wrapper、调度、模型 adapter、发布、审核和正式读取 | 隔离身份源、真实模型的 HTTP 响应 | 不等于真实模型语义质量 |
| 数据库 | 隔离 PostgreSQL、正式迁移、事务、约束、重建 | 合成账号及必要授权种子 | 不等于生产升级成功 |
| Provider | 后续单独真实调用其接口 | 全新合成输入 | 不等于手机/生产链通过 |
| 真机 | 后续实际安装版、SDK、网络及持久链 | 授权的合成 PCM 输入 | 不等于声学麦克风/扬声器效果 |

## 2. F：版本、原链与默认装配

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| F-01 | 开工与最终版本快照 | 两端 dirty 工作树、构建/依赖、工具/夹具、配置与迁移有真实指纹；不只记 HEAD；新文件纳入 |
| F-02 | 默认 API + 默认 Worker + 隔离 PG | 独立进程使用正常工厂，未传 extractor、未手挂 preorganizer；仅模型 HTTP 受控；从入站到同场候选可追踪 |
| F-03 | 开关及执行分支 | organization/long pipeline/worker 在 API admission 之前及 Worker 启动时一致；有实际新链 Run 绑定证据；旧链兼容另测 |
| F-04 | 非法本地环境 | 生产 DSN、外部模型地址/真实 key 被本地 runner 拒绝，业务请求数为 0；日志无口令 |
| F-05 | 默认会中预整理 | 真实后台调度在 close 前产生私有 WorkUnit，使用共享 Run；未授权预整理时零调用，获准会后用同链补齐；不由测试结束后手动补调 |

## 3. B：服务器查询与 admission

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| B-01 | 旧 thread_id SQL / 真实当前 schema | 隔离旧实现目标查询失败；修后 SELECT/JOIN 正确且实际执行 PG；响应字段兼容；GET 无业务写 |
| B-02 | status 的 exact-command 绑定 | active/ended、已提交/缺失、错账号/场/command/hash/版本分别正确；连续水位不能替代精确回执 |
| B-03 | 旧 context.authority_epoch / 新链开启 | 隔离旧实现 admission 字段异常；当前真实 HTTP 原子建立 Source、Run、job 和回执；不是只看 201 |
| B-04 | 合法/陈旧/错误 epoch、账号变化 | 合法 epoch 成功，其余按原权限合同拒绝，无半提交；不使用 0、默认值或错域身份 |
| B-05 | admission 幂等与事务故障 | 重复合法命令效果唯一；事务中断无残缺 Source/job；响应丢失按原命令只读核实，不能自动重发未知请求 |
| B-06 | 数据库迁移 | 空库全量迁移与旧 schema 升级均通过；已应用 0122 不改写；新证据/预算字段兼容读取，旧行缺资料不伪造 |
| B-07 | 新旧 Worker/迁移/回滚隔离演练 | 停旧后启新的默认步骤中，旧进程存活时不放出新 schema 任务；原预算/lease 状态保留。若采用滚动发布，则真实旧进程领取新版本任务被持久 fencing 拒绝；不兼容回退被拦截 |

## 4. I：采集、原命令、停止及传输恢复

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| I-01 | 原始非 ASR 开场 + 三轮 ASR/Chat/TTS + stop | 3 用户+3 助手逐条绑定真实磁盘与服务器，非 ASR 不造无正文 owner；最终水位 6 |
| I-02 | SDK final/interim/QueryConfirmed 多种顺序 | 真实解析器保留合法 final，interim 不覆盖；重复确认不重复封存；不得直接注入 complete 跳过解析 |
| I-03 | 单条争议与后续有效回合 | 原争议保留；后续输入、助手回复及 close intent 持久化；不能全场 ingress 被静默关闭 |
| I-04 | seq5 已提交但响应丢失，首次核实 500 后恢复 | seq5 仅一次业务 POST；只读精确确认落盘后 seq6 首次派发；全场 end/ACK/admit/候选完成 |
| I-05 | seq5 已曝光但服务器缺回执 | 原六条和水位保留；无额外 seq5 POST、无跳号关闭、无假成功；本例通过表示安全保护通过，不是保存成功 |
| I-06 | 明确未曝光的发前失败 | 原命令未变，不消耗已曝光恢复名额；当前授权有效后正常首次派发 |
| I-07 | 有原 pre-handler 证明的 401、同账号刷新 | fresh authority 成功后、曝光前原子领唯一名额；同 command/body；最多两次 POST；后缀顺序正确 |
| I-08 | 第二次曝光后 notSent/响应未知、进程重建 | 无第三次 POST，不回退 prepared；原预算持久、只读核实 |
| I-09 | 策略先恢复，再切账号，旧刷新迟到 | 旧账号新增请求、预算领取和 UI 提交均为 0；原磁盘命令不污染；不能用早期 deny 挡住场景后称隔离通过 |
| I-10 | 读取 500/429/超时、网络恢复、并发触发、旧轮回调 | 同场单飞，集中 12 次/180 秒上限及单读超时；抖动不重置；旧轮无越权推进；合法恢复可收敛 |
| I-11 | stop 前已准入但排队的尾段，页面销毁 | 屏障等待并保存所有水位内处置；不能按已上传水位截掉尾段；保存任务生命周期合法，音频资源释放正常 |
| I-12 | 重复 requestClose 默认空 manifest | 冻结非空 manifest 不被清空；状态/命令/水位一致；原红绿用例继续通过 |
| I-13 | 半句停止、无正文、落盘故障 | partial/无正文语义真实；不能把未同步完整内容降为 partial 求绿；暂时磁盘失败有界恢复，失败仍保留恢复依据 |
| I-14 | 真实账号代次、token/policy/lease 过期 | 使用生产生成器和消费者；逻辑时钟实际触达所测过期判断；FeatureGate 摘要不与 lease UUID 混比 |

## 5. U：当前场、冷启动和可见性

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| U-01 | 最新 closing Outbox 无 checkpoint，另有历史 acknowledged/pendingReview | 只读发现最新场，不跳过它选老场；不创建会重放写入的 CaptureCoordinator |
| U-02 | 多个旧、新 coordinator 任意回调顺序 | 页面只接受可见绑定；旧回调可记录自身状态但不能抢按钮/状态区；日志可解释接受与拒绝 |
| U-03 | 用户核实、离页重进、账号切换、冷启动 | GET 精确命中当前场坐标；本轮业务 POST 总数 0；新进程不是一直持有旧 Proposal 的假冷启 |
| U-04 | 第 7 次状态才完成、GET 预算到期、迟到低版本 | 保留当前合法进度；到期不假报后台失败；合法终态不回退；新读取轮遵守单飞与版本规则 |
| U-05 | pendingReview 但列表过滤/分页隐藏候选 | 真实 iOS 读取展示断言失败；不能只根据 pendingReview 或总数增加通过 |
| U-06 | 有正文入口、空候选、历史文案 | 入口仅表示正文可查看；合法纯问题可 0，明确事实缺候选为 FAIL；文案不能当当前 ACK/保存证明 |

## 6. M：容量、证据和语义

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| M-01 | 4001 字符原 turn，少量候选单条关系与 1×1 批页 | 原合法证据不丢字；分片后真实 adapter 命中受控 transport，关系及 support 完成；不只提高上限或更早拒绝 |
| M-02 | 8×32 页，40×1000 字符原文 | 缩页后全部应检查 global pairs 有证据，所有请求合规，最终候选与独立真值吻合 |
| M-03 | 初次切片的第二/第三片、重复句和前导空白 | 先核实当前源码红例；全局范围与原子串 hash 正确，不以生成文案 find 原文或默认 start=0 |
| M-04 | 中文、emoji、组合字符、非连续证据片段 | 沿用 Python code point 半开范围；跨端转换明确；局部索引唯一；UTF-8 SHA 相等；非连续片段不假拼接 |
| M-05 | 最终 support 对一个原 turn 的多个 fragments | 不重拼成超限单条；责任 atom 全覆盖；只检本页责任，不误判页外上下文遗漏 |
| M-06 | 单轮 12 个独立事实、短轮多维属性 | 无整场 8 条截断；独立事实保留、合理同事合并有依据；维度不丢、不为多个维度重复候选 |
| M-07 | 跨批重复/补充/明确纠正/撤回 | 跨真实 WorkUnit；重复保留两处证据但一候选；补充完整；纠正不旧新双发；撤回不发布 |
| M-08 | 事实+问题同发言、纯问题、助手推测、不同主体相似事件 | B7 保持；只保留合法用户事实；不整句丢弃、不把助手回答当用户经历、不误合独立事件 |
| M-09 | 合法释义、缺失/错误/歧义证据、错 turn/source | 合法释义通过；错误明确拒绝；实际到达证据检查阶段，不能被更早 200-turn 限制挡住冒充红例 |
| M-10 | 跨页关系多目标、同批关系、并行批次 | 扫描域完整且身份准确；不凑 scannedCount；相同事实不因并发互不可见生成两条；不允许向未来错误引用 |
| M-11 | request wire body 与计量/预留 | 出站 hash 与 Prepared 请求一致；schema/prompt/feedback/context 都计入；估算 token 不冒称真实 usage |
| M-12 | 最小不可分证据仍超限 | typed 容量失败，无截尾/假支持/半发布，正文与坐标保留；本故障保护不能替代 M-01/02 的健康容量成功 |
| M-13 | 最终全场 manifest、停止尾段、任一页遗漏 | 每个 required atom 有合法唯一处置；合并/纠正后证明更新；遗漏拒绝整批发布，不能放出部分冒充完整 |

## 7. P：模型错误、预算、并发与期限

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| P-01 | 同版策略名但数值改变、API/Worker 默认不同、重启恢复 | 新 Run 冻结完整 budget/deadline 快照和 hash；后续按 DB 快照；现有 PG 丢弃 policy 的反例应被检出 |
| P-02 | 旧 Run 缺快照 | 仅经已证实 registry 解析；未知不得采用新默认重置；不自动重处理历史；迁移兼容行为有测试 |
| P-03 | 输入预检失败、预留后未曝光、曝光后未知 | 各自准确持久分类和计费；未知不退款；模型 attempt 与业务 POST 合同分开 |
| P-04 | 429、connect/read/write/pool 超时、5xx | 实际到 transport；typed reason 区分；有限 Retry-After/退避受剩余期限和共享额外请求名额约束 |
| P-05 | 持续保活/慢流，HTTP 分阶段一直未超时 | 整体 deadline 仍到期，关闭/取消或 fencing 有效；无无限后台调用/预算槽泄漏；迟到结果不能提交 |
| P-06 | JSON 合法但 schema 错、length、异常/缺失 finish_reason、证据遗漏 | 严格解析并保留原观察；不伪造 stop；有限契约恢复不重跑整个 Source，不产假候选 |
| P-07 | 无进展/绝对期限，重启与时钟变化 | 心跳/轮询/重试不算进展；重启不延长期限；真实已完成页更新进展；超期保留正文，唤醒合法失败收尾 |
| P-08 | 并发 Worker、过期 lease/epoch、提交前撤权 | 单元和预算原子预留、fence 生效；旧结果不能提交，失败 Run 不再自动调 Provider |
| P-09 | 发布事务失败、提交后丢响应、重复 job | 原子候选提交/回滚；同 manifest 唯一效果；重建读回一致，不多生成正式记忆 |
| P-10 | 20/65 与 300 独立事实规划容量报告 | 报每阶段页数/实际请求/输入输出预留/额外恢复/费用估算来源；不扩限、不缩小健康夹具求绿；预算不够如实列缺口 |

## 8. G：完整短场先行与验收器自身正确性

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| G-01 | 短场 A/B：至少两轮，第二轮补充或纠正 | 真实原始入口到 PG，候选完整且 iOS 可见，审核后正式记忆及重建读取；各场 ID/Source 独立 |
| G-02 | logical20：110 用户+110 助手，跨度至少 1200 秒 | short-A 同版先行；220/220 身份/正文匹配；首中尾事实、后半补充/纠正、候选/正式链；明确逻辑与墙钟时间 |
| G-03 | logical65：150 用户+150 助手，跨度至少 3900 秒 | short-B 同版先行；300/300 正确；多次策略/凭证边界按实际所测范围记录；不能复用 short-A |
| G-04 | 已有 F-65 与新密集事实场 | 保留约 73728 字/301 消息原夹具（按其实际 manifest 核对），另测至少 300 独立事实；不能拼接多 Source 计数；完整长链各有有效短场门禁 |
| G-05 | receipt 缺失/失败/过期/已消费/错目标/错账号 | 在第一个长场业务请求前拒绝；本地 receipt 不放行真机 |
| G-06 | 改 admission service/domain、模型参数、Worker/config、迁移、iOS dylib、夹具或工具 | 旧 receipt 全部失效；不是只检查缓存摘要；受测依赖集合含新文件 |
| G-07 | 漏中段/尾段、同数量错内容、错 Source、隐藏候选、错正式版本 | 独立 oracle 分别拒绝；即使统计数量没变或状态 pendingReview，也不能通过 |
| G-08 | 全序列最终制品 | `short-A→20→short-B→65` 同版，四份完整证据；之后改变相关代码必须重新取得门禁，不用旧 xcresult 拼最终结果 |
| G-09 | 历史对照与本场统计 | 候选和正式记忆按 Source/manifest 精确分开；短场新增不计入长场；隔离历史任务不能冒充本场完成 |
| G-10 | 首次偶发测试失败 | 保留失败及随机种子/调度/首错；隔离重跑绿不抹去失败；查清确定性或报告稳定性未决，不能只挑最后一次绿 |

## 9. D/K：持久诊断与受影响功能

| ID | 场景 | 必须验收的结果 |
|---|---|---|
| D-01 | 业务首次错误后大量 SDK 回调、再发生 SQL/GET 错误 | 首个请求错误不可覆盖，后续恢复错误追加；能区分 first cause 与 subsequent blocker |
| D-02 | 销毁进程/Store 后读取诊断 | 保留场次/command 摘要、阶段、错误域码、时序和水位；仅读不触发业务恢复 |
| D-03 | 诊断磁盘写失败、目录容量上限 | 业务持久化不因诊断崩溃；诊断缺失标记诚实；不删除业务 Outbox 或用户正文 |
| D-04 | 脱敏与绑定 | 无原文、token、headers、完整 URL、模型原始输出；能关联对应客户端/服务端请求并明确证据强度 |
| K-01 | 音频/租约/自然完成/长回答/打断/恢复聆听 | 受影响模块实际回归保持；数字音频和真实声学分开；不得伪造 native 完成 |
| K-02 | 键盘文字与记忆档案输入 | 各自真实短闭环至候选/审核/正式记忆；共用服务修改不破坏其他入口 |
| K-03 | 审核更正、重复确认及正式读取/grounding 合同 | 更正合法 hash 变化受支持；正式内容/来源/version 正确；Live 上下文结构不退化；未确认候选不当正式事实 |
| K-04 | 旧非 Live Worker/旧 Source 与本轮新链并存 | 不饥饿、不擅自升级历史 pipeline/预算、不改旧 maxAttempts；相关既有回归通过 |
| K-05 | Lab 本地门禁与诊断 | source/instrumented source/完整 app bundle 区分；首错留证；缺实际播放器完成明确失败，不能直接注入事件求绿 |

## 10. 运行器交付与现有入口

### 10.1 可复用的现有入口

- iOS 测试：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`；由 Sol 查询实际 scheme/destination 后生成 xcodebuild 命令，不复用已失效模拟器 UUID。
- 后端 PG smoke：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`。已有注入测试保留其局部证明范围，不当 F-02 默认装配证据。
- 自动真机工具本地测试（不触发手机）：

```sh
cd /Users/gaominge/Documents/liftora
python3 -m unittest discover -s tools/live_device_lab -p 'test_*.py' -v
```

### 10.2 必须交付的统一本地 runner

优先提取现有工具为可维护入口，不继续把唯一入口藏在某份 outputs 历史目录。建议 `tools/live_memory_acceptance/run_local.py`，可以使用等价名称，但须实现相同行为。

以下是**待实现接口合同，不是当前已存在/已验证命令**：

```sh
python3 tools/live_memory_acceptance/run_local.py preflight --config <local-config-file>
python3 tools/live_memory_acceptance/run_local.py verify --config <local-config-file> --suite default-chain --out <new-run-directory>
python3 tools/live_memory_acceptance/run_local.py verify --config <local-config-file> --suite final-short20-short65 --out <new-run-directory>
```

runner 负责：安全预检→隔离 PG→受控模型 HTTP→正式 API 与默认 Worker→短/长次序→真实客户端证据→停止自身服务→报告。连接凭据从受限文件/环境读取，复制命令不包含密码。

当前实际启动入口可参考：

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port <local-port>
python -m app.async_effects.worker_activation --worker ownerTruthCandidateExtraction
python -m app.async_effects.owner_truth_candidate_extraction_worker --loop
```

Sol 先核实实际 CLI，按隔离依赖完整配置后执行，activation 只写隔离库。默认 Runtime、wrapper、repository 和实际配置不可替换成测试成品。`DEEPSEEK_BASE_URL` 指向回环受控服务，不加载真实 key。

跨进程时钟无法统一的部分，使用明确的短期隔离凭证与实际过期/恢复，记录墙钟；另用可注入时钟验证长期期限。每种证据列明边界，不能只修改 capturedAt 就说认证 TTL 已通过。

输出 `assembly-proof.json`，至少含：实际进程/命令、加载代码 hash、默认工厂、API/Worker 生效开关、DB schema、模型替换边界、每阶段命中次数。不要只打印 runner 期望值。

### 10.3 证据目录

```text
run-01/
  README.md
  baseline-and-final-fingerprints.json
  assembly-proof.json
  implementation-report.md
  acceptance-matrix.md
  red-green/
  short-a/ logical20/ short-b/ logical65/
  capacity/ fault-recovery/ regression/
  release-manifest.json
  release-and-device-runbook.md
  reproduction-runbook.md
```

每场至少保留 expected-facts、逐轮 ledger、实际 Source/候选/正式 ID 的脱敏关联、GET/POST 计数、short receipt、测试结果、数据库重建读取证据。正常关闭本次隔离实例；不得删除首次失败来整理出全绿目录。

## 11. 后续真实 Provider 与真机清单（本轮不执行）

| ID | 前置条件 | 后续执行要求 |
|---|---|---|
| EXT-01 | 用户另行发起真实 Provider 验证并确定有限调用范围 | 记录真实 endpoint/model/SDK、输入输出额度、响应型号、finish_reason、usage、时延；合成短/密集/跨批关系样本，按事实真值核对，不只看 HTTP200 |
| EXT-02 | 用户授权部署、最终 release manifest 可审阅 | 实际 API/Worker/artifact/schema/config 对齐；检查两项旧阻断修复生效；不能只重建 App 或切账号 |
| DEV-SHORT | 同版本部署/安装预检成功；用户发起 | 至少两轮含补充，建议增加第三轮覆盖 seq5；逐条正文、end/ACK/admit、候选可见、审核、更正/正式读取与冷启；旧任务不覆盖页面 |
| DEV-20M | 刚通过同版独立真机短场 | 物理持续≥1200秒，目标≥110用户轮；首中尾独立事实、重复/补充/纠正/撤回、多维属性；正文/候选/正式闭环与文字/音频实时性 |
| DEV-65M | 用户后续另行发起，另一个有效同版真机短场 | 物理持续≥3900秒，目标≥150用户轮；记录实际字数/请求/成本与多次凭证边界；同场完整闭环；不是首次20分钟前置 |
| DEV-ACOUSTIC | 单独发起硬件验证 | 麦克风真实拾音、扬声器/耳机播放、打断/回声/恢复聆听；静音数字 PCM 通过不替代 |
| DEV-OTHER | 共用链修改已本地通过、用户安排 | 键盘文字与档案输入分别验证实时显示、候选、审核及正式记忆，不扩大为整个产品测试 |
| DIAG-SEQ5 | 用户发起最小取证复现 | 新短场、请求级持久日志与对应服务端诊断，捕获首次异常；不重放旧命令、不保证一次复现，不把后续500倒推成首因 |

真实短场失败后，20/65 保持 NOT_RUN，先保存首次失败及停止后只读摘要。缺手机、Provider 未授权只是这些行 NOT_RUN，不使已完成本地任务卡住。

若 20 分钟内完成不了计划的 110 次有效交互，延长到完成，不缩短真实回复或伪造轮次；报告实际时长、轮数及语音发送/ASR/文字/解码/播放完成延迟分别统计。自然 401、限流或断网未出现记 NOT_OBSERVED，不能写成现场通过。

短场失败要区分本体产品、运行版本、自动工具 STREAM 和硬件问题；全部绑定本场，而不是在候选总数里猜原因。

## 12. 最终判定

本地必需健康链通过、保护场按预期行为通过、核心无未解释 skip、新增缺陷有目标阶段红绿、实际默认装配成立、最终指纹一致，才可标 `LOCAL_CHAIN_PASS`。

测试安全停留 unknown 是对应故障保护 PASS；该场业务保存仍未完成。合法纯问题零候选是语义 PASS；明确事实场零候选是业务 FAIL。首次历史原因未明保留未明。Provider/部署/真机/声学状态逐项报告，禁止用一个 LOCAL_PASS 覆盖全部。
