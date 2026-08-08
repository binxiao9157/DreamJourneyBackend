# C1 声音复刻生命周期与删除回执 Gate

状态：`CODE_COMPLETE_NON_DEVICE_VERIFIED / DEFAULT_OFF`

本项补齐声音复刻 Profile 的非真机生命周期证据，不代表声音复刻已经可公开发布。训练准入仍受 C0 的服务端强身份/活体 Gate 控制；真实声音 Provider、可用槽位、已批准的保留与删除政策，以及真机听感验收仍是外部发布条件。

## 已收敛的边界

1. Profile 删除请求先在本地撤销合成权限，再写入幂等 outbox 和 Provider effect 接受回执。
2. 独立 `voice_profile_deletion_worker` 只在三个开关都打开时运行：`ASYNC_EFFECT_V1_ENABLED`、`ASYNC_EFFECT_WORKER_ENABLED`、`VOICE_CLONE_DELETION_WORKER_ENABLED`。默认全部关闭。
3. Worker 在调用 Provider 前把回执写为 `unknown`。进程在调用后中断时，后续只能查询或进入人工处理，不能盲目重复删除。
4. Provider 返回 `completed` 时才将 Profile 收敛为 `deleted`，并记录不可逆的回执哈希；不会保存或返回原始 speaker ID、请求 ID、响应正文或密钥。
5. Provider 返回 `failed`、`unknown` 或 `unsupported` 时，Profile 保持 `deleting`，本地合成持续被禁止。`unsupported` 明确表示无法确认第三方删除，不会伪造成功。
6. Profile 版本、owner、authority epoch、operation ID 和 Provider effect key 全部必须匹配。旧 job、旧回调或重复请求不能覆盖新一代 Profile。

## 当前 Provider 结论

当前火山训练/查询 Adapter 只有已评审的训练和查询合同，没有可启用的 Profile 删除 API。因此它固定返回 `unsupported`，Worker 默认不启动。只有接入经过评审的删除/停用 API 后，才可实现该 Adapter 的 `request_profile_deletion` / `query_profile_deletion` 并由运维显式打开 worker。

本地撤权不依赖 Provider 删除能力：`deleting`、`deleted`、`paused` 和 `accessRevoked=true` 都不能作为 Echo 合成 Profile。

## 非真机 Gate

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
./scripts/run-backend-voice-clone-c1-lifecycle-gate.sh
```

Gate 使用 fake Provider，不消耗真实音色槽位，也不会发送真实样本。它覆盖：

- `training -> previewReady -> accepted` 的既有生命周期约束；
- accepted 前禁止 Echo 合成；
- accepted Profile 的 owner/profile/role/purpose/PCM binding；
- 默认关闭时不发出任何 Provider 删除调用；
- 完成、失败、未知、Provider 不支持四种删除回执；
- 删除幂等和 stale profile generation 阻断；
- 删除中、失败、未知或不支持时不回退默认音色。

## 部署边界

本次代码部署不应将 `VOICE_CLONE_DELETION_WORKER_ENABLED` 设为 `true`。部署后可以执行一次 worker CLI，预期返回父级或专属 Gate 的关闭原因（例如 `asyncEffectV1Disabled` 或 `voiceCloneDeletionWorkerDisabled`）；这证明默认部署不会误发 Provider 删除请求。

真实 Provider 删除 capability 评审通过后，才允许在独立变更中：配置 Provider Adapter、启用 worker、执行 sandbox 删除/查询/对账 smoke，并将生产回执证据记录到本文件的后续部署记录。

## 本次部署记录

- 代码版本：`af0d30a`（2026-08-08）。
- 生产 Postgres API 已重新构建并健康，公开 `/ready` 返回 `200`。
- 容器内执行一次 worker CLI 返回 `asyncEffectV1Disabled`。这是父级 async-effect Gate 仍保持默认关闭的结果，比单独的 `voiceCloneDeletionWorkerDisabled` 更早阻断；没有领取 job、调用 Provider 或改变 Profile。
- 即使将来启用 async-effect 基础设施，仍需显式设置 `VOICE_CLONE_DELETION_WORKER_ENABLED=true` 才会进入删除 Provider 路径。
