# DreamJourney Live 持久化授权真机复测报告

日期：2026-09-17\
状态：**核心保存链 DEVICE PASS；冷启动历史状态 UI 仲裁另行记录**

## 1. 安装基线

- 设备：iPhone 14 Pro Max，iOS 27.0，有线连接。
- 安装方式：当前工作树签名 Debug 包原位覆盖安装；未卸载、未清 App 数据。
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`。
- App 版本：`1.0.0 (1)`。
- 真机包可执行文件 SHA-256：`9d29fa691ad6133ba6373f1b64baae6c4cb5af04846b630b2f383b13ff26f0c5`。
- 后端未重新部署；未操作生产候选、正式记忆、历史或 Dead Letter。

## 2. 测试数据与前置条件

- 使用新的合成代号“海棠三十一号”。
- 测试前候选列表真实 GET：45 条，刷新后仍为 45 条；不存在该代号。
- 测试期间未执行候选确认、更正或拒绝。

## 3. 同场 Live 与保存链

| 检查项 | 结果 | 证据摘要 |
|---|---|---|
| 原生 Live 启动 | PASS | 正常进入聆听，有声回答，回答后自动恢复聆听。 |
| 用户表达采集 | PASS | ownerTurnCount=1、persistedOwnerTurnCount=1、queuedTurnCount=0。 |
| 手动结束 | PASS | 页面短暂经过保存状态，5 秒内到“已进入待确认记忆，可在记忆档案查看”，20 秒保持一致。 |
| ACK | PASS | 同一脱敏 trace 下回执解码、绑定检查、accepted 和 checkpoint commit 完成。 |
| candidate admission | PASS | fresh authority 允许；HTTP 201；typed decode、回执绑定、accepted 和 checkpoint commit 完成。 |
| 同场只读状态核实 | PASS | 同 trace 的 attempt 单调递增，状态由 queued/organizing 到 pendingReview。 |
| 卡死/报错 | PASS | 未出现长期 saving、错误或持续转圈。 |

## 4. 候选结果

- 首次刷新前后均为 46 条，比基线增加 1 条。
- “海棠三十一号”仅 1 条，详情正常，无占位符或异常。
- ASR 将测试句中的“真机”识别成“侦缉”，但代号识别正确。该现象单列为语音识别偏差，不影响本次持久化链路结论。

## 5. 冷启动保持性

- 完全划掉 App 后重新启动，未自动开启麦克风，无崩溃或卡住。
- 冷启动后候选真实 GET 仍为 46 条；目标候选仍为 1 条，内容一致，无重复。
- 冷启动读取观察未发现本场新增 start/append/end/ACK/admit 业务写；候选没有增加。

## 6. 独立发现

冷启动回响页显示：`上次对话已保存，尚未确认开始整理，可再次核实`，按钮为“核实整理状态”；但本场已在同进程到达 pendingReview，且候选真实存在。

判断：这是历史未完成工作流与最近已完成场次之间的 UI 状态仲裁问题。它不推翻本次保存链和候选结果，但页面文案对最近场次不准确。按本轮设计边界，本次不修改历史 UI 仲裁。

状态：**FAIL（独立问题，待 Astra 设计）**。

## 7. 结论与未执行项

- 本轮修复目标“生产账号代次与本地 lease 身份域误比较导致真实保存链发前失败”：**DEVICE PASS**。
- 同场 Live → 正文持久化 → end → ACK → admit → 状态读取 → 唯一候选：**DEVICE PASS**。
- 冷启动候选保持及不重复：**DEVICE PASS**。
- 历史状态 UI 仲裁：**FAIL，范围外独立问题**。
- 物理 20 分钟 Live、多轮 TTL、断网/未知写真机恢复：**NOT_RUN**。
- 候选审核写入：**NOT_RUN**。

## 8. 证据位置

- 安装与首进程设备控制台入口：`evidence/device-console.log`。
- 冷启动设备控制台入口：`evidence/cold-start-console.log`。
- 从非暂停式 devicectl console 摘录的脱敏阶段序列：`evidence/sanitized-stage-sequence.log`。
- `device-console.log` 与 `cold-start-console.log` 仅保存 devicectl 启动入口；完整实时控制台未原样落盘，避免保存 SDK 噪声或潜在敏感内容。脱敏摘录只保留白名单阶段、状态、HTTP 状态和请求计数，不记录正文、令牌、密钥或完整响应。
