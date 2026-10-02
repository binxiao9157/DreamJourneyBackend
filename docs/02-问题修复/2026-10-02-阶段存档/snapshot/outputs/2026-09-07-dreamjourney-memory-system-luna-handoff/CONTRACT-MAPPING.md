# DreamJourney B 增强记忆：需求、代码与测试合同映射

更新日期：2026-09-08。此表描述本次本地实现，不代表数据库 migration、真实模型或真机已运行。

## W00-W09 映射

| 工作包 | 主要合同 | 实际代码入口 | 本地测试证据 | 环境级状态 |
|---|---|---|---|---|
| W00 基线与质量种子 | 04 的场景种子、红测、证据分级 | `outputs/.../BASELINE.md`、`07-W00-W09-实施跟踪与验收证据.md`、`tests/test_owner_truth_b_memory_quality_fixture.py` | 200 条合成中文检索场景；K01/K02/K05/K20 及精度负例 | CODE/UNIT；DB/MODEL/DEVICE NOT RUN |
| W01 类型化事实与来源 | V5 `factType`、dimensions、claim subject、provenance、时间与文档/家人入口 | `app/domain/owner_truth/ontology.py`、`app/async_effects/owner_truth_candidate_extraction_worker.py`、`app/services/owner_truth_candidate_extraction.py` | `test_owner_truth_domain`、`test_owner_truth_candidate_extraction_worker`、`test_owner_truth_family_contribution` | CODE/UNIT；旧数据真实升级 NOT RUN |
| W02 可靠会话与整场整理 | `productSessionId`、客户端序号、关闭水位、幂等、唯一批次与重连隔离 | `app/domain/owner_truth/conversation.py`、`app/services/owner_truth_conversation.py`、`app/main.py`、`db/migrations/0109_*`、`0110_*`、iOS `OwnerTruthContracts.swift` 与 `EchoViewController.swift` | `test_owner_truth_conversation`、`test_owner_truth_interview_input_api`、`test_owner_truth_live_delivery_migration_contract`、`test_owner_truth_product_session_migration_contract`；iOS 选定合同测试 | CODE/UNIT；断网/杀进程/真机 NOT RUN |
| W03 匹配与 ChangeSet | add/addEvidence/refine/temporalChange/correct/dispute/duplicate/noPersonalFact、目标版本与依赖 | `app/domain/owner_truth/memory_changeset.py`、`memory_changeset_activation.py`、`person_memory_model.py` | `test_owner_truth_memory_changeset`、`test_owner_truth_memory_changeset_activation`、`test_owner_truth_domain` | CODE/UNIT；真实 embedding 比对 NOT RUN |
| W04 审核与原子写入 | base revision、CAS、原子依赖组、审核回执与投影解耦 | `app/services/owner_truth_candidate_review.py`、`app/domain/owner_truth/candidate_decisions.py`、`db/migrations/0108_*`、iOS `MemoryArchiveViewController.swift` | `test_owner_truth_memory_changeset_review`、`test_owner_truth_candidate_review_api` | CODE/UNIT；真实 PostgreSQL 并发/回滚 NOT RUN |
| W05 统一资格、检索与文字上下文 | 当前事实资格、对象约束同义词、服务端文字上下文、引用一致性 | `app/domain/owner_truth/formal_fact_eligibility.py`、`search_documents.py`、`owner_truth_context_materialization.py`、`owner_truth_echo_conversation_context.py`、`db/migrations/0111_*`、`/echo/answers` | `test_owner_truth_formal_fact_eligibility`、`test_owner_truth_memory_search`、`test_owner_truth_echo_conversation_context`、`test_echo_answer`、200 条质量集 | CODE/UNIT；pgvector/DeepSeek NOT RUN |
| W06 Live 事实背景 | 限定正式快照、coverage、checkpoint、authority epoch、context hash；不改原生音频状态机 | `app/services/formal_memory_conversation_snapshot.py`、`DreamJourneyBackendClient.swift`、`OwnerTruthContracts.swift` | `test_formal_memory_conversation_snapshot`；iOS 快照绑定/拒绝篡改合同测试 | CODE/UNIT；真实火山 SDK 注入、热更新、打断真机 NOT RUN |
| W07 整体人生记录、自传与内容记录 | 段落证据、概览与自传只读同一事实源、原始记录可追溯 | `app/services/owner_truth_person_memory_profile.py`、`app/services/narrative_project.py`、`app/api/narrative.py`、iOS `OwnerTruthFormalMemory.swift` | `test_owner_truth_person_memory_profile`、`test_owner_truth_formal_memory`；iOS 正式记忆 DTO 测试 | CODE/UNIT；长文 UI/真机视觉 NOT RUN |
| W08 校正、迁移与撤权 | 兼容旧载荷、dry-run/backfill、撤权使派生读取失效 | `app/services/owner_truth_derived_memory_access.py`、`owner_truth_legacy_*`、`owner_truth_person_memory_profile.py`、`narrative_project.py` | `test_owner_truth_legacy_*`、`test_owner_truth_projection_rights_fence` | CODE/UNIT；真实 migration/恢复演练 NOT RUN |
| W09 集成、DFX 与报告 | 合成闭环、隐私化操作指标、错误边界和最终报告 | `tests/test_owner_truth_b_memory_quality_fixture.py`、`app/observability/operation_metrics.py`、`app/main.py`、本目录 `FINAL-REPORT.md` | 208 项核心回归 + 71 项补充回归；`test_operation_metrics`、`test_operation_metric_coverage` | CODE/UNIT；SLO/压测/生产观测 NOT RUN |

## 关键 API 与存储合同

| 范围 | 生产者 | 消费者 | 存储/迁移 | 失败或拒绝语义 |
|---|---|---|---|---|
| 类型化候选事实 | 整理 Worker、素材入口、会话结束整理 | 审核、正式投影、搜索 | 既有 candidate/memory 载荷兼容 V5 | 来源、主体、时间不足时保留 unknown/待审核，不自动提升 |
| ChangeSet | 候选整理和语义归并 | 审核/激活 | `0108_owner_truth_memory_changesets` | base revision 过期、依赖组不完整、重复命令均返回可判别回执 |
| Live 会话投递 | iOS 持久发送箱 | `/interview-sessions/.../messages`、`end`、outcome | `0109_owner_truth_live_delivery_watermark`、`0110_owner_truth_product_session_isolation` | 缺序号/未排空关闭返回明确 pending 状态，不能提前触发整场整理 |
| 文字上下文 | `/echo/answers` | DeepSeek 答案编排 | `0111_owner_truth_echo_text_context` | 只保留有限已完成回合，过期/不同产品会话不可读取，不写正式事实 |
| 正式事实资格 | 当前 memory projection | 文字检索、Live 快照、概览、自传 | 读取时计算；不新增第二正式库 | 未就绪、已替代、争议、无权或撤权版本不进入上下文 |
| Live 快照 | 后端实时 token/config | iOS RuntimeConfig | 无客户端可写事实存储 | 版本、epoch、checkpoint、hash 或 schema 不一致则 iOS fail closed |
| 整体人生记录 | 正式事实投影 | 记忆档案/自传素材 | 派生 profile/章节证据 | 写作稿和回答不可回写正式事实；撤权后拒绝派生读取 |

## 场景种子追踪

| 场景 | 本次主要覆盖点 |
|---|---|
| K01、K02、K05、K20 | 200 条检索质量集：饮食、学校、历史限定、未知/反误召回 |
| K03、K04、K10、K16 | ChangeSet、时间变化、更正、审核依赖组与 CAS |
| K06、K07、K08、K09、K26 | 家人/文档/未知来源与候选提取链 |
| K11、K17、K25 | 正式投影、人生记录、段落证据与自传读取 |
| K12、K13、K14、K15 | 会话顺序、重放、结束水位、产品会话隔离 |
| K18 | 撤权后搜索、人生记录与自传派生读取拒绝 |
| K19、K23 | 文字事实权限和服务端 recent-turn 上下文隔离 |
| K21、K22 | 本地快照合同已覆盖；真实火山 SDK/真机仍为 NOT RUN |
| K24 | 媒体处理 Worker 与来源/候选链回归 |

## 保留边界

- 不创建平行 `memory-vNext` 或客户端可写的正式记忆库。
- 不让 DeepSeek 的回答、自传稿或火山音频回调绕过候选审核直接写正式事实。
- 不把受控词表的确定性检索称为已完成 pgvector 或模型重排。
- 不以本地合成测试替代真实 PostgreSQL、真实模型、供应商撤权或真机体验证据。
