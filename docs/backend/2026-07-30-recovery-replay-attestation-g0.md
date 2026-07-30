# G0 Recovery Replay Attestation

日期：2026-07-30

## 目的

恢复演练此前允许测试脚本用完整、手写的 replay bundle 生成 `GO` 记录。该行为只能说明
回放合同的结构可解析，不能证明 bundle 来自受控的 receipt authority。现在完整 replay
bundle 必须携带受控 HMAC attestation；没有有效 attestation 时，工具 fail-closed，不能
生成可完成的 replay evidence 或 `GO` recovery record。

## 合同

- replay bundle 使用 `schemaVersion=2`；
- `attestation` 固定包含 `schemaVersion`、`algorithm`、`keyId`、`bundleDigest`、`signature`；
- 算法为 `hmac-sha256`，签名绑定 canonical bundle body、key id 和 digest；
- 修改 receipt、coverage、LSN、source evidence 或 key id 都会使验证失败；
- verifier 只接受配置的 key id 和 root-only key file，文件必须为 `0600`；
- 没有 bundle 时仍输出 `NO_GO/replayBundleMissing`，不会要求 key；
- 不存在未签名的 test-only 完成旁路。

## 运维边界

真正的 receipt authority/worker 尚未实现。本次只固定“未来能产出 `GO` 的 bundle 必须可
验证”的边界，不生成 production key、不生成 replay bundle、不执行数据恢复或切流。

当可信 producer 落地后，由运维在服务器创建独立 key：

```bash
sudo install -d -m 700 /etc/dreamjourney
sudo sh -c 'umask 077; openssl rand -hex 32 > /etc/dreamjourney/recovery-replay-attestation.key'
sudo chmod 600 /etc/dreamjourney/recovery-replay-attestation.key
```

该 key 不得与 `BACKEND_API_TOKEN`、数据库密码、DeepSeek、火山或腾讯 provider 凭据混用。
未来执行携带 bundle 的恢复演练时，设置：

```bash
export RECOVERY_REPLAY_ATTESTATION_KEY_FILE=/etc/dreamjourney/recovery-replay-attestation.key
export RECOVERY_REPLAY_ATTESTATION_KEY_ID=recovery-replay-v1
```

## 验证

```bash
python3 -m unittest tests.test_recovery_record
python3 scripts/db/recovery-postgres-smoke.py
bash -n scripts/db/run-recovery-deployed-smoke.sh
```

smoke 会证明未签名完整 bundle 被拒绝，签名 bundle 才可进入绑定的 `GO` 合同路径。它不构成
真实 receipt authority、owner orphan 处理、真实 Postgres G2 恢复或任何生产切流证据。
