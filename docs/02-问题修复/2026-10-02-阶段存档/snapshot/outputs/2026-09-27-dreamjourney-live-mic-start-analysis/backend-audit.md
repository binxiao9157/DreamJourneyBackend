# Live 开麦前链路后端只读审查

审查日期：2026-09-27。后端工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`。

本轮只读代码、Git 历史及既有事件记录；未改业务代码，未连接生产、手机、真实 Provider，未运行测试。本文区分静态代码事实、历史观察和待验证假设。

## 结论

1. 已确认后端真实顺序是：HTTP 鉴权与发布策略 → 正式记忆快照 → 写入一次性 ticket → 客户端 WebSocket → 火山连接。约 50 秒窗口不能直接归因于火山。
2. 受控失败快照中的 `capturedPolicyExpired` 是客户端关联线索；尚不能证明它就是该次请求的直接拒绝值，更不能写成服务端返回了同名 deny。
3. 没有 ticket 完成日志不足以证明完全没有网络活动，也不足以排除请求已到后端但阻塞在响应前。
4. 发现一个应补入全局停摆分析的静态事实：`/live` 虽绕过 request UoW，响应前仍同步写入操作指标数据库，因此并非完整脱离数据库。该同步调用位于 async middleware，存在阻塞事件循环和健康接口的机制；尚未证明 9/24 的进程就是卡在此处。
5. 两起历史事件不能合并为同一根因。现有证据缺少贯穿请求的 trace、故障期 Python 栈以及各阶段耗时。

## 1. 实际服务端入口和调用顺序

- POST 入口为 [app/main.py:17810](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17810)。要求真实 user session；然后检查 capability、构造 snapshot，最后签发 ticket。
- target/family 授权在 [app/main.py:17328](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17328)，快照构造在 [app/main.py:17373](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17373)。
- [formal_memory_conversation_snapshot.py:399](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/formal_memory_conversation_snapshot.py:399) 读取当前正式 projection，检查 ready、rights、checkpoint、memory revision 并生成内容。该路径未见 LLM/火山调用。
- Postgres projection 读取 vault、checkpoint、memory revision、person projection，其中有 `FOR SHARE`：[owner_truth_memory_projection.py:411](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_memory_projection.py:411)、[同文件:570](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_memory_projection.py:570)。
- ticket 写入包括用户级 `pg_advisory_xact_lock`、subject share lock、既有票据 update lock，再检查并发上限并 INSERT：[postgres_store.py:1970](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/postgres_store.py:1970)。
- 真正火山连接仅在 WebSocket ticket `consume` 成功之后发生：[app/main.py:17866](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17866)、[realtime_voice_proxy.py:471](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:471)。
- capability 的 ready 判断检查配置与 URL 形状，并非现场 Provider 连通性验证：[realtime_voice_proxy.py:548](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:548)。整体 readiness PASS 不能替代 ticket/SDK/开麦验证。

## 2. FeatureGate 与认证边界

服务端也明确把 `/voice/realtime-token` 映射为 `echoTextInput`：[release_policy.py:985](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py:985)。middleware 在 endpoint 之前校验身份、所有权、test account entitlement、incident stop line 与 release policy：[app/main.py:7468](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7468)、[app/main.py:7065](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7065)。

POST capture 读取 feature、version/revision、decisionId、allowed、account generation，并要求用户请求携带 capture：[app/main.py:7179](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7179)。不能固定 gate 为 true，也不能以另一 feature 的权限代替本请求的权限。

服务端 `capture()` 重新构造当前 server snapshot，然后使用该新 snapshot 的 `expiresAt`：[release_policy.py:1030](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py:1030)、[同文件:1077](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py:1077)。它没有接收客户端旧 expiry。服务端相应过期码是 `capturedPolicyExpiredBeforeEffect`：[同文件:1102](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py:1102)。因此现场快照中的 `capturedPolicyExpired` 不能直接当作服务端 deny 的证据。

认证 middleware 直接同步调用 `resolve_access_token`：[app/main.py:7497](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7497)；该服务会读取 auth session、account 及授权快照：[auth_sessions.py:98](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/auth_sessions.py:98)。

## 3. “没有观察到 ticket”能与不能说明什么

历史报告记下的是邻近请求的完成时间，没有 ticket 发起时间，也没有同一端到端 trace：[开麦事件报告:25](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-live-mic-start-incident/run-01/incident-report.md:25)。22:57:49 ticket 200 和约一秒后 SDK callback 证明当次进入了后续阶段，但不能定位前面约 50 秒消耗在哪一段。23:05 窗口没有新 ticket/auth/policy/runtime 访问日志支持客户端发前失败或未到达后端，尚不是排他性证明。

仍需区分三个状态：客户端发前拒绝；DNS/TLS/连接阶段或请求未到应用；请求已进入后端但卡在 middleware、SQL、快照、ticket 写入、事务提交或响应前指标落库而没有完成日志。

`/config/runtime` 和 `/v2/release-policy` 还可能触发 capability collector 的数据库读取，以及配置启用时的 ClamAV sidecar 探针：[app/main.py:5926](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:5926)、[runtime_capability_control_collection.py:57](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/runtime_capability_control_collection.py:57)、[provider_runtime.py:416](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/provider_runtime.py:416)。这些不构成火山 Live 调用，也不能把开麦准备概括为完全无网络。

对本次新开麦链，没有有效 ticket 就不会按上述后端代码新建火山连接。但没观察到新 ticket 日志不足以排除之前仍存活的 WS 或其他独立 Provider 作业；本轮没有发现此类实际发生的证据。

## 4. 全局停摆候选机制：健康接口仍同步落库

`/live` 属于 UoW bypass 路径：[app/main.py:7280](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7280)，但也属于已登记路由：[route_ownership.py:168](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/route_ownership.py:168)。外层 `shadow_operation_metric_attempt` 对匹配路由在返回 response 前同步记录指标：[app/main.py:7831](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7831)、[同文件:7881](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7881)。

完整调用链为：

1. recorder 在启动时绑定 `store.append_evidence_event`：[app/main.py:6147](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:6147)。
2. `record_attempt` 同步调用 sink：[operation_metrics.py:159](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/observability/operation_metrics.py:159)。
3. Postgres sink 执行 `INSERT evidence_events`，使用 `commit=True`：[postgres_store.py:979](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/postgres_store.py:979)。

静态代码已证明：这段数据库等待发生在 async middleware 上，存在阻塞事件循环的机制，健康接口也经过它。异常捕获只处理已经抛出的错误，不能限制等待；指标 elapsed 在 sink 调用前计算，因此该段等待不会被自己的指标耗时覆盖。

其他同类同步点包括认证查询以及 UoW 的 getconn、BEGIN、commit：[app/main.py:7790](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7790)、[uow.py:81](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py:81)、[uow.py:117](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py:117)。

因此，“`/live` 超时，所以数据库或锁等待不能解释全局停摆”不能成立；“普通正式记忆查询本身不足以解释全部现象”仍成立。上述是可定位的候选机制，不是 9/24 故障根因结论。历史现场只有 futex 等待和重启后恢复，没有 Python 栈：[全局读取事件报告:12](/Users/gaominge/Documents/liftora/outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md:12)。

## 5. 超时与版本边界

- pool checkout 默认 5 秒、pool max 默认 10：[config.py:53](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:53)。此值不是 SQL、锁或完整 ticket 请求 deadline。已检查的 ticket/snapshot/UoW 路径没有独立整体 deadline；实际 DB role/DSN 的超时配置本轮未知。
- 火山 WebSocket connect timeout 默认 10 秒：[config.py:365](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:365)。不能拿它解释 ticket 完成之前的等待。
- ticket 默认 TTL 60 秒，从写库前计算：[realtime_voice_proxy.py:113](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:113)。写库或返回前等待可能消耗其可连接时间；尚无现场证据证明本次因此过期。
- 当前本地 clean `main` HEAD 为 `8141ff271228b78c35237f9947f4bf026332af43`，与历史事件报告所载 release SHA 相符。未重新核验线上当前运行版本。
- `ffd02f3..8141ff2` 未改上述 UoW、指标 recorder、snapshot builder、auth session；相关新变化主要为 Live 长记忆登记/结束和会话资源 profile。`git blame` 显示同步 UoW middleware 从 7/16 已存在，响应前指标写入从 7/18 已存在，不能指称为 9/24 新引入的基础设施回归。

## 6. 已有测试和本轮验证边界

本轮以下测试全部 `NOT_RUN`，这里只确认测试代码与覆盖范围。

- broker 单次票据、释放、并发限制、logout、refresh 保持 lease、authority 变更已有测试：[test_realtime_voice_proxy.py:334](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_realtime_voice_proxy.py:334)、[同文件:454](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_realtime_voice_proxy.py:454)、[同文件:510](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_realtime_voice_proxy.py:510)。
- route ready 与 snapshot failure 测试使用 InMemoryStore、mock snapshot 和 shadow policy，不能证明真实 Postgres 并发/超时或端到端开麦：[test_credential_response_boundary.py:21](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_credential_response_boundary.py:21)、[同文件:201](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_credential_response_boundary.py:201)。
- UoW health bypass 测试直接调用该层 middleware，未穿透完整健康接口链：[test_db_uow.py:282](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_db_uow.py:282)。
- metrics 测试覆盖 sink 立即抛异常后业务仍返回 200，未覆盖 sink 阻塞或并发拖住 liveness：[test_operation_metrics.py:330](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_operation_metrics.py:330)。另有测试明确预期 `/health` 被记录：[同文件:244](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_operation_metrics.py:244)。
- release policy 过期、缺失 capture、account generation mismatch 有服务级测试，未连接本次 iOS 调用链：[test_release_policy.py:1566](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_release_policy.py:1566)、[同文件:1633](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_release_policy.py:1633)。

## 7. 最小下一验证

1. 先做隔离离线故障注入：分别延迟完整 ASGI 链的 metrics sink、pool checkout、auth lookup，检测 `/live` 和事件循环进度是否一起受阻。必须使用 fake/in-memory 依赖，不能让测试导入真实生产 store 或调用 Provider。
2. 为一次开麦补同 trace 的客户端 request exposure，以及后端入站、鉴权、快照、ticket 写入、commit、指标 sink、响应发送、WS consume 与 upstream connect 各阶段时间；只记录白名单枚举、状态码、随机请求标识和耗时，不记录凭据、正文或完整响应。
3. 若再出现全局停摆，先保存 Python 线程栈、事件循环进度和池/锁等待证据，再按已有受控运维授权恢复进程。不要以连续开麦、重启或未知业务重放代替根因证据。
4. 取得证据前，两起事件保持独立。客户端发前策略恢复修复、后端同步阻塞修复和真实开麦验收必须分别报告，不能互相替代。

交付状态：静态后端审查完成；代码修复、测试执行、生产查询、真实 Provider 调用、手机复测均 `NOT_RUN`。
