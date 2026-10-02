# run-03 manifest 独立复核

本次提取最新真实 `OwnerTruthContracts.swift` 的完整 Outbox 连续段，执行 Swift CLI、实际文件持久化与 Store 重建。产品代码未修改。运行 `sh reproduce.sh` 可复现；每次使用新 `/private/tmp/astra-live-manifest-probe.*`。

## 结果

- R2-03-A 原窗口已关闭：仅登记 member 后重建 `pendingManifest=1 / unsealed=1`；原子 `persistCanonicalHandoff` 完成争议正文与 issue 后重建 `pendingManifest=0 / conflicts=1`。
- R2-03-B 原窗口已关闭：原子 handoff 先完成、stop manifest 后写入，重建 `pendingManifest=0 / unsealed=0`；重复同一非空 manifest 仍保持 0。
- 留有局部 Store 幂等合同缺口：上述已完成状态再次调用允许的无参 `requestClose()`，随后重建，变为 `pendingManifest=1 / unsealed=1`。这是 API 层实测回退，不能表述为正常当前 Coordinator 路径会再次失败。

当前 Coordinator 在调用前有 closeIntentPersisted/inflight 双重保护，恢复也从 snapshot.isClosing 初始化。因此本探针没有证明正常短场可触发此幂等分支。

## 最小修订建议

`requestClose` 验证参数与既存 manifest 相容后，应使用最终的 `envelope.closeManifest` 作为 effective manifest，构建身份字典及 resolved 交集；不要继续用本次可能为空的参数数组。补充 nonempty→同参→默认空参→Store重建的单调断言即可，不需要改 Controller 流程、音频或业务请求。

## 范围

`Outbox-extracted.swift` 全段未改，`LiveTurnDelivery-extracted.swift` 为真实类型提取；原文件与提取块指纹见 `source-fingerprint.json`。`ProbeDependencies.swift` 只适配未使用的账号/命令类型、诊断与 Foundation 原子文件写入；认证与网络路径未执行。`compile-output.txt` 只有未执行的 command 适配不抛错导致真实 delivery 内 try 冗余 warning。编译、执行退出码 0。

这是 Store 层组合证明，不是本次另跑 Controller、真机或 Provider 的结果。真实 Controller 覆盖须以 run-03 原始 xcresult 及测试源另行核对。
