# C0 声音复刻正式启用 Gate

状态：`CODE_COMPLETE / DEFAULT_OFF`

本项将“本人在世成年人私有声音复刻”的训练准入收敛到后端。它不代表 M1 已可公开发布；在真实强身份/活体 Provider、声音 Provider 生产权限、数据处理/保留批准和真机验收完成前，训练仍必须保持关闭。

## 准入合同

`POST /voice/profiles` 只有同时满足以下条件才会调用声音复刻训练 Provider：

1. 当前账号已通过服务端认证，且 `actorUserId == subjectUserId == userId`。
2. `personaScope=personal` 且 `digitalHumanId=userId`；任何家庭成员、他人、未成年人和逝者路径均 hard deny。
3. 客户端明确勾选授权，但该字段只表示 UI 操作，不能作为授权事实。
4. 服务端已签发且未过期的样本授权回执存在，音频样本通过既有格式、时长、清晰度和单人语音检查。
5. 独立的强身份/活体 Provider 返回绑定同一账号的、在有效期内的“在世成年人 + 活体通过”回执。
6. 声音复刻 Provider 已配置且可用。

任一环不满足时，后端不会调用训练 Provider。身份核验不可用返回 `503 voice_identity_verification_unavailable`；资格不满足返回不可重试的 `403 subject_eligibility_hard_denied`；Provider 故障保留既有明确失败状态，不静默改用默认音色或其他 profile。

服务端只保存最小化的回执摘要：Provider 类型、回执哈希和有效期。原始身份文件、活体材料、Provider 原始回执 ID 和 API key 均不会存入 profile，也不会返回给 iOS。

## 运行时能力

`GET /config/runtime` 的 `voiceClone` 新增：

| 字段 | 用途 |
| --- | --- |
| `identityEligibilityProviderReady` | 强身份/活体 Provider 是否完整配置。 |
| `identityEligibilityProvider` | 脱敏的 Provider 类型，未配置时为 `unavailable`。 |
| `trainingAdmissionEnabled` | 训练准入轴是否可启用，需同时满足声音 Provider 与身份/活体 Provider。 |
| `trainingAdmissionReason` | 未启用原因，例如 `identityLivenessProviderUnavailable`。 |
| `trainingAdmissionContractVersion` | 本合同版本。 |

iOS 仅在这些字段和现有 runtime/release policy 都允许时开放隐藏的训练提交动作；旧 profile 若缺少 `consent.source=serverReceipt`，不会再被视为可用于 Echo。

## 首发 Provider Port

目前首发 port 为 `httpJson`。服务器将向配置的 HTTPS 地址发送：

```json
{
  "contractVersion": 1,
  "capability": "clonedVoice",
  "actorUserId": "authenticated-owner-id",
  "subjectUserId": "authenticated-owner-id"
}
```

Provider 必须返回与请求完全绑定的短期回执，字段为 `receiptId`、`actorUserId`、`subjectUserId`、`ageStatus`、`livingStatus`、`livenessVerified`、`issuedAt` 和 `expiresAt`。`ageStatus` 仅允许 `adult/minor/unknown`，`livingStatus` 仅允许 `living/deceased/unknown`。任何缺字段、过期、跨账号或不支持值都 fail-closed。

服务器私有 `.env`：

```dotenv
VOICE_IDENTITY_ELIGIBILITY_PROVIDER=httpJson
VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_URL=https://identity.example.com/voice-eligibility
VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_API_KEY=<server-only-key>
VOICE_IDENTITY_ELIGIBILITY_HTTP_JSON_TIMEOUT_SECONDS=10
```

没有通过评审的强身份/活体 Provider 时，必须保持 `VOICE_IDENTITY_ELIGIBILITY_PROVIDER=disabled`。现有 OTP/SMS 身份挑战不能代替该 Provider。

## 验证与部署

本地 Gate：

```bash
./scripts/run-backend-voice-clone-c0-gate.sh
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  ./scripts/run-backend-voice-clone-c0-deployed-smoke.sh
```

已配置真实身份/活体 Provider 的环境，在线 smoke 前增加：

```bash
VOICE_IDENTITY_ELIGIBILITY_EXPECTED_READY=1 \
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  ./scripts/run-backend-voice-clone-c0-deployed-smoke.sh
```

默认部署 smoke 刻意验证未配置时 `trainingAdmissionEnabled=false`，防止因漏配或升级回退而意外开放声音训练。

## 本次部署记录

- 代码版本：`538cdf5`（2026-08-08）。
- 部署环境：生产 Postgres API，API 容器已重建并健康。
- 证据：`/ready` 返回 `200`；对公开 HTTPS API 运行 C0 deployed smoke 通过，确认 `identityEligibilityProviderReady=false`、`trainingAdmissionEnabled=false`，没有暴露身份 Provider 凭据或回执字段。
- 本次未配置或调用真实身份/活体 Provider，也未提交真实声音样本；这是安全默认关闭验证，不是 M1 生产启用验收。

## 仍未关闭的外部门

- 选定并接通经过审批的成年人强身份/活体 Provider。
- 声音 Provider 的生产权限、配额、删除回执与保留策略批准。
- 真实测试主体的训练、试听接受、Echo 音色一致性、停止/打断和删除真机验收。
- M1 的独立发布和合规审批。
