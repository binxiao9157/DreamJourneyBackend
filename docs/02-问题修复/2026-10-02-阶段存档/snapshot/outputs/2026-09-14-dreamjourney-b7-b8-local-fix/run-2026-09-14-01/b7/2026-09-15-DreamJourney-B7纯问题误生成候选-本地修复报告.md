# DreamJourney B7 纯问题误生成候选本地修复报告

## 1. 状态

`B7_A_LOCAL_INCOMPLETE`

本地实现、受控语义红绿测试和相关回归已完成；隔离 PostgreSQL、真实 Provider、B7 专项 BackendClient/FeatureGate/UIKit 组合尚未执行，因此不标记 `READY_FOR_B7_DEVICE_RETEST`。现场缺陷继续为 `FAIL`，修复版真机复测为 `NOT_RUN`。

## 2. 基线与边界

- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 两端原有未提交修改均保留；未 reset/clean、未 commit/push。
- 本问题未改 iOS、正式记忆、候选审核、Live 音频或文字问答链路。
- 未部署 Candidate Worker，未访问生产数据。

## 3. 已确认根因

修复前 Live Worker 在真实 parser/extractor 路由后，直接信任 organizer 返回的记忆草案。现有 Source index、V5 enrich 和 CandidateProposal builder 能验证结构与引用存在，却不能证明草案命题得到用户事实支持。因此，模型把纯问题改写成“用户问过……”时，仍可生成合法结构的 knowledge Candidate。

这不是 UIKit 展示问题，也不是问号字符过滤问题。修复前反例经过真实 SourceExtractor 路由、response parser、Live extractor、V5 CandidateProposal 和 Worker，两个纯问题生成 2 条候选。

## 4. 修改

| 文件 | 函数/区域 | 局部修改 |
|---|---|---|
| `app/services/owner_truth_live_memory_support.py` | `validate_live_memory_support` | 新增版本化、fail-closed 的整场支持合同；校验全部 user turn 覆盖、角色、speech act、草案 verdict、证据绑定、遗漏事实和纠正淘汰关系 |
| `app/services/deepseek.py` | `DeepSeekLiveMemoryOrganizationProxy`、`request_support_review`、`build_support_prompt` | Live organizer prompt 升级并增加独立整场语义复核请求；纯查询、助手答案、含糊附和不可作用户事实证据 |
| `app/async_effects/owner_truth_candidate_extraction_worker.py` | `LiveMemorySupportReviewProvider`、`ModelAssistedOwnerTruthLiveConversationExtractor.extract` | 所有 chunk 草案先汇总，再进行整场支持复核；只有 supported 草案才进入既有 V5 builder；空结果也必须通过遗漏事实检查 |
| `tests/test_owner_truth_candidate_extraction_worker.py` | 10 个 `test_b7_*` 场景 | 覆盖纯问题、助手答案偷用、同轮/跨 chunk 纠正、事实遗漏、问句后缀、历史引述、事实+查询、含糊“对”和 uncertain fail-closed |

没有修改外部 Candidate schema、Source ID/hash/version、authority、lease、CAS、Binding 或审核合同。

## 5. 红绿证据

### 修复前红例

- 证据：`b7/red/b7-r01-current-builder.json`
- 结果：两个纯问题经真实 builder/Worker 形成 2 条 knowledge Candidate，产品门禁 `FAIL`。
- 控制项：空 organizer 输出仍为 0；既有 assistant index 结构守卫通过。这证明缺口在语义支持，而不是 builder 完全失效。

### 修复后绿例

- `b7/green/b7-targeted-10-final.log`：10/10 PASS。
- `b7/green/backend-worker-focused-final.log`：45/45 PASS。
- `b7/green/backend-70-tests-final.log`：70/70 PASS。
- `b7/green/backend-compileall-final.log`：PASS；最终再次使用隔离 pycache 执行也通过。

## 6. B7 矩阵

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| B7-R01 纯问题错误草案 | PASS | 红例 2 条；相同业务断言绿测 0 条并合法 noChange |
| B7-R02 助手答案借 user index | PASS | `test_b7_assistant_answer_cannot_be_smuggled...` |
| B7-R03 同轮/跨片纠正 | PASS | whole-session 与 cross-chunk 两条测试只保留最终“晨星” |
| B7-R04 空结果漏事实 | PASS | omitted fact-bearing turn 明确失败，不伪装 noChange |
| B7-T05 问句、反问、事实后附问号 | PASS | 纯问题、uncertain 和 question suffix 正反例 |
| B7-T06 历史引述疑问 | PASS | 只保留“去了图书馆”，不生成未知开学日期 |
| B7-T07 同轮 query+事实 | PASS | 只生成 2021 搬到苏州的用户事实 |
| B7-T08 含糊附和/明确自述 | PASS | “对”不能采纳助手的杭州命题；现有明确自述保持测试通过 |
| B7-T09 多轮、跨 chunk、整场归并 | PASS | 受控多 chunk 真实 extractor，仅保留最终纠正；既有长文本分片测试通过 |
| B7-T10 Provider/合同异常 | NOT_RUN | 本地已有 transport/invalid/uncertain fail-closed 子项通过；未完成真实 Provider 401/429/超时全矩阵 |
| B7-T11 非法引用与 Source 绑定 | PASS | 相关 candidate extraction/proposal 回归包含严格拒绝，70 项通过 |
| B7-T12 iOS final/outbox 保持性 | PASS | iOS 603 项包含 Live role、稳定 sequence、outbox 作用域测试；未改变对应代码 |
| B7-T13 assistant context | PASS | iOS `testInterviewLiveAssistantTurnCarriesRole...` 等保持测试通过 |
| B7-T14 冻结边界/重复关闭 | PASS | iOS 关闭幂等及 B8 定向保持测试通过 |
| B7-T15 隔离 PG 纯问题全链 | BLOCKED | 本机无 Docker/psql，未连接生产替代 |
| B7-T16 隔离 PG 新事实/纠正 | BLOCKED | 同上 |
| B7-T17 PG 租约/事务/幂等 | BLOCKED | 同上 |
| B7-T18 GET/FeatureGate/UIKit 组合 | NOT_RUN | 没有用普通 iOS 总数冒充 B7 专项候选集合证据 |
| B7-T19 第二进程 query noChange | NOT_RUN | B8/B6 UIQA 不能替代 B7 语义专项 |
| B7-T20 正式事实+未审核 Source+新 Live | NOT_RUN | 需要后续授权的设备闭环 |
| B7-T21 真实 Provider 固定语料多次运行 | BLOCKED | 项目授权配置中 Provider key 未配置 |

## 7. 构建与保持性

- iOS 完整 `DreamJourneyTests`：603/603 PASS，`combined/ios-full-final.xcresult`。
- 通用模拟器构建：PASS，`combined/builds/simulator-final.xcresult`。
- 通用 iOS 设备目标编译：PASS，`combined/builds/generic-ios-device-final.xcresult`。
- 两端 `git diff --check`：PASS。
- 安全日志扫描只命中依赖源码/文件名中的 AccessToken/Authorization 类型名，未发现凭据值。

## 8. 指纹

见 `final/backend-b7-source-sha256.txt`。关键值：

- support validator：`42989d4f47edd5574602c47dfeb98ddb337c59a281e29ae509121242e34ab394`
- DeepSeek adapter：`e0e33d0a611737988fea6365219c644854c8785776cee7dab2c0203a1694baa8`
- Worker：`985dd531110dfef47c8a2df758af7c0dc54bfeeee045c28a4dfb0aafa5131fd3`
- tests：`9a497954f58a6fb19de71c20972856d1ce89cbd2fa6a030a2c55ed7a51ad1888`

## 9. 部署判断与风险

- B7 修复需要后续授权发布 Candidate Worker；本轮未部署。
- API、Projection Worker 和数据库 schema 无本问题新增修改，不因 B7 单独要求发布或迁移。
- 真实 Provider 质量、耗时、成本和心跳预算未验证；部署前必须完成 T21。
- 隔离 PG 全链和 B7 专项 UIKit 未完成，不能宣布本地或现场闭环。
- 新 Worker 不自动追溯、删除、隐藏或重放历史候选/Dead Letter。

## 10. 局部回退

仅回退上述四个 B7 代码/测试块：移除 support validator 接线并恢复原 Live prompt/version。回退不触碰 Source、历史候选、正式记忆、租约、审核或数据库；但会恢复纯问题误纳风险，因此只能作为停止发布的代码回退，不可用于生产数据修补。
