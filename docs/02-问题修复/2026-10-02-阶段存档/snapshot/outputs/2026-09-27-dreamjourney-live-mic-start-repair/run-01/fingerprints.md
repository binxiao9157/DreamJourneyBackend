# 最终源码、配置与构建指纹

采集时间：2026-09-27 23:16（Asia/Shanghai）。Xcode 27.0 (27A266a)，后端 venv Python 3.9.6。未读取或记录私有 `.env` 值；这些是本地源码/配置，不是生产发布指纹。

## iOS

HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`；dirty 工作区中其他已有修改保留。以下为 SHA-256：

| 文件/制品 | SHA-256 |
|---|---|
| `DreamJourney/Sources/Modules/Echo/EchoViewController.swift` | `6a6978e9b6a7266c64461521a88aaddf603d45deb508d0a5c8484d02310cc61c` |
| `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift` | `22d18ae883106e9adfe6a7b9fb4b4c6c3d0de641ea58ff933a3573282b36ae25` |
| `DreamJourney/Sources/Services/UserManager.swift` | `998d9bc9eeaa572ce5ae24670627d0f13bdee4fa767ffe34883fc4c8d65c04a3` |
| `DreamJourneyTests/OwnerTruthContractsTests.swift` | `394c96e649dc697d44d946acd7bed1b2a25e2fe42133fc5a633f8aa3b69f06aa` |
| `DreamJourney.xcodeproj/project.pbxproj` | `e408ddb1dae67021a824c87b3366e2b50817fcad14153b3712d9a5cde90ee806` |
| `DreamJourney.xcworkspace/contents.xcworkspacedata` | `ba03a8b5e7672a8f0eeb48874be5b30c469b397af292c2999f67b8792de68972` |
| `Podfile.lock` | `810a979c1721f7eabdc6691c1541d9be0ae2db14c8687a093fe64ab945fabbaa` |
| 本地无签名 `Debug-iphoneos/DreamJourney.app/DreamJourney.debug.dylib` | `52a222463975316b8937e723250be8f49bb68f96f00fe3eac136983677765d01` |

最终冻结源码的模拟器测试制品：[`evidence/ios-final-v10.xcresult`](evidence/ios-final-v10.xcresult)，599 total、596 passed、0 failed、3 skipped；较早失败轮次保留并在 [回归隔离记录](regression-isolation.md) 逐条归因。通用 iOS 设备编译退出 0。该 `.app` 仅为无签名本地编译产物，未安装。

## Backend

HEAD `8141ff271228b78c35237f9947f4bf026332af43`；本轮四个 dirty 文件未提交。SHA-256：

| 文件/配置 | SHA-256 |
|---|---|
| `app/main.py` | `6cc8d101ae5290ea2152fa0ab37020e847491ca0743290f8126ed4e950f8523c` |
| `app/services/tokens.py` | `9898bd957bdd0733ef15942266600a5944de8750eeb16037fc5708da18331d3c` |
| `app/services/realtime_voice_proxy.py` | `3126a7e85f05f4f974bc862022d4cfcb3ca9bdc9b3c2a7f0fad16f0507e8d2d0` |
| `tests/test_credential_response_boundary.py` | `994e2871bf7ac7c176d464eabf841c0aa7901ec9af64a3f28b205aa031755677` |
| `app/core/config.py` | `f6f7b9a0cea8b7409c2a9cecdf65009c8551265b2ee5c40caa22a51ae7a4829d` |
| `requirements.txt` | `329b71fa5064aea2f50da9f4cb60d18240e16985335e36483af57e9a58039a4d` |
| `db/migrations` sorted per-file SHA-256 aggregate | `d1de64a0ad6db2f72244dc43f35e5f80832e7fbdd476c6f765141f7354929483` |

后端只运行显式 `env -i ... STORE_BACKEND=memory` 的相关测试；未触碰生产配置或数据库。`git diff --check` 两端均通过。
