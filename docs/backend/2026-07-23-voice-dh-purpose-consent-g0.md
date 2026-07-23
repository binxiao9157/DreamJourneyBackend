# Voice/DH 用途与不可变同意 G0 合同

对应 V4 `WI-V0-01-01` 的最小 G0 切片。本次只建立纯策略合同，不代表声音复刻、TTS、腾讯数智人或访客语音已获准开放。

## 已实现

- `ProcessingBasis`、`ConsentReceipt`、`VoicePurposeGrant` 都是 `extra=forbid` 与 `frozen` 的不可变输入合同。
- 用途严格拆分为：`training`、`preview`、`private_synthesis`、`memoir`、`dh_audio_drive`、`visitor_public_voice`。
- 收据与 grant 都绑定 subject、actor、用途、policy、provider、region、签发/过期/撤销、前序 hash 和自身 hash。
- 策略只复用既有 `SubjectEligibilityDecision` 的 hard deny。即使旧路径能构造正向 decision，G0 也只能报告 synthetic 前置条件齐全，状态仍是拒绝；它不读取也不把旧 `consentVerified` 或 `authorizationConfirmed` 布尔值升级为授权。
- missing、legacy boolean、未生效、expired、revoked、offline、未知 provider/region、purpose/provider/region/binding 不一致都 fail closed；字符串布尔值一律不是有效输入。
- 未成年人、家庭代录、第三方、逝者和 subject/actor 不匹配复用既有主体资格 hard deny。
- `visitor_public_voice` 始终要求 M2/G4，G0 仍拒绝。

## 明确未做

- 不新增 API、数据库迁移、持久化收据、provider 调用、客户端入口、ReleasePolicy 提升或真实数据出站。
- 不把 synthetic 前置条件齐全当成可训练、可合成、可开腾讯 session 或可公开访问；G0 不返回任何可提升授权的状态。
- 不改变现有 Voice/DH 路由和旧 UI；后续 `WI-V0-01-02..11` 仍需独立完成相应 G1/G2/G3/G4。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-voice-dh-consent-policy-gate.sh
PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
```

通过该 gate 仅证明 G0 默认拒绝和 value-free 合同；不构成生产 Voice/DH 可用性、合规批准或真机验收。
