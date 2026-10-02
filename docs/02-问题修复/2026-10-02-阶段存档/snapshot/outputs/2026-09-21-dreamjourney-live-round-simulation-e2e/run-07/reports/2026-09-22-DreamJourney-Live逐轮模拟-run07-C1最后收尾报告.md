# DreamJourney Live 逐轮模拟 run-07：C1 最后收尾报告

日期：2026-09-22

结论：`SIM-C1-EVIDENCE-BINDING-01` 已完成同断言红绿闭环。最终状态为 `LOCAL_PASS / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN`。

## 1. 范围与根因

本轮保留 run-06 已认可的 A3、B1/B3、C2、正常短长保存链、音频、B7 和认证成果，只处理受控模型验收中的事实—证据错绑定。

修前规则分别确认“事实属于场景真值”和“引用属于本场用户”，但没有确认二者对应。因此 logical20 的阅读偏好事实由 turn 1 支持时正确；只把引用改为 turn 3 的纯问题后，support 仍返回 supported，relation 仍返回 duplicate，后置校验也放行。

本轮没有发现产品保存链业务缺陷，没有修改 iOS 或后端生产业务源码。

## 2. 局部修改

1. `DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`
   - 将独立场景真值从“事实 key 集合”改为“事实 key → 允许证据 turn 集合”。
   - 同时校验 `sourceTurnIndices`、`_sourceEvidenceRanges` 和 `evidenceFragmentIds` 与对应事实的关系。
   - 仅在受控关系检查已判定 duplicate/supplement/correction/retraction 后继承证据谱系；无关系的用户发言不会进入允许集合。
   - 保留分页责任范围、完整 turn 与精确 fragment 两种合法输入。
2. `run-07/tools/cap15_round_simulation_server.py`
   - 后置 Provider 请求校验使用同一事实—证据关系。
   - 保留跨批双位置重复，并按已验证关系继承补充、纠正和撤回来源。
3. `run-07/tools/provider_wrong_binding_probe.py`
   - 固化四项正反断言，并要求错误样本实际命中 transport 1 次后才拒绝。
4. `run-07/tools/run_round_simulation.py`
   - 绑定 run-07 证据目录；最终严格执行 `short→20→short→65`。

完整差异保存在：

- `artifacts/backend-validation-harness.patch`
- `artifacts/run06-run07-harness.patch`

## 3. 红绿证据

### 修前

`red/provider-wrong-binding-before.json`

- support 正确 turn 1：accepted。
- support 错误 turn 3：错误地 accepted，postflight 也 accepted。
- relation 正确 turn 1：accepted。
- relation 错误 turn 3：错误地 accepted 并返回 duplicate，postflight 也 accepted。
- 四项均实际命中 transport 1 次。

### 修后

`green/provider-wrong-binding-after.json`

- 两项正确对照仍 accepted，postflight accepted。
- 两项错误引用均在受控 transport 边界 rejected，postflight 也 rejected。
- 错误样本均 `transportDelta=1`，不是被输入上限、格式错误或未调用边界提前挡住。

第一次集成运行还揭示验收规则误伤合法纠正谱系：纠正后的事实保存 `[1,19,119]`，初版规则只允许 `[119]`。修正为“已判定关系才继承谱系”后，相同错误引用负例继续拒绝，完整 logical20 通过。

## 4. 最终本地验证

### 最终顺序与模拟器

命令：

```bash
env PYTHONDONTWRITEBYTECODE=1 \
  /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  tools/run_round_simulation.py \
  --admin-dsn postgresql://gaominge@127.0.0.1:55520/postgres \
  --scenario all
```

结果：PASS。`artifacts/runner-complete.json` 记录顺序为：

1. `logical20-short`
2. `logical20`
3. `logical65-short`
4. `logical65`

四份 xcresult 均 1/1 PASS、0 failure：

- `green/logical20-short.xcresult`：1.388 秒
- `green/logical20-long.xcresult`：28.581 秒
- `green/logical65-short.xcresult`：1.389 秒
- `green/logical65-long.xcresult`：58.857 秒

`build-for-testing`：PASS，见 `logs/build-for-testing.log` 和 `builds/round-simulation-build.xcresult`。

### 业务链结果

| 场景 | 会话 | 候选 | 审核后正式记忆 | 关键保持性 |
|---|---:|---:|---:|---|
| short→logical20 | short 4 总回合；long 220 总回合/110 用户轮 | short 1；long 4 | 共 5 | 候选真实 iOS 可见、隐藏负例、401 恢复、跨批重复 `[7,189]`、纠正谱系 `[1,19,119]` |
| short→logical65 | short 4 总回合；long 300 总回合/150 用户轮 | short 1；long 17 | 共 18 | 候选真实 iOS 可见、隐藏负例、未知写只读核实、跨批重复 `[7,221]` |

原始结果：

- `green/logical20-full-chain.json`
- `green/logical65-full-chain.json`

### 后端专项

- `tests.test_owner_truth_live_long_memory_pipeline` + `tests.test_owner_truth_candidate_extraction_worker`：92/92 PASS，见 `green/backend-unit-regression-final.log`。
- 独立 PostgreSQL 正式链：PASS，schema head `0122`；short 1、long 40、f65 149、正式记忆 190；存储重建读取、命令去重和 HTTP admission 均通过，见 `green/backend-live-formal-postgres-smoke.json`。
- run-06 既有伪事实、缺证据、关系伪事实、正文替换和跨阶段身份探针在最终规则下继续通过，见 `green/provider-input-probe-result.json` 与 `green/cross-stage-probe-result.json`。

## 5. 指纹

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 最终 source fingerprint：`e9da79ae9461fe50e94c05eda772ab75037a60e8ae89da2c9ebb48aa10821d9d`
- host executable SHA-256：`6d8efbd303bdb3c673670d3069d94c783fac7f5be6460277f70ecc7eac870f3e`
- test bundle executable SHA-256：`9257bb6ec79264c3dee83a2b6cf3713ff570f36805c16b26558e22fce53e7665`
- xctestrun SHA-256：`3ae6e6077a474a510fc4c511c7861a625844629ae2a0e73f106d771484ff5f5c`
- 后端受控验收脚本 SHA-256：`c4aea2c7aa6188600190d07eae9fb447b76c064642384e347fe9ac353aa7d304`

完整文件指纹见 `artifacts/final-fingerprints.txt`，构建绑定见 `artifacts/source-build-manifest.json`。

两端工作区原有未提交修改均保留；本轮未 reset/clean、未 commit/push。

## 6. 状态与未执行项

### 本地状态

`LOCAL_PASS`

C1 错误引用已关闭；A3、B1/B3、C2 及正常短长链保持通过。

### NOT_RUN

- 真实 Provider：`NOT_RUN`（按要求未调用）。
- iPhone/物理 20 分钟/物理 65 分钟：`NOT_RUN`（按要求未连接或等待手机）。
- 部署与生产数据：`NOT_RUN`。
- 本轮产品发布：不需要；没有产品业务源码变更。

## 7. 回退

若需回退本轮，只回退以下验收侧代码块：

1. 受控适配器中的 `trusted_fact_evidence`、证据索引/range/fragment 对应校验及关系谱系登记。
2. run-07 后置验收器中的 `expected_fact_evidence` 和 `controlled_relation`。
3. run-07 专用探针与报告目录。

不得回退或删除已有产品保存链、长场分批整理、认证恢复、未知写保护、B7 或音频修改。
