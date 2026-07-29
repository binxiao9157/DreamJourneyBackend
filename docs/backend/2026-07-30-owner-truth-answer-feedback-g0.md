# Owner QA Answer Feedback 与 Citation Currentness G0

日期：2026-07-30
状态：`LOCAL_G0_VERIFIED / QA_ONLY / DEFAULT_OFF / NOT_DEPLOYED`

## 范围

本轮补齐 Owner QA 的两个内部合同，不改变公开 Echo、iOS 页面、KBLite 或现有
Context 路由：

1. `GET /v2/vaults/{vaultId}/answers/{answerId}/citations`
   - 读取已持久化 Answer/Citation receipt 的 typed citation；
   - 为每条 citation 返回当前性标签：`current`、`citationNotCurrent`、
     `projectionUnavailable`、`projectionInputsChanged`、`rightsRevisionChanged` 或
     `rightsRevoked`；
   - 不返回 question、answer、Projection 正文、Memory 正文或 legacy Echo 内容。
2. `POST /v2/vaults/{vaultId}/answers/{answerId}/feedback`
   - 写入一次、不可变、无正文的 `helpful: boolean` receipt；
   - 同一 Answer 仅允许一条反馈；同一 command 重放幂等，不同 command 不能覆盖；
   - 仅当所有 citation 当前、同一 authority epoch 且 `helpful=true` 时，才会标记
     `metricEligible=true`。

两个路由均复用 `X-DreamJourney-QA-Owner-Truth: 1` 与
`OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED`。默认关闭，`include_in_schema=False`，普通
发布态没有入口。

## 当前性与失败关闭

- Projection 不是 `ready` 时，citation read 只报告重建/权限原因；反馈可以保留为
  `metricEligible=false`，但不能成为质量指标。
- 没有 citation 的回答可以得到一条无指标反馈，原因固定为 `noCitations`；它不能被
  误算为“有帮助的可信记忆复用”。
- 权限 epoch、Source、MemoryVersion 或 Projection checkpoint 不再匹配时，反馈不得
  生成指标信号。
- Postgres `0062_owner_truth_answer_feedback` 以唯一约束、触发器与 append-only trigger
  作为最终写入边界；Python 服务和 in-memory double 采用相同语义。

## 数据权利边界

`answerFeedback` 已纳入 Owner Truth 导出与终端清理计数。导出只包含 receipt 元数据：
ID、哈希、布尔反馈、citation 数量、当前性结果、authority epoch 和时间；不导出问答或
记忆正文。不可变 ledger 的实际保留/清理仍依赖后续专用 rights reconciler，本轮不夸大为
已完成删除能力。

## 验证

本地执行：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-answer-feedback-g0-gate.sh
```

gate 覆盖：当前 citation 的幂等 helpful receipt、无 citation 的非指标反馈、Projection
失效后的 fail-closed currentness、跨 Owner 拒绝、默认隐藏 API、路由 ownership inventory、
迁移静态合同和 data-rights 导出/计数。

## 非目标

- 不开放公开 Echo feedback 或产品指标面板；
- 不存储自由文本反馈、query、answer 或 Memory 正文；
- 不把 Owner QA receipt 直接接入模型训练、召回排序或线上 cohort 指标；
- 不部署 migration，也不声称 Postgres/生产环境已经验收。
