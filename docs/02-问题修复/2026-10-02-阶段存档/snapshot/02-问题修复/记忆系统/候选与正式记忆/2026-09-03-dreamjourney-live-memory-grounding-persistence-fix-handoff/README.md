# DreamJourney Live 正式记忆与会话沉淀修复交接包

日期：2026-09-03\
状态：待 GPT-5.6 Luna 执行\
适用范围：Live 实时语音、文字回响、正式记忆会话投影、会后待确认记忆

## 1. 文件说明

1. `2026-09-03-DreamJourney-Live正式记忆接入与会话沉淀修复方案.md`
   - 说明现象、证据、根因、目标架构、修复边界和技术决策。
2. `2026-09-03-DreamJourney-Live正式记忆接入与会话沉淀-Luna开发任务.md`
   - Luna 的分阶段编码任务，包含代码落点、测试、部署、停止条件和交付格式。
3. `2026-09-03-DreamJourney-Live正式记忆与会话沉淀验收矩阵.md`
   - 后端、iOS、生产部署和真机业务验收用例。

本包是对既有 Live 架构设计的缺陷修复补充，不替换以下上位设计：

- `../2026-09-03-dreamjourney-live-architecture-review-handoff/2026-09-03-DreamJourney-Live-架构设计定稿.md`
- `../2026-09-03-dreamjourney-live-architecture-review-handoff/2026-09-03-DreamJourney-Live-Luna编码开发指导.md`
- `../2026-09-03-dreamjourney-live-architecture-review-handoff/2026-09-03-DreamJourney-Live-验收测试矩阵.md`

若本包与上位设计冲突，以以下原则为准：

1. 正式记忆仍是唯一事实权威。
2. Live 仍由火山端到端实时语音负责，不恢复逐轮 `/echo/answers -> TTS`。
3. 文字回响仍由 `/echo/answers` 和 DeepSeek 负责，只输出文字。
4. Live 和文字回响逐回合可靠保存，整场结束后最多整理一次。
5. 用户确认前，任何对话内容都不能进入正式记忆。

## 2. 已确认事实

- Live 的连续会话、低延迟和随时打断已经达到用户预期，修复不得破坏。
- 生产正式记忆快照可正常生成，当前测试账户快照包含学校事实。
- Live 未命中学校事实发生在快照生成之后、火山实际采用上下文之前或之中。
- Live 与文字回响不生成待确认记忆的共同根因已经确认：生产 PostgreSQL 会话创建因原始 Python `dict` 未适配 JSONB 而返回 500。
- 文字 `/echo/answers` 同时返回 200，因此“回答成功但整理失败”不是 DeepSeek 余额、Worker 排队或整理耗时导致。

## 3. 给 Luna 的启动提示词

```text
请按照以下交接包执行 DreamJourney Live 正式记忆接入与会话沉淀修复：

/Users/gaominge/Documents/liftora/outputs/2026-09-03-dreamjourney-live-memory-grounding-persistence-fix-handoff/

先完整阅读 README、修复方案、Luna 开发任务和验收矩阵，再读取两个代码仓库的当前状态。严格保留 iOS 工作区中已有且未提交的修改，不得 reset、checkout、stash、覆盖或重写用户变更。

先修复并验证后端 PostgreSQL JSONB 会话创建错误，再增加隐私安全的 Live 上下文诊断，通过真实 SDK 证据判断 system_role 是否生效。不得猜测火山字段，不得恢复 Live 逐轮调用 /echo/answers，不得牺牲当前低延迟、连续会话和随时打断。

每完成一个工作包，按文档规定报告修改文件、行为变化、测试证据、未验证项和下一步。遇到停止条件必须停止并说明证据，不得伪造通过。
```

## 4. 完成定义

只有同时满足以下条件才能声称任务完成：

- 生产后端会话创建不再出现 `cannot adapt type 'dict'`；
- Live 与文字回响的最终用户回合能够持久化；
- 用户主动结束整场后，新增事实最多形成一个待确认审核批次；
- Live 能在不调用 `/echo/answers` 的情况下正确回答正式记忆中的学校事实；
- 未知事实不编造；
- Live 连续对话、随时打断和播放结束后立即聆听没有回归；
- 日志不包含正式记忆正文、完整转写、完整 StartEngine JSON、音频或密钥；
- 后端测试、iOS 测试、编译、生产部署 smoke 和真机验收分别有证据。

编译、安装、能听到声音或文字回答正常，均不能单独代表上述任务完成。
