# run-05 C/D 独立复核证据

本目录只增加复核脚本和结果，未修改产品、run-05交付或原测试。所有探针仅使用内存对象和 httpx.MockTransport，不建立数据库连接、不请求真实 Provider，不运行手机或重型集成。

## 受控模型输入

`provider-input-probe.py` 导入 run-05 的原受控适配器和后置校验器，并使用原 short/long 场景。`provider-input-probe-result.json` 为实际输出。

- short 原正文替换负例确实进入受控 HTTP：第 3–9 行，AssertionError，transportCount=1。
- long20/65 原负例把 220/300 轮交给单次最多 200 轮的 Proxy，在 organizationInput.inputInvalid 拦截，尚未到 HTTP：第 12–27 行。
- support 请求仅保留 short 的 turn 1/2，删除当前草案引用的 turn 3，并把摘要换为不存在的事实。原适配器仍返回 supported 与 [1,3]，后置校验也通过：第 30–57 行。
- relation 请求把用户证据正文和 incoming 事实替换，原适配器和后置校验仍通过：第 61–78 行。

这证明 C 尚有验收防假通过缺口，不证明产品实际已生成错误候选。有限修复是：在每次 support/relation 实际 HTTP 边界按该页责任范围验证独立事实、实际正文与所需证据，并以这两个负例验证拒绝；长场负例使用实际合法 unit 或引用已有效的 short 负例，记录精确异常 stage/reason 和实际 HTTP 命中。

## HTTP 429/timeout 分类、预算与恢复

本次直接运行已有 `tests.test_owner_truth_live_long_memory_pipeline.OwnerTruthLiveLongMemoryPipelineRedTests` 的 429、timeout 两项 Proxy 分类测试，2/2 PASS。

`transient-budget-probe.py` 复用已有 worker 两项完整预算测试，保留全部原断言，仅在内存 MockTransport 中把原 ConnectError 换为受控 HTTP 429 或 ReadTimeout。4/4 PASS；每项只注入一次，结果见 `transient-budget-probe-result.json`。

原测试分别位于 `tests/test_owner_truth_candidate_extraction_worker.py:2954`（合同失败→瞬态→第三次合法恢复，原 max_attempts=3）、`:3047`（瞬态→合同失败不再获得额外合同重试）。429/timeout 原分类测试位于 `tests/test_owner_truth_live_long_memory_pipeline.py:360`、`:386`。这是本次独立补充证据，不能写成 Sol 原 92 项中已有 429/timeout worker 注入。

## 运行方式

从后端仓库执行（没有网络调用）：

```sh
PYTHONPATH=/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/provider-input-probe.py
PYTHONPATH=/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-05/evidence/cd/transient-budget-probe.py
```

初次启动预算探针未设置 PYTHONPATH，报 `ModuleNotFoundError: No module named 'tests'`；补环境路径后完成上述结果，未改变测试或产品逻辑。
