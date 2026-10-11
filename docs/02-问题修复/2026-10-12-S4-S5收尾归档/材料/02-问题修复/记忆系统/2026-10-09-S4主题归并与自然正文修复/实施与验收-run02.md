# S4 真实表示差异与自然正文修复：run-02

日期：2026-10-09。范围：用户要求全面分析上轮未通过原因，修订原方案后严格开发，增强本地与真机环境一致性。

**结论：LOCAL_PASS / REAL_PROVIDER_SAMPLE_PASS / DEPLOY_NOT_RUN / DEVICE_NOT_RUN。S4 总体仍待新版本真机与阅读质量确认，不能进入已完成状态。** 本轮没有操作手机、用户数据、生产数据库、部署、commit 或 push。真实模型验证仅使用已授权合成内容，在服务器临时 Python 进程调用已有配置，没有更改服务器文件或服务进程。

- [原路径修订设计，第6节](<修复设计.md>)
- [上一轮真实失败及最小对照](<部署与真机验证.md>)
- [本轮证据目录](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02>)

## 1. 这次查清的原因

### 1.1 主题归组把模型字段当成唯一语义

上轮手机输入“我每周日下午在家练习折纸”与“我把折纸放在白色收纳盒里”。模型把后一条 object 填为“白色收纳盒里”，即位置，而非折纸。旧代码只在 object 为空时补文字锚点；位置字段与“折纸”冲突便退回独立段落。

同一句型中 object 可能为空、表示物品或表示位置，不能依赖模型总给一种结构。现场最小对照只移除后一条 object，两条就能归组。上一轮本地测试把完整事实降为 summary，正好丢了出错字段；这是测试保真不足，不能归咎于模型容量或超时。

### 1.2 年份关系有两个同时存在的表示差异

上轮旧事实 object 为“喝咖啡”，新事实为“咖啡”；新 validTime 同时有 start=end=2023。原规则按字段形式拒绝了同一字面喜好的时期关系。现场对照表明，只处理对象或只处理 end 都不足，两个差异一起处理才进入 temporalChange。

修复只规范比较副本；明示单年、完整喜好句式和其他业务限定共同约束等价。不同年份依然保留独立事实。同年是补证，不能变成任意覆盖正式记忆。

### 1.3 自然成文与事实审核目标不完全相同

本轮重新调用真实组织模型后，上述两种形状再次出现。生产提取器可合法生成候选；确认归组修复后，又发现三种写作验收差异：

1. 原提示词把两条折纸原句改为逗号拼接。audit 认为事实可靠，但质量检查正确拒绝 compositionNotIntegrated。
2. v5 用“则”衔接并合成一句，audit 指出可能把练习的时间扩大到收纳。保留拒绝，不强改为通过。
3. v6 折纸及咖啡通过，但“大学时偶尔喝咖啡＋打算周末喝、还没有开始”照抄原句，仍触发质量回退。

v7 明确“自然段可以有多个句子”，不同限定分别引用并断句，保留时间、计划、否定；补充语法改写和写作自检。最终固定样例集一次执行通过。未增加生产模型重试次数，未放松事实、引用或质量门槛。不能据有限样例保证今后每次模型输出都自然；失败仍保留忠实正文和正式事实。

## 2. 实际改动与影响范围

仅三个产品文件相对本轮基线改变：

| 文件 | 改动 | 影响与保护 |
|---|---|---|
| `app/domain/owner_truth/person_biography.py` | 明确“我把 X 放在/进/入 Y”中，object 与原句位置 Y 一致时按 X 归主题；算法 v4，提示词版本 v7 | 仅正文投影与派生任务版本；不改正式事实。不同实体、不同物品、代词、其他人、未知位置保持分开 |
| `app/domain/owner_truth/fact_representation.py` | 完整明示喜好句中的动宾/宾语及单年重复起止等价 | 三入口共用关系比较；保留主体、证据、场景、强度、附加字段和真实区间。多旧目标不能按顺序选取 |
| `app/async_effects/owner_truth_biography_worker.py` | writer/audit 指令明确分句限定、自然改写及正反例 | 仍为原有 write/audit 两请求；不改 HTTP 超时、累计预算、租约、恢复策略 |

未改 iOS、SDK、音频、开麦、认证、采集、同步、Live收尾、分页、确认事务、数据库 schema 或检索实现。比较器是共享后端能力，因此不能声称三个入口完全不受影响；已回归其候选、确认、关系和保存路径。

另加入完整现场 fixture、两个真实模型失败 fixture、13项新回归，以及三份 PostgreSQL/默认 HTTP 验证脚本。脚本和源码已放入实际后端仓库工作目录，尚未提交。

## 3. 本地验收如何贴近现场

| 层级 | 实际证据 | 结果与边界 |
|---|---|---|
| 完整现场红绿 | 同一10项断言，修前6失败、4通过；修改后10通过。最终补至13项 | 保留 content/object/qualifiers/semantic/schema/kind，不先把字段改成理想值；两个模型失败及歧义目标保护也加入 |
| 最终实际目录回归 | 209项：49项S4与160项关系、提取、候选确认、人物模型等定向回归 | 全通过；不是后端全量所有测试通过的声明 |
| 完整现场咖啡 PG | 当前129迁移、生产 preview/activate、真实事务、同命令重放、新连接冷读 | add→temporalChange→addEvidence；旧时期完整 payload 不变，同年补证不重写业务内容 |
| 完整现场折纸 PG | 两份完整候选经生产确认服务激活 | 一个主题，两份精确 memoryVersion 证据，非直接插正式记忆凑结果 |
| 新真实组织模型 | 3份 text组织响应、1份 Live组织响应；Live补真实support核验；全部进入生产提取器 | object位置和起止同年再次出现；没有手工规范原响应再验证。不是手机三个入口已重验 |
| 新真实正文与PG | 上述真实提取结果→生产确认→正文Worker→真实writer/audit→落PG→新连接冷读 | 咖啡、折纸均polished；全部事实业务内容和引用一致；连续10次读取不再调用模型 |
| 默认保存链 | 正常API lifespan、真实loopback HTTP路由、默认Worker、受控模型HTTP、本地PG | 独立短场1候选后运行40用户轮逻辑长场40候选；41正式事实、重启幂等和来源冷读通过；51次受控模型请求。不是物理十分钟或真实火山音频 |
| 数据库故障与并发 | 原S4 PG保护脚本14项 | 超时累计预算、过期租约、并发确认、回滚、撤销、跨账号、崩溃恢复、原文质量回退均通过 |
| 泛化与限定 | 最终v7固定4样例真实writer/audit | 陶笛、书法、计划否定、相对时间均通过；不声称概率为零 |

iOS HEAD 为 `40895f77454c6468f93d6092eca50940b0b554bc`，工作区干净，本轮未改。此前真机开麦/音频事实保持历史结论，本轮未重新运行模拟器/声学/手机测试；不把本地后端结果当作这些门禁的新证据。

## 4. 最终真实正文样例

- 咖啡：“2019年和2024年，我都喜欢喝咖啡。”
- 折纸：“每周日下午，我在家练习折纸。我把折纸放在白色收纳盒里。”
- 计划否定：“大学时，我会偶尔喝咖啡。至于周末喝咖啡的打算，我还没有开始。”
- 相对时间：“我打算减少喝咖啡，但还没有开始。那次谈起咖啡时，我提到最近两年偶尔在上午喝咖啡。”

这些是本轮真实返回的例子，不代表用户已确认阅读质量。正文只是正式事实的派生显示，原事实内容没有被这些段落覆盖。

## 5. 失败证据和测试装配修正

- `red.log`：正式现场红测6失败；`red-initial-fixture-error.log`另保留初次 fixture 包装键错误，未算产品回归。
- `pg-real-shapes.log`：原句拼接质量失败；`pg-real-shapes-02.log`：限定范围审核拒绝。对应原始 `prose-*-input.json` 和响应全部保留。
- `real-prose-variants.json`：v6计划否定样例照抄，其他2项通过；v7结果单独保存，没有覆盖它。
- `pg-real-shapes-03.log`：两个正文已通过，测试错误比较完整响应诊断字段失败。`cold-read-differences.json`证明仅 rebuildMode 从full变unchanged、affectedDimensions清空；最终断言除这两个字段外全部一致，而非忽略正文差异。
- 首次 PostgreSQL用户名不匹配、回放导入路径及测试脚本路径/模块配置错误均保留，修正测试环境后复跑；不算业务修复成功证据。
- 本轮正文采样共记录34个真实write/audit请求、28,366 tokens，包含失败与复验，不只计算最终绿色样本。组织/支持调用另存记录，不混入正文token统计。产品预算没有变。

## 6. 最终版本和复现入口

实际后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`。

HEAD：`b30798643c0127011c7ecbabe187bf4a25edfe07`；当前包含原有未提交S4工作及本轮修复，不是新Git提交。

- 修复清单指纹：`df7888ef0a5493209f55e43c7982cd4725b39f166803652258b9a4ff3710ba8d`
- 后端app及迁移源码指纹：`a976e70c7a6a2e207f6b8c8ac20c016162e330035ec0764c15312670a3978c6a`
- [逐文件最终指纹](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/final-fingerprints.json>)
- [本轮独立补丁](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/repair.patch>)
- [实际目录209项结果](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/actual-checkout-regression.log>)
- [完整现场PG最终结果](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/pg-real-payload-period-final-02.log>)
- [真实候选到正文PG结果](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/pg-real-shapes-result.json>)
- [默认短长场结果](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/pg-default-http-short-long-final.log>)
- [真实模型最终固定样例](</Users/gaominge/Documents/liftora/outputs/2026-10-09-s4-topic-prose-repair/run-02/real-prose-variants-v7.json>)

写回前核对2294份基线文件，无并发漂移；2291份已有文件保持不变，另外3份产品文件修复，新增7份测试/fixture/脚本。原有dirty工作完整保留。git diff --check通过。用于隔离测试的PostgreSQL已停止，数据和日志保留。不得为了复跑真实模型脚本盲目重复调用；先明确版本、调用预算和新证据目录。

## 7. 后续真机门禁与回退

用户发起部署后再验证：最终镜像/迁移/功能配置指纹→独立Live短场→独立两条档案折纸事实确认及同主题正文→档案旧年与文字新年时期关系、同年补证→旧正式内容/无关照片不变与冷启动读取→按原阶段计划的自然十分钟和阅读效果确认。每次长场前保留独立短场；不能用本轮40轮逻辑链冒充自然十分钟。新样例需与旧场隔离，测试账号数据清理沿用已授权精确范围和证据备份规则。

回退仅恢复本轮 `before-apply` 三份产品文件并移除本轮新增文件，不 reset 原有工作、不删记忆。schema仍129。历史登录异常、MIC04及其他已记录事项未在本轮关闭。
