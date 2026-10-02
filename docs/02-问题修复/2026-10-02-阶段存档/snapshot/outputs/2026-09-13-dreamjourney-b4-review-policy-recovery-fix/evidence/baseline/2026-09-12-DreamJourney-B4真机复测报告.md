# DreamJourney B4 真机复测报告

- 日期：2026-09-12（Asia/Shanghai）
- 设备：Minge 的 iPhone，iPhone 14 Pro Max
- 安装方式：原位覆盖安装；未卸载、未清缓存、未清本地恢复记录
- 后端：生产环境，只读观测；本轮未部署
- Git：未 commit、未 push

## 1. 版本与预检

| 项目 | 结果 | 证据 |
|---|---|---|
| iOS HEAD | `5fd061fd869edbe1fc13e8535a47880826581934` | 复测前后核对一致 |
| 未提交差异指纹 | `e4febb15561260111569ced2b0cfd7483a34e5f716a14f828b6deed7fedc7376` | 复测前后核对一致 |
| App 二进制 SHA-256 | `37f06d43912c9a6231ce091dd256536c735e872b40dbb9d218d0c4af24653621` | 本轮安装产物 |
| Bundle | `com.gaominge.dreamjourney.app`，1.0.0 (1) | 安装结果 |
| 运行配置 | 生产地址，无 QA 旁路、合成候选或故障注入启动参数 | 预检与启动命令 |
| 后端 readiness | API、数据库、迁移及认证 ready | 生产只读预检 |
| 真机构建 | PASS | `evidence/preflight/device-build.log` |
| 原位安装 | PASS | `evidence/preflight/install-result.json` |

## 2. 逐步结果

| 步骤 | 状态 | 真机与后台证据 |
|---|---|---|
| B4-0 预检、构建、安装、日志准备 | PASS | 安装前完成版本、生产配置、设备和日志检查；保留 App 数据 |
| 现有候选直接进入与刷新 | PASS | 刷新前后均为 36 条；两次真实 GET 均为 200；网络、HTTP、JSON、V5 解码及 UI 提交 trace 完整 |
| 原生 Live 事实回答 | PASS | 学校、职业等正式事实回答正确；每次有声音 |
| Live 打断与持续聆听 | PASS | 朗读中插话立即停止，自动恢复；约 10 轮持续交流，无被动关麦 |
| 本场会后保存 | PASS | 10 个 owner turn 全部持久化；`saving -> queued -> organizing -> pendingReview`；重复 ended receipt 被忽略；同一批次完成 end/ack/admit |
| 本场候选生成与读取 | PASS（生成/关联） | 基线 36 条，本场生成 2 条后为 38 条；同一 review batch/source 可核对 |
| 审核写入闭环 | FAIL | 用户连续点击两条并均看到成功；实际仅第一条发送审核 POST 并写入，第二条仍 pending |
| 候选首次读取恢复 | FAIL | 一次真实生产读取被 `expiredPolicyCache` 在发送前拒绝，UI 显示“暂时无法读取候选记忆”；没有候选 GET |
| 手动刷新恢复 | PASS | 发布策略恢复后候选 GET 200；12.497 秒完成，V5 解码并提交 37 条 |
| 退出审核入口再进入 | PASS | 新 trace、真实 GET 200、自动显示 37 条 |
| 前后台切换 | PASS | 页面安全保留 37 条，无错误或卡住；未伪造新 GET |
| 完全关闭并冷启动 | PASS | 重新认证/策略核对后真实 GET 200，自动显示 37 条 |
| 已接受记忆的正式写入 | PASS | 1 条 accepted，有审核回执和 1 个当前 MemoryVersion |
| 投影、检索文档、向量 | PASS | 当前版本已进入 projection/search document；embedding job=ready；向量存在 |
| 文字回响回查 | PASS | 回答正确且不朗读；`/echo/answers` 200；8 条引用中包含本轮新 MemoryVersion |
| 新 Live 回查 | PASS | 与文字回答一致、有声音、回答后恢复聆听；使用独立新会话 |
| 合法凭据轮换中的旧审核权失效 | NOT_RUN | 本轮未强制轮换凭据或退出账号制造场景 |
| 三条历史候选重复性 | NOT_RUN | 未自动删除、合并、确认或拒绝历史候选 |

## 3. 已证实缺陷

### F1 审核第二条出现“假成功”

- 用户对本场两条候选都点击“确认并纳入正式记忆”，两次页面均显示成功。
- 服务端只收到第一条审核请求并返回 201。
- 第二次客户端进入失败状态，没有产生审核请求；数据库显示一条 accepted、一条 pending。
- 影响：用户会误以为第二条已成为正式记忆，实际仍在待确认列表。
- 当前状态：**FAIL，需代码修复后复测**。不得用成功提示代替审核回执和正式版本。

### F2 过期策略缓存导致候选读取发送前失败

- 真实生产日志记录 `requestDenied reason=expiredPolicyCache stage=featureInitial`，随后 `permissionUnavailable`。
- 该次服务端没有候选 GET，UI 显示“暂时无法读取候选记忆”。
- 用户手动刷新后，策略恢复和新候选 GET 成功，列表恢复为 37 条。
- 影响：首次进入候选页可能失败，需要用户手工刷新。
- 当前状态：**FAIL，失败层已定位；为何首次读取没有完成有界自动恢复仍需代码分析**。

### F3 历史关闭任务缺少检查点

- 冷启动及页面切换时多次记录 `recoveryBlocked reason=closingOutboxMissingCheckpoint`。
- 该历史任务未被清理、猜测或重放；本轮新 Live 使用独立 session/batch，未受其串场影响。
- 当前状态：**残余风险**。应单独诊断历史记录的形成路径和可恢复边界。

## 4. 尚不能下结论的观察

- 为回查已存在事实而进行的一次文字问答和一次新 Live，各生成 1 条 pending experience candidate。
- 本轮未打开其写入预览，也未审核，无法仅凭候选数量判断最终 operation 是 duplicate、addEvidence 还是其他合法操作。
- 因此“新增两条是否语义重复”保持 **NOT_RUN**，不得直接判 PASS 或 FAIL。
- 首场会后曾出现一次兼容 `/kb/extract` 502，Owner Truth 的 end/ack/admit 与候选生成仍成功；后续同类请求为 200。本轮不把该相邻错误认定为 B4 根因。

## 5. B4 结论

- B4-1 至 B4-7：保留前序 PASS 记录。
- B4-8：核心会后整理、真实候选读取、来源关联、单条正式写入、投影/向量、文字及 Live 回查均取得真机证据；但候选首次读取失败及第二条审核假成功仍存在，因此 **B4-8 维持 FAIL**。
- B4 整体：**FAIL / NOT READY TO CLOSE**。
- 本轮未修改代码、未部署、未操作第二条 pending 候选、未清理历史、未重放 Dead Letter。

## 6. 证据索引

- `evidence/preflight/device-build.log`
- `evidence/preflight/install-result.json`
- `evidence/device/app-live-filtered.log`
- `evidence/device/key-events.log`
- `evidence/server/api-live-filtered.log`（本机受限原始访问日志）
- `evidence/server/api-live-sanitized.log`（脱敏副本）
- `evidence/server/read-only-integrity-check.txt`

## 7. 建议的下一轮局部修复

1. 修复审核成功提示的所有权：只有当前 Candidate/Proposal/Binding 对应的写请求取得服务端审核回执并完成状态核验，才能显示成功；旧成功状态不能跨候选复用。
2. 第一条审核后必须明确使旧列表中其他候选的确认闭包失效，并引导重新预览；失败时保留候选且显示准确原因。
3. 将 `expiredPolicyCache` 接入现有同主体有界单飞恢复，成功取得新策略后继续同一读取意图；恢复失败才提交 UI 错误。
4. 为 F1/F2 增加真实 BackendClient + FeatureGate + UseCase + UIKit 的反例回归，再进行本地门禁和真机复测。

## 8. 局部回退

本轮没有代码或生产部署变化，无需回退。保留安装构建、日志和只读核对证据即可。后续修复应仅回退对应审核 UI 状态与候选读取恢复改动，不得破坏不可变 binding、CAS、账号隔离或原生 Live 链路。
