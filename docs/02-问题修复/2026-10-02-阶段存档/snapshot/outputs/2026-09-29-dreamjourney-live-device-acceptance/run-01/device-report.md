# Live 部署与短场真机失败记录 · 2026-09-29

## 结论

`DEPLOYED / IOS_INSTALLED / DEVICE_SHORT_FAIL / DEVICE_20M_NOT_RUN`。

本轮完成后端发布、六个 Worker 对齐和真实 iPhone 安装。自动短场未通过；按先短后长门禁，物理20分钟没有开始。没有把真实开麦失败当成本地验收成功，没有审核历史候选或重放历史任务，没有commit/push。

**已取得新的故障期进程栈：API 主线程在统计中间件同步写 evidence 时等待数据库连接池，期间本机 `/live` 不响应。** 同场票据请求25秒后503，手机15秒超时。该路径属于内部后端，发生在真实供应商对话之前。连接池为什么首先耗尽尚未完整查明；不据此把9/24、历史seq5或历次长场失败都归为同一根因。

## 发布与版本

- 已验证本地交付：[接手入口](../../2026-09-29-dreamjourney-live-mic-start-takeover/run-01/README.md)。
- 后端基线 `8141ff271228b78c35237f9947f4bf026332af43` 加五个已验证实现文件；独立发布目录 `/opt/services/dreamjourney/releases/live-mic-20260929`。不是把整个dirty工作区发布，也没有创建Git提交。
- 部署前运行权威预检PASS；迁移前备份 `dj-20260929T052211Z-d501cf36`、部署后备份 `dj-20260929T053100Z-2210e64f` 均verified且schema0124；无新增迁移/历史维护。
- 运行镜像947个app/db/scripts文件与本地逐文件完全一致：[指纹检查](deployed-source-check.json)、[覆盖文件指纹](backend-overlay-manifest.json)。
- 六个已启用Worker经过正常宽限排空、镜像/schema/activation检查及两次稳定性检查；[发布日志](deploy-activate.log)。原API镜像保留为 `dreamjourney-rollback-api:pre-mic-20260929`，旧release目录保留。
- 第一次SSH stdin脚本被Compose消耗输入，仅执行到构建和迁移verify，没有切换API。以远端脚本文件继续激活；两份日志保留，不把第一次exit0误报为部署完成。
- 部署前48项后端测试初次1项失败：独立voice_launch logger不传播，而测试仍监听父级app.main。只改测试监听目标，48/48通过；未改变业务实现。此测试文件不是发布文件。
- iPhone14ProMax、iOS27.0、有线连接。应用 `com.gaominge.dreamjourney.app` 更新安装保留原数据。实际供应商SDK，编译flags仅 `DEBUG LIVE_DEVICE_AUTOMATION`，没有受控SDK替身。签名有效期覆盖本次窗口。完整源码/工具/签名bundle身份见[result-summary.json](result-summary.json)。
- 前述本地验收与本轮服务器可用性是不同层级；发布成功不代表真机通过。部署后公网健康曾超时，已在原[健康日志](post-deploy-health.log)保留。

## 真机过程与同请求证据

1. `SHORT`预检通过：当前账号、两项真实策略许可、媒体音量0；未开始Live。
2. 首次正式运行仍停在预检，47.55秒返回工具 `readTimeout`，`liveStarted=false`、完成0轮。本机与公网健康检查同窗超时。API自行恢复，未重启；迟到抓栈没有捕获阻塞，不能从这份恢复后栈下结论。
3. 保留全部初次证据后，开启有界只读健康观察，并建立 `SHORT02`。第一次没有业务场次，不是对已曝光业务写的重试。
4. `SHORT02`发起一次真实开麦尝试，最后 `timeout_liveListening`，完成0轮，`liveOpenAfterFailure=false`。工具总共等待60秒是其观察窗口；**产品自身在15.026秒已经收尾**，不能误称产品等了60秒。

手机与服务器共同trace：`133a3e0b-2084-4c7d-96f5-fbe4bb097211`。

| 阶段 | 相对手机点击时间/服务端时间 | 结果 |
|---|---:|---|
| 麦克风权限完成 | 3ms | 通过 |
| 当前策略判定 | 43ms | 通过 |
| 创建ticket请求 | 45ms | requestCount=1 |
| 请求resume | 47ms | taskResumed |
| 手机统一截止 | 15026ms | timeout；首错ticketResumed |
| 服务端ticket响应 | 服务端入站后25055ms | HTTP503；只见inbound/responseReady，没有ownerAuthorized或ticketStore阶段 |

证据：[手机启动诊断](SHORT02/launch-diagnostics.json)、[服务器安全阶段](server-sanitized-stages.log)、[首次结果](SHORT/result.json)、[第二次结果](SHORT02/result.json)。手机文件按该trace筛选，只保留本次诊断；没有导出原始对话。NativeLiveDiagnostics本场没有新文件，不能将其缺失解读成“正常”。

## 已定位的阻塞路径及未知项

[故障栈](api-stall-stack-03.txt)在本机 `/live` 同时超时时取得：

```
MainThread
  shadow_operation_metric_attempt (app/main.py:7893)
  _record_operation_metric_attempt (app/main.py:7381)
  OperationMetricRecorder.record_attempt
  PostgresStore.append_evidence_event
  PostgresStore._fetchone
  request_unit_of_work
  DatabaseUnitOfWork.__enter__
  PsycopgConnectionPool.getconn
  psycopg_pool.wait
```

该同步等待运行在Uvicorn主事件循环线程上，阻塞期间不仅当前统计写入，其他请求也无法正常推进。时间窗内服务端记录9次`database_pool_exhausted`。早一次PG快照没有活跃长事务/数据库锁等待；这只能排除该采样瞬间的DB锁，不能证明连接都被应用归还。

目前能确认的是**池等待在主事件循环发生的故障机制**。仍需本地复现并核对连接checkout/return、请求UoW生命周期、并发启动读取和统计sink竞争，才能判定最初耗尽原因。不能仅扩大池或超时，也不能仅把`/live`绕过统计就认定所有业务读取已修复。

真实火山会话未进入SDK启动/聆听，DeepSeek候选整理未进入；本场没有证据支持第三方模型容量、超时是此次首因。该结论不外推历史已进入整理阶段的失败。

原生栈模式工具报`UNW_EBADREG`，保留失败；随即使用Python栈成功捕获。首次公网源工具下载慢，使用服务器既有镜像源临时安装到/tmp；没有修改API镜像或其依赖环境。

## 验收状态与数据影响

| 项目 | 状态 |
|---|---|
| 后端发布/安装 | 完成；稳定性故障另列 |
| iPhone账号、真实策略、静音预检 | PASS |
| iPhone短场完整链 | FAIL，启动阶段受阻，0轮 |
| iPhone新启动15秒有界收尾 | 本次观察通过 |
| 火山对话、DeepSeek整理 | NOT_RUN |
| 待确认候选审核、正式记忆及冷读 | NOT_RUN |
| 物理20分钟、65分钟 | NOT_RUN |
| 声学麦克风/扬声器 | NOT_RUN |
| 历史重处理、批量清理、commit/push | NOT_RUN |

本次工具没有发送合成语音，也未执行本场候选审核或正式记忆写入。保留App已有记录，不覆盖或清空。服务器随后自行恢复；[结束健康检查](final-health.log)只能证明检查时可用，不构成根因修复。

后续优先处理DJ-API-READ-STALL-01 / DJ-API-LIVENESS-01：在隔离PG和默认API入口复现阻塞，验证修复后探针与无关请求能够在统计/连接池故障下继续推进，再同版重跑独立短场。短场完整进入候选、审核、正式记忆并冷读通过后，才能开始20分钟。
