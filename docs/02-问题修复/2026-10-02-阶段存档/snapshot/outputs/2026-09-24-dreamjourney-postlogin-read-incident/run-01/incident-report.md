# DreamJourney 登录后多页读取超时事件（独立记录）

## 状态与范围

- 事件日期：2026-09-24（北京时间）。当前状态：API 受控重启后健康接口及用户正式记忆读取已恢复；根因未定位，长期稳定性未验收。
- 仅记录本次登录后“待确认记忆、正式记忆、记录/人物记忆归纳”载入中与策略拦截提示。不要与 Live 候选整理、历史 seq5 或其他专项合并归因。
- 本次没有修改源码、部署新制品、审核候选、重放业务写或操作正式记忆。`docker compose restart -t 30 api` 只是同版本 API 进程恢复，不是代码修复。

## 现场与恢复证据

1. 用户重置测试账号验证码并成功登录后，报告待确认、正式记忆及记录页面均持续“载入中”，随后出现“发布策略拦截”。真机截图保存在 `/private/tmp/dreamjourney-postlogin-loading-20260924.png`；人物记忆归纳显示 `ownerTruthCandidateReview: readDeadlineExceeded`。截图证明客户端收到有界读取超时，不证明服务端发布策略作出 deny。
2. 故障期间，从 Mac 访问生产公开 `/live`、`/ready` 均超时；在服务器本机访问 `http://127.0.0.1:3100/live`、`/ready` 也超时。容器内访问 `127.0.0.1:8080/live` 同样超时。故障不局限于 iPhone、热点或公网代理。
3. 当时 `dreamjourneybackend-api-1` 为 running 但 `unhealthy`，未观察到自动重启；CPU 约 0%，内存约 99 MB，主机负载约 0.07，进程线程在 futex 等待。近 30 分钟 API 日志约 201 行，未检出 Traceback、ERROR、CRITICAL、PoolTimeout、PoolClosed、deadlock 或 readDeadlineExceeded；这不等于内部没有死锁/阻塞。故障时没有取得 Python 线程栈，不能指定具体锁或函数。
4. 同时用独立的一次性 API 容器执行只读迁移核验，返回 schema `0124`、零待迁移；数据库至少对该独立进程可访问，不能因此证明旧 API 进程的池、锁或事件循环正常。
5. 对当前 release 的 API 进程执行一次受控重启，未改环境、数据库、Worker 或制品。新进程 `2026-09-24T05:52:34Z` 启动后健康；本机 `/live` 为 HTTP 200、约 34 ms；公开部署 readiness smoke 再次通过，database/schema/auth/incident 组件 ready。
6. 用户随后确认待确认页面已不再载入中，显示“没有确认候选记忆”，正式记忆页面也能正常打开。候选是否应为 0、记录页面是否恢复以及正式记忆内容是否完整，尚待与当前账号/过滤条件核对，不能仅凭页面打开宣布数据完整或现场问题关闭。

## 代码定位（不是根因结论）

- 后端发布提交：`8141ff271228b78c35237f9947f4bf026332af43`；部署 release：`/opt/services/dreamjourney/releases/live-unified-8141ff2-20260924`，schema `0124`。后端工作区核查时为 clean `main`。
- `app/main.py` 的 `/live` 属于 `DATABASE_TRANSACTION_BYPASS_PATHS`，却也在旧进程中超时；因此“正式记忆 SQL 慢”不足以解释全局 liveness 停摆。新提交的 `app/main.py` 差异主要是 Live 长记忆登记/结束及候选审核参数，未观察到本次直接改动基础 middleware 的证据。
- iOS `DreamJourneyBackendClient.swift` 的 bounded read 截止分支将 `readDeadlineExceeded` 包装为 `ClientError.featurePolicyDenied`，通用错误文案是“功能请求已被发布策略拦截”。这能解释截图上的误导性措辞，但不是后端 `/live` 无响应的原因。修正文案不能充当本次主故障修复。
- 当前 iOS 工作区有前序大量未提交修改；本事件未改动这些文件。

## 待 Astra 分析的问题

1. 查明旧 API 进程为什么连 `/live` 都不能响应：事件循环阻塞、同步线程池耗尽、middleware/全局锁、数据库连接池等待或其他资源竞争，需用可复现测试或下次故障时的线程栈区分；不要把发布策略 deny 当作已证实根因。
2. 对照登录后的并发只读请求、认证/策略/运行配置恢复与当前 API middleware/UoW，找出能使全局 liveness 停摆的最小反例；确认本次新 release 是否引入，或仅暴露了既有缺陷。
3. 给出低风险的故障期取证方案（进程栈、事件循环/线程池/连接池占用和脱敏 trace），以及健康检查失效后的有界运维恢复方案。不能以自动重试审核或未知写、清理数据作为恢复手段。
4. 单独决定 iOS 是否把 `readDeadlineExceeded` 显示为“读取超时/服务暂不可用”而不是“发布策略拦截”；保留真正策略 deny 的原有含义和诊断字段。
5. 核对同一测试账号的待确认数量、过滤条件和历史预期，以及记录页的读取结果；当前“没有确认候选记忆”不等于已证明没有数据丢失或错账号作用域。

## 后续验证与安全边界

- 如再次出现，请先保留故障期容器/进程栈与健康探针结果，再考虑重启；不要靠反复重启代替根因修复。
- 复测待确认、正式记忆与记录三个页面的加载、数量/内容和账号作用域。该验证只读，不审核、不写入、不清理历史。
- 当前可表述为“服务临时恢复、原因待查”；不可表述为“发布策略已修复”或“生产全部正常”。
