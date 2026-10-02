# run05 A/B 独立复核

范围：2026-09-22，仅按 run04 authority 的 §2 A 与 §3 B。只读现源码/交付证据，以 AST 载入原验证函数执行临时样本，不导入产品 app、不启服务、不连接 PostgreSQL、Provider 或手机、不重跑全量。新增文件仅在本复核目录。

## 已确定通过

- A 配置双入口已关闭：`OwnerTruthContractsTests.swift:5666–5693` 拒绝非空 BASE64，只读一次磁盘 JSON，业务客户端与控制请求共用该 object；runner `run_round_simulation.py:221–228` 清除 simulator 和 runner 的 BASE64。`server:153–156, 271–284` 根据磁盘实际值重算摘要，忽略配置自带 `configFingerprint`。独立原函数探针实际配置 vault 值变化且保留旧缓存摘要返回 412，业务计数 0。
- A 宿主/测试/xctestrun 已绑定：runner `348–407` 完成 build-for-testing 后记录 source manifest 与三类产物；`232–237` 使用 manifest 同一 xctestrun 进行 test-without-building；server `109–144` 核验清单/source/三个产物。当前 21 项列内源码、host dylib、test executable 与 xctestrun 均与交付摘要相符。xctestrun 的 TestHostPath/TestBundlePath 解析到 manifest 对应的 DreamJourney.app 和 DreamJourneyTests.xctest。
- A 单次消费成立：server `276–305` 核验同 run/attempt/source/config/build/shortComplete 并消费 receipt。独立原函数探针正常 204、重复 409；Swift `6204–6212` 非预期状态会抛错，长场业务装配在调用成功后才执行。交付复制件负例在 `run_round_simulation.py:141–197` 复用构建校验器，配置/host/test/xctestrun 变化均拒绝并核对业务数不增加。
- B 新增证据有效部分：Swift `5975–6000` 确实从 outbox.load 读回磁盘 command/message/sequence/role/body；server `557–564` 确实采集实际 HTTP 请求与响应；`1117–1129` 核验磁盘 command/message/sequence/role/body 与 HTTP、Source 的正文/角色。交付 220/300 条账本不应作废。
- B 最终持久化 manifest 读回有效：server `1401–1444` 读 Live run、atom、publication manifest，`2045–2060` 在审核及 Store 重建后再次读回。独立对原始 JSON 检查 before/after 完全一致；logical20 的稳定兴趣 manifest memory/sourceEvidenceRanges 都为 turn 7/189，logical65 都为 7/221。原始证据确实没有在这两个 manifest 中丢失。

## 阻断 1：A3 仍不能称完整源码依赖绑定

依据原要求：run04 authority 第 38 行明确要求至少包括“候选读取/支持复核的实际实现”。

`server:64–86` 的 21 项清单仍不含 `app/services/owner_truth_candidate_review.py`。真实 `GET /v2/vaults/{vault_id}/candidates` 在 `app/main.py:8342–8355` 调 `OwnerTruthCandidateReviewService.list_pending`，`postgres_store.py:547–555` 建立该模块的 PostgreSQL repository，实际 SQL 在 `owner_truth_candidate_review.py:1932–1968`。因此修改候选读取实际实现不会改变门禁 `current_source_fingerprint()`，仅加入 postgres_store.py 这个工厂文件没有覆盖其实际实现。

另一个直接漏项是本轮实际修改并在已保存丢响应恢复链使用的 `app/services/owner_truth_conversation.py`：`postgres_store.py:897–910` 返回该 repository，`owner_truth_conversation.py:3358–3395` 为真实只读 delivery-status 查询，当前第 3377 行使用 `s.current_thread_id AS thread_id`。因此 A3 不仅漏候选读取，还没有覆盖本轮这项实际产品修复。这里确认的是其未进入门禁，未主张当前 SQL 修复失败。

这不是要求散列整个仓库；上述两个文件已处于上一份明确要求的候选读取/真实执行依赖范围。

最小收尾：补齐真实运行链相关实现清单，保留现有实际配置与产物绑定。源码指纹改变后按原 short→long 门禁规则产生对应证据。

## 阻断 2：B1 的 raw→disk 与 HTTP confirmation→Source 身份仍未连通

依据原要求：run04 authority 第 47–49 行要求磁盘 canonical 身份、实际请求命令/消息身份及服务器确认关联，且身份不能从预期数组反向填造。

- Swift `5982–6000` 将 raw member 与按序号排序的 delivery `zip`，但没有从磁盘 canonical 记录读出 canonicalTurnID→deliveryMessageID 的真实映射。磁盘已有这样的记录：`OwnerTruthContracts.swift:16373–16387`，其中 `deliveryMessageID` 第 16380 行。
- server `1112–1116` 只校 rawID 含角色、handoff 前缀自洽、唯一性；没有 raw→disk 身份比较。
- server `1097` 对 `append_receipts` 只比较数量，之后不读确认 payload。`1126–1129` 的 Source 校验仍是顺序/角色/正文；`1139` 记录 source turn index，却没有断言它与关联身份一致；请求 path 中 vault/session 与 Source 归属也未核对。
- 交付负例 `1145–1148` 交换 owner/assistant 的 rawID，命中角色错误，确实有效；但它不证明同角色错关联会被拒绝。

独立原函数探针结果 `probe-results.json:15–93`：四条相同数量/正文的正常对照接受；同角色两条 rawID+handoff（并连同 ordinal）互换仍接受；全部服务器确认身份损坏仍接受；Source id 错置、Source 两个同角色 index 互换、HTTP path 换成其他 vault/session 都仍接受。提供的 owner/assistant 错角色负例则被拒绝，说明探针没有关闭校验器。

最小收尾：从实际磁盘 canonical 映射、真实 HTTP receipt/状态读回、Source 关联记录补齐身份连接；用同角色、正文及数量不变的错关联负例验证。不要新增产品字段或同步等待要求。

## 阻断 3：B3 的双证据已在 manifest 保留，正式记忆关联尚未证明

依据原要求：run04 authority 第 51 行要求最终候选/正式记忆的证据绑定读回；允许产品只展示 Source 级 sourceRefs，不要求新增 UI 字段或重复 Source 引用。

`persisted_evidence_binding` 查询了 manifest 的 `candidate_ids`，但 `server:1443` 只返回其数量；`2047–2058` 只比较 manifest hash 和递归寻找一个含两处索引的 item。`confirm_and_rebuild:1569–1578` 正式 memory 读回仅核数量，`1986–1990` 在所有正式 memory JSON 拼接后核正文片段；没有定位稳定兴趣对应的那条正式 memory 并核它的 sourceRefs/既有持久化关联确实连到已读回双 turn 的 Source/manifest。

因此可以确认“publication manifest 中两处证据完整且审核前后一致”，不能据此把“最终正式 memory 的证据绑定已经读回”整体标 PASS。候选 sourceId 校验和稳定兴趣单条候选应保留（`validate_candidates:1174–1192`、`2011–2018`），本结论不主张实际候选/正式记忆发生了丢证据。

最小收尾：按现有 Source 级展示合同定位目标候选和对应正式 memory，核 sourceRefs/持久化关联并读回双 turn 的证据；保留当前 manifest 成果，不要求新增 UI 字段或重复 Source 引用。

## 证据文件

- `probe.py`：加载交付原函数的轻量隔离探针。
- `probe-results.json`：当前源码/构建核验、单次门禁、身份绕过对照、原始最终证据读回检查。

本次没有重新运行真实 HTTP/PG 集成、模拟器测试、Provider 或真机。新发现是验收门禁/账本完整性缺口，没有据此推断正常保存产品链失效。
