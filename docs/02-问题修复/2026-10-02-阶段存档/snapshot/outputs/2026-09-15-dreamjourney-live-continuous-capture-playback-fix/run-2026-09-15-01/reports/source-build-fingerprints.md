# 源码与构建指纹

## Git 基线

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- iOS branch：`feature/prd-stitch-ui-adaptation`
- Backend HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- Backend branch：`main`

两端均保留前序未提交工作；以下是本轮最终读取到的相关文件指纹，不代表只有这些文件在工作树中有修改。

## iOS SHA-256

- `OwnerTruthContracts.swift`: `a0c2802fa8b4622d422d6b3f0834eb6994647ffd8a15b1bed1ece68551f41696`
- `EchoViewController.swift`: `9fda448e2a21593b9736ce76bc3bcf15335c63e39f4f17902e9f5bfce82efddf`
- `ConversationMemoryManager.swift`: `d81c59b8ac0ba685dac72d989274d8419837685811ec0a19cece82df6a2846fa`
- `DialogEngineManager.swift`: `f3ed12555b3222fa27b81edd6efed98c53d9723b1af56893bdb93a5064cfce5b`
- `DreamJourneyBackendClient.swift`: `61cdc3f741de4a5e8464aff357b4a9f2a7f7dbd082c84f8e7a564a8d558d7de8`
- `OwnerTruthContractsTests.swift`: `247593a5d538af8dea5e2c50dfcc53f3b16e912b6bcc716841a849a59351443b`
- `AudioOwnerLeaseModelTests.swift`: `5c404ad85613cf910939a9230f393a589a314e08ec16883b948e19929b6375df`

## Backend SHA-256

- `app/domain/owner_truth/conversation.py`: `cfb1ec82ed53abce59599032dd5db55197165fdcb99635268e54e9ecd994b042`
- `app/main.py`: `565f8b43a7402f22f22b5085e5ad22fb3c4ef7988add3a62f9adbf61172591ec`
- `app/services/owner_truth_conversation.py`: `60b89c72f306d903a5bb647edf13153fe7dcc719996c1579c0ee5ccf1a57a050`
- `app/services/owner_truth_live_memory_support.py`: `42989d4f47edd5574602c47dfeb98ddb337c59a281e29ae509121242e34ab394`
- `tests/test_owner_truth_interview_input_api.py`: `194e253529ae0fcc92e9061ee41be65e4808d3583d9ab1e514a2d081d35bb304`
- `tests/test_owner_truth_conversation.py`: `521dfd6393198fa2f862c2aee39c9b78cd2c1fe6e7deaeaac9641e19ed2a36c8`
- `tests/test_owner_truth_candidate_extraction_worker.py`: `9a497954f58a6fb19de71c20972856d1ce89cbd2fa6a030a2c55ed7a51ad1888`
- `tests/test_owner_truth_candidate_review_api.py`: `2b78d23b4989d6142808a83d98f834576b7c968694db2ad3391785a6cf981831`

## App 构建产物 SHA-256

- Simulator app executable: `3ebc6d74905496f86e35f9965542ed08f7d9f10ab0e3d8bc66f611d487692cb4`
- Generic iOS app executable: `1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 最终检查

- iOS `git diff --check`: PASS。
- Backend `git diff --check`: PASS。
- Backend 相关模块 import/syntax：88 项真实 import 回归 PASS。
- 无 commit、push、部署或真机安装。
