# DreamJourney Live 正式记忆与会话沉淀验收矩阵

日期：2026-09-03\
适用对象：后端、iOS、部署与真机验收

## 1. 测试数据原则

- 自动化和 PoC 优先使用隔离测试人物与合成事实。
- 不把真实用户正式记忆、转写正文或音频写入报告和普通日志。
- 测试输出只保留 fact ref、数量、长度、hash、状态、HTTP code 和耗时。
- 生产验收如使用真实账户，只在设备上核对答案，不在文档中抄录完整事实。

隔离人物最小事实集：

```text
F01：本科毕业于 A 大学计算机专业，2016 年毕业。
F02：硕士毕业于 B 大学软件工程专业。
F03：职业是产品经理。
F04：姐姐叫 C。
F05：没有记录最喜欢的球队。
```

## 2. P0 后端 JSONB 回归

| ID | 场景 | 通过标准 | 证据 |
| --- | --- | --- | --- |
| DB-01 | productSessionId 有值时创建 session | HTTP 201/200；无 dict adaptation 错误 | 测试名、状态码 |
| DB-02 | productSessionId 为空时创建 session | HTTP 201/200；metadata 为 `{}` object | 测试名、JSON type |
| DB-03 | thread metadata | productSessionId 正确读取 | hash/布尔结果 |
| DB-04 | session metadata | 与 thread 绑定一致 | hash/布尔结果 |
| DB-05 | 相同 command 重放 | 不新增 thread/session，返回 deduplicated | 记录数量 |
| DB-06 | 中途失败 | 整个事务回滚，无半创建记录 | 记录数量 |
| DB-07 | 后续 owner message | 顺序号为 1，JSONB content 正常 | receipt 摘要 |
| DB-08 | 后续 assistant message | 角色正确，不能成为 Owner 事实 | receipt 摘要 |
| DB-09 | end/finalize | exactly once | end receipt hash |
| DB-10 | 生产日志 | 观察窗口内 `cannot adapt type 'dict'` 为 0 | value-free 日志统计 |

## 3. 会话整理与待确认记忆

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| MEM-01 | Live 三轮后主动关闭 | 一个 Source，最多一个审核批次 |
| MEM-02 | Live 用户只提问旧事实 | 允许 `noCandidates`，不复制正式记忆 |
| MEM-03 | Live 用户提供一个新事实 | 最终为 `reviewReady`，待确认可见 |
| MEM-04 | Live Assistant 扩写内容 | 不能单独形成 Candidate |
| MEM-05 | Live partial ASR | 不写消息、不形成 Candidate |
| MEM-06 | Live final ASR 重复回调 | 同一 turn 只保存一次 |
| MEM-07 | Live 用户打断 Assistant | 不 finalize，产品会话保持 active |
| MEM-08 | Live 主动关闭重复回调 | finalize exactly once |
| MEM-09 | Live 一分钟无输入 | 只在 listening 累计，自动 finalize 一次 |
| MEM-10 | typed 提供新事实并结束 | 一个 Source，最多一个审核批次 |
| MEM-11 | typed 只查询旧事实 | 不生成重复 Candidate |
| MEM-12 | typed 尚未结束 | UI 不显示整理失败 |
| MEM-13 | extraction 处理中 | UI 显示“正在整理”，不显示已完成 |
| MEM-14 | extraction 无候选 | UI 明确“没有需要确认的新记忆” |
| MEM-15 | extraction 失败 | UI 显示可重试/未完成，不伪造成功 |
| MEM-16 | 用户确认 Candidate | 更新唯一正式记忆和新 checkpoint |
| MEM-17 | 用户丢弃 Candidate | 正式记忆和 checkpoint 不变 |

## 4. Live 正式记忆上下文

| ID | 场景 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| CTX-01 | token 快照 | 开启 Live | schema、factCount、checkpoint、contextHash 完整 |
| CTX-02 | 直接学校问法 | “我本科在哪所大学读的？” | 命中 F01 全部关键实体 |
| CTX-03 | 口语学校问法 | “我读大学是哪儿来着？” | 命中 F01，不猜测 |
| CTX-04 | 连续追问 | 先问本科，再问“那研究生呢？” | 第二问命中 F02，保持同一会话 |
| CTX-05 | 职业 | 询问职业 | 命中 F03，不增加公司名 |
| CTX-06 | 家庭关系 | 询问姐姐 | 命中 F04，不错称关系 |
| CTX-07 | 未知事实 | 询问最喜欢球队 | 明确不知道，不编造 |
| CTX-08 | Candidate 隔离 | 创建但不确认新学校 Candidate | 当前 Live 不读取 |
| CTX-09 | 撤销隔离 | 撤销一个已确认事实后新开 Live | 新会话不读取 |
| CTX-10 | checkpoint 固定 | 会话中更新正式记忆 | 当前会话不热切换 |
| CTX-11 | 新 checkpoint | 关闭并重新打开 Live | 读取更新后的正式记忆 |
| CTX-12 | 模型路径 | 完成上述问答 | Live `/echo/answers` 调用数为 0 |
| CTX-13 | typed 对照 | 用文字问同一事实 | typed `/echo/answers` 正常返回 |
| CTX-14 | 事实一致性 | 比较 Live 和 typed | 措辞可不同，事实元组一致 |

事实元组按以下字段比较：

```text
(subject, relation, object, time, place, certainty)
```

## 5. 上下文容量 PoC

| ID | 输入规模 | 记录指标 | 通过标准 |
| --- | --- | --- | --- |
| CAP-01 | 500 中文字符 | 事实命中率、首音、prompt bytes | 全部黄金事实命中 |
| CAP-02 | 2K 中文字符 | 同上 | 全部黄金事实命中 |
| CAP-03 | 5K 中文字符 | 同上 | 全部黄金事实命中 |
| CAP-04 | 10K 中文字符 | 同上 | 输出真实结果，不预设通过 |
| CAP-05 | 15K 中文字符 | 同上 | 输出真实结果，不预设通过 |
| CAP-06 | 超安全预算 | 启动结果 | 明确失败，不静默截断 |

根据结果确定供应商安全预算。后端的 32K 字符上限不能代替供应商容量证据。

## 6. Live 体验回归

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| LIVE-01 | 连续 20 轮 | 0 次被动结束、0 次卡死 |
| LIVE-02 | Assistant 说话中打断 10 次 | 10 次都停止当前输出并接收新话 |
| LIVE-03 | 回答结束后立即说话 | 不丢首句，无人工恢复窗口 |
| LIVE-04 | 用户自然停顿 | 不过早抢话，最终转写完整 |
| LIVE-05 | 长回答 | 不因回答长度结束产品会话 |
| LIVE-06 | 后端持久化慢 | 对话和打断不受阻塞 |
| LIVE-07 | session start 暂时失败 | Live 可继续；状态准确；有界重试 |
| LIVE-08 | 用户主动关闭 | 立即结束音频，只 finalize 一次 |
| LIVE-09 | 来电/耳机/后台 | 恢复或明确结束，不进入假聆听 |

性能门槛：

| 指标 | 目标 |
| --- | --- |
| 点击 Live 到可说话 | P50 <= 1.2s，P95 <= 2.0s |
| 用户停句到首个 Assistant 音频 | P50 <= 800ms，P95 <= 1.5s |
| 用户插话到 Assistant 停声 | P50 <= 150ms，P95 <= 300ms |
| 回答结束到重新可聆听 | P95 <= 300ms |
| 增加持久化后的相对性能损耗 | P95 不超过原基线 20% |

## 7. 故障和重试

| ID | 故障 | 通过标准 |
| --- | --- | --- |
| ERR-01 | session start 500 | 标记 `sessionStartFailed`，不显示整理成功 |
| ERR-02 | turn persist 超时 | 不阻塞 Live；使用同一幂等键重试 |
| ERR-03 | finalize 失败 | 可重试；不创建第二审核批次 |
| ERR-04 | Candidate Worker 慢 | 正确保持 organizing，超时后明确提示 |
| ERR-05 | Candidate Worker 失败 | 区分 extractionFailed 与 noCandidates |
| ERR-06 | token 快照不可用 | 不启动无事实上下文 Live，文字回响仍可用 |
| ERR-07 | prompt 超安全预算 | 明确失败，不截断、不猜测 |
| ERR-08 | Provider 重连 | productSessionId/checkpoint 不变，权限重验 |
| ERR-09 | App 临时退出 | 已完成 final turn 可从短期 Outbox 恢复，若本轮未实现须明确记录残余风险 |

## 8. 隐私与安全

| ID | 检查 | 通过标准 |
| --- | --- | --- |
| SEC-01 | iOS 普通日志 | 无正式记忆 statement |
| SEC-02 | StartEngine 日志 | 无完整 JSON 或 prompt |
| SEC-03 | 转写日志 | 无完整用户/Assistant 正文 |
| SEC-04 | 凭据日志 | 无 token、AppKey、resource secret |
| SEC-05 | 后端日志 | 只含状态、数量、hash、错误码和耗时 |
| SEC-06 | QA 报告 | 不抄录真实账户完整事实 |
| SEC-07 | 账号切换 | 旧账户队列和快照不可进入新账户会话 |

用唯一 canary 构造测试输入，并扫描测试日志；canary 出现即失败。

## 9. 部署验收

| ID | 检查 | 通过标准 |
| --- | --- | --- |
| DEP-01 | 部署前 preflight | 全部通过，main 工作区干净 |
| DEP-02 | 备份 | 迁移前/后恢复点符合 Runbook |
| DEP-03 | migration | dry-run/verify 通过；本修复不应新增 schema |
| DEP-04 | API | `/live` 与 `/ready` 正常 |
| DEP-05 | Worker | 已启用 Worker running，RestartCount=0 |
| DEP-06 | 隔离 Postgres smoke | start/append/end/ack/admit/status 通过 |
| DEP-07 | 日志观察 | dict adaptation 500 为 0 |
| DEP-08 | 版本 | 生产 commit 等于批准目标 commit |

## 10. 真机验收脚本

### 场景 A：正式记忆

1. 打开 Live。
2. 问本科院校。
3. 用口语化方式重复询问。
4. 追问研究生院校。
5. 问一个正式记忆中不存在的事实。
6. 在回答中途插话。
7. 回答自然结束后立即开口。
8. 主动关闭 Live。

期望：事实正确、未知不编造、可打断、连续聆听、没有逐轮 `/echo/answers`。

### 场景 B：Live 新事实沉淀

1. 新开 Live。
2. 用户讲述一段此前未记录的真实经历。
3. 与 Assistant 连续交流至少两轮。
4. 主动关闭 Live。
5. 查看“待确认记忆”。

期望：整场最多一个审核批次；Candidate 只来自用户表达；Assistant 润色不成为事实。

### 场景 C：文字新事实沉淀

1. 打开文字回响。
2. 输入一个新话题和一段新事实。
3. 连续交流至少两轮。
4. 选择“结束并整理”。
5. 查看“待确认记忆”。

期望：回答由 DeepSeek 生成且不朗读；整场最多一个审核批次。

### 场景 D：审核闭环

1. 更正并确认场景 B 的 Candidate。
2. 丢弃场景 C 的 Candidate。
3. 查看正式记忆。
4. 新开 Live 询问已确认事实和已丢弃事实。

期望：只读取确认后的新 checkpoint；丢弃内容不可回答。

## 11. 最终 GO/NO-GO

### GO

以下条件全部满足：

- DB-01 至 DB-10 全通过；
- MEM-01 至 MEM-17 全通过；
- CTX-01 至 CTX-14 全通过；
- LIVE-01 至 LIVE-08 全通过；
- SEC-01 至 SEC-07 全通过；
- DEP-01 至 DEP-08 全通过；
- 真机场景 A-D 完成；
- 没有 P0/P1 未解释失败。

### NO-GO

任一情况即停止发布：

- Live 仍不能命中学校事实；
- Live 需要逐轮 `/echo/answers` 才能回答；
- session start 仍出现 500；
- UI 显示成功但没有审核批次；
- 一次会话生成多个重复审核批次；
- 打断、连续聆听或首音延迟明显回归；
- 日志泄露正文、音频或密钥；
- 用户确认前内容进入正式记忆。
