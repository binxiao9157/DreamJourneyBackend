# 发给 Astra 的分析与修复设计提示词

请完整阅读以下 DreamJourney B4 真机失败记录及证据，独立核对当前 iOS、本地后端和生产 API 版本，分析根因并输出可执行的局部修复设计文档：

`/Users/gaominge/Documents/liftora/outputs/2026-09-13-dreamjourney-b4-device-retest/2026-09-13-DreamJourney-B4-陈鑫更正为晨星-真机失败记录.md`

原始脱敏访问证据：

`/Users/gaominge/Documents/liftora/outputs/2026-09-13-dreamjourney-b4-device-retest/evidence/changeset-preview-access.log`

必须以下列已证实事实为起点，不要把待验证假设直接当成根因：

1. 生产候选列表真实 GET 成功，客户端 V5 解码并显示 40 条。
2. “陈鑫”候选的修订号、正文和记忆线索能正常展示。
3. 用户在更正编辑页仅将“陈鑫”改为“晨星”，编辑内容正确，未修改结构化线索。
4. 点击“提交更正并确认”时，生产 `changeset-preview` POST 已返回 HTTP 200。
5. 客户端仍显示“更正方案未生成，未写入任何正式记忆”，真正的“核对更正方案”二次确认页没有打开。
6. 本轮没有执行最终审核写入，不得建议用普通接受“陈鑫”来绕过问题。

请先查明 HTTP 200 后的精确失败层：

- `proposedChangeSet` 字段是否存在及 V5 Proposal 是否解码成功。
- proposal.candidateVersion 与列表 Candidate 版本是否一致。
- proposal.candidateContentHash 与列表 Candidate hash 是否一致。
- proposal.baseMemoryRevision 与客户端当前 memoryRevision=69 是否一致。
- proposal.reviewability 是否为 reviewable，若不是，缺失的结构是什么。
- 预览返回前后 operationGeneration、账号租约和 Candidate 上下文是否仍为同一所有者。
- 生产镜像 `dreamjourney-live-b1:20260911-0200` 与当前 iOS 未提交代码是否存在合同版本差异。

设计文档必须包含：

1. 已证实根因、已排除项和仍需验证项，三者分开。
2. 要求 -> 代码 -> 测试 -> 证据 -> 状态清单。
3. 局部修改文件与函数，禁止无关重构。
4. 先红后绿的自动化反例，包括 BackendClient、UseCase、FeatureGate/account lease、真实 UIKit 二次预览流程。
5. 为解码、Binding、revision、reviewability 和旧回调丢弃增加白名单脱敏 reason 证据，不记录正文、Prompt、token、密钥、完整响应或原始 hash。
6. 若需后端改动，明确与当前未部署代码的关系、发布顺序、兼容窗口、迁移/数据影响和回退方案。
7. 不得放宽账号权限、Candidate/Proposal/Binding、CAS、hash、revision 或 reviewability 校验。
8. 不修改 Live 原生音频、持续聆听、打断和正式记忆快照绑定；不恢复 ASR -> DeepSeek -> TTS 串行链路。
9. 不替用户审核、不修改生产正式记忆、不清理历史、不重放 Dead Letter。
10. 明确两个 decision-result 接口尚未部署，未部署前不得宣称未决写恢复已可真机验收。

本轮只要求 Astra 输出分析和修复设计文档，不要自动部署、安装 iPhone、处理生产候选或执行 commit/push。B4-8、更正写入、投影、向量以及文字和新 Live 回查必须继续保持 FAIL/NOT_RUN。
