# Run-02 R01–R03 独立组合反例

2026-09-20。本目录的 `semantic-regression-probes.py` 使用当前真实 `ModelAssistedOwnerTruthLiveConversationExtractor`、`DeepSeekLiveMemoryOrganizationProxy`、support/schema 解析及 InMemory 持久接口。所有 organization、support、relation HTTP 都经 `httpx.MockTransport`，域名固定 `controlled.invalid`，key 为合成值。没有真实 Provider 请求、生产/历史数据读取或修改，也没有产品源码修改。

受控模型按实际请求片段重新计算事实与草稿覆盖，不是固定返回原始前 8 条。support 依当前请求的证据和草稿逐项判断；relation 使用真实 adapter 的完整合法 JSON。

## 复现命令

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/semantic-regression-probes.py
```

保留 stdout：`semantic-regression-probes.log`。源码指纹：`semantic-source-fingerprints.sha256`。脚本断言故障按预期暴露；进程 exit 0 **不代表产品验收通过**。

## 结果

| case | 现场输入与结果 |
|---|---|
| control-dense-12-adaptive | 原 88 字单 turn 12 事实：8 条上限触发细分，6+6 全部通过，12 个原子和 12 条候选，readyToPublish。证实 R01 原始反例已修。 |
| cross-batch-exact-duplicate-loses-proof | 前 8 个 user turn 各陈述不同事实，第 9 个重复第一条。两片 organization/support 全部通过，9 个原子；完全相同命题去重后 proof 丢失而未重验，manifest 报 `publication item lacks current atom support binding`。 |
| dense-valid-supplement-pages-full-turn | 同 turn 12 事实完整细分成功。关系将“我在杭州工作”与“我在杭州的图书馆工作”合法合并；随后只取 4 条草稿，证据却重新使用含 12 事实的完整原 turn。受控 support 正确报遗漏，整场 `supportValidate.factOmitted`。 |
| same-turn-facts-and-queries-refinement | 同 turn 9 个事实后接 9 个纯问题。事实细分完整，纯问题正确过滤；合并覆盖摘要得到 required=[1] 且 excluded=[1]，manifest 报 `fact-bearing Source turn cannot be excluded`。 |

## 可定位的根因及局部修复要求

1. `owner_truth_candidate_extraction_worker.py:1144` 在关系裁决前做 exact 去重，`1916` 经 `2548` 合并 lineage，`2580` 删除 proof。`1151` 却只依据关系阶段 relations_changed 决定重验。应让 exact 合并也参加“证据/正文已变需重验”的判定，保持跨片重复只有一个候选且证明有效；不能关闭 manifest 支持证明检查。
2. 同文件 `2105–2119` 以 4 条候选分页，但重验从完整原 turns 按 index 取证，不按 `_sourceEvidenceRanges`/原子责任范围，继续复用要求输入证据无遗漏的全场 support 合同。应区分“本页命题的支持/纠正合法性”与“全场原子无遗漏”，按可界定证据范围验证前者并由台账验证后者；不能把每页之外合法事实误判遗漏，也不能放松真实漏事实/虚构命题校验。
3. 同文件 `1725–1749` 细分结果用 turnIndex 对 required/excluded 分别取并集，丢失同 turn 不同语义片的责任范围；`owner_truth_live_long_memory.py:1565` 正确拒绝这个自相矛盾摘要。应按证据范围记录事实/问题处置，同 turn 有事实不得被整体 excluded，仍须验证 B7 纯问题过滤。

这些是原 R01–R03 修复后的正常组合失败；不否定单独 88 字细分、伪造购房拒绝和遗漏 atom 拒绝等定向绿测。未做真实 Provider、PG 或设备验证；完整 run-02 总结由根任务另行审查。
