# DreamJourney Live 逐轮模拟 run-05 复核与有限收尾

日期：2026-09-22。

**结论：本次正常短场、长场保存链以及多项故障恢复验证符合预期；上一轮 A–D 中仍有三类验收缺口，暂不认可“完整本地验收全部通过”。这些是已复现的验收漏检，不是 Live 保存再次被改坏的证据。**

本文件承接 [run-04 复核与验收收尾](2026-09-21-Astra-Live逐轮模拟-run04复核与验收收尾.md)，只收尾其中明确要求的源码门禁、跨阶段证据绑定和受控模型请求校验。不新增产品功能，不重新设计保存、音频或记忆整理架构。

本轮整体交付建议表述为 `NORMAL_CHAIN_PASS / LOCAL_ACCEPTANCE_INCOMPLETE`。这是研发交付标签，不是 App 新状态。真实 Provider、部署、物理 iPhone、20/65 分钟及历史处理均保持 `NOT_RUN`。

## 1. 已核实通过，保留结果

本次读取了 Sol 的报告、A–D 矩阵、源码、产物和六份原始 xcresult，并运行了轻量隔离探针；没有重跑完整模拟器/HTTP/PostgreSQL 集成，也没有修改产品。

| 项目 | 复核结果与证据边界 |
|---|---|
| 两次独立 short 后分别运行 long | 四份原始 xcresult 各 1/1 PASS；顺序为 short→logical20→short→logical65 |
| 正常短长保存链 | 每个 short 4 回合、1 条候选；长场分别 220/300 回合、4/17 条候选；各组含短场的正式记忆为 5/18 条。真实客户端候选读取、隔离审核及 Store 重建有对应证据 |
| 配置统一和已有门禁 | Swift 拒绝 BASE64 内联配置，业务与控制请求共用磁盘配置；实际值重算摘要。独立探针正常消费 204、重复消费 409、配置变化保留旧摘要仍 412，业务计数 0 |
| 已列入的源码与构建产物 | 当前 21 项列内源码、宿主 dylib、测试可执行文件及 xctestrun 与 manifest 一致。依赖集合仍有漏项，见 §2 |
| 真实候选隐藏负例 | 真实 CandidateInbox Controller→BackendClient→受控 GET 响应，缺一条时原断言失败，恢复完整响应后通过；没有改数据库候选或直接填充 Controller 状态 |
| 逻辑时钟 | 注入生产共用的时钟实际推进 1200/3900 秒，经过 FeatureGate/authority 判断及刷新。不能外推为所有系统定时器或物理时长已经验证 |
| 401 恢复 | 本地真实 HTTP/PG 链业务处理前 401，实际认证刷新一次，原命令重试 201，完成后续保存 |
| 已保存但原响应丢失 | 本地服务端第 300 条写入成功后不交付原 201；客户端经 delivery-status GET 核实，原 append POST 仅一次。该装配产生 ASGI 错误响应，覆盖未知写恢复，不应称为真实物理断网 |
| 429/超时 | 原 Proxy 分类 2/2 通过；Astra 本轮复用既有 Worker 预算/恢复测试，仅替换为受控 429/ReadTimeout，4/4 通过，保留原预算断言。可直接纳入证据，不要求另起一轮重写预算逻辑 |
| OwnerTruth 原始全量结果 | **564 total = 561 PASS + 3 SKIP，0 FAIL**；保留跳过计数，独立 short/long 结果另列，不将 564 全写为通过数 |
| Echo/音频/账号 | 原始 xcresult 95/95 PASS |
| 后端与构建 | 原交付后端定向日志 92/92；模拟器及通用 iOS 无签名构建成功。此次没有重跑这些完整集合 |

不能因为剩余验收工具缺口撤销上述正常链结果，也不能让没有连接手机成为本地收尾的阻碍。

## 2. 剩余第一类：A3 源码门禁仍漏实际执行文件

### 已证实

[server:64](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/tools/cap15_round_simulation_server.py:64) 的 `PROTECTED_SOURCE_PATHS` 已补上一轮部分漏项，但仍没有：

- `app/services/owner_truth_candidate_review.py`：上一轮明确点名要求覆盖的候选读取实现。调用关系为 `app/main.py:8342` → `postgres_store.py:547` → 该模块的 PostgreSQL 查询 `:1932`。
- `app/services/owner_truth_conversation.py`：本轮实际修改的 delivery-status SQL 所在文件。
- 本验收链实际使用的 `owner_truth_formal_memory.py` 与 `owner_truth_interview_candidate_review.py`，分别承接正式记忆和审核仓储。

只散列工厂 `postgres_store.py` 不会自动散列其导入模块。这些实现改变后，现有门禁仍可能复用旧 short 凭证；当前列内指纹相符不能证明列外实现受到保护。

### 最小完成标准

补入上述实际实现，沿用现有 manifest、配置及构建产物绑定。用临时受保护文件副本的内容变更证明旧 receipt 在 long 启动前被拒绝，业务请求数不增加。无需为此散列整个仓库或修改业务实现。

## 3. 剩余第二类：B 的逐轮身份与最终正式记忆证据尚未完全关联

### B1：已记录真实数据，但部分阶段仍按位置配对

[Swift:5975](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5975) 确实读回磁盘 delivery，但把 raw member 与排序后的 delivery 进行 `zip`；尚未利用磁盘已有的 canonical→deliveryMessageID 映射连接二者。

[server:1080](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/tools/cap15_round_simulation_server.py:1080) 的跨阶段验证器：

- 对 raw 身份仅校角色、前缀及唯一性；
- 对 `append_receipts` 仅校数量，没有核确认身份；
- 磁盘与 HTTP 的 command/message/sequence/body 校验有效，应保留；
- Source 仍主要按数组位置、正文和角色配对。

用交付原验证函数执行的隔离探针确认：同角色、正文及数量不变，只互换 raw 身份与 handoff，仍被接受；损坏服务器确认身份也仍被接受。原先 owner/assistant 错角色负例能拒绝，但不能替代同角色错关联检查。此结论针对该验证器，不声称整个业务服务会接受所有错误请求。

**最小完成标准：** 从实际磁盘 canonical 映射、真实 HTTP receipt 或只读状态结果、服务器消息及 Source 关联记录连接身份，不能从预期数组填造关联。补同角色同正文错绑定、确认身份错绑定负例；正常对照继续通过。不要新增产品字段或要求每轮同步等待服务器。

### B3：双证据在 manifest 中完整，正式记忆的对应关系尚未验证

本轮确实读回了 publication manifest 和 atom，审核/重建前后保持一致；稳定重复事实分别保留 turn 7/189、7/221 的两处证据。这部分认可。

但 [server:1401](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/tools/cap15_round_simulation_server.py:1401) 查询的 `candidate_ids` 最后只保留数量，正式记忆校验主要为数量和所有内容拼接后的关键词，尚未定位“稳定兴趣对应候选→对应正式 memory→该 Source/manifest”的持久化关联。

**最小完成标准：** 保留实际候选 ID，利用现有审核与正式记忆关联记录定位目标 memory，重建读回后核其 `sourceRefs`/持久化关系连到含两个 turn 的证据记录。Source 级展示合同不变，不新增 UI 字段、不要求重复 Source 引用。补关联指向错误、正文和数量相同的负例。这里没有证据证明实际正式记忆已丢失原文。

## 4. 剩余第三类：C 的受控模型仍可能给错误输入返回“通过”

### C1：support 与关系判断边界未完整检查独立事实

[受控适配器:370](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:370) 及 [后置校验:1228](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/tools/cap15_round_simulation_server.py:1228) 对 support/关系判断的校验不足。

Astra 调用原适配器和原后置验证器的内存探针确认：

- 把 support 草案摘要替换为场景中无依据的事实后，仍返回 `supported` 和空遗漏列表，后置校验也通过；
- 把关系判断请求的用户证据正文与 incoming 事实替换，受控边界及后置校验仍接受。

因此不能据这些受控成功响应证明“模型收到正确的事实与证据”。这是验收模型可能误放行，不是 DeepSeek 本次真实响应出错的证据。

**最小完成标准：** 组织、support、关系请求在实际受控 HTTP 边界分别核验该批次/该页负责的事实、正文、证据引用与独立真值表。沿用合法分页规则，不要求每一页包含整场或草案全部页外证据；必须拦住本页负责事实被替换、所需证据丢失或错误引用。错误输入不得继续返回预设成功答案。用现有探针改成修前失败、修后同断言通过。

### C2：长场负例目前被 200 轮前置限制拦住，没有命中模型边界

long20/65 的原负例把整场 220/300 轮一次交给 Proxy；它的单次上限为 200 轮。因此记录的是 `organizationInput.inputInvalid` / `too many turns`，实际 transport 次数为 **0**。这不能证明长场请求正文篡改被受控 Provider 拦截。

short 对应负例实际 transport 次数为 1，命中受控正文校验，继续保留 PASS。

**最小完成标准：** 使用生产实际合法 unit/页的请求制造同索引正文替换或缺失，断言异常来自受控 Provider 边界及实际 transport 命中；或者准确引用已经通过的同边界 short 负例，不将超过 200 轮的前置拒绝当成长场边界验证。禁止为让此负例运行而提高产品 200 轮限制或取消分批。

## 5. 报告需同步校正，不另起产品改造

- C4 中 `hiddenResponseCount=2`、`normalResponseCount=4` 是累计 GET 响应次数，不是候选数。隐藏一条时应按该场实际条数断言，例如 long20 为 3→4，long65 为 16→17。真实 Controller 负例本身已有效，无需重做。
- 本轮 SQL 修改实际位于 `read_live_delivery_status`：将不存在的 `s.thread_id` 改为 `s.current_thread_id` 并保留别名；不是报告描述的“正式记忆重建 SQL 变量替换”。修正根因归属，避免下一轮修错模块。
- 报告声称认证重绑定“authority epoch 单调前进”，但 `EchoViewController.swift:2557` 的 retry 接受条件仅检查同场、同版本及 active，未直接比较新旧 epoch；单调比较在 delivery-status 分支 `:2966`。写清两条路径及各自证据，不混淆账号 lease、请求新鲜授权和服务端 receipt epoch。当前仍有新鲜授权与账号校验，不能仅据此断言发生跨账号写入，也不要求无证据改动此产品路径。
- D4 可纳入本轮 Astra 已完成的四项内存 Worker 探针，注明它们是追加证据，不宣称原 92 项已经逐项注入过这两类错误。不要求所有故障与两个时长做笛卡尔组合。

## 6. 有限收尾顺序与停止条件

1. 保留 run-05 全部原始证据。只补 §2–4 的验收工具/断言，校正 §5 的报告，不重做产品系统。
2. 若上述验收暴露真实产品缺陷，先保留最小失败证据，再局部修改，并把实际受影响模块列入回归；不能用降低断言、扩大预算、固定延时或虚构成功状态消除红测。
3. 完成后基于最终源码、配置和构建重新发放 short 凭证，固定执行 `short→logical20→short→logical65`。短场必须包含候选可见、审核、正式记忆及重建读回；每条 long 都使用自己的 fresh short 凭证。
4. 回归范围按实际变化确定；产品代码未变时准确复用仍有效的回归结果，不为凑数字重复全量。验收代码/构建变化影响的集成必须重跑并记录最终指纹。
5. 本文三类缺口及报告校正完成即可结束本地交付。没有额外隐藏门槛，也不要求连接手机才能标记本地完成。
6. Provider、发布和真机由用户另行主动发起。后续真实长场前仍先真实短场；物理 20 分钟通过后再按计划做 65 分钟，均验证进入待确认记忆、审核后进入正式记忆及重启读回。

## 7. 可直接交给 Sol 的提示词

请严格按《2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md》继续本地收尾。Astra 已认可本轮正常短长保存链、真实客户端候选读取与隐藏负例、逻辑时钟、HTTP 401 和未知写恢复，不要重做保存或音频系统。

只完成文档中的三类剩余项：A3 实际依赖清单；B1 原始事件到磁盘映射、真实确认及 Source 身份连接和 B3 对应正式记忆的证据关系；C1 support/关系请求独立事实校验与 C2 长场负例实际命中 Provider 边界。使用文档链接的 Astra 隔离探针复现，不把错角色、超过 200 轮前置拒绝、数量或关键词检查冒充关联/模型边界校验。合法分页保持，不扩大模型输入上限或重试预算。

429/timeout 的四项 Worker 预算探针已由 Astra 补做并通过，准确纳入证据即可。同步校正报告中候选数量、delivery-status SQL 和授权路径的表述。只有实际红测证明产品缺陷才局部改业务，并回归受影响模块。最终同一版本按 short→20→short→65 完成本地全链并交付精确矩阵、原始结果和指纹。持续完成本地任务，不检测或等待手机，不调用真实 Provider、不部署、不访问生产或历史数据、不 commit/push；真机由我主动发起。

## 8. 复核材料

- [Sol run-05 交付报告](../../outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-05/reports/2026-09-22-DreamJourney-Live逐轮模拟-run05验收收尾报告.md)
- [A/B 行号与核对说明](../../outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/gate-ledger/review.md)
- [A/B 原函数探针与结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/gate-ledger/probe-results.json)
- [C/D 运行说明](../../outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/README.md)
- [受控模型边界探针结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/provider-input-probe-result.json)
- [429/timeout Worker 预算与恢复追加结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/transient-budget-probe-result.json)

本次新增仅为上述复核文档及隔离证据；未修改产品、未重新连接数据库、未连接 iPhone 或真实模型，未操作生产、部署、历史任务或 Git 提交。
