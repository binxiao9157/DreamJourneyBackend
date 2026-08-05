# Voice Clone Delete Disclosure P1

日期：2026-08-06

## 变更

音色删除现在严格区分本地状态与第三方 Provider 状态：

- 删除请求立即撤销本地合成权限并将本地记录标记为删除中；
- 本地 outbox/effect 的 `accepted` 只代表本系统接收了删除意图；
- 在没有持久化 Provider 回执时，`providerCleanupState` 返回 `unsupported`；
- 只有 Provider 回执已持久化时，才可以返回 `completed`；
- Provider 回执失败或未知时，返回 `partial`。

这避免客户端把本地 tombstone 或 outbox 接受状态误写成第三方样本、训练产物或资产已删除。

## 合同

`VOICE_CLONE_DELETE_CONTRACT` 明确声明：尚未产生样本、训练产物或 Provider 资产的删除回执前，不能宣称第三方清理已完成。

`VOICE_CLONE_EXIT_DISCLOSURE_CONTRACT` 固定了本地撤权和无 Provider 回执删除的公共、脱敏状态投影。

## 验证

- Voice Profile lifecycle 定向测试通过；
- iOS `product-v4-voice-dh-exit-disclosure-check.py` 通过；
- M1 unified non-device evidence lane 通过，仍输出 `NO_GO`；
- `scripts/verify_backend.sh` 通过，包含 1894 个后端单测及全部现有 gate。

## 保留边界

真实 Provider 删除调用、Provider 回执持久化和对应外部验收尚未完成。该状态不应被视为第三方物理清理完成。
