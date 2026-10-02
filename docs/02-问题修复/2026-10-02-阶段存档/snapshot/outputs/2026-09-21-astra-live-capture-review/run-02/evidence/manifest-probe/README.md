# run-02 停止 manifest 两个窗口的真实 Store 探针

结论：两个窗口均已在本地 Swift CLI + 真实文件写入/Store 重建中复现。`probe-output.json` 的 `violationObserved=true` 表示缺陷复现成功，不表示修复通过。

## 证据边界

- `Outbox-extracted.swift` 逐字提取当前 `OwnerTruthContracts.swift` 第 16122 至 17657 行，覆盖 Store 实现、校验、编码、快照和落盘逻辑，未改产品算法。原文件及提取块 SHA-256 见 `source-fingerprint.json`。
- `LiveTurnDelivery-extracted.swift` 同样提取真实 delivery 类型。
- `ProbeDependencies.swift` 仅提供与本次路径无关的 AccountLease/command 等编译适配，落盘适配用 Foundation 原子文件写入。认证、append exposure 若误入会直接 fatalError。Store 锁、manifest、body、issue、编码和恢复逻辑全部执行真实提取源码。
- 全部对话和账号均为合成数据。只跑 Store 组合探针，不是 Controller 全链测试、真机、网络或 Provider 证据；没有改产品、访问手机或生产。
- Store 重建模拟两个写操作间进程丢失后能看到的持久状态；本探针没有发出 end/ACK/admit，不能据此声称已经观测到重启自动提交。

## A：登记 member 提前消掉尚未落盘的正文/争议义务

合法序列：旧 q1 已封存正文 A → stop manifest 包含 q1 新观察 h2 → `registerCanonicalMember(h2)` 成功 → 其后的 `upsertCanonicalTurn(B)` 尚未执行时丢失进程 → 重建 Store。

结果：`pendingManifest=0 / unsealed=0 / conflicts=0 / deliveryCount=1`，只保留 A。对照补做本地 B upsert 后 `conflicts=1`，证明被丢掉的是本应识别的真实争议观察。

源码定位：Echo 1899–1913 把 member 与 body 分成独立调用；Store 16564–16569（已有 member）和 16594–16601（新 member）在正文/issue 成功前就把 handoff 加入 resolved。manifest 只保留成员坐标，不保留正文或争议结果，因此前述窗口不可从盘上还原。

应修：hand-off 的完整结果（仅身份、正文、或明确 typed issue）与 durable 完成标记应当以一个一致的持久提交完成；只有身份登记不能证明携带正文的 handoff 已完成。恢复时未提交完成结果的 handoff 必须保留 gap/可恢复义务。

## B：正文完成在前，陈旧 stop manifest 写入在后

合法交错：主线程 finish 捕获 pending h1 清单 → 已排队的 h1 在 persistenceQueue 完成 register + body（内存 reservation 已解除） → 其后的 requestClose 用先前捕获清单写入 manifest → 重建。

结果：写 stop 前 `unsealed=0`，写 stop 后及重建后 `pendingManifest=1 / unsealed=1 / memberWithoutBody=0 / deliveryCount=1`。重复无参 requestClose 仍是 1，没有剩余 handoff 再将该条完成。

源码定位：Echo 2066 捕获清单，2083 调用异步持久 close；2332–2358 把清单排队。Store 17055–17057 初建 manifest 时把 resolved 清空，且没有独立持久化的 completed handoff 集合可用于对账。CAP09 的 2604–2611 强制 manifest 先成功，再于 2613 开始释放 handoff，只覆盖正向顺序，未覆盖本交错。

应修：stop manifest 与实际已持久完成的 handoff 必须在同一串行/原子边界对账，避免用主线程旧快照当作之后的未完成清单。不能仅凭 canonical ID 有正文就消账，因为那会重新引入 A 中不同观察的争议丢失。

## 正向对照及运行

manifest 先持久，再 register + body，重建结果 `pendingManifest=0 / unsealed=0`。因此两个失败由具体提交顺序触发，不是探针通用落盘失效。

本次编译与执行退出码均为 0。编译仅出现一个预期 warning：未调用的 append-command 适配 initializer 不抛错，真实 delivery 中的 `try` 因而冗余；与 Store 探针无关。

复现：在此目录执行 `sh reproduce.sh`。每次均使用全新的 `/private/tmp/astra-live-manifest-probe.*` 目录。标准输出为只含数量和判定的 JSON。
