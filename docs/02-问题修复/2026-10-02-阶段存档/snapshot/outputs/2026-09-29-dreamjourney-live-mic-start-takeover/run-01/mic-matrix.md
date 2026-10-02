# 最终 MIC 有限本地验收矩阵

日期：2026-09-29。本表指定有限本地分支已通过；最终构建/四场亦完成，整体 LOCAL_PASS，所有外部阶段 NOT_RUN。

主专项：[受控原生 Manager 编译下39项](native-mic-final-02.xcresult)。该编译运行生产 Manager 编排，供应商 SpeechEngine 边界受控；不是运行火山SDK二进制。标准 Simulator 全套另见 [all-ios-final-02.xcresult](all-ios-final-02.xcresult)。

| 项目 | 本轮证据/精确覆盖 | 本地结论 |
|---|---|---|
| MIC-01 当前策略 | 真实 Controller/Client/Gate 一次ticket进入聆听；原同断言红保留于前轮 | PASS |
| MIC-02 刷新 | 缺失/过期缓存仅刷新一次；累计budget、迟到policy零POST独立断言 | PASS |
| MIC-03 deny | 当前/刷新后deny零ticket；可见提示与请求原因不串场 | PASS |
| MIC-04 账号/共享等待 | 实际auth coalescer等待者2，取消1后另一成功；authPOST1/ticketPOST3；页面重绑即时取消，底层轮换有界截止另测 | PASS |
| MIC-05 回调隔离 | 真实Manager delegate proxy；取消后晚success/error、换场旧回调、Controller二次排队所有权 | PASS |
| MIC-06 去重/终态 | 重复权限回调1ticket；SDK同步失败原2终态红、现1终态绿，晚超时不重写 | PASS |
| MIC-07 认证预算 | canonical401一次恢复；非canonical不重发；auth中换账号/策略deny不能第二POST；Controller原因和0SDK | PASS |
| MIC-08 未知结果 | 第二POST未知不重发；首响应不能覆盖第二曝光；第一连接丢失1POST、network分类；503不重试 | PASS |
| MIC-09 截止 | policy/runtime/auth/SDK命名入口、实际transport取消回执、迟到结果归属；总预算未扩大 | PASS |
| MIC-10 累计时钟 | Controller→Client→Gate→Manager累计推进，墙钟倒退3600秒不增加单调15秒预算；0StartEngine | PASS |
| MIC-11 Manager实链 | init跨截止、send失败、missingStarted、取消、成功移交、替换场、实际ingress旧close不清新buffer；待启动取消原入口红绿、Stop计数直接检查 | PASS |
| MIC-12 健康路径 | 1ticket/1Start/1greeting，票据header/role精确绑定；阶段入口elapsed与总耗时、单调性直接断言 | PASS |
| MIC-13 错误提示 | 429/解码/503快照原因及UI；网络错误domain/code保留，不虚构HTTP状态 | PASS |
| MIC-14 诊断隔离 | 不可写目录、首错重建、迟到结果不覆写；原Controller静态A盘保护证据复用，活动A另见MIC-19 | PASS，限命名分支 |
| MIC-15 PG票据 | 后端未变化，复用run-03默认API/PG并发200/200仅一张可消费、跨owner403、commit/rollback/单消费 | PASS，复用有据 |
| MIC-16 有界取证 | 复用D5有限ASGI诊断机制；不扩大为已定位历史慢启动或API失活首因 | PASS，限诊断机制 |
| MIC-17 保存音频保护 | 完整标准Simulator套件；实际AudioSessionCoordinator lease在受控Manager失败后释放；外部声学/SDK原生音频未测 | PASS本地 / 外部NOT_RUN |
| MIC-18 四场 | 最后源码下 short-A→logical20→short-B→logical65，独立短场门禁、PG候选→审核→正式记忆→重建回查 | PASS，四份原产物各1/1 |
| MIC-19 活动A/B | A append在途屏障，B deny/timeout/SDK失败及无B对照；A原正文/身份保留、一次end；空B正常empty异步释放，不替A ACK/admit | PASS |

复用来源：[前轮矩阵](../../2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix-closure-04.md)、[PG并发结果](/Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/evidence/mic15-concurrent-identity-result-04.json)。后端进入/结束diff一致见 backend-entry.diff / backend-final.diff。

明确区分：MIC19的HTTP端口故障是受控transport；PG由独立四场提供。Manager实际代码编排被执行，但SpeechEngine边界是shim。逻辑分钟是脚本时标，不是物理等待。历史首因缺证维持未决，不以其作为本地无限扩项理由。
