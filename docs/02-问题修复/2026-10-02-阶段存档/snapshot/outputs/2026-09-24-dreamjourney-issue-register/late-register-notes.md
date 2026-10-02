# DreamJourney Live / 记忆问题统一登记建议（9/18–9/24 后段）

核对日期：2026-09-24。性质：给主登记册的合并建议，只新增本文；未修改源码、主登记册或历史记录，未执行测试、服务、生产、Provider 或手机操作。

## 登记口径

- 下列 18 项是“问题、调查、测试债和验收缺口”的分类条目，**不等于仍有 18 个未修产品缺陷**。
- 已有 SAVE / LAB / LIVE-L20-AUTH / DJ-ASR 编号沿用；标“建议新 ID”的编号仅为本次登记键，主册若已有对应编号应合并并保留别名，不再新建重复问题。
- “最早入口”指本次已核实材料中可追溯的记录，不声称等于代码首次引入日期。
- 状态分开登记：本地修复、部署、真实 Provider、真机、历史原因。原文旧 FAIL 永不改写，但当前状态以有明确关联的后续证据更新。
- 9/24 [发布结果](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md:5) 已确认后端 DEPLOYED、iOS INSTALLED / launch PASS；不要继续沿用 9/23 本地报告中的 DEPLOY_NOT_RUN。该发布记录同时明确新版本短场完整链、真实 Provider、物理 20/65 分钟尚未执行。
- [final08 收尾报告](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:5) 的 LOCAL_PASS 是受控本地结论，不等于实际长场保存问题已经现场关闭。

## 18 项建议条目

### 01 · DJ-LIVE-CAPTURE-01：真实转写封存、采集生命周期与停止水位（建议新 ID）

- **历史别名/关联**：9/15 canonical finality；9/17 非 ASR 假成员；9/20 第 13 轮后无新服务端正文；CAP-01～15；R01～R08；R2-01～R2-06。它们属于同一采集/封存边界的多轮修订，不把每个新增测试分别建产品 bug。
- **当前状态**：LOCAL_PASS，当前版本待真实 SDK/短长场验证。保留 partial、非 ASR 隔离、已曝光命令不可变；不能用该项解释 9/22 seq5，因为那一场手机六条均已完整封存。
- **最早入口**：[9/15–17 采集回归历史](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/ios-history.md:20)；[9/20 真机失败记录](/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-device-retest/run-2026-09-20-01/2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md:77)。
- **设计**：[采集中断开发与验收指导](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md:163)。
- **最新证据**：[run03 采集修复报告](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-03/reports/2026-09-21-DreamJourney-Live采集中断修复-run03本地报告.md:9)；[final08 逐轮完整链](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:26)。
- **下一动作**：在新版本真实短场中核对每轮 SDK→封存→服务端消息，再进入物理长场。9/20 当时首次触发究竟属于哪种冲突/结束/磁盘原因，若原始首错缺失仍保留历史未决，不能反推唯一原因。

### 02 · LIVE-L20-AUTH-01：长场认证续期、原命令恢复与重试额度

- **历史别名/关联**：70 本地 / 48 服务端；第 49 段 401→403；existingUseCaseNotSafelyResumable；C1～C7；唯一重试名额提前领取；明确 deny 刷新循环；迟到账号回调。
- **当前状态**：LOCAL_PASS；自然认证轮换的真实现场验证未闭合。未观察到自然 401 只标 NOT_OBSERVED，不重新定义为实现失败。
- **最早入口与设计**：[认证续修指导及原编号](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md:6)；[后续当前代码执行方案](../../02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md)。
- **最新证据**：[9/18 最终集成与迟到回调](/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-final-integration/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证恢复最终集成本地报告.md:14)；[final08 身份/旧回调保持性](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:35)。
- **下一动作**：真实长场记录自然 auth 事件；若没有发生，保持 NOT_OBSERVED。不得以重试未知 POST 制造“恢复成功”；9/17 实际 403 的历史具体 deny 原因也不能由受控测试补写。

### 03 · DJ-LIVE-CAPACITY-01：长场事实容量与全阶段请求分页（建议新 ID）

- **历史别名/关联**：DJ-LIVE-L20-01 的容量维度；SAVE-05、SAVE-06；8 条记忆上限与全场覆盖冲突；关系页重新拼接整 turn；4000/30000 字符内部限制；N6。
- **当前状态**：LOCAL_PASS，真实新 pipeline 的 Provider 容量/质量与物理长场待验。不是“用户只能说 8 轮”；不是已确认火山时长或 DeepSeek 窗口导致所有历史失败。
- **最早入口**：[9/19 独立问题记录](/Users/gaominge/Documents/liftora/outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md:3)；[真实模型 8 次对照与内部容量结论](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-19-Astra-Live长场无法生成待确认记忆-深度根因核查.md:7)。
- **设计**：[分批整理与统一发布设计](../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md)；[9/23 W4 容量与投影](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:203)。
- **最新证据**：[默认 Worker / real adapter / 受控 HTTP / PG 的 300 精确事实](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:22)；[C 容量审计说明](../2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/C-capacity/README.md)。
- **下一动作**：另行授权的真实 Provider 限定合成验证，记录真实 usage、finish_reason、耗时及独立事实覆盖，再进行真机长场。不能将 300 个受控事实的 PASS 当真实模型质量 PASS。

### 04 · DJ-LIVE-EVIDENCE-01：跨批语义、原文证据身份与候选/正式记忆归属（建议新 ID）

- **历史别名/关联**：R02-B-PARA；VERIFY-09；supportValidate.factOmitted；organizationValidate.schemaInvalid；C1 错引 turn；分片 _sourceStart；合法重复/补充/纠正/撤回。
- **当前状态**：LOCAL_PASS，真实模型语义质量待验。与容量条目分开：容量负责“装得下”，本项负责“不漏、不造、不重复、不误引”。历史 schemaInvalid 的具体字段缺失仍属取证边界，不能称已还原。
- **最早入口**：[9/19 合同失败链](/Users/gaominge/Documents/liftora/outputs/2026-09-19-dreamjourney-live-candidate-device-retest/run-2026-09-19-01/reports/2026-09-19-DJ-LIVE-L20-01长场候选整理合同失败-独立问题记录.md:56)。
- **设计**：[R02-B-PARA 复核](../../02-问题修复/记忆系统/长对话整理/2026-09-20-Astra-Live长对话分批整理-run03复核与剩余一项语义缺口.md)；[9/23 原文与有界投影](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:216)。
- **最新证据**：[final08 精确事实及负例](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:22)；[候选可见与归属矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:21)。
- **下一动作**：真实合成场按独立事实真值核对首中尾、后半场补充/纠正/撤回及 Source/turn 关联，不以候选总数增多替代正确性。

### 05 · SAVE-08：Run 预算快照、期限和模型曝光记账

- **历史别名/关联**：W5；P 系列；PG begin_or_load 丢 policy；配置变更后重取预算；N4 Frozen Prepared request；N5 preorganization timebase。
- **当前状态**：LOCAL_PASS；真实费用/usage 为 P10 NOT_RUN。预算持久化、迁移兼容、未知模型请求的费用保留已本地验证；不等于旧历史 Run 曾因此失败。
- **最早入口与设计**：[SAVE-08 已证实源码缺口](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:65)；[W5 预算与第三方合同](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:271)。
- **最新证据**：[N4/N5/N6 矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:13)；[单 Run 323 请求而非 646，拒绝也消耗预算](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:22)。
- **下一动作**：真实 Provider 采样实际 usage、成本及耗时。原 Run 快照保持，重启/换 lease/拆页不得重新领预算；不为通过测试自动加额度。

### 06 · SAVE-07：默认装配、版本发布与短场强制门禁失真

- **历史别名/关联**：W0/W6/W8；默认 Worker 被 stub/人工装配替代；API lifespan off；short receipt 依赖漏 admission service/domain、新文件、资源、expiry/消费；A3/F02–F05；final07 错绑 GET 计数。
- **当前状态**：LOCAL_PASS，发布部分已完成；这是一组验收/交付基础设施缺口，不应按每个测试失败建立独立产品 bug。当前真实短场门禁仍待执行。
- **最早入口**：[历史验收与运行版本复盘](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/validation-release.md:13)；[SAVE-07](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:64)。
- **设计**：[逐轮模拟补充设计](../../02-问题修复/测试与验收/2026-09-21-DreamJourney-Live逐轮模拟与待确认记忆全链验收补充设计.md)；[W6 同版/依赖/short receipt](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:310)。
- **最新证据**：[final08 四场正常 lifespan API 与官方独立 Worker](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:26)；[计数隔离修正与保留 final07 失败](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:11)；[9/24 停旧 Worker、迁移、启新发布](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md:19)。
- **下一动作**：每次真实长场前重新通过同版独立真实短场；本地 receipt 不放行真机。13/18 WorkUnit 只证明 end 转发前完成，不能写成全部在用户按停止前完成。final07 的 3 GET 已归因并补隔离，不能列成当前仍未修的产品重复请求。

### 07 · SAVE-01：delivery-status 使用不存在的数据库列

- **历史别名/关联**：s.thread_id / current_thread_id；9/22 人工短场 GET 500；曾被误称“正式记忆重建 SQL 错误”。
- **当前状态**：LOCAL_PASS、已进入 9/24 发布；当前发布真实未知响应恢复尚待验。不再登记为“源码尚未修复”。
- **最早入口**：[最早可核实 run05 修复与运行版比对](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/validation-release.md:99)。
- **设计**：[W1 Delivery-status](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:115)。
- **最新证据**：[保留 SQL 修复并在真实 PG 验证](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-01/implementation-report.md:5)；[final08 错绑/响应丢失恢复矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:11)；[发布固定源码](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md:12)。
- **下一动作**：按原场次只读恢复链验证；SQL 修复不是 seq5 从未执行的证明，也不授权重发未知 append。

### 08 · SAVE-02：admission 权限 epoch 字段及身份绑定错误

- **历史别名/关联**：context.authority_epoch AttributeError；CAP-15 authority_epoch；自动 short-08 end/ACK 成功后 admission 500。
- **当前状态**：LOCAL_PASS、已发布；当前发布真实新场 admit→候选→正式记忆尚待验。
- **最早入口**：[自动短场真实阻塞](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)；[既有修复未进入运行版的复盘](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/validation-release.md:83)。
- **设计**：[W1 Admission](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:125)。
- **最新证据**：[prepared epoch 修复保留及 PG 验证](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-01/implementation-report.md:5)；[默认链与故障事务矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:9)；[9/24 发布](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md:12)。
- **下一动作**：当前新短场验证有界关闭、同场 201 回执、Source/Run/candidate 绑定，不用默认 epoch、绕过授权或重放旧 admit。

### 09 · SAVE-04：当前场 UI、历史恢复和核实按钮归属

- **历史别名/关联**：历史 pendingReview 覆盖当前失败场；“核实整理状态”查到历史 workflow；W3；N3 newest blocked scene owner；U03。
- **当前状态**：LOCAL_PASS，待当前发布真机复测。当前没有证据支持“这部分至今完全没修改”。
- **最早入口**：[历史读恢复/UI 与保存链区分](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/ios-history.md:19)；[SAVE-04](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:61)。
- **设计**：[W3 当前场恢复与 UI 绑定](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:189)。
- **最新证据**：[N3/W3 与 U03 PASS](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:10)；[final08 同版 affected 4/4](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:35)。
- **下一动作**：真实新场结束、离页重进、冷启动时核对页面/按钮/候选均属于同场。安装后显示的旧恢复提示本身不是新场失败证据。

### 10 · DJ-LIVE-OBSERVATION-01：即时状态读取期限、迟到结果与轮次收尾（建议新 ID）

- **历史别名/关联**：saving 长留；第 6 次即时查询耗尽；W2/I10；U04；N1a in-flight 180s；N1b matching item stale envelope。它不等于所有“没有候选”的根因。
- **当前状态**：LOCAL_PASS，真实时序待验。原断言下 inverse-N1 反向重建红测三次、现版本绿测三次；不是完整历史源码复现。
- **最早入口**：[共享关闭与即时状态历史](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/ios-history.md:22)；[最新 N1 实现与证据](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/implementation-report.md:13)。
- **设计**：[W2 真实期限、同轮预算与迟到回调](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:154)。
- **最新证据**：[N1a/N1b 矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:7)；[最终红绿边界](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:20)。
- **下一动作**：真实短场观察截止/返回状态和候选实际发布；超时只说明未确认结果，不显示假成功，也不无限重启轮询。

### 11 · DJ-LIVE-DIAGNOSTICS-01：请求级首错持久化及日志故障隔离（建议新 ID）

- **历史别名/关联**：CAP-13；活动首错被淘汰；R2-05；R-08；D01～D04；N3 first error；AFError 遮蔽 -1001/-1005。
- **当前状态**：LOCAL_PASS。首错的 HTTP/system code、exposure、attempt、elapsed 可重建；诊断盘不可写不丢 Outbox。已有历史缺失字段不会因新代码自动补回。
- **最早入口**：[9/21 首错独立保留设计](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md:218)。
- **设计**：[9/23 请求首错持久要求](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:166)。
- **最新证据**：[D01～D04 真 BackendClient/Controller/磁盘故障矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:12)。
- **下一动作**：如新场再失败，保留首次请求断点并按同场脱敏关联服务器记录；本地诊断 PASS 不能替代历史 seq5 根因结论。

### 12 · SAVE-03：9/22 人工短场 seq5 初始未知结果（历史调查）

- **历史别名/关联**：DIAG_SEQ5_INITIAL_TRIGGER_UNRESOLVED；手机 6 条、服务器 4 条；seq5 outcomeUnknown；seq6 preparedNotExposed。
- **当前状态**：HISTORICAL_ROOT_CAUSE_UNRESOLVED。恢复 SQL、轮询、首错诊断已分别修复，但没有证明当时第 5 条最初为何进入未知。独立于 SAVE-01 和 SAVE-02。
- **最早入口**：[原人工短场记录](../2026-09-22-dreamjourney-live-device-retest/run-01/reports/2026-09-22-DreamJourney-Live短场门禁真机失败记录.md)；[后续手机 Outbox 核对结论](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/validation-release.md:103)。
- **设计**：[SAVE-03 限定事实及 W2](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:60)。
- **最新证据**：[9/24 发布仍明确未决](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-device-publish/release-result.md:8)。
- **下一动作**：先利用保留证据；若不足，在用户发起的新场复现时采首错。原未知业务写不得自动重放，不能为了“关闭问题”补写唯一原因。

### 13 · LAB-01：静音 STREAM 自动测试缺 native 播放完成回执

- **历史别名/关联**：自动 short-08 首轮后未进入第二轮；K05；LAB_COMPATIBILITY_DEVICE_PENDING。
- **当前状态**：本地工具诊断/防伪部分有证据；真实 SDK 自动模式兼容未闭环，保留 LAB_COMPATIBILITY_DEVICE_PENDING / DEVICE_NOT_RUN。不是已确认所有真人 Live 音频损坏。
- **最早入口**：[自动短场实测](../2026-09-22-live-device-lab/reports/2026-09-22-DreamJourney-iPhone自动化短场实测与阻塞分析.md)；[自动与人工明确分开](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-full-retrospective/2026-09-22-DreamJourney-Live全链路历史复盘与根因证据报告.md:68)。
- **设计**：[W7 精确状态与边界](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:361)。
- **最新证据**：[工具 16 测试与 native 缺项](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-01/acceptance-matrix.md:107)；[run03 外部 K05 NOT_RUN](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:27)。
- **下一动作**：后续授权分别测普通 mic、静音 STREAM，记录原生 drain/播放结束与恢复聆听；不注入假完成事件。仅解码非静音 PCM 不能关闭本项，也不能代替硬件声学验收。

### 14 · DJ-ASR-OBS-01：合成测试短语语音识别歧义

- **历史别名/关联**：临时代号同音、个别测试短语被识别为其他词；它与 canonical final/QueryConfirmed 来源隔离不是同一问题。
- **当前状态**：OBSERVATION，尚未确认为产品缺陷。没有当前证据允许升级成“转写功能未修”；也没有后续证据可宣称它已被特定修复消除。
- **最早入口/最新可核实专门记录**：[9/15 ASR 观察及缺失证据](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-b7-b8-live-device-retest/run-2026-09-15-01/reports/issues/2026-09-15-B7测试短语语音识别歧义-观察记录.md:3)。此项早于本段主范围，因用户要求避免混淆旧语音问题而纳入。
- **设计入口**：尚无已确认缺陷的独立修复设计；可沿用 [DEV-ACOUSTIC 验证边界](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-验收矩阵.md:210) 收集对照，不预设要改 B7。
- **下一动作**：如用户仍可复现，比较实际 ASR final、原口述、环境及重复一致性；优先用无歧义测试代号，不拿助手回答倒推 ASR 原文。

### 15 · DJ-TEST-ROUTE-BASELINE-01：后端路由计数基线债（建议新 ID）

- **历史别名/关联**：7 项 259/260 路由清单失败；全量“2598/2605”等旧表述；不包括已归因的 46 个 PoolClosed。
- **当前状态**：BASELINE_TEST_DEBT / OPEN，非本轮 Live 产品回归；未过滤全量后端不能标全绿。
- **最早入口**：[9/19 本地合同修复报告](../2026-09-18-dreamjourney-live-candidate-contract-fix/run-01/reports/2026-09-19-DreamJourney-Live记忆候选整理失败-本地修复报告.md)。
- **设计入口**：没有独立批准的路由基线修复方案；按 [W0 基线与不掩盖失败原则](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-统一修复开发设计.md:103) 保留，不借本轮改断言造绿。
- **最新证据**：[B 后端归因报告](../2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/B-backend-closure-report.md)；[最终矩阵](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/acceptance-matrix.md:24)。
- **下一动作**：另行审查路由清单基线与新增路由合同。46 个 PoolClosed 已映射到 44 个测试方法的错误 fixture，旧/新源码在规定 fixture 下 44/44 和相关 180 项通过，不再登记成 46 个未修数据库 bug。

### 16 · DJ-API-20260924-01：登录后 API 全局无响应（建议新 ID）

- **历史别名/关联**：登录后待确认/正式记忆/记录页持续 loading；容器 running / unhealthy；本机及容器内 /live 超时。原事件尚无问题编号。
- **当前状态**：TEMPORARILY_RECOVERED / ROOT_CAUSE_UNRESOLVED / NO_CODE_FIX。同版本受控重启恢复服务，不是修复闭环。
- **最早入口/最新证据**：[9/24 事件状态](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:5)；[各层健康接口超时及未取线程栈](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:12)；[重启恢复](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:15)。
- **设计入口**：尚无已完成根因设计；[事件待分析项](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:27)。不能套用旧 Live 模型容量方案，也不能先认定数据库锁或事件循环就是唯一原因。
- **下一动作**：定位登录并发读与 middleware/UoW/线程池/池/锁关系，建立可复现反例及故障期栈和资源诊断。优先于继续长场测试；不得通过重放业务写恢复。

### 17 · DJ-IOS-READ-TIMEOUT-01：读取超时误显示发布策略拦截（建议新 ID）

- **历史别名/关联**：readDeadlineExceeded → ClientError.featurePolicyDenied；“功能请求已被发布策略拦截”。不等于真实策略 deny。
- **当前状态**：CONFIRMED_NOT_FIXED。事件已定位映射，本事件没有修改 iOS 源码；与 API 全局无响应分开。
- **最早入口/最新证据**：[明确代码定位与误导文案](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:22)。
- **设计入口**：尚无独立定稿；[事件第 4 项要求](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:30) 要求保留真正 deny 语义。
- **下一动作**：区分超时、服务暂不可用与真正策略拒绝，并验证调用页呈现；修正文案不能替代修复第 16 项。

### 18 · DJ-MEMORY-READ-CHECK-20260924：重启恢复后候选数量与记忆完整性未核对（建议新 ID）

- **历史别名/关联**：待确认页“没有确认候选记忆”；正式页能够打开；记录页是否恢复不明。
- **当前状态**：READ_ONLY_VERIFICATION_PENDING，尚未证明数据丢失、过滤错误或账号串位；不是已确定待修代码缺陷。
- **最早入口/最新证据**：[9/24 恢复后的精确边界](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:16)。
- **设计入口**：暂无缺陷修复设计；[当前账号/过滤/内容只读核对要求](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:31)。
- **历史基线**：[9/21 用户授权清理 46→0，正式记忆和 Source 保留](../2026-09-21-dreamjourney-pending-memory-cleanup/清理完成记录.md)。它说明空列表有可能合理，但不能直接推断 9/24 应为 0。
- **下一动作**：同账号与过滤条件下只读核对当前候选基线、正式记忆内容及记录页；禁止为了核对新增/审核/清理历史数据。

## 应写入主册的状态总览

| 类别 | 条目 | 可用完成措辞 |
|---|---|---|
| 本地已修，真实验收待完成 | 01～11 中产品链路项；06 含验收基础设施 | 本地断言通过；9/24 已部署；当前版本真实短场与物理长场尚未验收 |
| 历史初始原因未决 | 12；01/02/04 内保留的历史具体原因边界 | 已修已知机制不等于已经还原每次历史首错 |
| 自动模式外部兼容未闭合 | 13 | 工具本地验证不替代 native playback / STREAM 真机证据 |
| 尚未确认的观察/读核对 | 14、18 | 不应提前写成确定产品缺陷或数据丢失 |
| 仍存在的测试债 | 15 | 七项旧基线异常保留；不是 Live 回归或 46 个数据库缺陷 |
| 当前明确未修/根因未定 | 16、17 | API 只临时恢复；超时错误分类尚未修正 |

外部统一验收仍对应 [EXT-01、DEV-SHORT、DEV-20M、DEV-65M、DEV-ACOUSTIC、DEV-OTHER](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-23-DreamJourney-Live短长对话保存闭环-验收矩阵.md:201)。短场先行；物理 65 分钟是后续独立目标，不能迫使当前本地任务等待手机。自然 401、断网、限流未发生则 NOT_OBSERVED，不拿“没遇到”当通过。

## 合并和更新建议

1. 主册一项问题只保留一个当前状态和一组验收状态；历史别名、各轮报告作为时间线链接。来源材料旧状态原样保留。
2. 以 latest-evidence 字段指向 final08 或 9/24 事件，不让 run01/run02 的 PARTIAL 和 final07 失败覆盖后续完成结果。
3. 将“已部署/已安装”与“真实短场保存通过/真实长场保存通过”分列。当前部署成功已经发生，真实长场闭环仍无本版本通过证据。
4. 根因不明用 UNKNOWN/UNRESOLVED，不用“推测已修”代替；没有证据的 ASR、数据丢失、Provider 限流也不升格为已确认缺陷。
5. 后续真实测试由用户主动发起，测试失败保留原场次证据；不用重放未知写、替换场次或清理历史制造通过。
