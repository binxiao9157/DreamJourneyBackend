# DreamJourney B6 会后任务冷启动只读恢复本地交付报告

日期：2026-09-14（Asia/Shanghai）\
状态：`A_LOCAL_PASS / READY_FOR_B6_DEVICE_RETEST`\
现场状态：`ISSUE-B6-01/B6 = FAIL`，真实 iPhone 复测未执行。

## 1. 范围和基线

- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- 基线 HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- 原有 B4、正式记忆策略及后端更正改动全部保留；未 reset、commit 或 push。
- 本轮没有生产访问、部署、iPhone 安装或测试，也没有操作正式记忆、候选、历史或 Dead Letter。

基线及原有脏文件归属见 `evidence/pre-fix/baseline.md`。后端当前脏文件均为前序 B4 工作，本轮没有修改后端业务源码。

## 2. 已证实根因与未证实项

### 已证实

1. 冷启动恢复原来构造可写的 `EchoLiveMemoryCaptureCoordinator`，会从检查点继续 end/ack/admit，违反只读恢复边界。
2. 临时账号或策略不可用会被折叠为终态 unavailable；后续 readiness/policy 恢复没有稳定接回原读取意图。
3. 原观察流程缺少共享的硬期限、GET 总预算和旧回调执行权隔离。
4. follow-up V1 解码失败会退化为空数组；没有原子 V2 迁移和迁移失败回退证据。
5. 检查点 receipt setter 可被迟到的旧回执降级阶段。

主业务红例：`evidence/pre-fix/R03-cold-recovery-write-replay-red.xcresult`。它在修复前通过真实恢复入口观察到冷启动写重放，修复后同一合同改为恢复写为 0。

### 仍未证实

- 先前真机首次出现“当前无法继续整理，原对话已保留”的最早失败层仍未知。本轮修复了可静态证实的错误机制，但不把策略过期写成唯一现场根因。
- 真机上的网络、系统前后台时序、声音、打断与连续聆听尚未复测。

## 3. 修改内容

### M01-M03：持久坐标与只读恢复

- `EchoViewController.swift:339`：增加账号作用域的 follow-up V2 envelope；原子写入并回读验证，V1 在迁移成功后仍作为故障回退。
- `EchoViewController.swift:353`：V1->V2 迁移失败保留旧记录；V2 损坏可从 V1 恢复；测试 suite 使用独立临时命名空间，生产 `.standard` 仍使用稳定 Application Support。
- `EchoViewController.swift:549`、`:784-881`：completion phase 单调；重复同身份回执幂等，错身份继续拒绝。
- `EchoViewController.swift:2337`：新增只读 recovery coordinator；它只持有 status/pending-batch GET 能力，不持有 start/append/end/ack/admit 或麦克风能力。
- `EchoViewController.swift:2811`、`:2871`：registry 以 scope+product 为执行所有者；checkpoint、follow-up、outbox 先合并后执行，冲突逐工作流隔离。

### M04-M06：真实只读请求与预算

- `OwnerTruthFormalMemory.swift:866`：增加 `liveMemoryRecoveryHandle`、`liveMemoryRecoveryStatus` 资源类型。
- `DreamJourneyBackendClient.swift:9272`：原 batch 状态读取接入真实账号租约、FeatureGate evaluator、策略/认证有界恢复和 typed decode。
- `DreamJourneyBackendClient.swift:9298`：无 batch 的 ended 检查点只读 pending batch，严格匹配原 thread/session。
- 每 round 30 秒、最多 6 次业务 GET、策略恢复最多 1 次、认证恢复最多 1 次；requestID/generation/lease/batch 共同隔离旧回调。

### M07-M09：页面生命周期与 readiness

- `EchoViewController.swift:11415`：页面先订阅 registry 再启动恢复；重新进入、前台、账号就绪和策略刷新均走同一恢复入口。
- 页面消失只释放订阅；账号真正变化才 suspend 原 scope。恢复不会启动麦克风或把新表达接到旧场次。
- `AccountLease.swift:147`、`AppCoordinator.swift:140`：在既有 active receipt 完成后发布局部 readiness 通知，不改变登录激活顺序。
- 增加“核实整理状态”展示和 queued/organizing/retry/pendingReview/empty/failure/quarantine/unknown 的准确映射。

### M10：测试和安全诊断

- `OwnerTruthContractsTests.swift:12`：V1->V2 原子迁移、迁移中断保留、V2 损坏回退。
- `OwnerTruthContractsTests.swift:62`：迟到重复 receipt 的单调和幂等。
- `OwnerTruthContractsTests.swift:260` 起：退出阶段、策略、401、硬期限、状态矩阵、绑定、合并、Controller 重建等 B6 组合回归。
- QA 脚本：`Scripts/QA/prd-stitch-ui/run-b6-cold-start-read-recovery-smoke.sh`，分两个真实模拟器进程；本轮另加第三进程复核残留坐标仍只读可恢复。
- 日志只记录白名单状态、attempt、generation、随机 trace/request 和哈希 workflow；未记录正文、转写、token、密钥或原始业务 ID。

## 4. 红绿与回归结果

### iOS

- 持久化/阶段定向：2/2 PASS，`evidence/post-fix/B6-persistence-v2-final.xcresult`。
- OwnerTruth：423/423 PASS，`evidence/post-fix/OwnerTruthContracts-v2-final-2.xcresult`。
- 全部 iOS：576/576 PASS，`evidence/post-fix/DreamJourneyTests-v2-final.xcresult`。
- 中间 `OwnerTruthContracts-v2-final.xcresult` 保留了 V2 初版测试命名空间串扰失败；修复后未删除或弱化原断言，最终结果包通过。

主要命令：

```text
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination id=67D3337E-0623-4578-9479-31F4CD9033DA
```

### 跨进程 UIKit/UIQA

证据目录：`evidence/post-fix/b6-cold-start-uiqa-v2/20260914-135604/`

- 第一进程 PID 31929：正常 append/end/ack/admit 各 1，状态 queued，写入持久 follow-up。
- 第二进程 PID 31953：同一哈希 workflow，状态 pendingReview，GET=2，恢复写=0，microphone=false，newCapture=false。
- 第三进程 PID 34668：不清数据再次冷启动，GET=1，仍为 pendingReview，恢复写=0，microphone=false，newCapture=false。
- 截图：`01-seed-queued.png`、`02-recovered-pending-review.png`。

### PostgreSQL 16 隔离验证

- `evidence/post-fix/b6-postgres-read-only-result.json`：2 次状态读取，76 张 owner_truth + 16 张 async_effects 表逐次前后计数相同，`changedTableCount=0`。
- `evidence/post-fix/b6-postgres-workflow-count-result.txt`：相同合成文本的两场独立 Live 保持 2 session、2 review batch、2 source，状态读取零写。
- 测试库为临时独立库，执行后删除；本地 PostgreSQL 已停止，只读镜像已卸载。

### 后端保持性

- 本轮后端业务代码未改。
- 相关门禁：29/29 PASS，4 条既有 FastAPI deprecation warning。
- 原始日志：`evidence/post-fix/backend-gate-final-2.log`。
- `backend-gate-final.log` 是受限环境无法访问依赖缓存的工具失败，保留但不算业务 FAIL。

### 编译与差异

- 模拟器：PASS，`evidence/build/simulator-build-v2-final.xcresult`。
- 通用 iOS 设备目标：PASS，`evidence/build/generic-ios-build-v2-final.xcresult`。
- iOS 与后端 `git diff --check` 均 PASS。
- 构建警告是锁定依赖的既有 umbrella/deprecation 警告；没有新增编译错误。

## 5. T01-T32

逐项状态、测试映射及证据见 `T01-T32-执行清单.md`。

- T01-T31：本地 PASS。
- T32：自动化保持性 PASS，真实 iPhone NOT_RUN。
- 未以测试总数代替具体场景；跨进程、PG、硬期限和 V1/V2 迁移均有独立证据。

## 6. 指纹

### 源码

- `AccountLease.swift`：`25bba74a1488099e15dc29ecc9fe7fd1003273bd6daa2556a68eb2e1d01423f7`
- `AppCoordinator.swift`：`9d3a65eb900214c8501a7bf9db94dad7bbd5272f0f78861e7c1582c27fbaac8c`
- `OwnerTruthFormalMemory.swift`：`ef432174acb33d69bbce1e3c0ce0f076d775838d7951d566cd5eebc753b86c10`
- `EchoViewController.swift`：`c3eef1382972b0224dc636296d9a249856fbe25608927c356347ec7a1b29e53a`
- `DreamJourneyBackendClient.swift`：`8889d7a0ee5cbf733f017a81e196bb05c60ad2b1f45e8337d92e050b7ce1ed79`
- `OwnerTruthContractsTests.swift`：`0ef53c798c2ca58b147402552206c4f38090112189a161e0b0cc89fee69dce3f`
- UIQA 脚本：`077990698ec2f273a62536b3504e29cdcc449ab72944c908ea48426b542aad09`

### 构建产物

- 模拟器 executable：`3dc47e47b397070224784dd04bbff5b267b3c07fe79f6496e212e5ad7c0af2ca`
- 通用 iOS executable：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 7. 部署判断和残余风险

- 后端合同未变，因此本缺陷不需要后端部署或迁移。
- iOS 尚未安装到 iPhone；真机仍运行旧/前一诊断版本的可能性必须在复测前通过源码与安装包指纹排除。
- 真机需验证：关闭 App 后后台完成、重新打开自动显示正确任务状态、无自动麦克风、无重复候选；同时保留正常声音、打断、连续聆听和正式记忆绑定。
- 历史现场最早失败层仍未定位；复测时应同时采集脱敏客户端 trace 和服务端只读访问证据。

## 8. 局部回退

仅回退本轮 iOS 冷启动恢复改动：

1. 移除 B6 readiness 通知接线、只读 coordinator/registry/service 和两个只读资源适配。
2. 回退 follow-up V2 写入时必须保留 V1 数据；不得删除用户容器内已有 V1/V2 文件。
3. 保留此前 B4、正式记忆策略、Live 音频及后端更正改动，不得整文件回退脏文件。
4. 回退后不得恢复冷启动写重放；若无法保留只读边界，应停用自动恢复并明确阻断，而不是重发 end/ack/admit。

## 9. 下一步真机复测

取得用户明确授权后：

1. 核对源码与本报告指纹，原位覆盖安装，保留 App 数据。
2. 开启脱敏设备日志与服务端只读观测后，再让用户开始操作。
3. 新开 Live 输入一条合成信息，手动停止，在 queued/organizing 时关闭 App。
4. 等后台完成后冷启动 App，确认自动显示 pendingReview/empty/明确失败之一，不长期停在模糊状态。
5. 核对恢复期没有 start/append/end/ack/admit、没有自动麦克风、没有新 session。
6. 刷新候选，核对同一 batch 来源和重复性；未获用户确认不得审核写入。

本地停止点已达到：`A_LOCAL_PASS / READY_FOR_B6_DEVICE_RETEST`。真机通过前不关闭 ISSUE-B6-01/B6。
