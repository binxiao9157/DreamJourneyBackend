# DreamJourney FM-POLICY-01、B6 及 B0-B8 真机验收总报告

## 1. 结论

- `FM-POLICY-01_DEVICE_PASS`
- `B6_DEVICE_PASS`
- 候选审核专项 B4-8：`PASS`
- 候选审核专项 B4：`PASS`
- Live 第二组 B0-B6：`PASS`
- Live 第二组 B7：`FAIL`
- Live 第二组 B8：`FAIL`
- B0-B8 整体：`FAIL / NOT READY TO CLOSE`

本报告只记录已实际执行或有既有有效证据的项目。B7、B8 的两个问题已分别形成独立问题单并交给 Astra 分析；本轮不继续重复制造生产候选，也不操作已有候选、正式记忆、审核历史或 Dead Letter。

## 2. 版本与环境

- 日期：2026-09-14
- 设备：iPhone 14 Pro Max，iOS 26.4.1
- App Bundle ID：`com.gaominge.dreamjourney.app`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- App 可执行文件 SHA-256：`f9b809bbc6ed10b5ed8e4481e58acb1ac29373eea0bda1136afd22db633a3d8b`
- 生产 API：`https://www.mmdd10.tech/dreamjourney-api`
- 安装方式：原位覆盖安装；未清 App 数据；未启用 QA 旁路、合成候选或故障注入
- 构建结果：`../evidence/device/device-build.xcresult`

## 3. 两项修复版专项复测

| 项目 | 状态 | 真机证据 |
| --- | --- | --- |
| FM-POLICY-01 正式记忆列表 | PASS | 正式记忆基于 69 条已确认记忆；刷新前后版本一致；无策略拦截、读取失败、登录提示或超过 10 秒等待 |
| FM-POLICY-01 搜索与详情 | PASS | 搜索“晨星”结果正确；详情连续两次打开成功；返回后搜索条件仍为“晨星” |
| FM-POLICY-01 实际策略恢复 | PASS | 人物归纳读取现场出现策略过期；同一读取意图完成有界策略/认证恢复，随后原只读资源请求成功并提交 UI |
| B6 冷启动恢复 | PASS | Live 停止显示“对话已保存，等待整理”后划掉 App；新进程显示“上次对话已进入待确认记忆”，未自动开麦、无错误或卡住 |
| B6 候选与去重 | PASS | 候选总数 42；“北辰七号”恰好 1 条；重复刷新和前后台切换后数量、来源均稳定 |
| B6 新 Live 隔离 | PASS | 新 Live 正确读取正式事实“晨星”，没有采用未审核“北辰七号”；声音、打断、恢复聆听正常；问答没有新增候选 |
| B6 恢复零写重放 | PASS | 冷启动恢复只观察到状态读取，未观察到 `end/ack/admit` 重放，也未自动开启新 Live |

说明：本次只证明修复版本现场行为通过，不据此反推历史失败的唯一根因。

## 4. Live 第二组 B0-B8 状态

这里的 B4 指原《Live 第二组真机问题设计与修复指导》中的“候选审核与正式记忆”步骤，不等同于候选审核专项内部仍称为 B4-8 的会后联合验收编号。

| 步骤 | 状态 | 证据与边界 |
| --- | --- | --- |
| B0 新安装版本 | PASS | 本轮核对安装包、生产配置、设备和登录状态；App 原位安装后可正常启动 |
| B1 四项正式事实 | PASS | 保留 2026-09-11 四项真机证据；本轮再次正确回答“晨星”，有声音 |
| B2 打断及至少 10 轮 | PASS | 保留 2026-09-11 十轮证据；本轮再次验证朗读中打断立即停止并自动恢复聆听 |
| B3 新事实、补充或更正、单批次 | PASS | 保留 2026-09-11 真机证据：同一场新事实和更正只形成一个逻辑整理批次和一条候选 |
| B4 候选审核与正式记忆 | PASS | “陈鑫→晨星”更正预览、二次确认、正式写入、投影、向量、文字及新 Live 回查均已取得 2026-09-14 真机证据 |
| B5 只问旧事实后 noChange | PASS | 本轮及前序真机均未产生新候选；结束状态为“本次没有需要整理的表达” |
| B6 整理中退出或重启恢复 | PASS | 本轮“北辰七号”冷启动恢复、单场候选、去重、新 Live 隔离均通过 |
| B7 未审核档案素材不成为正式事实 | FAIL | 未审核“青岚二号”未被 Live 采用，此子项 PASS；但 Live 中两个纯问题被错误提取为两条候选，B7 整体 FAIL |
| B8 文字会话结束及旧 Archive | FAIL | 文字结束进入“当前无法继续整理，原对话已保留”，状态核实为 `notObserved`；旧 Archive 两次进入均正常且未自动重试，此子项 PASS；B8 整体仍 FAIL |

## 5. B7 独立缺陷

- 问题编号：`B7-QUESTION-AS-CANDIDATE-01`
- Live 前候选总数：43
- Live 后候选总数：45
- 用户仅询问两个问题，没有陈述新事实。
- 新增候选却分别把两个问题改写成“用户问过……”的陈述。
- 两条错误候选未确认、未拒绝、未删除。

独立问题单：

- `2026-09-14-DreamJourney-B7纯问题误生成候选-真机问题记录.md`
- 截图：`../evidence/screenshots/B7-live-questions-became-candidates.jpg`

## 6. B8 独立缺陷

- 问题编号：`B8-TEXT-END-NOT-OBSERVED-01`
- 文字消息和回答正常。
- 结束后从 `saving -> queued -> unavailable`，页面显示“当前无法继续整理，原对话已保留”。
- 只读状态请求真实获得 HTTP 200 并完成解码，但业务结果为 `notObserved`。
- 未观察到恢复阶段重放 `end/ack/admit`。
- 旧 Archive 入口两次进入均正常，无 409、无自动写重试、无卡住。

独立问题单：

- `2026-09-14-DreamJourney-B8文字会话结束后状态notObserved-真机问题记录.md`
- 截图：`../evidence/screenshots/B8-text-session-not-observed.jpg`

`notObserved` 只表示当前客户端核实结果；现有证据不足以断言服务端确定未收到或未提交原命令。

## 7. B4 状态澄清

- 更正预览双重绑定：PASS。
- “陈鑫→晨星”真实更正写入：PASS。
- 正式记忆、投影、向量、文字和新 Live 回查：PASS。
- Live 清单中的 B4：PASS。
- 候选审核专项内部的 B4-8：PASS。本轮“北辰七号”同一场次取得会后状态、真实候选 GET/解码/UI、内容与场次关联、单条生成及无重复联合证据，补齐旧报告缺口。
- 候选审核专项 B4 整体：PASS。连续两条真实审核、更正写入、正式记忆版本、投影、向量、文字和新 Live 回查均已有现场证据。
- 历史三条候选重复性、新查询候选语义及 F3 实际影响继续保持各自 `NOT_RUN`/待验证；这些非本次 B4-8 联合门槛，不伪装为已验证。

相关报告：

- `/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md`

## 8. 证据索引

- 脱敏设备摘要：`../evidence/sanitized-logs/2026-09-14-B7-B8-device-evidence-summary.log`
- B7 问题单：`2026-09-14-DreamJourney-B7纯问题误生成候选-真机问题记录.md`
- B8 问题单：`2026-09-14-DreamJourney-B8文字会话结束后状态notObserved-真机问题记录.md`
- Astra 交接提示词：`../handoff/2026-09-14-Astra-B7-B8两项独立问题分析与修复设计提示词.md`
- B4 更正真机报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md`
- B5/B6 前序真机报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b5-b6-device-test/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B5-B6真机测试报告.md`

所有日志只摘录白名单状态、计数、随机关联标识和 HTTP/解码阶段，不保存完整转写、令牌、密钥、请求头或原始业务标识。

## 9. 停止点与下一步

1. 当前停止新增现场数据，不再重复 B7 问句或 B8 文字结束流程。
2. 等 Astra 分别输出 B7、B8 的局部修复设计。
3. 修复先完成本地红绿回归、模拟器/UIKit、构建和必要的隔离后端验证。
4. 获得部署和安装授权后，使用新的合成信息分别复测 B7 与 B8。
5. B7 必须证明纯问题得到 `noChange` 且候选数量不增加。
6. B8 必须证明文字结束任务可被唯一发现并收敛到合法终态，且随后新 Live 正常；恢复过程业务写重放为零。

在第 5、6 项取得修复版真机证据前，B0-B8 整体不得标记通过。
