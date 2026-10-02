# 2026-09-27 开麦专项源码审查指纹

本文件仅标识本轮读取的本地代码，未构建或执行产品测试；不是历史设备二进制或线上版本证明。指纹范围为下列文件，不是整个依赖闭包。实施方须重新核对完整受影响依赖。

## iOS

- 仓库：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 分支：`feature/prd-stitch-ui-adaptation`
- tracked dirty 文件数：16；未复制或修改已有差异。

| 文件 | SHA-256 |
|---|---|
| [EchoViewController.swift](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift>) | `6cd4ab13981b0234ccbacd8c67edd839022ae01f9c88e2790a72554a50d05bd8` |
| [DreamJourneyBackendClient.swift](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift>) | `e065feb47c9035f869ba7f3d7314825fd99bd2222b43646ce8c49a17f01b0701` |
| [ReleasePolicyStore.swift](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ReleasePolicyStore.swift>) | `95f8e9b2c86629b6b4a94dd07bf965d555a8cfc729d0b81f284ace7914c12b2b` |
| [DialogEngineManager.swift](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift>) | `2086476321ba621374bce16c042354eb7048618128cd2dd4b9fbfeb617e90d6e` |
| [MicrophonePermissionManager.swift](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/MicrophonePermissionManager.swift>) | `7163e78b31ad2dbd1b6527c6dadd3e9953b63e1578d7025ed46217466b21db9b` |

## Backend

- 仓库：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- HEAD：`8141ff271228b78c35237f9947f4bf026332af43`
- 分支：`main`
- tracked dirty 文件数：0；未复制或修改已有差异。

| 文件 | SHA-256 |
|---|---|
| [main.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py>) | `4732658120eadb9ceb55b2eeb0fd998f5f019424c2099514e39136412a45bcde` |
| [realtime_voice_proxy.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py>) | `69fa5a4500c83b2e3f127791032f7b90b59f0d2e019e828b3c5d1e9cb97e89bd` |
| [postgres_store.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/postgres_store.py>) | `b52e0dc1f3da3fb02304c3232c70ca3bde8224a8d6900b044cd3726564d9ec39` |
| [release_policy.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py>) | `e7eb0c8ab21c39ce0db294f4859eb84c76ebdc0d0693260f14a75c4e22651191` |
| [formal_memory_conversation_snapshot.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/formal_memory_conversation_snapshot.py>) | `ee2252184a567217bed0c6958aa82aa6eef2c41a93a1f9d44e4eb5e26c07e22a` |
| [owner_truth_memory_projection.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_memory_projection.py>) | `a255a7cba8f8f3bf6dfdd73afaa903e2f66ba5f9a2845b44eb981b754eb28f74` |
| [operation_metrics.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/observability/operation_metrics.py>) | `e92bfc329635cf33c06225199da17a7241e4c1d7a8676bc2bc7c0d18d7fdc4e0` |
| [uow.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py>) | `8192be912e1a9d9851dfaa3f1aca152927260141733a9b094446797cc3c9fe75` |
| [config.py](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py>) | `f6f7b9a0cea8b7409c2a9cecdf65009c8551265b2ee5c40caa22a51ae7a4829d` |
