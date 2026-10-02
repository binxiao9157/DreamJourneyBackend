# DreamJourney B4 更正预览与真机闭环复测报告

日期：2026-09-14\
结论：`CORRECTION_FLOW_PASS / B4_OVERALL_FAIL`\
失败原因：正式记忆列表在发布策略缓存过期时不能自行执行有界只读恢复。

## 1. 版本与边界

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，保留现有未提交修改。
- iOS 可执行文件 SHA-256：`9c037cdd137fbdca0c59066b298e0928bf759388eb979a7c28101ddc5c46c344`。
- Bundle ID：`com.gaominge.dreamjourney.app`；原位覆盖安装，未卸载、未清缓存。
- 后端 HEAD 基线：`a25b993922fc90dde1e689d19e51becb68fccdbf`，部署的是包含未提交修复的独立无密钥源码构件，不宣称为 Git HEAD 部署。
- 生产 API：`dreamjourney-b4-correction:20260914-0055`，健康、重启数 0。
- 数据库 schema head：`0121`；本次无迁移、无历史清理、无 Dead Letter 重放。
- 两个 decision-result 路由已随新 API 发布并通过鉴权分类探针；本次明确成功写入未触发未知结果查询。
- 用户亲自执行审核写入；后台仅做只读状态核验。

## 2. 真机执行结果

| 场景 | 结果 | 证据 |
|---|---|---|
| 候选列表真实刷新 | PASS | UI 40→40；同一 trace 完成门禁、taskResumed、HTTP 200、typedDecode、UI commit，候选 40 条 |
| “陈鑫”候选详情 | PASS | 基于正式记忆修订 69；正文、记忆线索及三个操作入口正常 |
| “陈鑫→晨星”更正预览 | PASS | HTTP 200、typed decode、`correctionBinding` 校验通过；前后正文正确，无占位符或持续加载 |
| 二次确认前零写入 | PASS | 预览完成后停留在二次确认页，用户未确认前未执行最终审核写 |
| 用户最终更正确认 | PASS | 单次确认后自动返回；候选 40→39；原候选消失；无失败或结果未知 |
| 后端正式写入事务 | PASS | candidate/receipt 均为 `corrected`；更正审计存在；revision 69→70；新 MemoryVersion 为 current |
| 投影与向量 | PASS | memory projection revision 70 ready；search document ready；embedding job ready |
| 正式记忆首次读取 | FAIL | 策略缓存过期时在客户端门禁前失败，未启动正式记忆 GET |
| 候选列表策略过期恢复 | PASS | 现场记录 `expiredPolicyCache`，attempt 2 策略请求 HTTP 200 后自动继续候选 GET 200，UI ready 39 条 |
| 策略恢复后正式记忆回查 | PASS | 可搜索“晨星”；正式内容正确；第一版，时间 2026-09-14 00:55；无重复或加载异常 |
| 文字问答回查 | PASS | DeepSeek 回答当前代号为“晨星”，未提旧值；`memoryGrounding=grounded`；无朗读 |
| 新 Live 回查 | PASS | sessionSnapshot memoryRevision 70；两次回答“晨星”；有声音；插话立即停止并自动恢复聆听 |
| Live 会后终态 | PASS | 手动停止后“本次没有需要整理的表达”；同场重复 ended receipt 被忽略；无新增事实符合预期 |

## 3. 新缺陷

### FM-POLICY-01：正式记忆读取缺少发布策略过期恢复

**现场反例**

1. 候选审核和写入成功，正式记忆 revision 已是 70。
2. 发布策略缓存到期后进入正式记忆列表。
3. 页面显示“正式记忆读取失败，功能请求已被发布策略拦截”。
4. 该次正式记忆请求在发送前终止，无 HTTP 状态。
5. 返回候选列表刷新时，候选读取成功执行 `expiredPolicyCache → policy refresh → candidate GET`。
6. 紧接着再次进入正式记忆列表即可读取成功。

**代码证据**

- `fetchOwnerTruthCandidateInbox` 已具备账号租约、共享预算、策略刷新、单飞和继续原 GET 的有界恢复。
- `fetchOwnerTruthFormalMemories` 仅同步调用 `requestFeatureDecision`；决策不允许时直接返回 `featurePolicyDenied`，没有复用上述只读恢复合同。

**影响**

- 不影响本次正式记忆写入、投影、向量、文字问答或 Live 快照。
- 用户在策略 TTL 到期后直接进入正式记忆列表会看到失败；先刷新候选列表后暂时恢复。
- 该临时路径仅用于定位，不视为产品修复。

**建议局部修复**

- 为正式记忆列表和详情读取接入与候选读取一致的账号作用域、单飞、有界策略恢复及旧回调隔离。
- 只允许 GET 自动恢复，不增加任何审核或正式记忆写请求的自动重试。
- 增加真实 UIKit + FeatureGate + BackendClient + URLProtocol 的过期策略红绿反例，并覆盖超时、账号切换和迟到响应。

## 4. 状态修订

- B4 更正预览双重绑定：PASS。
- “陈鑫→晨星”真实更正写入：PASS。
- 正式记忆写入、投影、向量、文字与新 Live 回查：PASS。
- 候选列表首次策略过期自动恢复：PASS（本轮取得现场证据）。
- 正式记忆读取策略过期自动恢复：FAIL（新缺陷 FM-POLICY-01）。
- B4-1 至 B4-7：保留既有 PASS。
- B4-8：本轮没有创建并关联新的会后候选，历史 FAIL 不由本轮改写。
- 历史三条候选重复性、新查询候选语义及 F3 实际影响：NOT_RUN。

## 5. 证据索引

- `evidence/preflight/preflight-summary.txt`
- `evidence/device/xcodebuild-device-escalated.log`
- `evidence/device/install.log`
- `evidence/device/correction-write-readonly-status.txt`
- `evidence/device/formal-memory-policy-expiry-failure.txt`
- `evidence/device/deployment-postcheck.txt`

设备控制台只用于现场观察；报告仅摘录白名单状态、计数、修订号和随机关联哈希，不保存正文、转写、凭据、请求头或原始业务标识。

## 6. 回退与后续

- iOS 局部回退：仅回退 correctionBinding/VerifiedCorrectionPreview 本轮相关改动并重新执行原红绿测试；不得回退账号隔离、CAS、typed 差异或未知写保护。
- 后端回退构件：`dreamjourney-b4-rollback:20260911-0200`。回退前需确认新客户端兼容窗口；不能在客户端依赖新 correctionBinding 时单独回退 API。
- 下一步应先为 FM-POLICY-01 输出局部设计并本地修复验证，之后重新部署匹配版本，再复测正式记忆直接进入场景。
- 未授权 commit/push，本轮未执行。
