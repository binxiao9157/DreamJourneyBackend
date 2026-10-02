继续【开发】任务中的 LIVE-L20-AUTH-01。用户明确要求：现在先完成本地修复与测试，最后才进行真机验收。请实际继续修改，不停在再次调查、状态回复或手机连接确认。

先完整阅读当前工作树对应的续修方案：
/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md

主设计已经同步为 v1.2：
/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/记忆系统/2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md

Astra 已读取真实 xcresult：live-l20-r3-integrated.xcresult 的当前组合用例为 FAIL；pendingTurns=6，append 实际 15 次而非 21，end/ACK/admit 均为 0。不能说本地已完成或可以开始安装真机。R1/R2 有初步进展，但 GET 路由测试不能代替真实 GET 401/refresh/header 组合证明。

保留现有 R1/R2 及前序未提交修复，按 C1–C7 继续完成以下具体工作：

1. 先处理 prepareVerifiedAuthenticationRetry 保留 inFlightTurn，但 advanceNaturalInputPipeline 又要求 inFlightTurn=nil 的推进阻断。用专门、单一 owner 的原 append 恢复入口；不要无条件清空保护或让所有失败用例变 ready。先用小场景红绿证明恢复和排空，再扩展 70/48 场景。
2. 修复 nil UseCase→普通 ensure/start 的恢复方式。中途恢复只能绑定原 productSession/thread/session/epoch/版本，current 为空或不匹配时 start=0，不得新建场次绕过失败。
3. 区分一次性重试许可和重试已曝光。authenticationRetryExposed 不能作为普通 FIFO 的自动发送条件；它必须进入 unknown/截止时间/冷启动只读保护。崩溃、重建和重复唤醒不得产生第三次 POST。
4. 补齐原完整 append、认证拒绝证据、作用域及 attempt 预算的最小持久化。仅 commandID/messageID 相同不够，session/expected versions/payload 也必须冻结。旧缺证据记录仍保持安全，不清历史、不补造事实。
5. 在本地真实 middleware 合同测试证明认证拒绝发生在业务 handler 前，再形成绑定原请求的可恢复类型；不要把名字 Verified 或普通错误字符串当完整证据。补回调时钟前进的认证预检反例，用当前时刻重新检查 successor；至少一项用生产 refresh/CAS 与隔离 auth store。
6. 恢复既有逻辑 20 分钟/断网未知结果场景，新增独立命名的认证 401 组合测试，不用改变旧场景取代已通过覆盖。补正确策略夹具下真正针对认证缺口的修前红测。两个 Live GET 都需实际 HTTP 401→刷新→fresh headers 证明。
7. 完成 70/48 完整链、主设计全部适用本地测试、短场持久化保持性、OwnerTruth、B8/D1/音频和共享 auth/HTTP 受影响回归及编译。交付报告、实际 xcresult、逐文件影响与测试映射、源码/构建指纹、最终真机清单。

执行约定：当前仅 A 本地修改、B 本地验证；手机截图或连接状态是补充，简短回应后继续原开发目标，不再转成手机操作引导。不要在加入枚举、单项测试通过或编译通过后结束。全部适用本地必测项通过才交付 LOCAL_PASS / DEVICE_PENDING；若失败继续修复，只有真实外部阻断才明确列出并继续其余工作。

本轮不操作手机、不读取/修改生产业务数据、不部署、不清理历史、不 commit/push；不修改历史 UI 仲裁、B7、音频产品策略和覆盖摘要规则。之前已通过的短场保存链必须保住；受影响模块必须写入本地回归和最终真机验收。现在从现有本地失败继续实现，不必重新询问是否开始。
