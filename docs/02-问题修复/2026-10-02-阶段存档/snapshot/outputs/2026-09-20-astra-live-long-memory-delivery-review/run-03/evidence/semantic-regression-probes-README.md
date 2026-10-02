# Run-03 R01-B / R02-B / R03-B 独立只读复核

日期：2026-09-20。使用当前真实 `ModelAssistedOwnerTruthLiveConversationExtractor`、`DeepSeekLiveMemoryOrganizationProxy`、schema/support/manifest 校验和 InMemory Run repository。organization/support/relation 全部使用 `httpx.MockTransport`；固定合成 key、`controlled.invalid` 域名和合成对话。未请求真实 Provider，未访问生产/历史，未修改产品源码或启动真机。

## 复现

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/semantic-regression-probes.py
```

脚本基于 run-02 同一组受控输入，更新原三条故障的期望为成功，并加入候选条数断言；受控模型按实际请求片段自适应生成，未使用固定原始前 8 条的无效 fake。stdout 在 `semantic-regression-probes.log`。第 5 条是有意暴露缺陷的失败断言；脚本退出 0 不表示 5 个产品场景都通过。

## 结论

| 项目 | 独立结果 |
|---|---|
| R01 原密集对照 | PASS：88 字单 turn 12 事实经 6+6 细分，12 atoms、12 候选，readyToPublish。 |
| R01-B 混合事实/问题 | PASS：9 事实 + 9 问题分别保留 range disposition；全 turn required=[1]、excluded=[]，9 候选。问题 range 仍为 excluded。 |
| R03-B 跨批精确重复 | PASS：前 8 轮 8 事实，第 9 轮重复第一条；9 atoms 归并为 8 候选，重新 support 后 readyToPublish。 |
| R02-B 原合法补充场景 | PASS：12 atoms 完整提取；合法补充后按 evidence range 分成 4/4/3 草稿验证，最终 11 候选。 |
| R02-B 正常释义对照 | FAIL：相同输入仅把“我的研究主题是古琴。”整理为“我的研究方向为古琴。”，全部提取/support 仍通过；合法补充后的首个 4 草稿页重新携带 6 个事实，报 `candidateExtraction.live.supportValidate.factOmitted`。 |

因此 R01-B / R03-B 原缺口可按本地证据关闭，R02-B 的逐字复制版本修复有效，但不能整体关闭。

## R02-B 仍失败的直接原因

- `owner_truth_candidate_extraction_worker.py:1472–1475` 通过 `text.find(primary_value)` 寻找记忆正文。只有草稿逐字出现在原文时才缩小范围；正常整理改写找不到时，`evidence_text` 退回整个提取 fragment。
- 同文件 `2209–2214` 重验仍每页固定 4 个候选，通过 `_owned_evidence_turns` 取 ranges。
- 同文件 `2346–2359` 把这些 ranges 的完整文字重新拼入该页。上述正常释义给一个原子绑定了 6 事实范围，因此页外事实进入本页全覆盖 support，触发旧 `factOmitted`。
- 本反例不是模型漏事实、网络错误、固定返回 8 条，也不是异常/虚构模型输出：12 atoms 全部存在，组织后的同义改写已通过初次 support。

局部修复仍需把原子的准确证据范围与记忆的可改写正文分开：范围由可靠的 source quote/offset 绑定，或把当前页责任原子与额外上下文明确分开验证，不能依赖草稿与原文完全相同。全场 manifest 的原子无遗漏检查和正文真实性校验均需保留；不要强制模型逐字复制，不要直接忽略全部 omitted 结果。

## 保持性检查

另独立运行以下现有定向测试，2/2 PASS，stdout 在 `semantic-negative-controls.log`：

```sh
.venv/bin/python -m unittest \
  tests.test_owner_truth_live_long_memory_pipeline.OwnerTruthLiveLongMemoryPipelineRedTests.test_lm_r02_relation_text_is_revalidated_against_original_user_evidence \
  tests.test_owner_truth_live_long_memory_pipeline.OwnerTruthLiveLongMemoryPipelineRedTests.test_lm_r03_manifest_requires_a_disposition_for_every_recognized_atom -v
```

它们证明伪造购房命题仍被拒绝、已识别 atom 缺少最终处置仍被拒绝。源码指纹保存在 `semantic-source-fingerprints.sha256`。本分工未审查 PG、默认 Runtime、R04-B、F-65 或 iOS，相关结论由根任务另行给出。
