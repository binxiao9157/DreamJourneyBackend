# 当前真实 router 源片段反例

日期：2026-09-17。只运行本地合成源片段；未运行 SDK、手机、生产请求、UIKit 或完整 Store。未修改业务源码。

## 提取内容

- `DialogEngineManager.swift` 原样提取 `DialogProviderEventMetadata` 至 `DialogProviderReplyCorrelation` 之前（当前 992–1410 行），含真实 JSON parser、finality mapper、router 与 reserve/deliver 实现。
- `OwnerTruthContracts.swift` 原样提取消息 role 定义与 canonical finality/event/member 定义。
- 驱动仅提供合成 metadata 和监听真实 router 的输出，没有另写 router 状态机。
- 源码 SHA 见 `source-sha256.txt`。

## 运行命令

```sh
swift -module-cache-path /private/tmp/dj-live-device01-module-cache /Users/gaominge/Documents/liftora/outputs/2026-09-17-live-device-failure-analysis/router-probe/current-router-source-probe.swift
```

## 反例

1. 用 `.other` 投递带合成 question/reply ID 的开场类型 metadata。
2. 投递另一个 question 的 identifier-only ASRInfo。
3. 投递不带 ID、`results[0].is_interim=false` 的 ASRResponse。

当前输出（`stdout.txt`）：非 ASR 投递产生 owner member 却没有正文；ASR final 正确映射 complete；共两成员一正文，最早成员无正文。

这直接证明当前 router 会把非 ASR question metadata 错登记为 owner 槽位。Store `flushCompletedCanonicalPrefix` 的 16781–16788 行在最早 member 无正文时 break，因此此槽能挡住后续 complete。该 Store 后果本次为静态核查，未在此 probe 执行 Store。

## 与现场关系

`../live-console.safe.log` 原行 L0394/L0396/L0397 在首 ASR 前出现开场 TTS，question 别名 ID-42、reply ID-43，turnSequence=0、staleQuestion；L0398 的首 ASR 为另一别名 ID-44，第二问为 ID-46。两问 final=true 分别见 L0572、L0849，说明当前 strict parser 已识别 explicitFinal，不能再把它诊断成 final 字段类型不兼容。两问 ChatEnded、TTSEnded 均已在停止前观察到；停止后 unsealed=5 与“额外空 owner + 两问两答”一致。

现场缺 canonical member 逐条落盘信息、接收 ordinal 和当场 outbox 快照；不得将一致性推断表述成已读取到五个槽位的真实内容或唯一故障。
