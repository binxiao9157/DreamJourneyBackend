# DreamJourney B4 候选审核页占位符问题记录

日期：2026-09-11\
用途：交给 Astra 独立复核根因并输出修复方案\
当前结论：B3 已通过；B4 因审核预览合同展示异常暂停，尚未确认候选、尚未写入正式记忆。

## 1. 测试背景

本轮使用隔离合成事实验证 Live 会话结束后的记忆沉淀链路：

1. 用户在同一场 Live 中说：“这是我明确同意记录的一条合成测试事实：我的测试阅读计划代号是蓝桥。”
2. 随后更正：“更正一下，我的测试阅读计划代号是青禾，以青禾为准，蓝桥作废。”
3. 用户主动停止 Live。
4. 系统完成会后整理并发送候选通知。
5. 用户进入“记忆档案 → 待确认记忆 → 候选记忆”查看候选。

边界要求：正式记忆必须由用户审核后才能写入；本轮不允许系统替用户确认，也不允许通过修改已有正式事实绕过问题。

## 2. 已确认正常的行为

### 2.1 会后整理和候选生成正常

- iOS 已持久化同一场 Live 的两个用户轮次并触发一次会后整理。
- 后端 Candidate Extraction 第 1 次执行完成。
- 同一会话只生成 1 个 review batch。
- `candidateCount=1`，候选状态为 `pendingReview`。
- 没有分别生成“蓝桥”和“青禾”两条重复候选。

### 2.2 用户看到的候选正文正确

候选正文为：

> 用户的测试阅读计划代号是青禾，以青禾为准，蓝桥作废。

这与用户在同一会话中的最终更正一致，没有把已经作废的“蓝桥”作为最终事实。

### 2.3 通知正常

- 用户已收到整理完成通知。
- 从通知/记忆档案能够找到该候选。
- 页面中只看到一条对应候选，没有重复候选。

因此，本问题不是“整理太慢”“没有生成候选”或“重复抽取”。

## 3. 异常现象

候选详情页的“本次确认将如何更改正式记忆”区域直接显示了未解析的内部占位符：

- `基于正式记忆第 (proposal.baseMemoryRevision) 次修订快照`
- `确认后：(afterText)`

截图中其余关键信息：

- 操作类型：新增一条正式记忆。
- 目标：新建一条独立正式记忆。
- 证据：`0 → 1 条`。
- 候选正文、候选属性和来源依据均能正常显示。

截图证据：

`/Users/gaominge/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/terry_forever_23bf/temp/RWTemp/2026-09/afd838444d1159a5ad86eaae48764d17/9f849a1e9fd4d8688831a676c0f44a86.jpg`

## 4. 影响与风险

### 4.1 当前直接影响

用户无法在确认前看清：

- 候选基于哪一次正式记忆修订快照；
- 确认后究竟会写入什么事实；
- 如果是更正操作，确认前后的事实差异是什么。

### 4.2 合同风险

该页面承担正式记忆写入前的知情确认职责。即使后端 proposal 数据正确，只要 UI 没有展示真实值，就不能认为用户完成了有效审核。

### 4.3 当前处置

- 已明确要求用户不要点击“确认并纳入正式记忆”。
- B4 保持 `BLOCKED/NOT_RUN`，不得标记 PASS。
- 候选仍停留在待确认状态。
- 没有替用户审核，没有修改现有正式记忆。

## 5. 初步代码定位

问题集中在 iOS 文件：

`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`

原始实现把 Swift 插值表达式误写成普通括号文本，涉及：

- 正式记忆基础修订号；
- 目标正式记忆版本；
- 确认前文本；
- 确认后文本；
- 变化字段路径；
- 关联候选预览数量。

同一文件中的其他预览函数已经使用正确的 `\(...)` 插值方式，因此当前现象更像局部 UI 字符串实现缺陷，不足以据此认定后端 proposal 合同错误。

## 6. 尚需 Astra 独立核对

1. 后端返回的 `baseMemoryRevision`、`operations`、`factDiff.before/after` 是否完整且与候选绑定一致。
2. iOS 解码后的 `OwnerTruthCandidateChangeSetProposal` 是否保留真实值，还是仅正文卡片正常、change set 数据缺失。
3. 审核页是否应对 `add`、`correct`、`merge`、`supersede` 等每类操作分别建立快照测试。
4. 当 proposal 版本过期、正式记忆 revision 已变化或 after 为空时，是否继续 fail closed，禁止确认。
5. 关联候选批量预览是否存在同类占位符问题。
6. Accessibility/UI 自动化是否能直接断言页面中不得出现 `proposal.`、`afterText`、`beforeText`、`targetVersion` 等内部变量名。
7. 修复后是否需要重新拉取候选，确保展示的 proposal 与确认时提交的 proposal hash/revision 是同一个版本。

## 7. 用户提出本记录时的本地改动状态

在用户要求“先记录并交给 Astra 分析”到达前，已产生一组尚未验证的本地初步修改：

- 将上述 6 处普通括号文本改为 Swift 插值或格式化函数。
- 新增一个单元测试，检查修订号、目标版本、确认前后文本和变化字段能够渲染真实值，且不出现内部占位符名。

涉及文件：

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`

这些改动当前状态为：

- 未运行自动化测试；
- 未编译；
- 未安装真机；
- 未部署后端；
- 未提交或推送 Git；
- 不能作为问题已解决的证据。

Astra 应独立审阅这些改动，决定保留、调整或替换；不得仅凭代码看起来正确就判定 B4 通过。

## 8. 建议验收证据

修复后至少需要以下证据，任何未执行项不得标记 PASS：

1. 单元测试：所有 change set 操作均展示真实值，不出现内部变量名或占位符。
2. UI/模拟器测试：候选详情页显示实际 base revision、before、after、target version 和 changed fields。
3. 真机复测：重新打开同一待确认候选，确认展示内容与候选正文一致。
4. 用户主动确认后，后端生成新的 MemoryVersion，revision 按合同递增。
5. 投影和向量更新完成，状态不是 pending/rebuilding/blocked。
6. 文字和 Live 分别询问“测试阅读计划代号是什么”，都回答“青禾”，不得回答“蓝桥”。
7. 审核历史可追溯到原 Source、Candidate、proposal 和用户确认回执。

## 9. 现有证据索引

- B3 真机日志：`/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/b3-device-console.log`
- App 重启/恢复日志：`/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/b3-resume-device-console.log`
- 当前交付报告：`/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/2026-09-11-DreamJourney-Live-B1修复交付报告.md`
- 当前执行清单：`/Users/gaominge/Documents/liftora/outputs/2026-09-11-dreamjourney-live-b1-session-snapshot-fix/2026-09-11-Live-B1修复执行清单.md`
- 用户截图：`/Users/gaominge/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/terry_forever_23bf/temp/RWTemp/2026-09/afd838444d1159a5ad86eaae48764d17/9f849a1e9fd4d8688831a676c0f44a86.jpg`

## 10. 当前验收状态

| 项目 | 状态 | 说明 |
|---|---|---|
| B3 会后整理并形成候选 | PASS | 1 个批次、1 条候选、最终更正正确、无重复 |
| B4 候选审核预览 | FAIL | 页面暴露内部占位符，无法完成有效知情确认 |
| B4 正式记忆写入 | NOT_RUN | 用户尚未点击确认 |
| B4 投影与向量更新 | NOT_RUN | 尚无新正式记忆版本 |
| B4 文字/Live 回查 | NOT_RUN | 等正式记忆写入并投影完成后执行 |
| B5-B8 | NOT_RUN | 尚未开始 |
