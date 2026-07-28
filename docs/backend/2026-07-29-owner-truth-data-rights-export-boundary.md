# Owner Truth 数据权利导出边界（G0）

## 本轮范围

账户数据副本现在额外包含当前账号在 Owner Truth Vault 中的以下可读记录：

- Vault 元数据。
- Source 元数据与文本内容载荷；不包含媒体二进制。
- Candidate 与其审核状态。
- DecisionReceipt 的最小审计字段。
- MemoryVersion 的当前与历史版本载荷。
- Answer/Citation 的哈希、长度、引用关系和 fallback 摘要。
- Correction 请求及其已存在的 resolution。

查询始终以 `owner_truth.vaults.owner_subject_id` 为边界；调用方不能通过 Vault ID 读取其他账号的数据。通用导出脱敏器继续会移除 token、凭据、密码、签名和类似敏感字段。

每一类 Owner Truth 记录的数据库读取最多返回 1000 条。导出会同时读取精确总数；若总数超过当前导出窗口，资源会标记为 `partial`，携带 `reasonCode=ownerTruthExportBoundedAt1000` 和 `totalItemCount`，不会被描述为完整副本。

## 终端清理口径

`ownerTruth / appendOnlyAuthorityLedger` 已加入账户 30 天终端清理的资源统计和 rights execution。其结果固定为 `pending`，不会生成“已删除”回执。

原因是 Owner Truth 的 Source、DecisionReceipt、MemoryVersion、Answer/Citation 和 Correction 等记录使用 append-only 约束、外键和保留语义。直接删除这些行会破坏权威链与审计证据，也会与 V4 的“先撤访问、再按模块收敛、partial/unknown 不得伪装成功”规则冲突。

本轮没有删除生产或测试业务数据，也没有新建公开路由、Provider 调用或 iOS UI。

## 仍然未完成

以下内容必须由后续专用 Owner Truth rights reconciler 处理，当前导出会以 `appendOnlyAuthorityLedgerBoundary=partial` 明示：

- 访谈会话与消息文本。
- 可重建 SearchDocument、投影 checkpoint 和其他 derived projection。
- 不可变 command/operation 账本的保留、撤权与最终物理清理回执。
- 外部对象、备份和 Provider 留存。

在该 reconciler、保留策略和恢复演练具备前，M0 的 Copy/Export/Delete 只能视为部分闭环，不能作为公开发布或“已不可逆删除”的证据。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python -m unittest \
  tests.test_data_rights_module_inventory \
  tests.test_owner_truth_data_rights_projection
```

测试覆盖：本人导出、跨账号隔离、敏感字段脱敏、终端清理把 Owner Truth 正确保留为 `pending`、Postgres 投影查询只读及资源统计参数化。
