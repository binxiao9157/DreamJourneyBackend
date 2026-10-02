# LM01-LM32 / LI01-LI10 执行清单

状态说明：`PASS` 仅表示相应本地业务断言已通过；真实 Provider、真机和部署另行标记，不由本表替代。

| 编号 | 状态 | 核心断言与证据 |
|---|---|---|
| LM-01 | PASS | 12 个独立事实不受旧 8 条上限截断；`test_lm_01_twelve_independent_facts_are_not_capped_at_eight`，定向日志。 |
| LM-02 | PASS | 短场补充、候选、审核、Memory/Version、重建幂等；PG formal chain 与 candidate UIQA。 |
| LM-03 | PASS | 20 分钟、120 用户/241 总回合、超过 30k 字；大于 32 候选分页和审核链有界。 |
| LM-04 | PASS | 逻辑 65 分钟、150 用户/301 总回合、61 分钟后纠正；Realtime 3599/3601/3900/7201 和 epoch/账号隔离。 |
| LM-05 | PASS | 单 turn 超 4000 字、20 个 facts，子片唯一且覆盖完整。 |
| LM-06 | PASS | 饱和、length、超预算、最小片失败均 typed；不静默截尾或假 empty。 |
| LM-07 | PASS | 300 独立事实，关系页 8×32；228 relation、304 总 Provider 调用，低于 512。 |
| LM-08 | PASS | 首尾重复折叠为一份最终事实并保留证据。 |
| LM-09 | PASS | 跨批补充、纠正、撤回关系正确；被替代/撤回值不发布。 |
| LM-10 | PASS | 同实体不同事件和过去/现在偏好不误合并。 |
| LM-11 | PASS | relation batch 完整页回执、受控 HTTP typed decode；漏比/不确定保持 unresolved。 |
| LM-12 | PASS | B7 纯问题、引述、助手建议、混合问句回归通过。 |
| LM-13 | PASS | facts/facets/dimensions/qualifiers/affect typed 保持，不把 unknown 推断为已知。 |
| LM-14 | PASS | 漏 atom、伪 superseded、悬空/循环关系阻止发布；完成 unit 重建不重调 Provider。 |
| LM-15 | PASS | 每个候选与属性有原文 support，摘要不能替代证据；证明版本随关系变更失效。 |
| LM-16 | PASS | 会中形成私密草稿，候选/Source/ACK/admit 不提前创建，append 不等待模型。 |
| LM-17 | PASS | 预整理关闭、积压或失败不阻断采集；关闭后用同算法补齐，不回退旧整场路径。 |
| LM-18 | PASS | 尾部不足一批和最后 partial 被规划并保留真实覆盖状态。 |
| LM-19 | PASS | Source、输入哈希、顺序和前缀绑定；不匹配时旧草稿不能发布。 |
| LM-20 | PASS | 重复唤醒复用同 Run/unit/预算，不重复 Source 或候选。 |
| LM-21 | PASS | job 等 Run 时不领 lease、不烧 attempt、不走旧提取；ready/failed 均唤醒。 |
| LM-22 | PASS | PG 中途提交失败整批回滚；重试一组候选、无新增 Provider 请求。 |
| LM-23 | PASS | 两 Worker/旧 lease 隔离；单 Run 最多两个 Provider unit 并发，第三个无租约。 |
| LM-24 | PASS | 暴露前后及落盘前失败保守记账；未知 Live 业务写不重发。 |
| LM-25 | PASS | 每 unit 一次 extra、全场 32 共享恢复；PG 重建不清零、耗尽不提交候选。 |
| LM-26 | PASS | 拆分树和关系分页有界，父子覆盖准确，失败不无限增加 planned calls。 |
| LM-27 | PASS | authority epoch、账号、FeatureGate 变化阻断迟到提交，不跨账号污染。 |
| LM-28 | PASS | PG 重建恢复 Run、预算、关系、manifest；只用隔离合成数据。 |
| LM-29 | PASS | 短场 1 条、长场 5 条候选审核后共 6 条正式记忆；拒绝/幂等/重建链保持。 |
| LM-30 | PASS | 新旧 pipeline、非 Live 和默认 CLI/Worker 装配回归；新 Run 不误走旧 8 条。 |
| LM-31 | PASS | Live 专用 admit 真实构造完整 Source；普通 20001 字仍按旧合同拒绝。 |
| LM-32 | PASS | 100k 用户字、200k 总字、2 MiB 边界及越界；客户端 metadata 不能伪造 profile。 |
| LI-01 | PASS | 超过旧 6 次 GET 后同场可完成；24 GET 有界且零业务 POST；`li01-li02.xcresult`。 |
| LI-02 | PASS | 180 秒到期保留坐标、停止轮询；后台完成后新鲜 GET 恢复；`li01-li02.xcresult`。 |
| LI-03 | PASS | 页面重进、冷启动、旧 round/poll 迟到不释放或覆盖新 round；OwnerTruth 530。 |
| LI-04 | PASS | GET 超时、磁盘失败、后台 typed failure 分离；恢复依据和原文保留。 |
| LI-05 | PASS | 服务端长 profile 3599/3601/3900 合法、7201 关闭；默认 profile 与客户端伪造保护。 |
| LI-06 | PASS | 1 GiB 累计真实编码流量边界允许，下一字节拒绝；非空帧计量。 |
| LI-07 | PASS | 连接恢复、迟到 final、generation 和 canonical outbox 保持同场水位，不宣称自动续连。 |
| LI-08 | PASS | 自然长回答、主动打断和恢复聆听的音频租约保持；Audio 5/5。 |
| LI-09 | PASS | typed 阶段与脱敏日志；新增诊断无正文、token、密钥或原始业务哈希。 |
| LI-10 | PASS | 大于 32 候选分页一致；UIKit/UIQA 审核、正式记忆、版本历史和发布预览通过。 |

## 证据索引

- 后端定向：`../evidence/backend-targeted-tests.log`
- PostgreSQL 并发/回滚：`../evidence/postgres-concurrency-rollback.log`
- PostgreSQL 正式记忆链：`../evidence/postgres-formal-memory-chain.log`
- PostgreSQL 合同恢复：`../evidence/postgres-contract-retry.log`
- OwnerTruth：`../evidence/ownertruth-530.xcresult`
- LI01/LI02：`../evidence/li01-li02.xcresult`
- 音频：`../evidence/audio-5.xcresult`
- UIQA：`../uiqa/candidate/`、`../uiqa/formal/`
- 构建：`../build/simulator-build-escalated.log`、`../build/generic-ios-build-escalated.log`
