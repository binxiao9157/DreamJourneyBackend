# Live 长场语义与覆盖本地反例

日期：2026-09-20。目的：只读审查 Sol 本地交付的默认模型合同、合并后支持校验和最终覆盖门禁。

## 复现

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/semantic-probes.py
```

实际 stdout 已保留在 `semantic-probes.log`；涉及产品源码的 SHA-256 保存在 `source-fingerprints.sha256`。

## 三项反例与结果

1. `dense-short-one-turn`：88 字的单条用户发言包含 12 个独立事实。实际 DeepSeek adapter 收到结构合法的前 8 条组织结果，以及正确报告遗漏的 support 结果。新 pipeline 仅发出组织和 support 两个请求，直接抛出 `candidateExtraction.live.supportValidate.factOmitted`，未细分处理剩余事实。
2. `unsupported-post-merge`：用户原话只有周六杭州看展、与女儿同行；组织结果及第一次独立 support 均正确。受控 relation 补充结果采用合法 schema，却增加了用户未说的购房事实。提取最终 succeeded，CandidateProposal 包含新增虚构内容；没有 relation 改写后的独立 support 请求。本例是故障注入，证明变更内容后的验证缺口，不证明真实供应商本次实际生成过该内容。
3. 最终覆盖：`manifest-drops-eleven-known-atoms` 是构建函数直接检查；`actual-repository-twelve-atoms-one-item-frozen` 进一步使用真实 InMemoryLiveLongMemoryRepository，正常创建 run、绑定 Source、保存 completed unit 和 12 个合法 active atoms，只给最终 manifest 1 条 memory，仍冻结为 `readyToPublish`。后者是本项主要证据：已有 11 个原子事实未映射至最终表达或替代终态，turn 级覆盖仍放行。

## 执行边界

- 第一、二项均使用真实 `DeepSeekLiveMemoryOrganizationProxy` 及其请求、HTTP envelope、JSON/schema 解析路径；所有 HTTP 请求通过显式注入的 `httpx.MockTransport` 在进程内应答。
- URL 为 `https://controlled.invalid/chat/completions`，key 为脚本内的合成测试字符串；未读取现有 Provider 密钥，未调用真实 DeepSeek/火山或其他外部网络。
- 第三项仅使用内存仓储和 manifest 构建/冻结代码，无数据库、服务器、生产任务或候选写入。
- 所有对话是脚本内合成内容；未读取用户原对话、重试业务写、操作手机、部署、commit 或 push。
- 产品源码未修改。新增内容仅为这份审查目录内的探针、stdout、指纹与说明。
- 这是本地确定性反例，不是 Provider 质量统计，也不是历史私人现场响应重放。
